"""Model, part 2 - facts derived from the collected service configuration, metrics,
diagnostic settings, usage, billing context and Entra data (raw files that older
snapshots do not have; every method returns empty results when its file is absent).
Same rule as model.py: facts only, no scores, no recommendations."""
import re
from collections import Counter, defaultdict

from . import catalogue as cat
from . import metrics as mx
from .util import g, lc, name_of

# raw file -> attribute name; each holds {lowercase resource id: record}
CONFIG_FILES = ["cosmos_config", "servicebus_config", "site_config", "aks_config", "loganalytics_tables",
                "logic_targets", "diagnostics", "loganalytics_usage"]

QUOTA_NAMES = {
    "payasyougo_2014-09-01": "Pay-As-You-Go", "enterpriseagreement_2014-09-01": "Enterprise Agreement",
    "csp_2015-05-01": "CSP", "csp_2012-01-01": "CSP", "msdn_2014-09-01": "Visual Studio subscriber",
    "msdndevtest_2014-09-01": "Visual Studio Dev/Test", "freetrial_2014-09-01": "Free trial",
    "sponsored_2016-01-01": "Sponsored", "azureforstudents_2018-01-01": "Azure for Students",
    "internal_2014-09-01": "Microsoft internal", "mpn_2014-09-01": "Microsoft Partner Network",
    "dreamspark_2015-02-01": "DreamSpark",
}
NO_RESTRICTION = 2147483647          # priority of the implicit "Allow all" access restriction


def _ms(t):
    return (t or {}).get("throughput")


def _tp(t):
    if not t:
        return None
    if t.get("autoscaleMax"):
        return f"autoscale up to {t['autoscaleMax']:,} RU/s"
    return f"{t['throughput']:,} RU/s manual" if t.get("throughput") is not None else None


def parse_runtime(web, kind=""):
    """Runtime of a site from its config/web: stack, major version and a display string. Container
    sites report the image instead. Function apps cannot be told apart as in-process or isolated
    from here (that is an app setting, which the snapshot does not read)."""
    web = web or {}
    out = {"stack": "", "major": "", "display": "", "image": "", "registry": ""}
    lfx = web.get("linuxFxVersion") or ""
    wfx = web.get("windowsFxVersion") or ""
    fx = lfx or (wfx if wfx.upper().startswith("DOCKER|") else "")
    if fx.upper().startswith("DOCKER|"):
        image = fx.split("|", 1)[1]
        first = image.split("/", 1)[0]
        out.update(stack="container", image=image, display=f"Container {image}",
                   registry=first.lower() if ("." in first or ":" in first) and "/" in image else "docker hub")
        return out
    if lfx and "|" in lfx:
        stack, ver = lfx.split("|", 1)
        s = stack.lower().replace("dotnet-isolated", "dotnet").replace("dotnetcore", "dotnet")
        m = re.match(r"(\d+)", ver)
        out.update(stack=s, major=m.group(1) if m else "", display=f"{lfx}")
        return out
    nf = web.get("netFrameworkVersion") or ""
    m = re.match(r"v(\d+)\.(\d+)", nf)
    if m and int(m.group(1)) >= 5:
        out.update(stack="dotnet", major=m.group(1), display=f".NET {m.group(1)} ({nf})")
    elif "functionapp" in lc(kind) and m:
        out.update(stack="dotnetfx", major="4", display=f".NET Framework ({nf})")
    for key, stack in (("nodeVersion", "node"), ("pythonVersion", "python"), ("javaVersion", "java"),
                       ("phpVersion", "php"), ("powerShellVersion", "powershell")):
        v = web.get(key)
        if v and not out["stack"]:
            mm = re.search(r"(\d+)", v)
            out.update(stack=stack, major=mm.group(1) if mm else "", display=f"{stack} {v}")
    if not out["stack"] and m:
        out.update(stack="dotnetfx", major="4", display=f".NET Framework ({nf})")
    return out


class ConfigFacts:
    # -- loading ---------------------------------------------------------------

    def _load_config(self, load):
        self.cfg = {}
        for name in CONFIG_FILES:
            recs = load(name, [])
            self.cfg[name] = {lc(r["id"]): r for r in recs if isinstance(r, dict) and r.get("id") and "error" not in r}
        self.metric_batches = [b for b in load("metrics", []) if "error" not in b]
        self.cost_pricing = load("cost_pricing", [])
        self.entra_raw = load("entra", {})
        self.series = defaultdict(list)
        for b in self.metric_batches:
            for s in b.get("series", []):
                self.series[(lc(s["resourceId"]), s["metric"])].append(s)

    def collected(self, stage):
        return bool(self.cfg.get(stage)) or self.manifest.get("stages", {}).get(stage) == "done"

    # -- Cosmos DB (section 6) ---------------------------------------------------

    def cosmos_config(self):
        accounts, detail = [], []
        for r in self.of("microsoft.documentdb/databaseaccounts"):
            p = r.get("properties") or {}
            rec = self.cfg["cosmos_config"].get(lc(r["id"]))
            bp = p.get("backupPolicy") or {}
            if lc(bp.get("type")) == "continuous":
                backup = "continuous " + str(g(bp, "continuousModeProperties", "tier", default="")).strip()
            elif bp:
                pp = bp.get("periodicModeProperties") or {}
                backup = f"periodic, every {pp.get('backupIntervalInMinutes')} min, keep {pp.get('backupRetentionIntervalInHours')} h" if pp else "periodic"
            else:
                backup = ""
            row = {**self.where(r), "name": r["name"],
                   "consistency": g(p, "consistencyPolicy", "defaultConsistencyLevel", default=""),
                   "regions": len(p.get("locations") or []), "multiWrite": bool(p.get("enableMultipleWriteLocations")),
                   "backup": backup.strip(),
                   "keyAuth": "disabled" if p.get("disableLocalAuth") else "enabled",
                   "publicNetworkAccess": p.get("publicNetworkAccess") or "",
                   "serverless": "EnableServerless" in [c.get("name") for c in p.get("capabilities") or []],
                   "databases": "", "containers": "", "throughput": "not collected"}
            if rec:
                dbs = rec.get("databases", [])
                parts, nc = [], 0
                for d in dbs:
                    cs = d.get("containers", [])
                    nc += len(cs)
                    own = [c for c in cs if c.get("throughput")]
                    if rec.get("serverless"):
                        parts.append(f"{d['name']}: serverless, {len(cs)} container(s)")
                    elif d.get("throughput"):
                        parts.append(f"{d['name']}: shared {_tp(d['throughput'])} across {len(cs)} container(s)"
                                     + (f"; {len(own)} also dedicated" if own else ""))
                    elif own:
                        parts.append(f"{d['name']}: dedicated on {len(own)} of {len(cs)} container(s)")
                    else:
                        parts.append(f"{d['name']}: no throughput reported, {len(cs)} container(s)")
                    for c in cs:
                        mode = ("serverless" if rec.get("serverless") else "dedicated" if c.get("throughput")
                                else "shared (database)" if d.get("throughput") else "not reported")
                        detail.append({**self.where(r), "account": r["name"], "database": d["name"], "container": c["name"],
                                       "partitionKey": ", ".join(c.get("partitionKey") or []), "mode": mode,
                                       "throughput": _tp(c.get("throughput")) or (_tp(d.get("throughput")) if d.get("throughput") else ""),
                                       "defaultTtl": c.get("defaultTtl") if c.get("defaultTtl") is not None else ""})
                row.update(databases=len(dbs), containers=nc, throughput="; ".join(parts) or "none")
            accounts.append(row)
        return {"accounts": sorted(accounts, key=lambda x: (x["subscription"], x["name"])), "containers": detail}

    # -- Service Bus (section 6) ----------------------------------------------------

    def servicebus_config(self):
        rows, entities = [], []
        for r in self.of("microsoft.servicebus/namespaces"):
            p = r.get("properties") or {}
            rec = self.cfg["servicebus_config"].get(lc(r["id"]))
            row = {**self.where(r), "name": r["name"], "sku": g(r, "sku", "name", default=""),
                   "keyAuth": "disabled" if p.get("disableLocalAuth") else "enabled",
                   "publicNetworkAccess": p.get("publicNetworkAccess") or "",
                   "premiumPartitions": p.get("premiumMessagingPartitions") or "",
                   "topics": "", "queues": "", "partitioned": "", "topicsWithoutSubscriptions": "", "deadLetters": "",
                   "topWithoutSubscriptions": []}
            if rec:
                topics, queues = rec.get("topics", []), rec.get("queues", [])
                empty = [t["name"] for t in topics if not t.get("subscriptions") and not t.get("subscriptionCount")]
                dl = 0
                for t in topics:
                    tdl = g(t, "countDetails", "deadLetterMessageCount", default=0) or 0
                    for s in t.get("subscriptions", []):
                        sdl = g(s, "countDetails", "deadLetterMessageCount", default=0) or 0
                        dl += sdl
                        entities.append({**self.where(r), "namespace": r["name"], "kind": "subscription",
                                         "entity": f"{t['name']}/{s['name']}", "partitioned": "",
                                         "active": g(s, "countDetails", "activeMessageCount", default=0),
                                         "deadLetters": sdl, "maxDeliveryCount": s.get("maxDeliveryCount")})
                    entities.append({**self.where(r), "namespace": r["name"], "kind": "topic", "entity": t["name"],
                                     "partitioned": bool(t.get("enablePartitioning")), "active": "",
                                     "deadLetters": tdl, "maxDeliveryCount": "",
                                     "subscriptions": len(t.get("subscriptions", []))})
                    dl += tdl
                for q in queues:
                    qdl = g(q, "countDetails", "deadLetterMessageCount", default=0) or 0
                    dl += qdl
                    entities.append({**self.where(r), "namespace": r["name"], "kind": "queue", "entity": q["name"],
                                     "partitioned": bool(q.get("enablePartitioning")),
                                     "active": g(q, "countDetails", "activeMessageCount", default=0),
                                     "deadLetters": qdl, "maxDeliveryCount": q.get("maxDeliveryCount")})
                row.update(topics=len(topics), queues=len(queues), deadLetters=dl,
                           partitioned=sum(1 for e in topics + queues if e.get("enablePartitioning")),
                           topicsWithoutSubscriptions=len(empty), topWithoutSubscriptions=empty)
            rows.append(row)
        return {"namespaces": sorted(rows, key=lambda x: (x["subscription"], x["name"])), "entities": entities}

    # -- sites (section 5) -------------------------------------------------------------

    def registries(self):
        """Container registries in the capture, by login server."""
        return {lc(g(r, "properties", "loginServer", default="") or r["name"] + ".azurecr.io"): r
                for r in self.of("microsoft.containerregistry/registries")}

    def site_facts(self):
        regs = self.registries()
        rows = []
        for r in self.of("microsoft.web/sites"):
            p = r.get("properties") or {}
            rec = self.cfg["site_config"].get(lc(r["id"]))
            web = (rec or {}).get("web") or {}
            rt = parse_runtime(web, r.get("kind")) if rec else {"stack": "", "major": "", "display": "", "image": "", "registry": ""}
            reg = rt["registry"]
            in_capture = ""
            if reg and reg != "docker hub":
                hit = regs.get(reg)
                in_capture = (f"{hit['name']} ({self.sub(hit['subscriptionId'])})" if hit else "not in this capture")
            restrictions = [x for x in web.get("ipSecurityRestrictions") or [] if (x.get("priority") or 0) < NO_RESTRICTION]
            bit = web.get("use32BitWorkerProcess")
            rows.append({**self.where(r), "name": r["name"], "kind": r.get("kind") or "",
                         "isFunction": "functionapp" in lc(r.get("kind")),
                         "runtime": rt["display"] or ("not collected" if not rec else ""),
                         "stack": rt["stack"], "major": rt["major"],
                         "workerBits": "" if bit is None else "32-bit" if bit else "64-bit",
                         "alwaysOn": web.get("alwaysOn"), "healthCheckPath": web.get("healthCheckPath") or "",
                         "scaleLimit": web.get("functionAppScaleLimit"),
                         "image": rt["image"], "registry": reg, "registryInCapture": in_capture,
                         "publicNetworkAccess": p.get("publicNetworkAccess") or "Enabled",
                         "accessRestrictions": len(restrictions), "collected": bool(rec),
                         "plan": name_of(p.get("serverFarmId"))})
        return sorted(rows, key=lambda x: (x["subscription"], x["name"]))

    def site_summary(self):
        rows = self.site_facts()
        coll = [r for r in rows if r["collected"]]
        public = [r for r in rows if lc(r["publicNetworkAccess"]) != "disabled"]
        return {
            "sites": len(rows), "collected": len(coll),
            "runtimes": Counter((("Functions " if r["isFunction"] else "Web ") + r["runtime"]) for r in coll if r["runtime"]).most_common(),
            "bits32": [r["name"] for r in coll if r["workerBits"] == "32-bit"],
            "bits64": sum(1 for r in coll if r["workerBits"] == "64-bit"),
            "noHealthCheck": [r["name"] for r in coll if not r["healthCheckPath"]],
            "registries": Counter((r["registry"], r["registryInCapture"]) for r in coll if r["registry"]).most_common(),
            "publicAccess": len(public), "publicRestricted": sum(1 for r in public if r["accessRestrictions"]),
            "privateOnly": len(rows) - len(public),
        }

    # -- AKS (section 4) ------------------------------------------------------------------

    def aks_props(self, r):
        """Cluster properties, from the direct ARM read when collected (it carries the ingress profile)."""
        rec = self.cfg["aks_config"].get(lc(r["id"]))
        return {**(r.get("properties") or {}), **((rec or {}).get("properties") or {})}

    def aks_facts(self, r):
        p = self.aks_props(r)
        wr = g(p, "ingressProfile", "webAppRouting") or {}
        ranges = g(p, "apiServerAccessProfile", "authorizedIPRanges") or []
        private = bool(g(p, "apiServerAccessProfile", "enablePrivateCluster"))
        return {"ingress": ("application routing on" + (f" ({g(wr, 'nginx', 'defaultIngressControllerType')})"
                                                          if g(wr, "nginx", "defaultIngressControllerType") else " (NGINX)"))
                if wr.get("enabled") else "application routing off",
                "appRoutingNginx": bool(wr.get("enabled")),
                "plugin": g(p, "networkProfile", "networkPlugin", default=""),
                "pluginMode": g(p, "networkProfile", "networkPluginMode", default="") or "",
                "policy": g(p, "networkProfile", "networkPolicy", default="") or "",
                "authorisedRanges": ("private cluster" if private else f"{len(ranges)} range(s)" if ranges else "none (open to the internet)"),
                "hasRanges": bool(ranges), "private": private}

    # -- Logic Apps and registries (cross-reference facts) -------------------------------------

    def logic_hosts(self):
        """Hostnames each Logic App talks to, from its collected connection targets and the
        connection resources' non-secret parameters."""
        conns = {}
        for c in self.of("microsoft.web/connections"):
            p = c.get("properties") or {}
            pv = {**(p.get("nonSecretParameterValues") or {}), **(p.get("parameterValues") or {})}
            host = next((str(pv[k]) for k in ("hostName", "serverAddress", "server", "siteUrl", "gateway") if pv.get(k)), "")
            conns[lc(c["id"])] = {"name": c["name"], "api": g(p, "api", "name", default=""), "host": host}
        out = {}
        for rid, rec in self.cfg["logic_targets"].items():
            hosts = set()
            for u in rec.get("uris", []):
                m = re.match(r"https?://([^/:]+)", u.get("uri") or "")
                if m:
                    hosts.add(m.group(1).lower())
            used = []
            for c in rec.get("connections", []):
                cid = lc((rec.get("connectionIds") or {}).get(c["connection"]))
                ci = conns.get(cid)
                if ci:
                    used.append(ci)
                    if ci["host"]:
                        hosts.add(lc(ci["host"]))
            out[rid] = {"hosts": sorted(hosts), "connections": used}
        return out

    # -- measured load (section 5/6/10, E2) ------------------------------------------------------

    def _vals(self, rid, metric, field, dim=None):
        out = []
        for s in self.series.get((lc(rid), metric), []):
            if dim and any(lc((s.get("dimensions") or {}).get(k)) != lc(v) for k, v in dim.items()):
                continue
            out += [p[field] for p in s["points"] if field in p]
        return out

    def measured_load(self):
        if not self.metric_batches:
            return {}
        days = self.manifest.get("metricsDays") or 30
        want = mx.expected_points(days)
        start = min((b["start"] for b in self.metric_batches), default="")
        end = max((b["end"] for b in self.metric_batches), default="")

        def cover(rid, metric, field):
            n = len(self._vals(rid, metric, field))
            return f"{round(100 * n / want)}%" if want else ""

        cosmos, containers = [], []
        for r in self.of("microsoft.documentdb/databaseaccounts"):
            rid = lc(r["id"])
            ss = self.series.get((rid, "TotalRequests"))
            if not ss and not self.series.get((rid, "NormalizedRUConsumption")):
                continue
            total = thr = 0.0
            by = defaultdict(lambda: [0.0, 0.0])
            for s in ss or []:
                t = sum(p.get("total", 0) for p in s["points"])
                coll = (s.get("dimensions") or {}).get("collectionname") or "(all)"
                total += t
                by[coll][0] += t
                if (s.get("dimensions") or {}).get("statuscode") == "429":
                    thr += t
                    by[coll][1] += t
            mx_ru = self._vals(rid, "NormalizedRUConsumption", "maximum")
            avg_ru = self._vals(rid, "NormalizedRUConsumption", "average")
            full_days = {p["timeStamp"][:10] for s in self.series.get((rid, "NormalizedRUConsumption"), [])
                         for p in s["points"] if p.get("maximum", 0) >= 99.5}
            all_days = {p["timeStamp"][:10] for s in self.series.get((rid, "NormalizedRUConsumption"), []) for p in s["points"]}
            du = self._vals(rid, "DataUsage", "maximum")
            cosmos.append({**self.where(r), "account": r["name"], "requests": int(total), "throttled429": int(thr),
                           "throttledPct": round(100 * thr / total, 1) if total else None,
                           "normalisedRuMax": round(max(mx_ru), 1) if mx_ru else None,
                           "normalisedRuAvg": round(sum(avg_ru) / len(avg_ru), 1) if avg_ru else None,
                           "daysAtFullRu": f"{len(full_days)} of {len(all_days)}" if all_days else "",
                           "dataGB": round(max(du) / 1024 ** 3, 1) if du else None,
                           "truncated": bool(ss) and len(ss) >= 100, "coverage": cover(rid, "NormalizedRUConsumption", "maximum")})
            for coll, (t, th) in sorted(by.items(), key=lambda kv: -kv[1][0]):
                if coll != "(all)":
                    containers.append({"account": r["name"], "container": coll, "requests": int(t), "throttled429": int(th),
                                       "throttledPct": round(100 * th / t, 1) if t else None})
        sb = []
        for r in self.of("microsoft.servicebus/namespaces"):
            rid = lc(r["id"])
            if not any(self.series.get((rid, m)) for m in ("IncomingMessages", "DeadletteredMessages", "ActiveMessages")):
                continue
            inc, dl, act = (self._vals(rid, "IncomingMessages", "total"), self._vals(rid, "DeadletteredMessages", "maximum"),
                            self._vals(rid, "ActiveMessages", "maximum"))
            dla = self._vals(rid, "DeadletteredMessages", "average")
            sb.append({**self.where(r), "namespace": r["name"], "incoming": int(sum(inc)),
                       "deadLetterPeak": int(max(dl)) if dl else None, "deadLetterAvg": round(sum(dla) / len(dla)) if dla else None,
                       "activePeak": int(max(act)) if act else None, "coverage": cover(rid, "IncomingMessages", "total")})
        plans = []
        for r in self.of("microsoft.web/serverfarms"):
            rid = lc(r["id"])
            if not self.series.get((rid, "CpuPercentage")):
                continue
            row = {**self.where(r), "plan": r["name"], "sku": g(r, "sku", "name", default=""),
                   "workers": g(r, "sku", "capacity"), "coverage": cover(rid, "CpuPercentage", "average")}
            for key, m in (("cpu", "CpuPercentage"), ("mem", "MemoryPercentage")):
                a, mxv = self._vals(rid, m, "average"), self._vals(rid, m, "maximum")
                row[key + "Avg"] = round(sum(a) / len(a), 1) if a else None
                row[key + "Peak"] = round(max(mxv), 1) if mxv else None
            plans.append(row)
        sql = []
        for r in self.of("microsoft.sql/servers/databases", "microsoft.sql/servers/elasticpools"):
            rid = lc(r["id"])
            if not self.series.get((rid, "cpu_percent")):
                continue
            a, mxv = self._vals(rid, "cpu_percent", "average"), self._vals(rid, "cpu_percent", "maximum")
            sql.append({**self.where(r), "name": r["name"], "type": cat.friendly(r["type"]), "sku": g(r, "sku", "name", default=""),
                        "cpuAvg": round(sum(a) / len(a), 1) if a else None, "cpuPeak": round(max(mxv), 1) if mxv else None,
                        "coverage": cover(rid, "cpu_percent", "average")})
        return {"window": f"{start[:10]} to {end[:10]}, hourly samples", "days": days, "cosmos": cosmos,
                "containers": containers, "servicebus": sb, "plans": sorted(plans, key=lambda x: -(x["cpuPeak"] or 0)),
                "sql": sorted(sql, key=lambda x: -(x["cpuPeak"] or 0))}

    # -- diagnostic settings (section 10, E6) --------------------------------------------------------

    def diagnostic_settings(self):
        """One row per resource with at least one setting, plus coverage by service type."""
        recs = self.cfg["diagnostics"]
        if not recs:
            return {}
        rows, cover = [], defaultdict(lambda: {"resources": 0, "withSettings": 0, "none": 0, "unsupported": 0})
        dest = Counter()
        for rid, rec in recs.items():
            n = self.nodes.get(rid)
            t = rec.get("type") or ""
            if t == "subscription":
                label, svc, where = f"Activity Log: {self.sub(name_of(rid))}", "Subscription Activity Log", {}
            elif n:
                label, svc, where = n["name"], cat.friendly(t), self.where(n)
            else:
                continue
            settings = [s for sc in rec.get("scopes", []) if sc.get("supported") for s in sc.get("settings", [])]
            supported = any(sc.get("supported") for sc in rec.get("scopes", []))
            c = cover[svc]
            c["resources"] += 1
            if not supported:
                c["unsupported"] += 1
            elif settings:
                c["withSettings"] += 1
            else:
                c["none"] += 1
            for s in settings:
                ds = []
                if s.get("workspaceId"):
                    ds.append("workspace " + name_of(s["workspaceId"]))
                if s.get("storageAccountId"):
                    ds.append("storage " + name_of(s["storageAccountId"]))
                if s.get("eventHubAuthorizationRuleId"):
                    ds.append("event hub " + (s.get("eventHubName") or name_of(s["eventHubAuthorizationRuleId"].split("/authorizationrules/")[0])))
                if s.get("marketplacePartnerId"):
                    ds.append("partner solution")
                for d in ds:
                    dest[d] += 1
                rows.append({**where, "resource": label, "service": svc, "setting": s["name"], "destinations": ", ".join(ds),
                             "logCategories": len(s.get("logs") or []), "logsOff": len(s.get("logsDisabled") or []),
                             "metrics": ", ".join(s.get("metrics") or [])})
        by_type = [{"service": k, **v} for k, v in sorted(cover.items(), key=lambda kv: (-kv[1]["resources"], kv[0]))]
        return {"settings": sorted(rows, key=lambda x: (x["service"], x["resource"], x["setting"])), "coverage": by_type,
                "destinations": dest.most_common(),
                "asked": sum(v["resources"] for v in cover.values()),
                "withSettings": sum(v["withSettings"] for v in cover.values()),
                "none": sum(v["none"] for v in cover.values())}

    def log_analytics_usage(self):
        """Billable ingestion by table per workspace (30-day Usage query) and table retention overrides."""
        out = []
        for r in self.of("microsoft.operationalinsights/workspaces"):
            rid = lc(r["id"])
            u = self.cfg["loganalytics_usage"].get(rid)
            t = self.cfg["loganalytics_tables"].get(rid)
            if not u and not t:
                continue
            default = (r.get("properties") or {}).get("retentionInDays")
            tabs = (t or {}).get("tables", [])
            override = [x for x in tabs if x.get("retentionInDays") not in (None, default, -1) or (x.get("totalRetentionInDays") or 0) > (default or 0)]
            billable = [x for x in (u or {}).get("tables", []) if x["billable"]]
            total = sum(x["gb"] for x in billable)
            days = (u or {}).get("days") or 30
            out.append({**self.where(r), "workspace": r["name"], "days": days,
                        "billableGB": round(total, 1) if u else None, "perMonthGB": round(total * 30 / days, 1) if u else None,
                        "topTables": [{"table": x["table"], "gb": x["gb"], "share": round(100 * x["gb"] / total) if total else 0} for x in billable[:8]],
                        "tables": len(tabs) if t else None, "basicPlan": sum(1 for x in tabs if lc(x.get("plan")) == "basic") if t else None,
                        "overrides": [f"{x['name']} {x.get('retentionInDays')}d" + (f" (total {x['totalRetentionInDays']}d)" if x.get('totalRetentionInDays') else "") for x in override][:12]})
        return out

    # -- billing context (section 11, E7) ----------------------------------------------------------------

    def billing(self):
        cost = self.cost_by_sub()
        rows = []
        for s in self.manifest["subscriptions"]:
            q = s.get("quotaId")
            rows.append({"subscription": s["name"], "quotaId": q or "not recorded",
                         "offer": QUOTA_NAMES.get(lc(q), q or "not recorded"),
                         "spendingLimit": s.get("spendingLimit") or "", "cost": cost.get(s["id"])})
        pricing = defaultdict(float)
        for c in self.cost_pricing:
            pricing[c["pricingModel"]] += c["cost"]
        tot = sum(pricing.values())
        return {"subscriptions": rows,
                "pricingModels": [(k, round(v, 2), round(100 * v / tot) if tot else 0)
                                  for k, v in sorted(pricing.items(), key=lambda kv: -kv[1])],
                "recorded": any(s.get("quotaId") for s in self.manifest["subscriptions"])}

    # -- retirements (E9) --------------------------------------------------------------------------------

    def retirements(self, path=None):
        from . import register
        return register.match(self, register.load(path))

    # -- Entra (E9, opt-in) ----------------------------------------------------------------------------------

    def entra(self):
        d = self.entra_raw or {}
        if not d:
            return {}
        pols = d.get("conditional_access") or []
        roles = d.get("directory_roles") or []
        org = (d.get("organization") or [{}])[0] if d.get("organization") else {}
        doms = d.get("domains") or []
        return {
            "organization": org, "federatedDomains": [x["id"] for x in doms if lc(x.get("authenticationType")) == "federated"],
            "managedDomains": [x["id"] for x in doms if lc(x.get("authenticationType")) == "managed"],
            "policies": pols, "policyStates": Counter(p["state"] for p in pols),
            "mfaPolicies": sum(1 for p in pols if "mfa" in (p.get("grant") or []) or p.get("authStrength")),
            "roles": [{"role": r["role"], "members": len(r["members"]), "who": ", ".join(
                (m.get("upn") or m.get("name") or "?") for m in r["members"][:8]) + (" ..." if len(r["members"]) > 8 else "")}
                for r in sorted(roles, key=lambda r: -len(r["members"])) if r["members"]],
            "have": sorted(k for k, v in d.items() if v is not None),
        }

    # -- referenced but not captured (E3) ---------------------------------------------------------------------

    def scope_gaps(self):
        """Every subscription outside the capture that something inside it points at, with the
        referencing resource and the property that carries the reference."""
        refs = self.outside_scope()
        in_scope = set(self.sub_names)
        extra = []
        # diagnostic setting destinations
        for rid, rec in self.cfg["diagnostics"].items():
            n = self.nodes.get(rid)
            for sc in rec.get("scopes", []):
                for s in sc.get("settings", []) if sc.get("supported") else []:
                    for key, label in (("workspaceId", "Log Analytics workspace"), ("storageAccountId", "Storage account"),
                                       ("eventHubAuthorizationRuleId", "Event Hub")):
                        t = s.get(key)
                        parts = lc(t).split("/")
                        if t and len(parts) > 8 and parts[2] not in in_scope:
                            extra.append({"from": n["name"] if n else f"Activity Log of {self.sub(name_of(rid))}",
                                          "fromService": cat.friendly(rec.get("type")) if n else "Subscription",
                                          "toType": "/".join(parts[6:8]), "to": parts[8], "subscription": self.sub(parts[2]),
                                          "subscriptionId": parts[2], "via": f"diagnostic setting {s['name']} ({label})"})
        # container registries named by site images that are not in the capture
        for r in self.site_facts():
            if r["registry"] and r["registry"] != "docker hub" and r["registryInCapture"] == "not in this capture":
                extra.append({"from": r["name"], "fromService": "App Service", "toType": "container registry (by host name)",
                              "to": r["registry"], "subscription": "unknown (host name only)", "subscriptionId": "",
                              "via": "container image"})
        rows = refs + extra
        by = defaultdict(lambda: {"references": 0, "from": Counter(), "targets": Counter(), "via": Counter()})
        for x in rows:
            k = x.get("subscriptionId") or x["subscription"]
            by[k]["references"] += 1
            by[k]["from"][x["fromService"]] += 1
            by[k]["targets"][f"{x['to']} ({x['toType']})"] += 1
            by[k]["via"][x["via"]] += 1
            by[k]["name"] = x["subscription"]
        summary = [{"subscription": v["name"], "references": v["references"],
                    "referencedFrom": ", ".join(f"{k} x{n}" for k, n in v["from"].most_common(4)),
                    "via": ", ".join(k for k, _ in v["via"].most_common(3)),
                    "examples": ", ".join(k for k, _ in v["targets"].most_common(3))}
                   for k, v in sorted(by.items(), key=lambda kv: -kv[1]["references"])]
        return {"summary": summary, "references": rows}

    # -- headline facts (E10) ----------------------------------------------------------------------------------------

    def headline(self):
        """A short list of counted facts for the top of the brief. Each line states a number the
        reader can check against a section; none judges it."""
        L = []
        prim = [r for r in self.resources if lc(r["type"]) not in cat.SUPPORTING]
        L.append(f"{len(self.resources)} resources ({len(prim)} primary) in {len(self.manifest['subscriptions'])} subscription(s) and "
                 f"{len(self.rgs)} resource group(s), in {len(self.regions())} region(s): "
                 + (", ".join(f"{k} ({v})" for k, v in self.regions().most_common(4)) or "none") + ".")
        nets = self.of("microsoft.network/virtualnetworks")
        if nets:
            peers = sum(len((n.get("properties") or {}).get("virtualNetworkPeerings") or []) for n in nets)
            L.append(f"{len(nets)} virtual network(s) with {peers} peering(s); {len(self.of('microsoft.network/virtualnetworkgateways'))} "
                     f"virtual network gateway(s); {len(self.of('microsoft.network/publicipaddresses'))} public IP address(es).")
        vms = self.of("microsoft.compute/virtualmachines")
        bits = []
        if vms:
            bits.append(f"{len(vms)} VM(s)")
        sites = self.of("microsoft.web/sites")
        if sites:
            fn = sum(1 for s in sites if "functionapp" in lc(s.get("kind")))
            bits.append(f"{len(sites)} App Service site(s) ({fn} function app(s))")
        aks = self.of("microsoft.containerservice/managedclusters")
        if aks:
            bits.append(f"{len(aks)} AKS cluster(s)")
        if bits:
            L.append("Compute: " + ", ".join(bits) + ".")
        data = Counter()
        for t, label in (("microsoft.sql/servers/databases", "Azure SQL database(s)"), ("microsoft.documentdb/databaseaccounts", "Cosmos DB account(s)"),
                         ("microsoft.servicebus/namespaces", "Service Bus namespace(s)"), ("microsoft.storage/storageaccounts", "storage account(s)"),
                         ("microsoft.keyvault/vaults", "Key Vault(s)")):
            n = len([r for r in self.of(t) if lc(r["name"]) != "master"])
            if n:
                data[label] = n
        if data:
            L.append("Data and integration: " + ", ".join(f"{n} {k}" for k, n in data.items()) + ".")
        paas = self.paas_network_access()
        if paas:
            c = Counter(p["stance"] for p in paas)
            L.append(f"{len(paas)} PaaS resource(s) report a network access setting: " + ", ".join(f"{n} {k}" for k, n in c.most_common()) + ".")
        nsg = self.open_nsg_rules()
        if nsg:
            c = Counter(r["effective"] for r in nsg)
            L.append(f"{len(nsg)} NSG rule(s) allow inbound traffic from any source or the Internet; evaluated in priority order: "
                     + ", ".join(f"{n} {k}" for k, n in c.most_common()) + ".")
        rs = self.resilience()
        if rs["vmCount"]:
            L.append(f"{rs['vmProtected']} of {rs['vmCount']} VM(s) have Azure Backup; {rs['zonalVms']} are in availability zones.")
        costs = self.costs()
        if costs["total"]:
            L.append(f"Actual cost over the last {costs['days']} days: {costs['currency']} {costs['total']:,.0f}"
                     + (f"; unavailable for {', '.join(costs['unavailable'])}" if costs["unavailable"] else "") + ".")
        sp = self.spend()
        if sp["lastFull"]:
            lf = sp["lastFull"]
            text = f"Spend, last full calendar month ({lf['label']}): {sp['currency']} {lf['cost']:,.0f}"
            if lf["change"] is not None:
                text += f", {lf['change']:+.1f}% on {lf['previous']}"
            if sp["mtd"]:
                mt = sp["mtd"]
                text += (f"; month to date to {mt['days']} {mt['label'].split()[0]}: "
                         f"{sp['currency']} {mt['cost']:,.0f}")
            L.append(text + ".")
        gaps = self.scope_gaps()["summary"]
        subs_out = [x for x in gaps if not x["subscription"].startswith("unknown")]
        hosts_out = sum(x["references"] for x in gaps if x["subscription"].startswith("unknown"))
        if subs_out or hosts_out:
            L.append("Resources in the capture point outside it: "
                     + ", ".join(filter(None, [f"{len(subs_out)} other subscription(s)" if subs_out else "",
                                               f"{hosts_out} container registry host name(s) not found in the capture" if hosts_out else ""]))
                     + " (section 12).")
        errs = self.manifest.get("errors") or {}
        if errs:
            L.append(f"{len(errs)} collection gap(s) are listed at the end of section 13.")
        return L
