"""Collection stages against a fake Azure client: shapes, read-only behaviour, resume."""
import json
import os
import re

import pytest

import fixtures
from estatesnapshot import collect, config, metrics
from estatesnapshot.azure import AzureError

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SUB = fixtures.SUB


class FakeClient:
    """Serves canned ARM, Resource Graph, Monitor and Graph answers and records every call."""
    instances = []
    fail_on = None            # substring of a URL that raises once, to simulate an interrupted run
    resources = []            # what the Resource Graph "resources" table returns

    def __init__(self, tenant=None):
        self.tenant = tenant
        self.calls = []
        FakeClient.instances.append(self)

    def subscriptions(self):
        return [{"subscriptionId": SUB, "displayName": "Sub prod", "state": "Enabled", "tenantId": "t-1",
                 "subscriptionPolicies": {"quotaId": "CSP_2015-05-01", "spendingLimit": "Off"}}]

    def arg(self, query, subs):
        self.calls.append(("ARG", query.split("\n")[0][:30]))
        return self.resources if query.startswith("resources") else []

    def pages(self, url, **kw):
        return self.request("GET", url).get("value", [])

    def request(self, method, url, body=None, audience="arm", **kw):
        self.calls.append((method, url))
        if FakeClient.fail_on and FakeClient.fail_on in url:
            FakeClient.fail_on = None
            raise KeyboardInterrupt("simulated interruption")
        return self.route(method, url, body)

    def route(self, method, url, body):
        u = url.split("?")[0]
        if "throughputSettings/default" in u:
            if "/containers/" in u:
                raise AzureError("HTTP 404", 404)
            return {"properties": {"resource": {"throughput": 400, "minimumThroughput": 400}}}
        if u.endswith("/sqlDatabases"):
            return {"value": [{"id": u.rsplit("/", 1)[0] + "/sqlDatabases/app", "name": "app"}]}
        if u.endswith("/containers"):
            return {"value": [{"id": u.rsplit("/", 1)[0] + f"/containers/{n}", "name": n,
                               "properties": {"resource": {"partitionKey": {"paths": ["/id"]}, "defaultTtl": 3600}}} for n in ("a", "b")]}
        if u.endswith("/topics"):
            return {"value": [{"id": u.rsplit("/", 1)[0] + "/topics/t0", "name": "t0", "properties": {"subscriptionCount": 0}},
                              {"id": u.rsplit("/", 1)[0] + "/topics/t1", "name": "t1", "properties": {"subscriptionCount": 1}}]}
        if u.endswith("/subscriptions") and "/topics/" in u:
            return {"value": [{"name": "s1", "properties": {"countDetails": {"deadLetterMessageCount": 5}}}]}
        if u.endswith("/queues"):
            return {"value": [{"name": "q1", "properties": {"enablePartitioning": False}}]}
        if u.endswith("/config/web"):
            return {"properties": {"linuxFxVersion": "DOTNET|8.0", "use32BitWorkerProcess": True, "appSettings": "SECRET",
                                   "ipSecurityRestrictions": [{"name": "r", "action": "Allow", "priority": 100, "ipAddress": "1.2.3.4/32", "extra": 1}]}}
        if "managedClusters" in u and method == "GET":
            return {"sku": {"tier": "Standard"}, "properties": {"ingressProfile": {"webAppRouting": {"enabled": True}},
                                                                "servicePrincipalProfile": {"secret": "SECRET"}}}
        if u.endswith("/tables"):
            return {"value": [{"name": "Perf", "properties": {"plan": "Analytics", "retentionInDays": 30, "totalRetentionInDays": 30}}]}
        if "/providers/Microsoft.Logic/workflows/" in u:
            return {"properties": {"state": "Enabled", "parameters": {"$connections": {"value": {"sftp": {"connectionId": "/c/sftp"}}}},
                                   "definition": {"actions": {"Call": {"type": "Http", "inputs": {
                                       "method": "POST", "uri": "https://api.example.net/x?sig=SECRET&sv=1",
                                       "authentication": {"password": "SECRET"}}},
                                       "Put": {"type": "ApiConnection", "inputs": {"host": {"connection": {
                                           "name": "@parameters('$connections')['sftp']['connectionId']"}}, "method": "post", "path": "/out"}}}}}}
        if u.endswith("/diagnosticSettings"):
            if "/networkInterfaces/" in u:
                raise AzureError("HTTP 400", 400)
            return {"value": [{"name": "d1", "properties": {"workspaceId": "/subscriptions/x/resourceGroups/r/providers/microsoft.operationalinsights/workspaces/w",
                                                            "logs": [{"category": "A", "enabled": True}, {"category": "B", "enabled": False}],
                                                            "metrics": [{"category": "AllMetrics", "enabled": True}]}}]}
        if u.endswith("metrics:getBatch"):
            rid = body["resourceids"][0]
            return {"values": [{"resourceid": rid, "value": [{"name": {"value": "TotalRequests"}, "unit": "Count", "timeseries": [
                {"metadatavalues": [{"name": {"value": "StatusCode"}, "value": "429"}],
                 "data": [{"timeStamp": "2026-10-01T00:00:00Z", "total": 3}, {"timeStamp": "2026-10-01T01:00:00Z"}]}]}]}]}
        if "CostManagement/query" in u:
            return {"properties": {"columns": [{"name": "Cost"}, {"name": "PricingModel"}, {"name": "Currency"}],
                                   "rows": [[10.0, "OnDemand", "GBP"]]}} if "PricingModel" in json.dumps(body) else \
                   {"properties": {"columns": [{"name": "Cost"}, {"name": "ResourceGroupName"}, {"name": "ServiceName"}, {"name": "Currency"}],
                                   "rows": [[10.0, "rg", "svc", "GBP"]]}}
        if u.endswith("roleDefinitions"):
            return {"value": []}
        if u.endswith("getByIds"):
            return {"value": []}
        if u.endswith("/query"):
            return {"tables": [{"columns": [{"name": "DataType"}, {"name": "IsBillable"}, {"name": "GB"}],
                                "rows": [["AKSAudit", True, 12.5]]}]}
        raise AssertionError(f"unexpected call {method} {url}")


@pytest.fixture
def fake(tmp_path, monkeypatch):
    src = fixtures.build(str(tmp_path / "src"), "prod", with_config=False)
    FakeClient.resources = json.load(open(os.path.join(src, "raw", "resources.json")))
    FakeClient.instances.clear()
    FakeClient.fail_on = None
    monkeypatch.setattr(collect, "Client", FakeClient)
    return tmp_path


def acct():
    return {"id": fixtures.rid("rg", "Microsoft.DocumentDB/databaseAccounts", "c"), "properties": {"capabilities": []}}


def test_cosmos_fetcher_shape():
    rec = config.cosmos(FakeClient(), acct())
    db = rec["databases"][0]
    assert db["throughput"] == {"throughput": 400, "autoscaleMax": None, "minimum": 400}
    assert [c["name"] for c in db["containers"]] == ["a", "b"] and db["containers"][0]["throughput"] is None
    assert db["containers"][0]["partitionKey"] == ["/id"] and db["containers"][0]["defaultTtl"] == 3600


def test_servicebus_only_lists_subscriptions_when_there_are_some():
    c = FakeClient()
    rec = config.servicebus(c, {"id": fixtures.rid("rg", "Microsoft.ServiceBus/namespaces", "ns")})
    assert [t["name"] for t in rec["topics"]] == ["t0", "t1"]
    assert rec["topics"][0]["subscriptions"] == [] and rec["topics"][1]["subscriptions"][0]["countDetails"]["deadLetterMessageCount"] == 5
    assert sum(1 for m, u in c.calls if "/topics/t0/subscriptions" in u) == 0


def test_site_config_drops_everything_not_whitelisted():
    rec = config.site(FakeClient(), {"id": fixtures.rid("rg", "Microsoft.Web/sites", "s"), "kind": "functionapp"})
    assert "appSettings" not in rec["web"] and rec["web"]["use32BitWorkerProcess"] is True
    assert rec["web"]["ipSecurityRestrictions"] == [{"name": "r", "action": "Allow", "priority": 100, "ipAddress": "1.2.3.4/32"}]


def test_aks_fetcher_drops_the_service_principal_profile():
    rec = config.aks(FakeClient(), {"id": fixtures.rid("rg", "Microsoft.ContainerService/managedClusters", "k")})
    assert rec["properties"]["ingressProfile"]["webAppRouting"]["enabled"] and "servicePrincipalProfile" not in rec["properties"]


def test_logic_app_keeps_targets_but_no_secrets():
    rec = config.logic_app(FakeClient(), {"id": fixtures.rid("rg", "Microsoft.Logic/workflows", "w")})
    assert rec["uris"] == [{"method": "POST", "uri": "https://api.example.net/x"}]           # query string (SAS) dropped
    assert rec["connections"] == [{"connection": "sftp", "path": "/out", "method": "post"}] and rec["connectionIds"] == {"sftp": "/c/sftp"}
    assert "SECRET" not in json.dumps(rec)


def test_diagnostics_lists_every_setting_and_marks_unsupported():
    c = FakeClient()
    rec = config.diagnostics(c, {"id": fixtures.rid("rg", "Microsoft.KeyVault/vaults", "kv"), "type": "microsoft.keyvault/vaults"})
    s = rec["scopes"][0]["settings"][0]
    assert s["logs"] == ["A"] and s["logsDisabled"] == ["B"] and s["metrics"] == ["AllMetrics"]
    nic = config.diagnostics(c, {"id": fixtures.rid("rg", "Microsoft.Network/networkInterfaces", "n"), "type": "x"})
    assert nic["scopes"][0]["supported"] is False
    st = config.diagnostics(c, {"id": fixtures.rid("rg", "Microsoft.Storage/storageAccounts", "s"), "type": "microsoft.storage/storageaccounts"})
    assert len(st["scopes"]) == 5                     # the account plus blob, file, queue and table services


def test_metrics_plan_batches_by_subscription_region_type_and_50():
    rs = [{"id": f"/subscriptions/{SUB}/resourceGroups/r/providers/Microsoft.DocumentDB/databaseAccounts/c{i}", "type": "microsoft.documentdb/databaseaccounts",
           "subscriptionId": SUB, "location": "uksouth"} for i in range(120)]
    rs.append({**rs[0], "id": rs[0]["id"] + "x", "location": "ukwest"})
    rs.append({"id": "x", "type": "microsoft.web/sites", "subscriptionId": SUB, "location": "uksouth"})        # no metrics wanted
    batches = metrics.plan(rs, 30)
    cos = [b for b in batches if metrics.SPECS[b["spec"]]["type"].endswith("databaseaccounts")]
    assert len(cos) == 3 * 3 + 3                                  # (50+50+20) + 1 ukwest, for each of the 3 Cosmos DB call shapes
    assert all(len(b["ids"]) <= 50 for b in batches) and len({b["id"] for b in batches}) == len(batches)


def test_metrics_fetch_parses_dimensions_and_drops_empty_points():
    c = FakeClient()
    b = metrics.plan([{"id": fixtures.rid("rg", "Microsoft.DocumentDB/databaseAccounts", "c"), "type": "microsoft.documentdb/databaseaccounts",
                       "subscriptionId": SUB, "location": "uksouth"}], 30)[0]
    rec = metrics.fetch(c, b, 30)
    s = rec["series"][0]
    assert s["dimensions"] == {"statuscode": "429"} and s["points"] == [{"timeStamp": "2026-10-01T00:00:00Z", "total": 3}]
    method, url = c.calls[0]
    assert method == "POST" and url.startswith("https://uksouth.metrics.monitor.azure.com/subscriptions/") and "metrics:getBatch" in url
    assert "api-version=2023-10-01" in url and "filter=StatusCode%20eq%20'*'" in url and " " not in url


def test_nothing_in_collection_can_write():
    """Read-only guarantee: no PUT, PATCH or DELETE anywhere, and POST only to the query-style endpoints."""
    allowed_post = ("Microsoft.ResourceGraph/resources", "CostManagement/query", "getByIds", "metrics:getBatch", "/v1/workspaces/")
    for f in ("collect.py", "config.py", "metrics.py", "entra.py", "azure.py"):
        src = open(os.path.join(ROOT, "estatesnapshot", f), encoding="utf-8").read()
        assert not re.search(r'"(PUT|PATCH|DELETE)"', src), f
        for m in re.finditer(r'request\("POST",\s*([^\n]+)', src):
            assert any(a in m.group(1) or a in src[m.start() - 400:m.start() + 400] for a in allowed_post), (f, m.group(1))
    for f in ("config.py", "collect.py", "entra.py"):
        src = open(os.path.join(ROOT, "estatesnapshot", f), encoding="utf-8").read()
        assert not re.search(r"listkeys|listsecrets|appsettings/list|/list\?|listConnectionStrings", src, re.I), f


def test_full_run_writes_every_stage(fake):
    out = str(fake / "run")
    m = collect.run(out, customer="X", log=lambda *_: None)
    for stage in ("cosmos_config", "servicebus_config", "site_config", "aks_config", "loganalytics_tables", "logic_targets",
                  "diagnostics", "metrics", "loganalytics_usage", "cost_pricing"):
        assert m["stages"][stage] == "done", stage
    assert m["subscriptions"][0]["quotaId"] == "CSP_2015-05-01" and "completedAt" in m
    non_get = {(mth, u.split("?")[0].rsplit("/", 1)[-1]) for mth, u in (c for c in FakeClient.instances[0].calls if c[0] != "ARG") if mth != "GET"}
    assert non_get <= {("POST", "query"), ("POST", "getByIds"), ("POST", "metrics:getBatch")}, non_get


def test_interrupted_run_resumes_without_recollecting(fake):
    out = str(fake / "run")
    FakeClient.fail_on = "/config/web"             # dies part-way through the site configuration stage
    with pytest.raises(KeyboardInterrupt):
        collect.run(out, customer="X", log=lambda *_: None)
    m = json.load(open(os.path.join(out, "manifest.json")))
    assert m["stages"]["resources"] == "done" and m["stages"]["costs"] == "done" and m["stages"].get("site_config") != "done"
    first = FakeClient.instances[0]
    FakeClient.instances.clear()
    collect.run(out, customer="X", resume=True, log=lambda *_: None)
    second = FakeClient.instances[0]
    assert [c for c in second.calls if c[0] == "ARG"] == []                                 # Resource Graph tables kept
    assert not [c for c in second.calls if "CostManagement" in c[1]]                        # cost kept
    assert not [c for c in second.calls if "sqlDatabases" in c[1]]                          # Cosmos stage kept
    assert [c for c in second.calls if "/config/web" in c[1]]                               # the unfinished stage ran
    assert len([c for c in first.calls if c[0] == "ARG"]) == len(collect.QUERIES)
    assert json.load(open(os.path.join(out, "manifest.json")))["stages"]["site_config"] == "done"


def test_resume_refuses_a_different_scope(fake):
    out = str(fake / "run")
    collect.run(out, customer="X", log=lambda *_: None, skip=["config", "diagnostics", "metrics", "usage"])
    m = json.load(open(os.path.join(out, "manifest.json")))
    m["subscriptions"][0]["id"] = "ffffffff-ffff-ffff-ffff-ffffffffffff"
    json.dump(m, open(os.path.join(out, "manifest.json"), "w"))
    with pytest.raises(AzureError, match="differ"):
        collect.run(out, customer="X", resume=True, log=lambda *_: None)


def test_skip_leaves_stages_out_and_rejects_typos(fake):
    out = str(fake / "run")
    m = collect.run(out, customer="X", log=lambda *_: None, skip=["metrics", "diagnostics"])
    assert "metrics" not in m["stages"] and "diagnostics" not in m["stages"] and m["stages"]["site_config"] == "done"
    with pytest.raises(AzureError, match="--skip accepts"):
        collect.run(str(fake / "run2"), customer="X", log=lambda *_: None, skip=["metric"])


def test_failed_resource_is_recorded_and_retried_on_resume(fake):
    out = str(fake / "run")
    orig = FakeClient.route

    def flaky(self, method, url, body):
        if url.split("?")[0].endswith("/config/web") and "func-prod-b" in url:
            raise AzureError("HTTP 403 forbidden", 403)
        return orig(self, method, url, body)
    FakeClient.route = flaky
    try:
        m = collect.run(out, customer="X", log=lambda *_: None)
    finally:
        FakeClient.route = orig
    assert m["stages"]["site_config"] == "partial" and "1 of 3 failed" in m["errors"]["site_config"]
    recs = json.load(open(os.path.join(out, "raw", "site_config.json")))
    assert sum(1 for r in recs if "error" in r) == 1
    FakeClient.instances.clear()
    m = collect.run(out, customer="X", resume=True, log=lambda *_: None)
    redo = [c for c in FakeClient.instances[0].calls if "/config/web" in c[1]]
    assert len(redo) == 1 and "func-prod-b" in redo[0][1] and m["stages"]["site_config"] == "done"
    assert "site_config" not in m["errors"]
