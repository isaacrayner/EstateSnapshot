"""Emitter - estate.md, the current-state brief the ADS skill reads in Step 1.
Compact, factual, tabular. Long lists are capped and point at inventory.xlsx."""
CAP = 25


def _cell(v):
    if v is None:
        return ""
    if isinstance(v, bool):
        return "Yes" if v else "No"
    if isinstance(v, float):
        v = f"{v:,.2f}"
    return str(v).replace("|", "/").replace("\n", " ").strip()


def table(headers, rows, cap=CAP, sheet=None):
    if not rows:
        return "_None found._\n"
    out = ["| " + " | ".join(h for h, _ in headers) + " |", "|" + "---|" * len(headers)]
    for r in rows[:cap]:
        out.append("| " + " | ".join(_cell(r.get(k) if isinstance(r, dict) else r[k]) for _, k in headers) + " |")
    if len(rows) > cap:
        out.append(f"\n_Showing {cap} of {len(rows)}." + (f" Full list in inventory.xlsx, sheet '{sheet}'._" if sheet else "_"))
    return "\n".join(out) + "\n"


def money(v, cur):
    return f"{cur} {v:,.0f}" if v is not None else "n/a"


def _part1(e):
    m = e.manifest
    nets = e.networks()
    topo = e.topology(nets)
    costs = e.costs()
    cur = costs["currency"]
    captured = m["capturedAt"][:16].replace("T", " ")
    L = []
    w = L.append

    w(f"# Current-State Estate Brief: {m.get('customer') or 'Customer'}\n")
    w(f"Captured {captured} UTC from Azure Resource Graph, Cost Management and Microsoft Graph by EstateSnapshot, "
      f"using read-only access. Tenant `{m.get('tenantId')}`, {len(m['subscriptions'])} subscription(s).\n")
    w("This brief states facts observed in the estate. It contains no recommendations. Resource Graph can lag recent "
      "changes by up to a day. Anything about applications, users, on-premises systems, data volumes or business "
      "requirements is outside what the snapshot can see and must come from discovery (see section 13).\n")

    w("## Headline facts\n")
    for line in e.headline():
        w(f"- {line}")
    w("")

    w("## 1. Shape of the estate\n")
    overview = e.overview()
    for r in overview:
        r["costFmt"] = money(r["cost"], cur) if r["cost"] is not None else "n/a"
    w(table([("Subscription", "subscription"), ("Resource groups", "resourceGroups"), ("Resources", "resources"),
             ("Primary resources", "primaryResources"), ("Regions", "regions"), (f"Cost, last {costs['days']} days", "costFmt")],
            overview))
    regions = e.regions()
    w("\nRegions in use: " + (", ".join(f"{k} ({v})" for k, v in regions.most_common()) or "none") + ".\n")
    w("\n**Services by category** (counts include supporting resources such as disks and NICs)\n")
    for c, counter in e.categories().items():
        w(f"- **{c}:** " + ", ".join(f"{k} x{v}" for k, v in counter.most_common()))
    w("")

    w("## 2. Network topology\n")
    w(f"**Shape:** {topo['shape']}\n")
    for h in topo["hubs"]:
        w(f"- {h}")
    if topo["isolated"]:
        w(f"- Isolated VNets (no peering): {', '.join(topo['isolated'])}")
    overlaps = e.address_overlaps(nets)
    w("\n**Address space overlaps:** " + ("; ".join(overlaps) if overlaps else "none detected between VNets and on-premises ranges in scope."))
    w("\n### 2.1 Virtual networks and subnets\n")
    for n in nets:
        dns = ", ".join(n["dnsServers"]) or "Azure-provided"
        w(f"**{n['name']}** ({', '.join(n['addressSpace'])}) - {n['subscription']} / {n['resourceGroup']} / {n['location']}; DNS {dns}"
          + ("; DDoS plan" if n["ddos"] else ""))
        w("")
        w(table([("Subnet", "name"), ("Prefix", "prefix"), ("NSG", "nsg"), ("Route table", "routeTable"),
                 ("NAT GW", "natGateway"), ("Delegation", "delegation"), ("PEs", "privateEndpoints"), ("Contains", "occupants")],
                n["subnets"], cap=40, sheet="Subnets"))
    w("### 2.2 Peering\n")
    peer_rows = [{"vnet": n["name"], **p} for n in nets for p in n["peerings"]]
    w(table([("VNet", "vnet"), ("Remote", "remote"), ("Remote in scope", "remoteInScope"), ("State", "state"),
             ("Fwd traffic", "allowForwardedTraffic"), ("Gateway transit", "allowGatewayTransit"),
             ("Use remote GW", "useRemoteGateways")], peer_rows, cap=40, sheet="Peerings"))
    gw = e.gateways()
    w("### 2.3 Hybrid connectivity\n")
    w(table([("Gateway", "name"), ("Type", "type"), ("SKU", "sku"), ("VPN type", "vpnType"), ("Active-active", "activeActive"),
             ("BGP", "bgp"), ("VNet", "vnet")], gw["gateways"]))
    if gw["connections"]:
        w(table([("Connection", "name"), ("Type", "type"), ("Gateway", "gateway"), ("Peer", "peer"), ("Status", "status")], gw["connections"]))
    if gw["localGateways"]:
        w("\nOn-premises sites (local network gateways):\n")
        w(table([("Site", "name"), ("Public address", "address"), ("On-premises prefixes", "prefixes"), ("BGP", "bgp")], gw["localGateways"]))
    if gw["circuits"]:
        w(table([("Circuit", "name"), ("Provider", "provider"), ("Location", "location"), ("Mbps", "bandwidthMbps"), ("SKU", "sku")], gw["circuits"]))
    w("### 2.4 Edge, egress and shared network services\n")
    w(table([("Name", "name"), ("Service", "service"), ("Detail", "detail"), ("Subscription", "subscription")], e.edge_services()))
    rts = e.route_tables()
    if rts:
        w("\n**Route tables**\n")
        w(table([("Route table", "name"), ("Routes", "routes"), ("Subnets", "subnets"), ("BGP propagation off", "bgpPropagationDisabled")], rts))
    w("### 2.5 Private endpoints and DNS\n")
    pes = e.private_endpoints()
    w(table([("Endpoint", "name"), ("Subnet", "subnet"), ("Target", "target"), ("Target service", "targetService"),
             ("Sub-resource", "groupIds"), ("Target in scope", "targetInScope")], pes, sheet="PrivateEndpoints"))
    d = e.dns()
    w("\n**Private DNS zones**\n")
    w(table([("Zone", "zone"), ("Records", "records"), ("Linked VNets", "linkedVnets"), ("Subscription", "subscription")], d["private"]))
    if d["resolvers"]:
        w(table([("DNS resolver", "name"), ("VNet", "vnet")], d["resolvers"]))
    if d["public"]:
        w("\n**Public DNS zones:** " + ", ".join(f"{z['zone']} ({z['records']} records)" for z in d["public"]))
    w("")
    return L


def _part2(e):
    L = []
    w = L.append
    costs = e.costs()
    cur = costs["currency"]

    w("## 3. Internet exposure\n")
    w("**Public IP addresses**\n")
    w(table([("Public IP", "name"), ("Address", "ip"), ("SKU", "sku"), ("Attached to", "attachedTo"),
             ("Service", "attachedService"), ("DNS name", "dns"), ("NSG rules naming this address", "nsgRules")],
            e.public_ips(), sheet="PublicIPs"))
    nsg = e.open_nsg_rules()
    w("\n**NSG rules allowing inbound traffic from any source or the Internet**, each evaluated in priority order "
      "against the higher-priority deny rules of the same NSG. 'Closed' means deny rules cover all of the rule's traffic; "
      "'partly closed' lists what remains. A NIC-level and a subnet-level NSG both filter the same traffic; the "
      "'also filtered by' column names the other one.\n")
    w(table([("NSG", "nsg"), ("Rule", "rule"), ("Priority", "priority"), ("Protocol", "protocol"), ("Ports", "ports"),
             ("Destination", "destination"), ("Evaluated", "effective"), ("Closed by", "closedBy"), ("Remaining", "remaining"),
             ("Management ports", "managementPorts"), ("Also filtered by", "alsoFilteredBy"), ("Attached to", "attachedTo")],
            nsg, cap=40, sheet="OpenNSGRules"))
    waf = e.waf_policies()
    if waf:
        w("\n**Web application firewall policies**\n")
        w(table([("Policy", "name"), ("Service", "service"), ("Mode", "mode"), ("State", "state"), ("Rule sets", "ruleSets"),
                 ("Custom rules", "customRules"), ("Used by", "attachedTo")], waf))
    paas = e.paas_network_access()
    w("\n**PaaS network access** (from each service's public network access and firewall default action)\n")
    w(table([("Resource", "name"), ("Service", "service"), ("Stance", "stance"), ("Private endpoints", "privateEndpoints"),
             ("Subscription", "subscription")], paas, cap=40, sheet="PaaSNetworkAccess"))

    w("## 4. Compute\n")
    vms = e.vms()
    w(table([("VM", "name"), ("Size", "size"), ("OS", "os"), ("Image", "image"), ("Disk", "osDisk"),
             ("Data disks", "dataDisks"), ("Zone", "zone"), ("Power", "power"), ("Subnet", "subnet"),
             ("Private IP", "privateIp"), ("Public IP", "publicIp"), ("Backup", "backup"), ("SQL", "sql")],
            vms, cap=40, sheet="VirtualMachines"))
    for title, rows, cols in (
        ("Scale sets", e.scale_sets(), [("Name", "name"), ("SKU", "sku"), ("Capacity", "capacity"), ("Mode", "mode"), ("Subnet", "subnet")]),
        ("AKS clusters", e.aks(), [("Cluster", "name"), ("Version", "version"), ("Tier", "tier"), ("Node pools", "pools"),
                                   ("Network", "network"), ("Network policy", "networkPolicy"), ("Ingress", "ingress"),
                                   ("Authorised API ranges", "authorisedRanges"), ("Private API", "private"), ("Subnet", "subnet")]),
        ("AVD host pools", e.avd(), [("Host pool", "name"), ("Type", "type"), ("Load balancing", "loadBalancing"), ("Max sessions", "maxSessions")]),
    ):
        if rows:
            w(f"\n**{title}**\n")
            w(table(cols, rows))

    w("## 5. Web, containers and integration\n")
    asv = e.app_service()
    if asv["plans"]:
        w("**App Service plans**\n")
        w(table([("Plan", "name"), ("SKU", "sku"), ("OS", "os"), ("Workers", "workers"), ("Zone redundant", "zoneRedundant"),
                 ("Apps", "apps"), ("Subscription", "subscription")], asv["plans"]))
        w("\n**Apps**\n")
        w(table([("App", "name"), ("Kind", "kind"), ("Plan", "plan"), ("State", "state"), ("HTTPS only", "httpsOnly"),
                 ("Public access", "publicNetworkAccess"), ("VNet integration", "vnetIntegration"),
                 ("Private endpoints", "privateEndpoints"), ("Custom domains", "customDomains")], asv["apps"], cap=40, sheet="Apps"))
    if asv["apps"]:
        ss = e.site_summary()
        w(f"\n**Public network access:** {ss['publicAccess']} of {ss['sites']} site(s) accept public network access "
          f"({ss['publicRestricted']} of those have access restrictions configured); {ss['privateOnly']} are private only.\n")
        if ss["collected"]:
            w(f"**Site configuration** (read from each site configuration; {ss['collected']} of {ss['sites']} collected)\n")
            w("Runtimes: " + ", ".join(f"{k} x{n}" for k, n in ss["runtimes"]) + ".")
            w(f"Worker bitness: {len(ss['bits32'])} on 32-bit, {ss['bits64']} on 64-bit. "
              f"Without a health check path: {len(ss['noHealthCheck'])}.")
            if ss["registries"]:
                w("Container registries named by site images: " + ", ".join(
                    f"{reg} ({where}) x{n}" for (reg, where), n in ss["registries"]) + ".")
            w("")
            w(table([("Site", "name"), ("Kind", "kind"), ("Runtime", "runtime"), ("Workers", "workerBits"), ("Always on", "alwaysOn"),
                     ("Health check", "healthCheckPath"), ("Scale limit", "scaleLimit"), ("Image registry", "registry"),
                     ("Registry in capture", "registryInCapture"), ("Subscription", "subscription")],
                    e.site_facts(), cap=60, sheet="SiteConfig"))
    ca = e.container_apps()
    if ca["environments"]:
        w("\n**Container Apps**\n")
        w(table([("Environment", "name"), ("Internal", "internal"), ("Subnet", "subnet"), ("Profiles", "profiles")], ca["environments"]))
        w(table([("App", "name"), ("Environment", "environment"), ("Ingress", "ingress"), ("Port", "targetPort")], ca["apps"]))
    other = e.other_services()
    if other:
        w("\n**Integration, AI and other services**\n")
        w(table([("Name", "name"), ("Service", "service"), ("SKU", "sku"), ("Subscription", "subscription"),
                 ("Resource group", "resourceGroup")], other, cap=40, sheet="Resources"))

    w("## 6. Data and storage\n")
    w(table([("Name", "name"), ("Service", "service"), ("Detail", "detail"), ("Subscription", "subscription"),
             ("Region", "location")], e.data(), cap=40, sheet="Data"))

    cz = e.cosmos_config()
    if cz["accounts"] and e.collected("cosmos_config"):
        w("\n**Cosmos DB accounts and throughput** (database and container settings read from ARM)\n")
        w(table([("Account", "name"), ("Consistency", "consistency"), ("Backup", "backup"), ("Key auth", "keyAuth"),
                 ("Public access", "publicNetworkAccess"), ("Regions", "regions"), ("Multi-write", "multiWrite"),
                 ("Databases", "databases"), ("Containers", "containers"), ("Throughput", "throughput")],
                cz["accounts"], cap=40, sheet="CosmosAccounts"))
        w("\nContainer detail (partition key, throughput mode) is in inventory.xlsx, sheet 'CosmosContainers'.\n")
    sb = e.servicebus_config()
    if sb["namespaces"] and e.collected("servicebus_config"):
        w("\n**Service Bus namespaces** (topics, subscriptions and queues read from ARM)\n")
        w(table([("Namespace", "name"), ("SKU", "sku"), ("Key auth", "keyAuth"), ("Topics", "topics"), ("Queues", "queues"),
                 ("Partitioned entities", "partitioned"), ("Topics with no subscriptions", "topicsWithoutSubscriptions"),
                 ("Dead-lettered messages now", "deadLetters")], sb["namespaces"], cap=40, sheet="ServiceBus"))
        for n in sb["namespaces"]:
            if n["topWithoutSubscriptions"]:
                w(f"\n{n['name']}: topics with no subscriptions: " + ", ".join(n["topWithoutSubscriptions"][:30])
                  + (" ..." if len(n["topWithoutSubscriptions"]) > 30 else "") + ".")
        w("")
    ml = e.measured_load()
    if ml:
        w("\n### 6.1 Measured load\n")
        w(f"Azure Monitor metrics, {ml['window']}. Percentages are of requests or samples in the window; "
          "'coverage' is the share of expected hourly samples that returned data.\n")
        if ml["cosmos"]:
            w("**Cosmos DB**\n")
            w(table([("Account", "account"), ("Requests", "requests"), ("Throttled (429)", "throttled429"), ("% throttled", "throttledPct"),
                     ("Normalised RU, peak %", "normalisedRuMax"), ("Normalised RU, mean of hourly means %", "normalisedRuAvg"),
                     ("Days at 100%", "daysAtFullRu"), ("Data GB (peak)", "dataGB"), ("Coverage", "coverage")], ml["cosmos"], sheet="MeasuredCosmos"))
            top = [c for c in ml["containers"] if c["throttled429"]][:12]
            if top:
                w("\nRequests throttled, by container (most 429s first)\n")
                w(table([("Account", "account"), ("Container", "container"), ("Requests", "requests"),
                         ("Throttled (429)", "throttled429"), ("% throttled", "throttledPct")], top, cap=12))
            if any(c["truncated"] for c in ml["cosmos"]):
                w("\n_Some accounts returned the maximum number of series; container splits may be incomplete._")
        if ml["servicebus"]:
            w("\n**Service Bus**\n")
            w(table([("Namespace", "namespace"), ("Incoming messages", "incoming"), ("Dead-lettered, peak", "deadLetterPeak"),
                     ("Dead-lettered, mean", "deadLetterAvg"), ("Active, peak", "activePeak"), ("Coverage", "coverage")], ml["servicebus"]))
        if ml["plans"]:
            w("\n**App Service plans**\n")
            w(table([("Plan", "plan"), ("SKU", "sku"), ("Workers", "workers"), ("CPU mean %", "cpuAvg"), ("CPU peak %", "cpuPeak"),
                     ("Memory mean %", "memAvg"), ("Memory peak %", "memPeak"), ("Coverage", "coverage")], ml["plans"], cap=30, sheet="MeasuredPlans"))
        if ml["sql"]:
            w("\n**Azure SQL**\n")
            w(table([("Database or pool", "name"), ("Type", "type"), ("SKU", "sku"), ("CPU mean %", "cpuAvg"), ("CPU peak %", "cpuPeak"),
                     ("Coverage", "coverage")], ml["sql"], cap=30, sheet="MeasuredSQL"))
        w("")

    w("## 7. Identity and access\n")
    acc = e.access()
    ids = e.identities()
    priv = [a for a in acc if a["privileged"] and a["_level"] <= 1]
    w(f"{len(acc)} role assignments in scope. Managed identities: {ids['systemAssigned']} system-assigned, "
      f"{ids['userAssigned']} user-assigned.\n")
    w("**Privileged roles at subscription scope or above** (Owner, Contributor, User Access Administrator, RBAC Administrator)\n")
    w(table([("Scope", "scope"), ("Role", "role"), ("Principal", "principal"), ("Type", "principalType"), ("UPN", "upn")],
            priv, cap=40, sheet="Access"))
    kvs = e.key_vaults()
    if kvs:
        w("\n**Key Vaults**\n")
        w(table([("Vault", "name"), ("RBAC model", "rbac"), ("Soft delete", "softDelete"), ("Purge protection", "purgeProtection"),
                 ("Public access", "publicNetworkAccess"), ("Firewall default", "defaultAction")], kvs))

    en = e.entra()
    if en:
        w("\n### 7.1 Entra ID (collected with --entra)\n")
        o = en["organization"]
        if o:
            w(f"- Tenant {o.get('displayName')}: on-premises sync "
              f"{'enabled' if o.get('onPremisesSyncEnabled') else 'not enabled'}"
              + (f", last sync {o['onPremisesLastSyncDateTime']}" if o.get("onPremisesLastSyncDateTime") else "") + ".")
        if en["federatedDomains"] or en["managedDomains"]:
            w(f"- Domains: {len(en['managedDomains'])} managed, {len(en['federatedDomains'])} federated"
              + (f" ({', '.join(en['federatedDomains'])})" if en["federatedDomains"] else "") + ".")
        if en["policies"]:
            w(f"- Conditional Access: {len(en['policies'])} policies, "
              + ", ".join(f"{n} {k}" for k, n in en["policyStates"].most_common()) + f"; {en['mfaPolicies']} require MFA or an authentication strength.")
            w("")
            w(table([("Policy", "name"), ("State", "state"), ("All users", "allUsers"), ("All apps", "allApps"),
                     ("Grant controls", "grant"), ("Authentication strength", "authStrength"), ("Locations", "locations")], en["policies"], cap=40, sheet="ConditionalAccess"))
        if en["roles"]:
            w("\n**Directory roles with members**\n")
            w(table([("Role", "role"), ("Members", "members"), ("Who", "who")], en["roles"], cap=25, sheet="DirectoryRoles"))
        w("")

    w("## 8. Governance\n")
    pol = e.policy()
    w(f"{len(pol)} policy assignment(s) in scope.\n")
    w(table([("Assignment", "assignment"), ("Scope", "scope"), ("Enforcement", "enforcement"), ("Initiative", "initiative"),
             ("Built-in", "builtIn"), ("Non-compliant resources", "nonCompliant")], pol, sheet="Policy"))
    tg = e.tags()
    w(f"\n**Tagging:** {tg['untagged']} of {tg['resources']} primary resources have no tags. "
      + ("Most used keys: " + ", ".join(f"{k} ({p}%)" for k, _, p in tg["keys"]) + "." if tg["keys"] else "No tag keys in use."))
    if tg["rgKeys"]:
        w("Resource group tag keys: " + ", ".join(f"{k} ({p}%)" for k, _, p in tg["rgKeys"]) + ".")
    w("")

    ret = e.retirements()
    if ret:
        w("\n### 8.1 Dated platform changes that apply to resources in this estate\n")
        w("Each row is a published date from the retirement register matched to resources in the capture. "
          "The register and its sources are in estatesnapshot/data/retirements.json.\n")
        w(table([("Change", "change"), ("Date", "date"), ("Days from capture", "daysFromCapture"), ("Affected", "affected"),
                 ("Resources", "resources"), ("Source", "source")], ret, cap=20, sheet="Retirements"))
        w("")

    w("## 9. Resilience and recovery\n")
    rs = e.resilience()
    w(f"- Virtual machines: {rs['vmCount']}; with Azure Backup: {rs['vmProtected']}; in availability zones: {rs['zonalVms']}; "
      f"in availability sets: {rs['avSetVms']}.")
    if rs["unprotectedVms"]:
        w(f"- VMs without Azure Backup: {', '.join(rs['unprotectedVms'][:40])}" + (" ..." if len(rs["unprotectedVms"]) > 40 else ""))
    w("- Storage account redundancy: " + (", ".join(f"{k} x{v}" for k, v in rs["storageRedundancy"].most_common()) or "none"))
    w(f"- Site Recovery replicated items: {len(rs['asr'])}")
    w(f"- Regions holding resources: {', '.join(rs['regions']) or 'none'}\n")
    w(table([("Vault", "name"), ("Type", "service"), ("Redundancy", "redundancy"), ("Cross-region restore", "crossRegionRestore"),
             ("Soft delete", "softDelete"), ("Immutability", "immutability"), ("Protected items", "items")], rs["vaults"]))

    w("## 10. Monitoring and security posture\n")
    mon = e.monitoring()
    w(table([("Workspace", "name"), ("SKU", "sku"), ("Retention days", "retentionDays"), ("Daily cap GB", "dailyCapGB"),
             ("Subscription", "subscription")], mon["workspaces"]))
    ds = e.diagnostic_settings()
    if ds:
        w(f"\n**Diagnostic settings:** {ds['withSettings']} of {ds['asked']} resource(s) whose type accepts them have at least one "
          f"({ds['none']} have none). Every setting on a resource is listed in inventory.xlsx, sheet 'DiagnosticSettings'.\n")
        w(table([("Service", "service"), ("Resources", "resources"), ("With settings", "withSettings"), ("None", "none"),
                 ("Type not supported", "unsupported")], ds["coverage"], cap=30))
        if ds["destinations"]:
            w("\nDestinations: " + ", ".join(f"{k} x{n}" for k, n in ds["destinations"][:15]) + ".")
    w(f"\n- Application Insights: {len(mon['appInsights'])} component(s)"
      + (f"; classic (no workspace): {sum(1 for a in mon['appInsights'] if a['workspace'].startswith('classic'))}" if mon["appInsights"] else ""))
    w("- Alerting: " + (", ".join(f"{k} x{v}" for k, v in mon["alerts"].most_common()) or "no alert rules or action groups found"))
    w(f"- Data collection rules: {mon['dcrCount']}")
    for s in e.manifest["subscriptions"]:
        plans = mon["defenderPlans"].get(s["name"])
        score = mon["secureScores"].get(s["name"])
        w(f"- {s['name']}: Defender plans on: {', '.join(plans) if plans else 'none (free tier)'}; secure score {f'{score}%' if score is not None else 'n/a'}")
    sec = e.security()
    if sec["severity"]:
        w("\nUnhealthy Defender for Cloud assessments by severity: " + ", ".join(f"{k} {v}" for k, v in sec["severity"].most_common()) + "\n")
        w(table([("Severity", "severity"), ("Finding", "finding"), ("Resources", "resources")], sec["top"], cap=15))
    adv = e.advisor()
    if adv["counts"]:
        w("\nAzure Advisor recommendations: " + ", ".join(f"{c} {i} {n}" for (c, i), n in sorted(adv["counts"].items())) + "\n")
        w(table([("Category", "category"), ("High-impact recommendation", "problem"), ("Resources", "resources")], adv["high"], cap=15))
    w("")

    w("## 11. Cost and spend\n")
    sp = e.spend()
    if sp["months"]:
        scur = sp["currency"] or cur
        w("**Actual cost by calendar month** (Cost Management, every subscription in scope)\n")
        rows = []
        for m in sp["months"]:
            short = m["label"].split()[0][:3]
            if m["change"] is not None:
                change = f"{m['change']:+.1f}% on {m['previous']}"
            else:
                change = "n/a (part month)" if not m["complete"] else "n/a (month before not in this capture)"
            rows.append({"month": m["label"],
                         "covered": "whole month" if m["complete"] else f"1 to {m['days']} {short} ({m['days']} of {m['daysInMonth']} days)",
                         "costFmt": money(m["cost"], scur),
                         "perDay": money(m["perDay"], scur) if m["perDay"] is not None else "n/a",
                         "change": change})
        w(table([("Month", "month"), ("Covered", "covered"), ("Actual cost", "costFmt"),
                 ("Cost a day", "perDay"), ("Change on prior month", "change")], rows, cap=13, sheet="CostByMonth"))
        lf, mt = sp["lastFull"], sp["mtd"]
        if lf:
            line = f"Last full calendar month ({lf['label']}): {money(lf['cost'], scur)}"
            line += f", {lf['change']:+.1f}% on {lf['previous']}" if lf["change"] is not None else f"; {lf['previous']} not in the capture"
            w(line + ".")
        if mt:
            w(f"Month to date is {money(mt['cost'], scur)} over {mt['days']} days of {mt['label']}, "
              f"{money(mt['perDay'], scur)} a day. Part-month figures are not comparable with a whole month.\n")
        if lf and len(lf["bySub"]) > 1:
            w("**Last full calendar month by subscription**\n")
            w(table([("Subscription", 0), ("Cost", 1)],
                    [(k, money(v, scur)) for k, v in lf["bySub"].items()], cap=40))
        if sp["unavailable"]:
            w(f"Spend data unavailable for: {', '.join(sp['unavailable'])}.\n")
    else:
        w("No monthly spend data. Cost Management may be unavailable (common with CSP subscriptions). "
          + (f"Failed for: {', '.join(sp['unavailable'])}." if sp["unavailable"] else "") + "\n")
    w(f"### Cost detail, last {costs['days']} days\n")
    bill = e.billing()
    if bill["recorded"]:
        w("**Subscription offer** (the quota id Azure holds for each subscription)\n")
        for b in bill["subscriptions"]:
            b["costFmt"] = money(b["cost"], cur) if b["cost"] is not None else "n/a"
        w(table([("Subscription", "subscription"), ("Offer", "offer"), ("Quota id", "quotaId"), ("Spending limit", "spendingLimit"),
                 (f"Cost, last {costs['days']} days", "costFmt")], bill["subscriptions"], cap=40, sheet="Billing"))
        w("")
    if bill["pricingModels"]:
        w("**Cost by pricing model:** " + ", ".join(f"{k} {money(v, cur)} ({p}%)" for k, v, p in bill["pricingModels"]) + ".\n")
    if costs["total"]:
        w(f"Total {money(costs['total'], cur)}" + (f". Cost data unavailable for: {', '.join(costs['unavailable'])}." if costs["unavailable"] else ".") + "\n")
        w(table([("Service", 0), ("Cost", 1)], [(k, money(v, cur)) for k, v in costs["topServices"]], cap=15))
        w("")
        w(table([("Subscription", 0), ("Resource group", 1), ("Cost", 2)], [(s, r, money(v, cur)) for (s, r), v in costs["topRgs"]], cap=15))
    else:
        w("No cost data returned. Cost Management access may be missing (common with CSP subscriptions). "
          + (f"Failed for: {', '.join(costs['unavailable'])}." if costs["unavailable"] else ""))
    w("")
    lau = e.log_analytics_usage()
    if lau:
        w("**Log Analytics ingestion** (Usage table, billable data)\n")
        for u in lau:
            u["top"] = ", ".join(f"{t['table']} {t['gb']:,.0f} GB ({t['share']}%)" for t in u["topTables"][:5])
            u["ov"] = "; ".join(u["overrides"][:6])
        w(table([("Workspace", "workspace"), ("Billable GB in window", "billableGB"), ("Days", "days"), ("GB per 30 days", "perMonthGB"),
                 ("Largest tables", "top"), ("Tables with own retention", "ov")], lau, cap=20, sheet="LogAnalyticsUsage"))
        w("")

    w("## 12. Observed dependencies\n")
    w("Resource-to-resource references found in configuration (resource IDs held in properties). These show how "
      "components are wired in Azure. Application-level dependencies such as connection strings, protocols and "
      "authentication are not visible here and must be confirmed in discovery.\n")
    w(table([("From", "from"), ("Service", "fromService"), ("To", "to"), ("Service ", "toService"), ("Via property", "via")],
            e.dependencies(), cap=60, sheet="Dependencies"))
    gaps = e.scope_gaps()
    if gaps["summary"]:
        w("\n**Referenced but not captured.** Subscriptions outside the snapshot that resources inside it point at "
          "(peerings, private endpoint targets, registries, diagnostic destinations and other resource ids). "
          "Confirm whether they belong in scope and request Reader access.\n")
        w(table([("Subscription outside the capture", "subscription"), ("References", "references"), ("Referenced from", "referencedFrom"),
                 ("Through", "via"), ("Examples", "examples")], gaps["summary"], cap=20, sheet="OutsideScope"))
        w("\nEvery reference:\n")
        w(table([("From", "from"), ("Service", "fromService"), ("Through", "via"), ("Target type", "toType"), ("Target", "to"),
                 ("Subscription", "subscription")], gaps["references"], cap=30, sheet="OutsideScope"))
    w("")

    w("## 13. Not visible to the snapshot (confirm in discovery)\n")
    for line in (
        "Applications, their owners, users and business criticality; RPO and RTO targets.",
        "Application-level dependencies: connection strings, protocols, ports and authentication between components.",
        "On-premises estate beyond the address ranges declared on local network gateways.",
        *(() if e.collected("diagnostics") else ("Diagnostic settings (which resources send logs where).",)),
        "Data volumes and growth beyond the measured windows in this brief.",
        *(() if e.entra() else ("Entra ID configuration: Conditional Access, directory roles and hybrid identity sync (collect with --entra).",)),
        "Entra ID licensing and Privileged Identity Management.",
        *(() if e.measured_load() else ("Measured load: Azure Monitor metrics were not collected in this snapshot.",)),
        "Guest OS state: patch levels, installed software, local firewall and agents.",
        "Reserved Instances, Savings Plans and Azure Hybrid Benefit entitlement (licence type is shown per VM where set).",
    ):
        w(f"- {line}")
    errs = e.manifest.get("errors") or {}
    if errs:
        w("\n**Collection gaps in this snapshot**\n")
        for k, v in errs.items():
            w(f"- {k}: {v[:200]}")
    w("")
    return L


def render(e):
    return "\n".join(_part1(e) + _part2(e)) + "\n"
