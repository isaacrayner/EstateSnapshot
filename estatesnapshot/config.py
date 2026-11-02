"""Stage 1b - service configuration. Resource Graph holds each resource's own
properties but not its child resources (Cosmos DB databases and containers,
Service Bus topics, site configuration) or its diagnostic settings, so these are
read from ARM, one resource at a time. Every call is a GET: nothing here writes,
and nothing here calls a "list keys", "list secrets" or app-settings action.

Each fetcher returns one self-describing record with an `id`, so the collect
stage can checkpoint and resume per resource. Fetchers keep raw facts and drop
only fields that could hold secrets."""
import re
from urllib.parse import urlsplit

from .azure import ARM, NOT_THERE, AzureError
from .util import g, lc, name_of

API_COSMOS = "2024-05-15"
API_SERVICEBUS = "2021-11-01"
API_SITES = "2023-12-01"
API_AKS = "2024-09-01"
API_LA_TABLES = "2022-10-01"
API_LOGIC = "2019-05-01"
API_DIAG = "2021-05-01-preview"
API_FLOWLOG = "2024-05-01"


def pick(d, keys):
    d = d or {}
    return {k: d[k] for k in keys if k in d}


def _opt(client, url):
    """GET that treats 'not set / not supported here' as no value rather than a failure."""
    try:
        return client.request("GET", url)
    except AzureError as e:
        if e.status in NOT_THERE:
            return None
        raise


# -- Cosmos DB ---------------------------------------------------------------

# API flavour -> (database collection, child collection, partition key field)
COSMOS_APIS = {
    "sql": ("sqlDatabases", "containers", "partitionKey"),
    "mongodb": ("mongodbDatabases", "collections", "shardKey"),
    "gremlin": ("gremlinDatabases", "graphs", "partitionKey"),
    "cassandra": ("cassandraKeyspaces", "tables", None),
}


def _throughput(client, url):
    d = _opt(client, url + "/throughputSettings/default?api-version=" + API_COSMOS)
    if not d:
        return None
    r = (d.get("properties") or {}).get("resource") or {}
    auto = (r.get("autoscaleSettings") or {}).get("maxThroughput")
    return {"throughput": r.get("throughput"), "autoscaleMax": auto, "minimum": r.get("minimumThroughput")}


def _partition_keys(res, field):
    if not field:
        return []
    pk = res.get(field) or {}
    return pk.get("paths") or list(pk.get("keys") or [])


def cosmos(client, account):
    """Databases, containers and their throughput for one Cosmos DB account."""
    rid = account["id"]
    caps = [c.get("name") for c in (account.get("properties") or {}).get("capabilities") or []]
    flavours = ["sql"]
    for cap, api in (("EnableMongo", "mongodb"), ("EnableGremlin", "gremlin"), ("EnableCassandra", "cassandra")):
        if cap in caps:
            flavours = [api]
    rec = {"id": rid, "serverless": "EnableServerless" in caps, "databases": []}
    for api in flavours:
        dbs, kids, pkfield = COSMOS_APIS[api]
        for db in client.pages(f"{ARM}{rid}/{dbs}?api-version={API_COSMOS}"):
            base = f"{ARM}{db['id']}"
            d = {"name": db["name"], "api": api, "throughput": None if rec["serverless"] else _throughput(client, base),
                 "containers": []}
            for c in client.pages(f"{base}/{kids}?api-version={API_COSMOS}"):
                res = (c.get("properties") or {}).get("resource") or {}
                d["containers"].append({
                    "name": c["name"], "partitionKey": _partition_keys(res, pkfield),
                    "defaultTtl": res.get("defaultTtl"), "analyticalTtl": res.get("analyticalStorageTtl"),
                    "uniqueKeys": len((res.get("uniqueKeyPolicy") or {}).get("uniqueKeys") or []),
                    "throughput": None if rec["serverless"] else _throughput(client, f"{ARM}{c['id']}")})
            rec["databases"].append(d)
    return rec


# -- Service Bus -------------------------------------------------------------

_ENTITY = ["status", "enablePartitioning", "maxSizeInMegabytes", "sizeInBytes", "messageCount", "countDetails",
           "maxDeliveryCount", "lockDuration", "defaultMessageTimeToLive", "requiresDuplicateDetection",
           "requiresSession", "deadLetteringOnMessageExpiration", "autoDeleteOnIdle", "forwardTo",
           "forwardDeadLetteredMessagesTo", "enableBatchedOperations", "createdAt", "accessedAt"]


def servicebus(client, ns):
    """Topics, subscriptions and queues of one Service Bus namespace."""
    rid = ns["id"]
    rec = {"id": rid, "topics": [], "queues": []}
    for t in client.pages(f"{ARM}{rid}/topics?api-version={API_SERVICEBUS}"):
        p = t.get("properties") or {}
        topic = {"name": t["name"], **pick(p, _ENTITY + ["subscriptionCount", "supportOrdering"]), "subscriptions": []}
        if (p.get("subscriptionCount") or 0) > 0 or "subscriptionCount" not in p:
            for s in client.pages(f"{ARM}{t['id']}/subscriptions?api-version={API_SERVICEBUS}"):
                topic["subscriptions"].append({"name": s["name"], **pick(s.get("properties"), _ENTITY)})
        rec["topics"].append(topic)
    for q in client.pages(f"{ARM}{rid}/queues?api-version={API_SERVICEBUS}"):
        rec["queues"].append({"name": q["name"], **pick(q.get("properties"), _ENTITY)})
    return rec


# -- App Service and Functions -----------------------------------------------

_SITE_CONFIG = ["linuxFxVersion", "windowsFxVersion", "netFrameworkVersion", "phpVersion", "pythonVersion",
                "nodeVersion", "javaVersion", "javaContainer", "javaContainerVersion", "powerShellVersion",
                "use32BitWorkerProcess", "alwaysOn", "healthCheckPath", "functionAppScaleLimit",
                "functionsRuntimeScaleMonitoringEnabled", "minimumElasticInstanceCount", "preWarmedInstanceCount",
                "numberOfWorkers", "minTlsVersion", "scmMinTlsVersion", "ftpsState", "http20Enabled",
                "webSocketsEnabled", "vnetRouteAllEnabled", "vnetPrivatePortsCount", "acrUseManagedIdentityCreds",
                "ipSecurityRestrictionsDefaultAction", "scmIpSecurityRestrictionsUseMain", "publicNetworkAccess",
                "autoHealEnabled", "managedPipelineMode", "remoteDebuggingEnabled", "loadBalancing"]


def site(client, s):
    """Site configuration (config/web). Not app settings: those can hold secrets and need an action."""
    d = client.request("GET", f"{ARM}{s['id']}/config/web?api-version={API_SITES}")
    p = d.get("properties") or {}
    web = pick(p, _SITE_CONFIG)
    web["ipSecurityRestrictions"] = [pick(r, ["name", "action", "priority", "ipAddress", "tag"])
                                     for r in p.get("ipSecurityRestrictions") or []]
    return {"id": s["id"], "kind": s.get("kind"), "web": web}


# -- AKS ---------------------------------------------------------------------

_AKS = ["kubernetesVersion", "currentKubernetesVersion", "supportPlan", "ingressProfile", "networkProfile",
        "apiServerAccessProfile", "autoUpgradeProfile", "securityProfile", "oidcIssuerProfile", "addonProfiles",
        "agentPoolProfiles", "disableLocalAccounts", "aadProfile", "nodeResourceGroup", "azureMonitorProfile",
        "workloadAutoScalerProfile", "storageProfile", "serviceMeshProfile", "enableRBAC", "powerState"]


def aks(client, c):
    d = client.request("GET", f"{ARM}{c['id']}?api-version={API_AKS}")
    return {"id": c["id"], "sku": d.get("sku"), "properties": pick(d.get("properties"), _AKS)}


# -- Log Analytics -----------------------------------------------------------

def workspace_tables(client, ws):
    rows = []
    for t in client.pages(f"{ARM}{ws['id']}/tables?api-version={API_LA_TABLES}"):
        p = t.get("properties") or {}
        rows.append({"name": t["name"], "plan": p.get("plan"), "retentionInDays": p.get("retentionInDays"),
                     "totalRetentionInDays": p.get("totalRetentionInDays"),
                     "archiveRetentionInDays": p.get("archiveRetentionInDays")})
    return {"id": ws["id"], "tables": rows}


# -- Logic Apps: connection targets only -------------------------------------

def _no_query(uri):
    """Drop the query string and fragment: SAS tokens and API keys live there."""
    if not isinstance(uri, str) or uri.startswith("@") or "://" not in uri:
        return uri
    u = urlsplit(uri)
    return f"{u.scheme}://{u.netloc}{u.path}"


def logic_targets(definition, parameters):
    """Where a workflow talks to: HTTP URIs (without query strings), the named connections it uses
    (resolved to connection resource ids) and the path on each. Authentication blocks, headers and
    bodies are never read."""
    out = {"uris": [], "connections": [], "connectionIds": {}}
    for name, v in ((parameters or {}).get("$connections", {}).get("value") or {}).items():
        out["connectionIds"][name] = v.get("connectionId")

    def walk(node):
        if isinstance(node, dict):
            inputs = node.get("inputs")
            if isinstance(inputs, dict):
                if "uri" in inputs:
                    out["uris"].append({"method": inputs.get("method"), "uri": _no_query(inputs["uri"])})
                conn = ((inputs.get("host") or {}).get("connection") or {}).get("name")
                if conn:
                    m = re.search(r"\['([^']+)'\]", conn)
                    out["connections"].append({"connection": m.group(1) if m else conn,
                                               "path": inputs.get("path") if isinstance(inputs.get("path"), str) else None,
                                               "method": inputs.get("method")})
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(definition or {})
    return out


def logic_app(client, w):
    d = client.request("GET", f"{ARM}{w['id']}?api-version={API_LOGIC}")
    p = d.get("properties") or {}
    return {"id": w["id"], "state": p.get("state"), **logic_targets(p.get("definition"), p.get("parameters"))}


# -- Network security groups: the last N days of virtual network flow logs --------
#
# The flow log is a file in a storage account, one per hour, holding "flows" and "trafficAnalytics"
# blocks. This reads the newest files the log covers and keeps four figures per flow: the source and
# destination (address, port, no address at all for private endpoint traffic), the NSG decision and
# direction, and the byte and packet tallies. A missing or unreadable file is a collection gap, never
# a silent zero: "no file" and "no traffic" are different facts.
FLOWLOG = re.compile(r"PT1H_(\d{14})_fl-\w+_(acctime|flowlogtime)")
FINE_ACCTIME = re.compile(r"PT[1-5]M_(\d{14})_")
FLOW_MAX_FILES = 48


def _flow_records(log):
    """One flow log file as a list of records the model can pair by flow tuple. V1 files carry only
    `flows`; V2 files add `trafficAnalytics` blocks, which are read the same way."""
    out, seen = [], set()
    blocks = [log] + list(log.get("trafficAnalytics") or [])
    for block in blocks:
        direction = block.get("direction")
        for flow in block.get("flows") or []:
            rule = flow.get("rule") or ""
            if "defaultrule" in lc(rule):
                continue                      # Azure's own default allow/deny is not a customer rule decision
            mac = flow.get("mac")
            for grp in flow.get("flows") or []:
                rows = grp.get("flowTuples") or []
                for row in rows.split("|") if isinstance(rows, str) else rows:
                    bits = row if isinstance(row, list) else row.split(",")
                    if len(bits) < 8:
                        continue
                    key = (mac, bits[0], bits[1], bits[2], bits[3], bits[4], bits[5], bits[6])
                    if key in seen:
                        continue
                    seen.add(key)
                    out.append({"mac": mac, "src": bits[1], "dst": bits[2], "srcPort": bits[3], "dstPort": bits[4],
                                "protocol": {"T": "TCP", "U": "UDP"}.get(bits[5].upper(), bits[5]),
                                "decision": "allowed" if bits[6].upper().startswith("A") else "denied",
                                "direction": direction, "rule": rule, "traffic": bits[7] if len(bits) > 7 else None})
    return out


def flow_log(client, log, max_files=FLOW_MAX_FILES):
    """One NSG flow log: its configuration, where it writes, and the newest records in that storage
    account. Read-only: a GET of the log, a GET of the account, then blob list and read with a SAS
    signed by the local az identity (never the account key)."""
    rid = log["id"]
    d = client.request("GET", f"{ARM}{rid}?api-version={API_FLOWLOG}")
    p = d.get("properties") or {}
    rec = {"id": rid, "target": p.get("targetResourceId"), "enabled": p.get("enabled"),
           "storageId": p.get("storageId"), "retentionDays": (p.get("retentionPolicy") or {}).get("days"),
           "version": g(p, "format", "version"),
           "workspaceId": g(p, "flowAnalyticsConfiguration", "networkWatcherFlowAnalyticsConfiguration",
                            "workspaceResourceId"),
           "files": 0, "records": [], "gap": None}
    if not p.get("enabled"):
        rec["gap"] = "the flow log is disabled"
        return rec
    account = p.get("storageId")
    if not account:
        rec["gap"] = "the flow log has no storage account"
        return rec
    host = client.storage_blob_host(name_of(account))
    container = "insights-logs-networksecuritygroupflowevent"
    parts = rid.strip("/").split("/")
    # /subscriptions/<s>/resourceGroups/<rg>/providers/Microsoft.Network/networkWatchers/<w>/flowLogs/<nsg>
    sub, rg, nsg = parts[1], parts[3], parts[-1]
    prefix = f"{sub}/resourceGroups/{rg}/providers/Microsoft.Network/networkSecurityGroups/{nsg}/"
    try:
        sas = client.sas(name_of(account), container)
        names = [n for n in client.storage_blobs(host, container, prefix, sas) if n.endswith(".json")]
    except AzureError as e:
        rec["gap"] = f"the flow log storage could not be listed: {e}"
        return rec

    def stamp(n):
        m = FLOWLOG.search(n) or FINE_ACCTIME.search(n)
        return m.group(1) if m else "0"
    names = sorted(names, key=stamp, reverse=True)[:max_files]
    rec["files"] = len(names)
    if not names:
        rec["gap"] = "the storage account holds no flow log files for this NSG yet (they appear within the hour)"
        return rec
    for n in names:
        try:
            rec["records"] += _flow_records(client.get_gzip_json(f"{host}{container}/{n}?{sas}"))
        except AzureError as e:
            rec["gap"] = f"{len(names)} file(s) listed, one could not be read: {e}"
    return rec


# Types that accept diagnostic settings. A resource outside this list is not asked: NICs, disks and
# the like answer 400, which would only add noise. Storage accounts take their settings on the
# per-service child resources, so those are asked as well.
DIAGNOSTIC_TYPES = {
    "microsoft.keyvault/vaults", "microsoft.storage/storageaccounts", "microsoft.sql/servers/databases",
    "microsoft.sql/managedinstances", "microsoft.documentdb/databaseaccounts", "microsoft.servicebus/namespaces",
    "microsoft.eventhub/namespaces", "microsoft.eventgrid/topics", "microsoft.eventgrid/domains",
    "microsoft.eventgrid/systemtopics", "microsoft.web/sites", "microsoft.web/sites/slots",
    "microsoft.containerservice/managedclusters", "microsoft.network/applicationgateways",
    "microsoft.network/azurefirewalls", "microsoft.network/networksecuritygroups",
    "microsoft.network/publicipaddresses", "microsoft.network/virtualnetworks", "microsoft.network/loadbalancers",
    "microsoft.network/virtualnetworkgateways", "microsoft.network/bastionhosts", "microsoft.network/frontdoors",
    "microsoft.network/trafficmanagerprofiles", "microsoft.cdn/profiles", "microsoft.apimanagement/service",
    "microsoft.cache/redis", "microsoft.cache/redisenterprise", "microsoft.logic/workflows",
    "microsoft.datafactory/factories", "microsoft.automation/automationaccounts",
    "microsoft.recoveryservices/vaults", "microsoft.operationalinsights/workspaces",
    "microsoft.containerregistry/registries", "microsoft.signalrservice/signalr",
    "microsoft.cognitiveservices/accounts", "microsoft.search/searchservices", "microsoft.app/managedenvironments",
    "microsoft.dbforpostgresql/flexibleservers", "microsoft.dbformysql/flexibleservers",
    "microsoft.desktopvirtualization/hostpools", "microsoft.desktopvirtualization/workspaces",
    "microsoft.desktopvirtualization/applicationgroups", "microsoft.synapse/workspaces",
    "microsoft.databricks/workspaces", "microsoft.kusto/clusters", "microsoft.dataprotection/backupvaults",
    "microsoft.netapp/netappaccounts", "microsoft.network/dnsresolvers", "microsoft.network/privatednszones",
    "microsoft.network/expressroutecircuits", "microsoft.network/networkmanagers",
    "microsoft.insights/components", "microsoft.network/firewallpolicies",
}
STORAGE_SERVICES = ("blobServices", "fileServices", "queueServices", "tableServices")


def _setting(s):
    p = s.get("properties") or {}
    logs = p.get("logs") or []
    return {"name": s.get("name"), "workspaceId": p.get("workspaceId"), "storageAccountId": p.get("storageAccountId"),
            "eventHubName": p.get("eventHubName"), "eventHubAuthorizationRuleId": p.get("eventHubAuthorizationRuleId"),
            "marketplacePartnerId": p.get("marketplacePartnerId"), "logAnalyticsDestinationType": p.get("logAnalyticsDestinationType"),
            "logs": [(l.get("category") or l.get("categoryGroup")) for l in logs if l.get("enabled")],
            "logsDisabled": [(l.get("category") or l.get("categoryGroup")) for l in logs if not l.get("enabled")],
            "metrics": [m.get("category") for m in p.get("metrics") or [] if m.get("enabled")]}


def diagnostics(client, target):
    """Every diagnostic setting on one resource (not just the last). `target` is a resource id
    or a subscription id for the Activity Log."""
    rid = target["id"]
    scopes = [rid]
    if target.get("type") == "microsoft.storage/storageaccounts":
        scopes += [f"{rid}/{s}/default" for s in STORAGE_SERVICES]
    out = {"id": rid, "type": target.get("type"), "scopes": []}
    for sc in scopes:
        try:
            items = client.pages(f"{ARM}{sc}/providers/Microsoft.Insights/diagnosticSettings?api-version={API_DIAG}")
        except AzureError as e:
            if e.status in NOT_THERE:
                out["scopes"].append({"scope": sc, "supported": False})
                continue
            raise
        out["scopes"].append({"scope": sc, "supported": True, "settings": [_setting(i) for i in items]})
    return out
