"""Acceptance tests from docs/Improvement-Handoff.md, against the synthetic estate in fixtures.py."""
EM_DASH = chr(0x2014)
from estatesnapshot import nsg, register
from estatesnapshot.model_config import parse_runtime


# -- E1: configuration facts ---------------------------------------------------------------------

def test_cosmos_facts(prod):
    a = prod.cosmos_config()["accounts"][0]
    assert a["consistency"] == "BoundedStaleness"
    assert a["backup"].startswith("periodic")
    assert a["keyAuth"] == "enabled"
    assert "shared 400 RU/s manual across 7 container(s)" in a["throughput"]
    modes = {c["container"]: c["mode"] for c in prod.cosmos_config()["containers"]}
    assert set(modes.values()) == {"shared (database)"} and len(modes) == 7


def test_servicebus_facts(prod):
    n = prod.servicebus_config()["namespaces"][0]
    assert (n["topics"], n["queues"], n["partitioned"]) == (5, 3, 0)
    assert n["topicsWithoutSubscriptions"] == 2 and n["topWithoutSubscriptions"] == ["topic0", "topic1"]
    assert n["deadLetters"] == 3 * 1000 + 3 * 10        # three subscriptions with 1000, three queues with 10


def test_site_facts(prod):
    s = prod.site_summary()
    assert s["collected"] == 3
    assert dict(s["runtimes"])["Functions DOTNET-ISOLATED|8.0"] == 1
    assert s["bits32"] == ["func-prod-a"]
    assert set(s["noHealthCheck"]) == {"func-prod-a", "func-prod-c"}
    rows = {r["name"]: r for r in prod.site_facts()}
    assert rows["func-prod-c"]["registry"] == "acrdev.azurecr.io"
    assert rows["func-prod-c"]["registryInCapture"] == "not in this capture"


def test_runtime_parsing():
    assert parse_runtime({"linuxFxVersion": "DOTNETCORE|8.0"})["major"] == "8"
    assert parse_runtime({"netFrameworkVersion": "v8.0"}, "functionapp")["stack"] == "dotnet"
    assert parse_runtime({"netFrameworkVersion": "v4.0"}, "functionapp")["stack"] == "dotnetfx"
    c = parse_runtime({"linuxFxVersion": "DOCKER|reg.azurecr.io/team/app:2"})
    assert c["registry"] == "reg.azurecr.io" and c["stack"] == "container"
    assert parse_runtime({"linuxFxVersion": "DOCKER|library/nginx"})["registry"] == "docker hub"


def test_aks_facts(prod):
    r = prod.of("microsoft.containerservice/managedclusters")[0]
    f = prod.aks_facts(r)
    assert f["appRoutingNginx"] and f["plugin"] == "kubenet" and f["authorisedRanges"].startswith("none")


# -- E5: exposure in priority order -------------------------------------------------------------------

def test_ingress_ip_closed_by_denies(prod):
    rows = {(r["nsg"], r["rule"]): r for r in prod.open_nsg_rules()}
    r = rows[("nsg-aks", "k8s-azure-lb_allow_IPv4")]
    assert r["effective"] == "partly closed" or r["effective"] == "closed"
    # tcp 80 and 443 are denied at 402 and 403; protocol * also covers udp, icmp, esp and ah, which no deny takes
    assert "deny-http (402)" in r["closedBy"] and "deny-https (403)" in r["closedBy"]
    assert "tcp" not in r["remaining"]
    assert rows[("nsg-vm", "RDP")]["effective"] == "open"


def test_allow_fully_closed_when_protocol_is_tcp():
    rules = [
        {"name": "d1", "properties": {"priority": 402, "access": "Deny", "direction": "Inbound", "protocol": "Tcp",
                                      "destinationPortRange": "80", "sourceAddressPrefix": "Internet", "destinationAddressPrefix": "20.1.2.3"}},
        {"name": "d2", "properties": {"priority": 403, "access": "Deny", "direction": "Inbound", "protocol": "Tcp",
                                      "destinationPortRange": "443", "sourceAddressPrefix": "Internet", "destinationAddressPrefix": "20.1.2.3"}},
        {"name": "a", "properties": {"priority": 501, "access": "Allow", "direction": "Inbound", "protocol": "Tcp",
                                     "destinationPortRanges": ["80", "443"], "sourceAddressPrefix": "Internet", "destinationAddressPrefix": "20.1.2.3"}}]
    (rule, ev), = nsg.inbound_allows_from_internet(rules)
    assert ev["status"] == "closed" and [n for n, _ in ev["closedBy"]] == ["d1", "d2"]


def test_deny_does_not_shadow_when_it_cannot_be_shown_to_cover():
    base = {"direction": "Inbound", "protocol": "Tcp", "destinationPortRange": "443", "sourceAddressPrefix": "*",
            "destinationAddressPrefix": "*"}
    allow = {"name": "a", "properties": {**base, "priority": 500, "access": "Allow"}}

    def status(deny_props):
        d = {"name": "d", "properties": {**base, "priority": 400, "access": "Deny", **deny_props}}
        return nsg.inbound_allows_from_internet([d, allow])[0][1]["status"]

    assert status({}) == "closed"
    assert status({"sourceAddressPrefix": "Internet"}) == "open"             # Internet is narrower than *
    assert status({"destinationAddressPrefix": "10.0.0.4"}) == "open"        # one address, not all
    assert status({"destinationPortRange": "444"}) == "open"
    assert status({"destinationPortRange": "400-500"}) == "closed"
    assert status({"destinationApplicationSecurityGroups": [{"id": "x"}]}) == "open"   # cannot tell; stays open
    assert status({"priority": 600}) == "open"                              # lower priority than the allow


def test_port_arithmetic():
    assert nsg._subtract([(0, 65535)], [(80, 80), (443, 443)]) == [(0, 79), (81, 442), (444, 65535)]
    assert nsg.fmt_ports(nsg.parse_ports(["80", "81", "443", "*"])) == "*"


def test_public_ip_shows_rule_evaluation(prod):
    ip = next(r for r in prod.public_ips() if r["name"] == "pip-ingress")
    assert "k8s-azure-lb_allow_IPv4 (501" in ip["nsgRules"] and "deny-http (402)" in ip["nsgRules"]


def test_waf_detection_mode(prod):
    assert prod.waf_policies()[0]["mode"] == "Detection"
    assert "Detection mode" in prod._waf_text(prod.of("microsoft.network/applicationgateways")[0])
    gw = next(r for r in prod.edge_services() if r["service"] == "Application Gateway")
    assert "Detection" in gw["detail"] and "OWASP 3.2" in gw["detail"]


# -- E2: measured load ---------------------------------------------------------------------------------------

def test_measured_load_throttling(prod):
    ml = prod.measured_load()
    by = {c["container"]: c for c in ml["containers"]}
    assert by["transactions"]["throttledPct"] == 42.0 and by["vault"]["throttledPct"] == 59.0
    acc = ml["cosmos"][0]
    assert acc["throttledPct"] == 50.5
    assert acc["daysAtFullRu"] == "10 of 10" and acc["normalisedRuMax"] == 100.0


# -- E3: referenced but not captured ----------------------------------------------------------------------------

def test_scope_gaps_list_uncaptured_subscriptions(prod):
    s = prod.scope_gaps()
    texts = " ".join(f"{r['subscriptionId']} {r['via']} {r['to']}" for r in s["references"])
    assert "22222222-2222-2222-2222-2222222222cc" in texts and "VNet peering" in texts       # the hub, by peering
    assert "11111111-1111-1111-1111-1111111111bb" in texts and "diagnostic setting act" in texts   # Activity Log destination
    assert "acrdev.azurecr.io" in texts                                                            # registry host not in the capture


# -- E7: billing ---------------------------------------------------------------------------------------------------

def test_billing_and_log_analytics_usage(prod):
    b = prod.billing()
    assert b["subscriptions"][0]["offer"] == "Pay-As-You-Go" and b["subscriptions"][0]["quotaId"] == "PayAsYouGo_2014-09-01"
    assert b["pricingModels"][0][0] == "OnDemand" and b["pricingModels"][1][2] == 20
    u = prod.log_analytics_usage()[0]
    assert u["topTables"][0] == {"table": "AKSAudit", "gb": 300.0, "share": 75} and u["perMonthGB"] == 400.0
    assert any("Heartbeat" in o for o in u["overrides"])


# -- E6: diagnostics ---------------------------------------------------------------------------------------------------

def test_diagnostic_settings(prod):
    d = prod.diagnostic_settings()
    assert d["asked"] == 3 and d["withSettings"] == 2 and d["none"] == 1
    assert ("workspace law-prod", 1) in d["destinations"]


# -- E9: retirements -------------------------------------------------------------------------------------------------------

def test_retirements_match_facts(prod):
    rows = {r["change"]: r for r in prod.retirements()}
    fn = rows["Azure Functions: .NET 8 (LTS) end of support"]
    assert fn["affected"] == 2 and fn["date"] == "10/11/2026" and fn["daysFromCapture"] == 14
    assert rows["AKS kubenet networking retires"]["resources"] == "aks-prod"
    assert "AKS application routing add-on: managed NGINX ingress, Microsoft support for security patches ends" in rows
    assert rows["Network security group flow logs retire"]["affected"] == 1
    assert rows["VM sizes retire: D, Ds, Dv2 and Dsv2 series"]["affected"] == 2      # the VM and the AKS node pool


def test_register_patterns_do_not_overreach():
    import re
    d = {e["id"]: re.compile(e["match"]["pattern"]) for e in register.load()["entries"] if e["match"]["kind"] == "vm-size"}
    assert d["vm-d-dsv2"].match("standard_ds2_v2") and d["vm-d-dsv2"].match("standard_d13_v2")
    assert not d["vm-d-dsv2"].match("standard_d2s_v3") and not d["vm-d-dsv2"].match("standard_d2ds_v5")
    assert d["vm-av2-b1"].match("standard_b2s") and not d["vm-av2-b1"].match("standard_b2s_v2")
    assert d["vm-dv3-ev3"].match("standard_d2s_v3") and d["vm-dv3-ev3"].match("standard_e64-16s_v3")
    assert not d["vm-dv3-ev3"].match("standard_d2s_v5")


def test_register_override_path(prod, tmp_path):
    p = tmp_path / "r.json"
    p.write_text('{"entries": [{"id": "x", "title": "Only this", "date": "2026-12-01", "source": "s", "match": {"kind": "aks-network-plugin", "value": "kubenet"}}]}')
    rows = prod.retirements(str(p))
    assert [r["change"] for r in rows] == ["Only this"]


def test_spend_by_calendar_month(prod):
    sp = prod.spend()
    assert [m["month"] for m in sp["months"]][0] == "2026-10"                 # newest first
    assert sp["lastFull"]["month"] == "2026-09" and sp["lastFull"]["cost"] == 120.0
    assert sp["lastFull"]["change"] == 20.0                                   # 100 -> 120
    assert sp["lastFull"]["previous"] == "August 2026"
    assert sp["mtd"]["month"] == "2026-10" and sp["mtd"]["days"] == 27         # captured on the 27th
    assert sp["mtd"]["change"] is None                                        # a part-month has no comparison
    assert sp["currency"] == "GBP" and sp["unavailable"] == []


def test_spend_is_empty_for_a_capture_without_monthly_cost(prod):
    prod.cost_month_rows = []                              # a capture taken before this stage existed
    assert prod.spend()["months"] == [] and prod.spend()["lastFull"] is None


# -- headline facts and the brief ---------------------------------------------------------------------------------------------

def test_headline_and_brief_sections(prod):
    from estatesnapshot import brief
    text = brief.render(prod)
    assert text.index("## Headline facts") < text.index("## 1. Shape of the estate")
    for needle in ("Cosmos DB accounts and throughput", "Service Bus namespaces", "6.1 Measured load", "Site configuration",
                   "8.1 Dated platform changes", "Referenced but not captured", "Log Analytics ingestion", "Subscription offer",
                   "Actual cost by calendar month", "Last full calendar month (September 2026)",
                   "Diagnostic settings:", "Web application firewall policies"):
        assert needle in text, needle
    assert EM_DASH not in text                       # no em dashes in generated output
    assert "recommend" not in text.lower().replace("contains no recommendations", "")
