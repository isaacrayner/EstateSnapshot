"""Stage 1c - measured load. A fixed set of Azure Monitor metrics per resource type,
read through the data-plane metrics:getBatch API. A batch must share one subscription,
one region and one resource type, and holds at most 50 resources, so resources are
grouped that way: a few calls per type instead of one per resource, which is what
avoids the throttling of the per-resource metrics API.

Raw time series are saved as returned (hourly points); percentages and peaks are
worked out in the model, so the window can be re-read offline."""
import datetime as dt
from collections import defaultdict
from urllib.parse import quote

from .azure import AzureError

API = "2023-10-01"
BATCH = 50
INTERVAL = "PT1H"

# One entry per getBatch call shape. The status-code split on Cosmos DB requests is what shows
# throttling (429); the collection split attributes it to a container.
SPECS = [
    {"type": "microsoft.documentdb/databaseaccounts", "metrics": ["TotalRequests"], "aggregation": "Total",
     "filter": "StatusCode eq '*' and CollectionName eq '*'", "top": 100, "orderby": "total desc"},
    {"type": "microsoft.documentdb/databaseaccounts", "metrics": ["NormalizedRUConsumption"],
     "aggregation": "Maximum,Average"},
    {"type": "microsoft.documentdb/databaseaccounts", "metrics": ["DataUsage"], "aggregation": "Maximum"},
    {"type": "microsoft.servicebus/namespaces", "metrics": ["IncomingMessages"], "aggregation": "Total"},
    {"type": "microsoft.servicebus/namespaces", "metrics": ["DeadletteredMessages", "ActiveMessages"],
     "aggregation": "Maximum,Average"},
    {"type": "microsoft.web/serverfarms", "metrics": ["CpuPercentage", "MemoryPercentage"],
     "aggregation": "Maximum,Average"},
    {"type": "microsoft.sql/servers/databases", "metrics": ["cpu_percent"], "aggregation": "Maximum,Average"},
    {"type": "microsoft.sql/servers/elasticpools", "metrics": ["cpu_percent"], "aggregation": "Maximum,Average"},
]
TYPES = {s["type"] for s in SPECS}


def window(days, now=None):
    end = (now or dt.datetime.now(dt.timezone.utc)).replace(minute=0, second=0, microsecond=0)
    return end - dt.timedelta(days=days), end


def _iso(t):
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def plan(resources, days):
    """Batches to run: [{key, spec index, subscription, region, ids}]. The key is stable, so a
    resumed run recognises the batches it has already finished."""
    groups = defaultdict(list)
    for r in resources:
        t = (r.get("type") or "").lower()
        loc = (r.get("location") or "").lower().replace(" ", "")
        if t in TYPES and loc and loc != "global":
            groups[(t, r["subscriptionId"], loc)].append(r["id"])
    out = []
    for i, spec in enumerate(SPECS):
        for (t, sub, loc), ids in sorted(groups.items()):
            if t != spec["type"]:
                continue
            ids = sorted(ids)
            for n in range(0, len(ids), BATCH):
                chunk = ids[n:n + BATCH]
                out.append({"id": f"{i}|{sub}|{loc}|{chunk[0].lower()}|{len(chunk)}|{days}d", "spec": i,
                            "subscription": sub, "region": loc, "ids": chunk})
    return out


def fetch(client, batch, days, now=None):
    spec = SPECS[batch["spec"]]
    start, end = window(days, now)
    q = (f"starttime={_iso(start)}&endtime={_iso(end)}&interval={INTERVAL}&metricnamespace={quote(spec['type'])}"
         f"&metricnames={','.join(spec['metrics'])}&aggregation={spec['aggregation']}&api-version={API}")
    if spec.get("filter"):
        q += "&filter=" + quote(spec["filter"], safe="'=*")
    if spec.get("top"):
        q += f"&top={spec['top']}&orderby={quote(spec['orderby'])}"
    url = f"https://{batch['region']}.metrics.monitor.azure.com/subscriptions/{batch['subscription']}/metrics:getBatch?{q}"
    d = client.request("POST", url, {"resourceids": batch["ids"]}, audience="metrics", retries=6, max_wait=90, base_wait=2)
    series = []
    for v in d.get("values", []):
        rid = (v.get("resourceid") or "").lower()
        for m in v.get("value", []):
            for ts in m.get("timeseries") or []:
                dims = {(x.get("name") or {}).get("value", "").lower(): x.get("value") for x in ts.get("metadatavalues") or []}
                pts = [{k: p[k] for k in p if p[k] is not None} for p in ts.get("data") or []]
                series.append({"resourceId": rid, "metric": (m.get("name") or {}).get("value"), "unit": m.get("unit"),
                               "dimensions": dims, "points": [p for p in pts if len(p) > 1]})
    return {"id": batch["id"], "type": spec["type"], "start": _iso(start), "end": _iso(end), "interval": INTERVAL,
            "resources": len(batch["ids"]), "series": series}


def expected_points(days):
    return days * 24


def check_error(e):
    """Turn the usual getBatch failures into something a person can act on."""
    s = getattr(e, "status", None)
    if s == 401:
        return "401: getBatch is a subscription-level call and needs Monitoring Reader on the subscription"
    if s == 403:
        return "403: the Microsoft.Insights resource provider may not be registered for the subscription"
    return str(e)


__all__ = ["SPECS", "TYPES", "plan", "fetch", "AzureError", "check_error", "expected_points"]
