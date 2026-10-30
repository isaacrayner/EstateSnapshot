"""A small synthetic estate (no customer data) that exercises each acceptance item in
docs/Improvement-Handoff.md. build(tmp, env) writes a snapshot folder the model can read."""
import json
import os

SUB = "00000000-0000-0000-0000-0000000000aa"
OTHER = "11111111-1111-1111-1111-1111111111bb"        # a subscription outside the capture
HUB = "22222222-2222-2222-2222-2222222222cc"


def rid(rg, ptype, name, sub=SUB):
    ns, _, t = ptype.partition("/")
    return f"/subscriptions/{sub}/resourceGroups/{rg}/providers/{ns}/{t}/{name}"


def res(rg, ptype, name, props=None, **kw):
    r = {"id": rid(rg, ptype, name), "name": name, "type": ptype.lower(), "location": "uksouth", "resourceGroup": rg,
         "subscriptionId": SUB, "properties": props or {}, "tags": {}, "zones": None, "kind": None, "sku": None}
    r.update(kw)
    return r


def nsg_rule(name, prio, access, proto, ports, src="Internet", dst="*", direction="Inbound"):
    p = {"priority": prio, "access": access, "direction": direction, "protocol": proto, "sourceAddressPrefix": src,
         "destinationAddressPrefix": dst, "sourcePortRange": "*"}
    p["destinationPortRanges" if isinstance(ports, list) else "destinationPortRange"] = ports
    return {"name": name, "properties": p}


def build(tmp, env="prod", registry="acrdev.azurecr.io", functions=3, with_config=True, quota="PayAsYouGo_2014-09-01"):
    rg = f"rg-app-{env}"
    plan_id = rid(rg, "Microsoft.Web/serverFarms", f"asp-{env}")
    R = [res(rg, "Microsoft.Web/serverFarms", f"asp-{env}", {"reserved": False, "numberOfSites": functions},
             sku={"name": "P1v3", "capacity": 2})]
    for i in range(functions):
        R.append(res(rg, "Microsoft.Web/sites", f"func-{env}-{'abcdefgh'[i]}",
                     {"serverFarmId": plan_id, "publicNetworkAccess": "Enabled", "hostNames": [f"func-{env}-{'abcdefgh'[i]}.azurewebsites.net"]},
                     kind="functionapp"))
    R.append(res(rg, "Microsoft.ContainerRegistry/registries", f"acr{env}", {"loginServer": f"acr{env}.azurecr.io"}))
    R.append(res(rg, "Microsoft.DocumentDB/databaseAccounts", f"cosmos-{env}",
                 {"consistencyPolicy": {"defaultConsistencyLevel": "BoundedStaleness"}, "backupPolicy": {"type": "Periodic",
                  "periodicModeProperties": {"backupIntervalInMinutes": 240, "backupRetentionIntervalInHours": 8}},
                  "disableLocalAuth": False, "locations": [{"locationName": "UK South"}], "capabilities": []}))
    R.append(res(rg, "Microsoft.ServiceBus/namespaces", f"sb-{env}", {"disableLocalAuth": False}, sku={"name": "Standard"}))
    R.append(res(rg, "Microsoft.OperationalInsights/workspaces", f"law-{env}",
                 {"retentionInDays": 30, "customerId": "cust-1", "sku": {"name": "PerGB2018"}}))
    R.append(res(rg, "Microsoft.Compute/virtualMachines", f"vm-{env}-01",
                 {"hardwareProfile": {"vmSize": "Standard_DS2_v2"}, "storageProfile": {"osDisk": {"osType": "Linux"}}}))
    R.append(res(rg, "Microsoft.Network/networkWatchers/flowLogs", f"fl-{env}",
                 {"targetResourceId": rid(rg, "Microsoft.Network/networkSecurityGroups", "nsg-aks")}))
    R.append(res(rg, "Microsoft.Web/connections", f"sftp-{env}",
                 {"api": {"name": "sftpwithssh"}, "nonSecretParameterValues": {"hostName": "sftp-test.partner.example.net"}}))
    R.append(res(rg, "Microsoft.Logic/workflows", f"logic-{env}", {"state": "Enabled"}))

    # AKS with the ingress path the handoff describes: public IP, allow at 501, deny at 402 to 404
    R.append(res(rg, "Microsoft.ContainerService/managedClusters", f"aks-{env}", {
        "kubernetesVersion": "1.30.4", "networkProfile": {"networkPlugin": "kubenet"},
        "agentPoolProfiles": [{"name": "system", "vmSize": "Standard_DS2_v2", "count": 3, "osType": "Linux", "mode": "System"}],
        "apiServerAccessProfile": {}}, sku={"tier": "Standard"}))
    R.append(res(rg, "Microsoft.Network/publicIPAddresses", "pip-ingress", {"ipAddress": "20.1.2.3"}, sku={"name": "Standard"}))
    R.append(res(rg, "Microsoft.Network/networkSecurityGroups", "nsg-aks", {"securityRules": [
        nsg_rule("deny-http", 402, "Deny", "Tcp", "80", dst="20.1.2.3"),
        nsg_rule("deny-https", 403, "Deny", "Tcp", "443", dst="20.1.2.3"),
        nsg_rule("deny-udp", 404, "Deny", "Udp", "*", dst="20.1.2.3"),
        nsg_rule("k8s-azure-lb_allow_IPv4", 501, "Allow", "*", ["80", "443"], dst="20.1.2.3")]}))
    R.append(res(rg, "Microsoft.Network/networkSecurityGroups", "nsg-vm", {"securityRules": [
        nsg_rule("RDP", 300, "Allow", "Tcp", "3389", src="*")]}))
    R.append(res(rg, "Microsoft.Network/applicationGatewayWebApplicationFirewallPolicies", "waf-pol",
                 {"policySettings": {"mode": "Detection", "state": "Enabled"},
                  "managedRules": {"managedRuleSets": [{"ruleSetType": "OWASP", "ruleSetVersion": "3.2"}]}}))
    R.append(res(rg, "Microsoft.Network/applicationGateways", f"agw-{env}", {
        "firewallPolicy": {"id": rid(rg, "Microsoft.Network/applicationGatewayWebApplicationFirewallPolicies", "waf-pol")},
        "sku": {"name": "WAF_v2", "tier": "WAF_v2"}, "httpListeners": [{}]}, sku={"name": "WAF_v2"}))

    # network: peering to a VNet in a subscription that is not captured
    subnet_id = rid(rg, "Microsoft.Network/virtualNetworks", f"vnet-{env}") + "/subnets/snet-app"
    R.append(res(rg, "Microsoft.Network/virtualNetworks", f"vnet-{env}", {
        "addressSpace": {"addressPrefixes": ["10.10.0.0/16"]},
        "subnets": [{"id": subnet_id, "name": "snet-app", "properties": {"addressPrefix": "10.10.1.0/24"}}],
        "virtualNetworkPeerings": [{"name": "to-hub", "properties": {
            "remoteVirtualNetwork": {"id": rid("rg-hub", "Microsoft.Network/virtualNetworks", "vnet-hub", HUB)},
            "peeringState": "Connected", "remoteAddressSpace": {"addressPrefixes": ["10.0.0.0/16"]}}}]}))

    manifest = {"tool": "EstateSnapshot", "version": 2, "customer": "Synthetic", "tenantId": "t-1",
                "capturedAt": "2026-10-27T09:00:00+00:00", "costDays": 30, "metricsDays": 30,
                "subscriptions": [{"id": SUB, "name": f"Sub {env}", "quotaId": quota}],
                "counts": {}, "errors": {}, "stages": {}}
    raw = {"resources": R, "containers": [{"id": f"/subscriptions/{SUB}/resourceGroups/{rg}", "name": rg,
                                           "type": "microsoft.resources/subscriptions/resourcegroups", "subscriptionId": SUB}],
           "costs": [{"subscriptionId": SUB, "resourceGroup": rg, "service": "Azure Cosmos DB", "cost": 100.0, "currency": "GBP"}],
           "cost_pricing": [{"subscriptionId": SUB, "pricingModel": "OnDemand", "cost": 80.0, "currency": "GBP"},
                            {"subscriptionId": SUB, "pricingModel": "Reservation", "cost": 20.0, "currency": "GBP"}],
           # monthly actual cost: May to September whole, October part (captured on the 27th)
           "cost_months": [{"subscriptionId": SUB, "month": month, "cost": cost, "currency": "GBP"}
                           for month, cost in (("2026-05", 100.0), ("2026-06", 110.0), ("2026-07", 120.0),
                                               ("2026-08", 100.0), ("2026-09", 120.0), ("2026-10", 70.0))]}
    if with_config:
        site_cfg = [
            ("a", {"linuxFxVersion": "DOTNET-ISOLATED|8.0", "use32BitWorkerProcess": True, "alwaysOn": True}),
            ("b", {"linuxFxVersion": "DOTNET|8.0", "use32BitWorkerProcess": False, "healthCheckPath": "/health"}),
            ("c", {"linuxFxVersion": f"DOCKER|{registry}/app:1", "use32BitWorkerProcess": False}),
        ]
        sites = []
        for i in range(functions):
            letter, web = site_cfg[i % 3]
            web = {**web, "ipSecurityRestrictions": [{"name": "Allow all", "action": "Allow", "priority": 2147483647}]}
            sites.append({"id": rid(rg, "Microsoft.Web/sites", f"func-{env}-{'abcdefgh'[i]}"), "kind": "functionapp", "web": web})
        raw["site_config"] = sites
        raw["cosmos_config"] = [{"id": rid(rg, "Microsoft.DocumentDB/databaseAccounts", f"cosmos-{env}"), "serverless": False, "databases": [
            {"name": "app", "api": "sql", "throughput": {"throughput": 400, "autoscaleMax": None, "minimum": 400},
             "containers": [{"name": n, "partitionKey": ["/id"], "defaultTtl": None, "throughput": None}
                            for n in ("transactions", "vault", "a", "b", "c", "d", "e")]}]}]
        topics = [{"name": f"topic{i}", "subscriptionCount": 0 if i < 2 else 1, "enablePartitioning": False,
                   "countDetails": {"deadLetterMessageCount": 0},
                   "subscriptions": [] if i < 2 else [{"name": "s1", "countDetails": {"deadLetterMessageCount": 1000}}]} for i in range(5)]
        raw["servicebus_config"] = [{"id": rid(rg, "Microsoft.ServiceBus/namespaces", f"sb-{env}"), "topics": topics,
                                     "queues": [{"name": f"q{i}", "enablePartitioning": False,
                                                 "countDetails": {"deadLetterMessageCount": 10}} for i in range(3)]}]
        raw["aks_config"] = [{"id": rid(rg, "Microsoft.ContainerService/managedClusters", f"aks-{env}"), "properties": {
            "ingressProfile": {"webAppRouting": {"enabled": True}}, "networkProfile": {"networkPlugin": "kubenet"}}}]
        raw["logic_targets"] = [{"id": rid(rg, "Microsoft.Logic/workflows", f"logic-{env}"), "state": "Enabled", "uris": [],
                                 "connections": [{"connection": "sftpwithssh", "path": "/files", "method": "post"}],
                                 "connectionIds": {"sftpwithssh": rid(rg, "Microsoft.Web/connections", f"sftp-{env}")}}]
        raw["loganalytics_tables"] = [{"id": rid(rg, "Microsoft.OperationalInsights/workspaces", f"law-{env}"), "tables": [
            {"name": "AKSAudit", "plan": "Analytics", "retentionInDays": 30, "totalRetentionInDays": 30},
            {"name": "Heartbeat", "plan": "Basic", "retentionInDays": 8, "totalRetentionInDays": 90}]}]
        raw["loganalytics_usage"] = [{"id": rid(rg, "Microsoft.OperationalInsights/workspaces", f"law-{env}"), "days": 30, "tables": [
            {"table": "AKSAudit", "gb": 300.0, "billable": True}, {"table": "Perf", "gb": 100.0, "billable": True}]}]
        raw["diagnostics"] = [
            {"id": rid(rg, "Microsoft.DocumentDB/databaseAccounts", f"cosmos-{env}"), "type": "microsoft.documentdb/databaseaccounts",
             "scopes": [{"scope": "x", "supported": True, "settings": [{"name": "s1", "workspaceId": rid(rg, "Microsoft.OperationalInsights/workspaces", f"law-{env}"),
                                                                       "logs": ["DataPlaneRequests"], "logsDisabled": [], "metrics": ["Requests"]}]}]},
            {"id": rid(rg, "Microsoft.ServiceBus/namespaces", f"sb-{env}"), "type": "microsoft.servicebus/namespaces",
             "scopes": [{"scope": "y", "supported": True, "settings": []}]},
            {"id": f"/subscriptions/{SUB}", "type": "subscription", "scopes": [{"scope": "z", "supported": True, "settings": [
                {"name": "act", "workspaceId": f"/subscriptions/{OTHER}/resourceGroups/rg-sec/providers/Microsoft.OperationalInsights/workspaces/law-central",
                 "logs": ["Administrative"], "logsDisabled": [], "metrics": []}]}]}]
        raw["metrics"] = [_metrics(rg, env)]
    os.makedirs(os.path.join(tmp, "raw"), exist_ok=True)
    for name, data in raw.items():
        json.dump(data, open(os.path.join(tmp, "raw", name + ".json"), "w"))
        manifest["counts"][name] = len(data)
        manifest["stages"][name] = "done"
    json.dump(manifest, open(os.path.join(tmp, "manifest.json"), "w"))
    return tmp


def _metrics(rg, env):
    cid = rid(rg, "Microsoft.DocumentDB/databaseAccounts", f"cosmos-{env}").lower()

    def ts(coll, code, total):
        return {"resourceId": cid, "metric": "TotalRequests", "unit": "Count",
                "dimensions": {"collectionname": coll, "statuscode": code},
                "points": [{"timeStamp": "2026-10-20T00:00:00Z", "total": total}]}
    series = [ts("transactions", "200", 580), ts("transactions", "429", 420), ts("vault", "200", 410), ts("vault", "429", 590),
              {"resourceId": cid, "metric": "NormalizedRUConsumption", "unit": "Percent", "dimensions": {},
               "points": [{"timeStamp": f"2026-10-{d:02d}T{h:02d}:00:00Z", "maximum": 100.0 if h == 3 else 40.0, "average": 50.0}
                          for d in range(1, 11) for h in range(24)]}]
    return {"id": "0|batch", "type": "microsoft.documentdb/databaseaccounts", "start": "2026-09-27T00:00:00Z",
            "end": "2026-10-27T00:00:00Z", "interval": "PT1H", "resources": 1, "series": series}
