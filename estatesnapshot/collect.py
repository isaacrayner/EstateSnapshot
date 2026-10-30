"""Stage 1 - collect. Read-only and deliberately dumb: whole-table Resource Graph
dumps plus a few targeted calls for what Resource Graph does not hold. Nothing
here interprets the estate; that is the model's job."""
import datetime as dt
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import config, entra, metrics
from .azure import ARM, GRAPH, LOGANALYTICS, AzureError, Client

GUID = r"([0-9a-fA-F-]{36})$"

QUERIES = {
    "resources": """resources
| project id, name, type, kind, location, resourceGroup, subscriptionId, sku, plan,
          properties, tags, zones, identity, managedBy, extendedLocation""",

    "containers": """resourcecontainers
| project id, name, type, location, resourceGroup, subscriptionId, properties, tags""",

    "role_assignments": f"""authorizationresources
| where type =~ 'microsoft.authorization/roleassignments'
| extend g = tolower(extract(@'{GUID}', 1, tostring(properties.roleDefinitionId)))
| join kind=leftouter (
    authorizationresources
    | where type =~ 'microsoft.authorization/roledefinitions'
    | extend g = tolower(extract(@'{GUID}', 1, id))
    | summarize roleName = take_any(tostring(properties.roleName)), roleType = take_any(tostring(properties.type)) by g
  ) on g
| project id, subscriptionId, scope = tostring(properties.scope), principalId = tostring(properties.principalId),
          principalType = tostring(properties.principalType), roleDefinitionGuid = g, roleName, roleType""",

    "policy_assignments": """policyresources
| where type =~ 'microsoft.authorization/policyassignments'
| project id, subscriptionId, name, displayName = tostring(properties.displayName),
          scope = tostring(properties.scope), enforcementMode = tostring(properties.enforcementMode),
          definitionId = tostring(properties.policyDefinitionId)""",

    "policy_compliance": """policyresources
| where type =~ 'microsoft.policyinsights/policystates'
| summarize resources = dcount(tostring(properties.resourceId)),
            nonCompliant = dcountif(tostring(properties.resourceId), tostring(properties.complianceState) =~ 'NonCompliant')
            by assignmentId = tolower(tostring(properties.policyAssignmentId))""",

    "advisor": """advisorresources
| where type =~ 'microsoft.advisor/recommendations'
| project subscriptionId, category = tostring(properties.category), impact = tostring(properties.impact),
          problem = tostring(properties.shortDescription.problem), solution = tostring(properties.shortDescription.solution),
          impactedType = tostring(properties.impactedField), impactedName = tostring(properties.impactedValue),
          resourceId = tolower(tostring(properties.resourceMetadata.resourceId))""",

    "defender_plans": """securityresources
| where type =~ 'microsoft.security/pricings'
| project subscriptionId, plan = name, tier = tostring(properties.pricingTier), subPlan = tostring(properties.subPlan)""",

    "secure_scores": """securityresources
| where type =~ 'microsoft.security/securescores'
| project subscriptionId, current = todouble(properties.score.current), max = todouble(properties.score.max),
          percentage = todouble(properties.score.percentage)""",

    "security_findings": """securityresources
| where type =~ 'microsoft.security/assessments' and tostring(properties.status.code) =~ 'Unhealthy'
| project subscriptionId, finding = tostring(properties.displayName), severity = tostring(properties.metadata.severity),
          resourceId = tolower(tostring(properties.resourceDetails.Id))""",

    "backup_items": """recoveryservicesresources
| where type =~ 'microsoft.recoveryservices/vaults/backupfabrics/protectioncontainers/protecteditems'
| project vaultId = tolower(tostring(split(id, '/backupFabrics/')[0])), subscriptionId,
          sourceResourceId = tolower(tostring(properties.sourceResourceId)), friendlyName = tostring(properties.friendlyName),
          workloadType = tostring(properties.workloadType), policyName = tostring(properties.policyName),
          protectionState = tostring(properties.protectionState), lastBackupStatus = tostring(properties.lastBackupStatus),
          lastBackupTime = tostring(properties.lastBackupTime)""",

    "asr_items": """recoveryservicesresources
| where type =~ 'microsoft.recoveryservices/vaults/replicationfabrics/replicationprotectioncontainers/replicationprotecteditems'
| project vaultId = tolower(tostring(split(id, '/replicationFabrics/')[0])), subscriptionId,
          friendlyName = tostring(properties.friendlyName), health = tostring(properties.replicationHealth),
          state = tostring(properties.protectionState), primary = tostring(properties.primaryFabricFriendlyName),
          recovery = tostring(properties.recoveryFabricFriendlyName),
          sourceResourceId = tolower(tostring(properties.providerSpecificDetails.fabricObjectId))""",
}


def _cost_error(e):
    """Explain a Cost Management refusal in terms Isaac can act on."""
    msg = str(e)
    if "SubscriptionCostDisabled" in msg:
        return "cost visibility is disabled for this subscription (typical for CSP; the partner can enable it)"
    if "HTTP 403" in msg:
        return "no permission to read cost (needs Cost Management Reader or Reader with billing access)"
    if "429" in msg:
        return "Cost Management throttled the request; rerun collect later"
    return msg


def _cost(client, sub_id, days):
    end = dt.date.today()
    start = end - dt.timedelta(days=days)
    body = {
        "type": "ActualCost",
        "timeframe": "Custom",
        "timePeriod": {"from": start.isoformat() + "T00:00:00Z", "to": end.isoformat() + "T23:59:59Z"},
        "dataset": {
            "granularity": "None",
            "aggregation": {"totalCost": {"name": "Cost", "function": "Sum"}},
            "grouping": [{"type": "Dimension", "name": "ResourceGroupName"},
                         {"type": "Dimension", "name": "ServiceName"}],
        },
    }
    d = client.request("POST", f"{ARM}/subscriptions/{sub_id}/providers/Microsoft.CostManagement/query"
                               f"?api-version=2023-11-01", body, retries=3, max_wait=40, base_wait=5)
    cols = [c["name"] for c in d.get("properties", {}).get("columns", [])]
    rows = [dict(zip(cols, r)) for r in d.get("properties", {}).get("rows", [])]
    return [{"subscriptionId": sub_id, "resourceGroup": (r.get("ResourceGroupName") or "").lower(),
             "service": r.get("ServiceName"), "cost": round(float(r.get("Cost") or 0), 2),
             "currency": r.get("Currency")} for r in rows]


def _cost_months(client, sub_id, months, today=None):
    """Actual cost per calendar month, for the last `months` complete months plus the current
    part-month. Cost Management resolves a monthly window to whole months, so the current month
    comes back as its month-to-date figure and the model marks it as incomplete."""
    end = today or dt.date.today()
    y, m = end.year, end.month - (max(1, months) - 1)
    while m <= 0:
        m += 12
        y -= 1
    start = dt.date(y, m, 1)
    body = {
        "type": "ActualCost",
        "timeframe": "Custom",
        "timePeriod": {"from": start.isoformat() + "T00:00:00Z", "to": end.isoformat() + "T23:59:59Z"},
        "dataset": {"granularity": "Monthly", "aggregation": {"totalCost": {"name": "Cost", "function": "Sum"}}},
    }
    d = client.request("POST", f"{ARM}/subscriptions/{sub_id}/providers/Microsoft.CostManagement/query"
                               f"?api-version=2023-11-01", body, retries=3, max_wait=40, base_wait=5)
    cols = [c["name"] for c in d.get("properties", {}).get("columns", [])]
    rows = [dict(zip(cols, r)) for r in d.get("properties", {}).get("rows", [])]
    out = []
    for r in rows:
        ud = r.get("UsageDate")
        if isinstance(ud, float) and ud.is_integer():
            ud = int(ud)
        s = str(ud or "")
        if s.isdigit() and len(s) == 6:                       # 202609, the monthly-granularity form
            month = f"{s[:4]}-{s[4:]}"
        elif len(s) >= 7 and s[:4].isdigit() and s[5:7].isdigit():
            month = s[:7]
        else:
            continue
        out.append({"subscriptionId": sub_id, "month": month,
                    "cost": round(float(r.get("Cost") or 0), 2), "currency": r.get("Currency")})
    return out


def _cost_pricing(client, sub_id, days):
    """Cost split by pricing model (on demand, reservation, savings plan, spot): the commitment
    coverage that Cost Management exposes without any billing-account access."""
    end = dt.date.today()
    start = end - dt.timedelta(days=days)
    body = {"type": "ActualCost", "timeframe": "Custom",
            "timePeriod": {"from": start.isoformat() + "T00:00:00Z", "to": end.isoformat() + "T23:59:59Z"},
            "dataset": {"granularity": "None", "aggregation": {"totalCost": {"name": "Cost", "function": "Sum"}},
                        "grouping": [{"type": "Dimension", "name": "PricingModel"}]}}
    d = client.request("POST", f"{ARM}/subscriptions/{sub_id}/providers/Microsoft.CostManagement/query"
                               f"?api-version=2023-11-01", body, retries=3, max_wait=40, base_wait=5)
    cols = [c["name"] for c in d.get("properties", {}).get("columns", [])]
    return [{"subscriptionId": sub_id, "pricingModel": r.get("PricingModel") or "Unknown",
             "cost": round(float(r.get("Cost") or 0), 2), "currency": r.get("Currency")}
            for r in (dict(zip(cols, x)) for x in d.get("properties", {}).get("rows", []))]


def _usage(client, ws, days):
    """Billable ingestion by table from the workspace's Usage table (a data-plane query, read-only)."""
    cid = (ws.get("properties") or {}).get("customerId")
    if not cid:
        raise AzureError("workspace has no customerId")
    q = (f"Usage | where TimeGenerated > ago({days}d) | summarize GB = sum(Quantity) / 1024.0 by DataType, IsBillable "
         "| order by GB desc | take 40")
    d = client.request("POST", f"{LOGANALYTICS}/v1/workspaces/{cid}/query", {"query": q, "timespan": f"P{days}D"},
                       audience="loganalytics", retries=3, max_wait=40)
    t = (d.get("tables") or [{}])[0]
    cols = [c["name"] for c in t.get("columns", [])]
    rows = [dict(zip(cols, r)) for r in t.get("rows", [])]
    return {"id": ws["id"], "customerId": cid, "days": days,
            "tables": [{"table": r["DataType"], "gb": round(float(r["GB"] or 0), 3), "billable": bool(r["IsBillable"])} for r in rows]}


def _role_definitions(client, sub_ids):
    """Built-in role definitions are not in Resource Graph, so resolve names from ARM."""
    out = {}
    for sid in sub_ids:
        url = f"{ARM}/subscriptions/{sid}/providers/Microsoft.Authorization/roleDefinitions?api-version=2022-04-01"
        while url:
            d = client.request("GET", url)
            for r in d.get("value", []):
                p = r.get("properties") or {}
                out[r["name"].lower()] = {"roleName": p.get("roleName"), "roleType": p.get("type")}
            url = d.get("nextLink")
    return out


def _principals(client, ids):
    """Resolve role-assignment principal IDs to names through Microsoft Graph."""
    out = {}
    ids = sorted(i for i in ids if i)
    for i in range(0, len(ids), 1000):
        d = client.request("POST", f"{GRAPH}/v1.0/directoryObjects/getByIds",
                           {"ids": ids[i:i + 1000], "types": ["user", "group", "servicePrincipal"]},
                           audience="graph")
        for o in d.get("value", []):
            out[o["id"]] = {"displayName": o.get("displayName"),
                            "type": (o.get("@odata.type") or "").split(".")[-1],
                            "upn": o.get("userPrincipalName"),
                            "appId": o.get("appId"),
                            "spType": o.get("servicePrincipalType")}
    return out


# Config stages: (stage name, resource type asked, fetcher). Each fetcher is a GET-only ARM read.
CONFIG_STAGES = [
    ("cosmos_config", "microsoft.documentdb/databaseaccounts", config.cosmos),
    ("servicebus_config", "microsoft.servicebus/namespaces", config.servicebus),
    ("site_config", "microsoft.web/sites", config.site),
    ("aks_config", "microsoft.containerservice/managedclusters", config.aks),
    ("loganalytics_tables", "microsoft.operationalinsights/workspaces", config.workspace_tables),
    ("logic_targets", "microsoft.logic/workflows", config.logic_app),
]
OPTIONAL = ("config", "diagnostics", "metrics", "usage")      # values accepted by --skip
WORKERS = 8


class Run:
    """One collection run: raw files plus a manifest that records which stages finished,
    so an interrupted run can resume without recollecting them."""

    def __init__(self, out_dir, manifest, resume, log):
        self.out, self.raw, self.manifest, self.resume, self.log = out_dir, os.path.join(out_dir, "raw"), manifest, resume, log
        self.started = time.time()
        os.makedirs(self.raw, exist_ok=True)

    def path(self, name):
        return os.path.join(self.raw, name + ".json")

    def done(self, name):
        return self.resume and self.manifest["stages"].get(name) == "done" and os.path.exists(self.path(name))

    def load(self, name, default):
        p = self.path(name)
        return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else default

    def save(self, name, data, status="done", compact=False):
        with open(self.path(name), "w", encoding="utf-8") as f:
            json.dump(data, f, **({"separators": (",", ":")} if compact else {"indent": 1}))
        self.manifest["counts"][name] = len(data)
        self.manifest["stages"][name] = status
        self.flush()

    def flush(self):
        with open(os.path.join(self.out, "manifest.json"), "w", encoding="utf-8") as f:
            json.dump(self.manifest, f, indent=1)

    def say(self, msg):
        self.log(f"[{int(time.time() - self.started):>4}s] {msg}")

    def skip_done(self, name):
        """True (with a line of output) when a resumed run already holds this stage."""
        if self.done(name):
            self.say(f"  {name:<22} {self.manifest['counts'].get(name, 0):>6}  (already collected)")
            return True
        return False

    def fan_out(self, name, jobs, fn, compact=False):
        """Run fn(payload) for every (key, payload) job on a small thread pool. Finished records
        are checkpointed as the run goes; on resume only the missing or failed ones are redone."""
        have = {r["id"]: r for r in self.load(name, []) if r.get("id") and "error" not in r} if self.resume else {}
        todo = [(k, p) for k, p in jobs if k not in have]
        failed, step, n = {}, max(1, len(todo) // 10), 0
        if have and todo:
            self.say(f"  {name:<22} resuming: {len(have)} kept, {len(todo)} to fetch")
        with ThreadPoolExecutor(WORKERS) as ex:
            futs = {ex.submit(fn, p): k for k, p in todo}
            for fut in as_completed(futs):
                k, n = futs[fut], n + 1
                try:
                    have[k] = fut.result()
                except AzureError as e:
                    failed[k] = e
                    have[k] = {"id": k, "error": metrics.check_error(e)}
                if n % step == 0 and n < len(todo):
                    self.save(name, sorted(have.values(), key=lambda r: r["id"]), status="partial", compact=compact)
                    self.say(f"  {name:<22} {n:>4}/{len(todo)} fetched")
        self.save(name, sorted(have.values(), key=lambda r: r["id"]), compact=compact,
                  status="done" if not failed else "partial")
        if failed:
            first = next(iter(failed.values()))
            self.manifest["errors"][name] = f"{len(failed)} of {len(jobs)} failed; first: {metrics.check_error(first)[:200]}"
            self.flush()
            self.say(f"  {name:<22} {len(jobs) - len(failed):>6} ok, {len(failed)} FAILED: {metrics.check_error(first)[:120]}")
        else:
            self.manifest["errors"].pop(name, None)
            self.say(f"  {name:<22} {len(jobs):>6}")


def run(out_dir, tenant=None, subscription_ids=None, customer=None, cost_days=30, log=print,
        resume=False, skip=(), metrics_days=30, with_entra=False, cost_months=6):
    skip = set(skip or ())
    bad = skip - set(OPTIONAL)
    if bad:
        raise AzureError(f"--skip accepts {', '.join(OPTIONAL)}; got {', '.join(sorted(bad))}")
    client = Client(tenant)
    subs = client.subscriptions()
    if subscription_ids:
        wanted = {s.lower() for s in subscription_ids}
        subs = [s for s in subs if s["subscriptionId"].lower() in wanted
                or s["displayName"].lower() in wanted]
    if not subs:
        raise AzureError("no enabled subscriptions matched; check --tenant / --subscriptions and az login")
    sub_ids = [s["subscriptionId"] for s in subs]
    log(f"Scope: {len(subs)} subscription(s) - " + ", ".join(s["displayName"] for s in subs))

    manifest_path = os.path.join(out_dir, "manifest.json")
    if resume and os.path.exists(manifest_path):
        manifest = json.load(open(manifest_path, encoding="utf-8"))
        if {s["id"] for s in manifest["subscriptions"]} != set(sub_ids):
            raise AzureError("--resume: the subscriptions in scope differ from the interrupted run; use a fresh output folder")
        manifest.setdefault("stages", {})
        log(f"Resuming the run begun {manifest['capturedAt']}: "
            f"{sum(1 for v in manifest['stages'].values() if v == 'done')} stage(s) already complete")
    else:
        resume = False
        manifest = {
            "tool": "EstateSnapshot", "version": 2,
            "customer": customer, "tenantId": tenant or subs[0].get("tenantId"),
            "capturedAt": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "subscriptions": [{"id": s["subscriptionId"], "name": s["displayName"],
                               "quotaId": (s.get("subscriptionPolicies") or {}).get("quotaId"),
                               "spendingLimit": (s.get("subscriptionPolicies") or {}).get("spendingLimit")} for s in subs],
            "costDays": cost_days, "costMonths": cost_months, "metricsDays": metrics_days,
            "skipped": sorted(skip), "entra": with_entra,
            "counts": {}, "errors": {}, "stages": {},
        }
    r = Run(out_dir, manifest, resume, log)
    r.flush()

    # -- Resource Graph tables ------------------------------------------------
    for name, q in QUERIES.items():
        if r.skip_done(name):
            continue
        try:
            rows = client.arg(q, sub_ids)
            r.save(name, rows)
            manifest["errors"].pop(name, None)
            r.say(f"  {name:<22} {len(rows):>6}")
        except AzureError as e:
            manifest["errors"][name] = str(e)
            r.save(name, [], status="failed")
            r.say(f"  {name:<22}  FAILED: {e}")
    resources = r.load("resources", [])

    # -- cost -----------------------------------------------------------------
    if not r.skip_done("costs"):
        costs = []
        for s in subs:
            try:
                rows = _cost(client, s["subscriptionId"], cost_days)
                costs += rows
                manifest["errors"].pop(f"cost:{s['displayName']}", None)
                r.say(f"    cost {s['displayName']:<28} {len(rows):>4} rows")
            except AzureError as e:
                msg = _cost_error(e)
                manifest["errors"][f"cost:{s['displayName']}"] = msg
                r.say(f"    cost {s['displayName']:<28} unavailable: {msg}")
        r.save("costs", costs)
        r.say(f"  {'costs':<22} {len(costs):>6}" + ("  (some subscriptions failed)" if any(k.startswith('cost:') for k in manifest['errors']) else ""))

    if not r.skip_done("cost_months"):
        monthly = []
        for s in subs:
            try:
                rows = _cost_months(client, s["subscriptionId"], cost_months)
                monthly += rows
                manifest["errors"].pop(f"cost_months:{s['displayName']}", None)
                r.say(f"    months {s['displayName']:<26} {len(rows):>4} rows")
            except AzureError as e:
                msg = _cost_error(e)
                manifest["errors"][f"cost_months:{s['displayName']}"] = msg
                r.say(f"    months {s['displayName']:<26} unavailable: {msg}")
        r.save("cost_months", monthly)
        r.say(f"  {'cost_months':<22} {len(monthly):>6}")

    if not r.skip_done("cost_pricing"):
        pricing = []
        for s in subs:
            if f"cost:{s['displayName']}" in manifest["errors"]:
                continue
            try:
                pricing += _cost_pricing(client, s["subscriptionId"], cost_days)
            except AzureError as e:
                manifest["errors"][f"cost_pricing:{s['displayName']}"] = str(e)[:200]
        r.save("cost_pricing", pricing)
        r.say(f"  {'cost_pricing':<22} {len(pricing):>6}")

    # -- identity names ---------------------------------------------------------
    if not r.skip_done("role_definitions"):
        try:
            defs = _role_definitions(client, sub_ids)
            manifest["errors"].pop("role_definitions", None)
        except AzureError as e:
            manifest["errors"]["role_definitions"] = str(e)
            defs = {}
        r.save("role_definitions", defs)
        r.say(f"  {'role_definitions':<22} {len(defs):>6}")

    if not r.skip_done("principals"):
        try:
            principals = _principals(client, {x.get("principalId") for x in r.load("role_assignments", [])})
            manifest["errors"].pop("principals", None)
        except AzureError as e:
            manifest["errors"]["principals"] = str(e)
            principals = {}
        r.save("principals", principals)
        r.say(f"  {'principals':<22} {len(principals):>6}")

    # -- service configuration (E1) ---------------------------------------------
    if "config" not in skip:
        for name, rtype, fn in CONFIG_STAGES:
            items = [x for x in resources if (x.get("type") or "").lower() == rtype]
            if items and not r.skip_done(name):
                r.fan_out(name, [(x["id"], x) for x in items], lambda x, fn=fn: fn(client, x))

    # -- diagnostic settings (E6) -------------------------------------------------
    if "diagnostics" not in skip and not r.skip_done("diagnostics"):
        targets = [{"id": x["id"], "type": (x.get("type") or "").lower()} for x in resources
                   if (x.get("type") or "").lower() in config.DIAGNOSTIC_TYPES]
        targets += [{"id": f"/subscriptions/{sid}", "type": "subscription"} for sid in sub_ids]
        r.fan_out("diagnostics", [(t["id"], t) for t in targets], lambda t: config.diagnostics(client, t))

    # -- measured load (E2) -------------------------------------------------------
    if "metrics" not in skip:
        batches = metrics.plan(resources, metrics_days)
        if batches and not r.skip_done("metrics"):
            r.say(f"  metrics: {len(batches)} getBatch call(s) for {metrics_days} days")
            r.fan_out("metrics", [(b["id"], b) for b in batches], lambda b: metrics.fetch(client, b, metrics_days), compact=True)

    # -- Log Analytics usage (E7) -------------------------------------------------
    if "usage" not in skip:
        wss = [x for x in resources if (x.get("type") or "").lower() == "microsoft.operationalinsights/workspaces"]
        if wss and not r.skip_done("loganalytics_usage"):
            r.fan_out("loganalytics_usage", [(x["id"], x) for x in wss], lambda x: _usage(client, x, 30))

    # -- Entra (opt-in) -----------------------------------------------------------
    if with_entra and not r.skip_done("entra"):
        data, gaps = entra.collect(client, log=r.say)
        r.save("entra", data)
        manifest["errors"].update(gaps)
        r.say(f"  {'entra':<22} {len(data):>6} of {len(entra.SCOPES)} part(s)")

    manifest["completedAt"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    r.flush()
    return manifest
