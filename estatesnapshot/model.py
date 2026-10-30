"""Stage 2b - the model. Turns the raw snapshot plus the graph into facts an
architect needs for an as-is: shape, networking, exposure, compute, data,
access, governance, resilience, monitoring and cost. Facts only - no scoring,
no recommendations. Interpretation belongs to the ADS."""
import calendar
import datetime as dt
import ipaddress
import json
import os
from collections import Counter, defaultdict

from . import catalogue as cat
from . import nsg as nsgrules
from .graph import Graph
from .model_config import ConfigFacts
from .util import g, gb, lc, name_of  # noqa: F401  (re-exported for design.py)

T_VNET = "microsoft.network/virtualnetworks"
T_SUBNET = "microsoft.network/virtualnetworks/subnets"
T_NIC = "microsoft.network/networkinterfaces"
T_VM = "microsoft.compute/virtualmachines"
T_PE = "microsoft.network/privateendpoints"
PRIVILEGED = {"owner", "contributor", "user access administrator", "role based access control administrator"}
OPEN_SOURCES = {"*", "internet", "0.0.0.0/0", "any"}
MGMT_PORTS = {"22": "SSH", "3389": "RDP", "1433": "SQL", "3306": "MySQL", "5432": "PostgreSQL",
              "5985": "WinRM", "5986": "WinRM", "445": "SMB", "23": "Telnet", "21": "FTP"}


class Estate(ConfigFacts):
    def __init__(self, snap_dir):
        self.dir = snap_dir
        raw = os.path.join(snap_dir, "raw")

        def load(n, default):
            p = os.path.join(raw, n + ".json")
            return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else default

        self.manifest = json.load(open(os.path.join(snap_dir, "manifest.json"), encoding="utf-8"))
        self.resources = load("resources", [])
        self.containers = load("containers", [])
        self.role_assignments = load("role_assignments", [])
        self.policy_assignments = load("policy_assignments", [])
        self.policy_compliance = load("policy_compliance", [])
        self.advisor_recs = load("advisor", [])
        self.defender = load("defender_plans", [])
        self.scores = load("secure_scores", [])
        self.findings = load("security_findings", [])
        self.backup_items = load("backup_items", [])
        self.asr_items = load("asr_items", [])
        self.cost_rows = load("costs", [])
        self.cost_month_rows = load("cost_months", [])
        self.principals = load("principals", {})
        self.role_defs = load("role_definitions", {})
        self.manifest.setdefault("stages", {})

        dup = Counter(s["name"] for s in self.manifest["subscriptions"])
        for s in self.manifest["subscriptions"]:
            if dup[s["name"]] > 1:
                s["name"] = f"{s['name']} ({s['id'][:8]})"
        self.sub_names = {s["id"]: s["name"] for s in self.manifest["subscriptions"]}
        self.graph = Graph(self.resources)
        self.nodes = self.graph.nodes
        self._load_config(load)
        self.by_type = defaultdict(list)
        for r in self.resources:
            self.by_type[lc(r["type"])].append(r)
        self.rgs = [c for c in self.containers if lc(c["type"]) == "microsoft.resources/subscriptions/resourcegroups"]
        self.backup_by_src = {b["sourceResourceId"]: b for b in self.backup_items if b.get("sourceResourceId")}
        self.asr_by_name = {lc(a.get("friendlyName")): a for a in self.asr_items}

    # -- helpers -------------------------------------------------------------

    def sub(self, sid):
        return self.sub_names.get(sid, sid)

    def label(self, rid):
        n = self.nodes.get(lc(rid))
        return n["name"] if n else name_of(rid)

    def resolve(self, rid):
        """Longest prefix of a resource ID that is a node; child IDs map to their parent resource."""
        s = lc(rid).rstrip("/")
        while "/providers/" in s:
            if s in self.nodes:
                return s
            s = s.rsplit("/", 2)[0]
        return lc(rid)

    def type_of(self, rid):
        n = self.nodes.get(lc(rid))
        return lc(n["type"]) if n else ""

    def vnet_subnet(self, subnet_id):
        n = self.nodes.get(lc(subnet_id))
        if not n:
            parts = lc(subnet_id).split("/")
            return f"{parts[-3] if len(parts) > 3 else '?'}/{parts[-1]}"
        return f"{self.label(n['parent'])}/{n['name']}"

    def of(self, *types):
        out = []
        for t in types:
            out += self.by_type.get(t, [])
        return out

    def where(self, r):
        return {"subscription": self.sub(r.get("subscriptionId")), "resourceGroup": r.get("resourceGroup"),
                "location": r.get("location")}

    # -- overview ------------------------------------------------------------

    def overview(self):
        cost = self.cost_by_sub()
        rows = []
        for s in self.manifest["subscriptions"]:
            rs = [r for r in self.resources if r.get("subscriptionId") == s["id"]]
            rows.append({
                "subscription": s["name"], "id": s["id"],
                "resourceGroups": sum(1 for c in self.rgs if c.get("subscriptionId") == s["id"]),
                "resources": len(rs),
                "primaryResources": sum(1 for r in rs if lc(r["type"]) not in cat.SUPPORTING),
                "regions": ", ".join(f"{k} ({v})" for k, v in Counter(
                    r.get("location") for r in rs if r.get("location") not in (None, "", "global")).most_common()),
                "cost": cost.get(s["id"]),
            })
        return rows

    def regions(self):
        return Counter(r.get("location") for r in self.resources if r.get("location") not in (None, "", "global"))

    def categories(self):
        out = defaultdict(Counter)
        for r in self.resources:
            t = lc(r["type"])
            out[cat.category(t)][cat.friendly(t)] += 1
        return {c: out[c] for c in cat.CATEGORY_ORDER if c in out}

    def sku_of(self, r):
        p = r.get("properties") or {}
        return (g(p, "hardwareProfile", "vmSize") or g(r, "sku", "name") or g(r, "sku", "tier")
                or (r.get("sku") if isinstance(r.get("sku"), str) else None) or "")

    def inventory(self):
        rows = []
        for r in sorted(self.resources, key=lambda x: (self.sub(x.get("subscriptionId")), lc(x.get("resourceGroup")), lc(x["type"]), lc(x["name"]))):
            t = lc(r["type"])
            rows.append({"subscription": self.sub(r.get("subscriptionId")), "resourceGroup": r.get("resourceGroup"),
                         "name": r["name"], "service": cat.friendly(t), "category": cat.category(t),
                         "type": r["type"], "location": r.get("location"), "sku": self.sku_of(r),
                         "kind": r.get("kind") or "", "zones": ",".join(r.get("zones") or []),
                         "supporting": t in cat.SUPPORTING,
                         "tags": "; ".join(f"{k}={v}" for k, v in (r.get("tags") or {}).items())})
        return rows

    # -- networking ----------------------------------------------------------

    def subnet_occupants(self, subnet_id):
        """Primary resources in a subnet: anything referencing it directly or through a NIC,
        less the plumbing (NICs, NSGs, route tables, private endpoints)."""
        sid = lc(subnet_id)
        found = Counter()
        skip = {T_NIC, T_PE, T_SUBNET, T_VNET, "microsoft.network/networksecuritygroups",
                "microsoft.network/routetables", "microsoft.network/natgateways"}
        for e in self.graph.referrers(sid):
            t = self.type_of(e["from"])
            if t == T_NIC:
                for e2 in self.graph.referrers(e["from"]):
                    t2 = self.type_of(e2["from"])
                    if t2 not in skip:
                        found[(e2["from"], t2)] += 1
            elif t not in skip:
                found[(e["from"], t)] += 1
        return sorted(found)

    def networks(self):
        out = []
        for v in self.of(T_VNET):
            p = v.get("properties") or {}
            vid = lc(v["id"])
            subnets = []
            for sid, s in self.nodes.items():
                if s.get("parent") != vid:
                    continue
                sp = s.get("properties") or {}
                occ = self.subnet_occupants(sid)
                subnets.append({
                    "name": s["name"],
                    "prefix": sp.get("addressPrefix") or ", ".join(sp.get("addressPrefixes") or []),
                    "nsg": name_of(g(sp, "networkSecurityGroup", "id")),
                    "routeTable": name_of(g(sp, "routeTable", "id")),
                    "natGateway": name_of(g(sp, "natGateway", "id")),
                    "delegation": ", ".join(g(d, "properties", "serviceName", default="") for d in sp.get("delegations") or []),
                    "serviceEndpoints": ", ".join(se.get("service", "") for se in sp.get("serviceEndpoints") or []),
                    "privateEndpoints": len(sp.get("privateEndpoints") or []),
                    "nics": sum(1 for c in sp.get("ipConfigurations") or [] if "/networkinterfaces/" in lc(c.get("id"))),
                    "occupants": ", ".join(f"{self.label(i)} ({cat.friendly(t)})" for i, t in occ),
                    "_occupants": occ, "_id": sid,
                })
            peerings = []
            for pe in p.get("virtualNetworkPeerings") or []:
                pp = pe.get("properties") or {}
                rid = lc(g(pp, "remoteVirtualNetwork", "id"))
                peerings.append({
                    "name": pe.get("name"), "remote": self.label(rid), "remoteId": rid,
                    "remoteInScope": rid in self.nodes, "state": pp.get("peeringState"),
                    "allowForwardedTraffic": pp.get("allowForwardedTraffic"),
                    "allowGatewayTransit": pp.get("allowGatewayTransit"),
                    "useRemoteGateways": pp.get("useRemoteGateways"),
                    "remotePrefixes": ", ".join(g(pp, "remoteAddressSpace", "addressPrefixes", default=[]) or []),
                })
            out.append({**self.where(v), "name": v["name"], "_id": vid,
                        "addressSpace": g(p, "addressSpace", "addressPrefixes", default=[]) or [],
                        "dnsServers": g(p, "dhcpOptions", "dnsServers", default=[]) or [],
                        "ddos": bool(p.get("enableDdosProtection")),
                        "subnets": sorted(subnets, key=lambda x: x["prefix"]), "peerings": peerings})
        return sorted(out, key=lambda x: (x["subscription"], x["name"]))

    def topology(self, nets=None):
        nets = nets or self.networks()
        degree = {n["_id"]: len(n["peerings"]) for n in nets}
        special = {"gatewaysubnet", "azurefirewallsubnet", "azurebastionsubnet"}
        hubs = [n for n in nets if degree[n["_id"]] >= 2
                or (degree[n["_id"]] >= 1 and any(lc(s["name"]) in special for s in n["subnets"]))]
        hub_ids = {h["_id"] for h in hubs}
        lines = []
        for h in hubs:
            spokes = [p["remote"] for p in h["peerings"]]
            lines.append(f"Hub candidate {h['name']} ({', '.join(h['addressSpace'])}) in {h['subscription']}, "
                         f"peered to {len(spokes)}: {', '.join(spokes)}")
        isolated = [n["name"] for n in nets if degree[n["_id"]] == 0]
        spokes_only = [n["name"] for n in nets if n["_id"] not in hub_ids and degree[n["_id"]] > 0]
        if not nets:
            shape = "No virtual networks in scope (PaaS-only or empty estate)."
        elif hubs:
            shape = f"Hub and spoke: {len(hubs)} hub candidate(s), {len(spokes_only)} spoke(s), {len(isolated)} isolated VNet(s)."
        elif any(degree.values()):
            shape = "Peered VNets without a clear hub (mesh or point-to-point)."
        else:
            shape = f"{len(nets)} isolated VNet(s); no peering."
        return {"shape": shape, "hubs": lines, "isolated": isolated}

    def address_overlaps(self, nets=None):
        nets = nets or self.networks()
        ranges = []
        for n in nets:
            for a in n["addressSpace"]:
                try:
                    ranges.append((f"VNet {n['name']}", ipaddress.ip_network(a, strict=False)))
                except ValueError:
                    pass
        for l in self.of("microsoft.network/localnetworkgateways"):
            for a in g(l, "properties", "localNetworkAddressSpace", "addressPrefixes", default=[]) or []:
                try:
                    ranges.append((f"On-premises via {l['name']}", ipaddress.ip_network(a, strict=False)))
                except ValueError:
                    pass
        out = []
        for i in range(len(ranges)):
            for j in range(i + 1, len(ranges)):
                (na, a), (nb, b) = ranges[i], ranges[j]
                if na != nb and a.version == b.version and a.overlaps(b):
                    out.append(f"{na} {a} overlaps {nb} {b}")
        return out

    def gateways(self):
        gws = []
        for gw in self.of("microsoft.network/virtualnetworkgateways"):
            p = gw.get("properties") or {}
            subnets = self.graph.subnets_of(lc(gw["id"]))
            gws.append({**self.where(gw), "name": gw["name"], "type": p.get("gatewayType"), "vpnType": p.get("vpnType"),
                        "sku": g(p, "sku", "name"), "activeActive": p.get("activeActive"), "bgp": p.get("enableBgp"),
                        "vnet": ", ".join(self.vnet_subnet(s).split("/")[0] for s in subnets)})
        conns = []
        for c in self.of("microsoft.network/connections"):
            p = c.get("properties") or {}
            peer = g(p, "localNetworkGateway2", "id") or g(p, "peer", "id") or g(p, "virtualNetworkGateway2", "id")
            conns.append({**self.where(c), "name": c["name"], "type": p.get("connectionType"),
                          "gateway": name_of(g(p, "virtualNetworkGateway1", "id")), "peer": name_of(peer),
                          "status": p.get("connectionStatus") or "", "protocol": p.get("connectionProtocol") or ""})
        lngs = []
        for l in self.of("microsoft.network/localnetworkgateways"):
            p = l.get("properties") or {}
            lngs.append({**self.where(l), "name": l["name"], "address": p.get("gatewayIpAddress") or p.get("fqdn"),
                         "prefixes": ", ".join(g(p, "localNetworkAddressSpace", "addressPrefixes", default=[]) or []),
                         "bgp": bool(p.get("bgpSettings"))})
        circuits = []
        for c in self.of("microsoft.network/expressroutecircuits"):
            p = c.get("properties") or {}
            sp = p.get("serviceProviderProperties") or {}
            circuits.append({**self.where(c), "name": c["name"], "provider": sp.get("serviceProviderName"),
                             "location": sp.get("peeringLocation"), "bandwidthMbps": sp.get("bandwidthInMbps"),
                             "sku": g(c, "sku", "name")})
        return {"gateways": gws, "connections": conns, "localGateways": lngs, "circuits": circuits}

    def edge_services(self):
        rows = []
        for r in self.of("microsoft.network/azurefirewalls"):
            p = r.get("properties") or {}
            rows.append({**self.where(r), "name": r["name"], "service": "Azure Firewall",
                         "detail": f"{g(p, 'sku', 'tier', default='')} tier; policy {name_of(g(p, 'firewallPolicy', 'id')) or 'classic rules'}; "
                                   f"threat intel {p.get('threatIntelMode') or 'n/a'}"})
        for r in self.of("microsoft.network/applicationgateways"):
            p = r.get("properties") or {}
            waf = self._waf_text(r)
            auto = g(p, "autoscaleConfiguration")
            cap = f"autoscale {auto.get('minCapacity')}-{auto.get('maxCapacity', '?')}" if auto else f"capacity {g(p, 'sku', 'capacity')}"
            backends = []
            for bp in p.get("backendAddressPools") or []:
                for a in g(bp, "properties", "backendAddresses", default=[]) or []:
                    backends.append(a.get("fqdn") or a.get("ipAddress"))
                for ipc in g(bp, "properties", "backendIPConfigurations", default=[]) or []:
                    nic = lc(ipc.get("id", "")).split("/ipconfigurations/")[0]
                    vm = [self.label(e["from"]) for e in self.graph.referrers(nic, T_VM)]
                    backends += vm or [name_of(nic)]
            rows.append({**self.where(r), "name": r["name"], "service": "Application Gateway",
                         "detail": f"{g(p, 'sku', 'name')}; {cap}; WAF {waf}; {len(p.get('httpListeners') or [])} listener(s); "
                                   f"backends: {', '.join(sorted(set(filter(None, backends)))) or 'none'}"})
        for r in self.of("microsoft.cdn/profiles", "microsoft.network/frontdoors"):
            rows.append({**self.where(r), "name": r["name"], "service": cat.friendly(r["type"]),
                         "detail": g(r, "sku", "name", default="")})
        for r in self.of("microsoft.network/loadbalancers"):
            p = r.get("properties") or {}
            public = any(g(f, "properties", "publicIPAddress") for f in p.get("frontendIPConfigurations") or [])
            rows.append({**self.where(r), "name": r["name"], "service": "Load Balancer",
                         "detail": f"{g(r, 'sku', 'name', default='')}; {'public' if public else 'internal'}; "
                                   f"{len(p.get('loadBalancingRules') or [])} rule(s)"})
        for r in self.of("microsoft.network/bastionhosts"):
            rows.append({**self.where(r), "name": r["name"], "service": "Azure Bastion", "detail": g(r, "sku", "name", default="")})
        for r in self.of("microsoft.network/natgateways"):
            subs = [self.vnet_subnet(s.get("id")) for s in g(r, "properties", "subnets", default=[]) or []]
            rows.append({**self.where(r), "name": r["name"], "service": "NAT Gateway", "detail": "subnets: " + (", ".join(subs) or "none")})
        for r in self.of("microsoft.network/trafficmanagerprofiles"):
            rows.append({**self.where(r), "name": r["name"], "service": "Traffic Manager",
                         "detail": g(r, "properties", "trafficRoutingMethod", default="")})
        for r in self.of("microsoft.network/ddosprotectionplans"):
            rows.append({**self.where(r), "name": r["name"], "service": "DDoS Protection Plan", "detail": ""})
        for r in self.of("microsoft.apimanagement/service"):
            p = r.get("properties") or {}
            rows.append({**self.where(r), "name": r["name"], "service": "API Management",
                         "detail": f"{g(r, 'sku', 'name')}; VNet mode {p.get('virtualNetworkType') or 'None'}"})
        return rows

    def private_endpoints(self):
        rows = []
        for r in self.of(T_PE):
            p = r.get("properties") or {}
            conns = (p.get("privateLinkServiceConnections") or []) + (p.get("manualPrivateLinkServiceConnections") or [])
            for c in conns:
                cp = c.get("properties") or {}
                tid = lc(cp.get("privateLinkServiceId"))
                rows.append({**self.where(r), "name": r["name"], "subnet": self.vnet_subnet(g(p, "subnet", "id")),
                             "target": self.label(tid), "targetService": cat.friendly(self.type_of(tid) or "/".join(tid.split("/")[6:8])),
                             "groupIds": ", ".join(cp.get("groupIds") or []), "targetInScope": tid in self.nodes,
                             "status": g(cp, "privateLinkServiceConnectionState", "status", default=""),
                             "_target": tid, "_subnet": lc(g(p, "subnet", "id"))})
        return rows

    def dns(self):
        links = defaultdict(list)
        for l in self.of("microsoft.network/privatednszones/virtualnetworklinks"):
            zone = lc(l["id"]).split("/virtualnetworklinks/")[0]
            links[zone].append(self.label(g(l, "properties", "virtualNetwork", "id")))
        private = [{**self.where(z), "zone": z["name"], "records": g(z, "properties", "numberOfRecordSets"),
                    "linkedVnets": ", ".join(sorted(links.get(lc(z["id"]), [])))}
                   for z in self.of("microsoft.network/privatednszones")]
        public = [{**self.where(z), "zone": z["name"], "records": g(z, "properties", "numberOfRecordSets")}
                  for z in self.of("microsoft.network/dnszones")]
        resolvers = [{**self.where(r), "name": r["name"], "vnet": self.label(g(r, "properties", "virtualNetwork", "id"))}
                     for r in self.of("microsoft.network/dnsresolvers")]
        custom = [(n["name"], ", ".join(n["dnsServers"])) for n in self.networks() if n["dnsServers"]]
        return {"private": private, "public": public, "resolvers": resolvers, "customDnsVnets": custom}

    def route_tables(self):
        rows = []
        for r in self.of("microsoft.network/routetables"):
            p = r.get("properties") or {}
            routes = [f"{g(x, 'properties', 'addressPrefix')} -> {g(x, 'properties', 'nextHopType')}"
                      + (f" {g(x, 'properties', 'nextHopIpAddress')}" if g(x, 'properties', 'nextHopIpAddress') else "")
                      for x in p.get("routes") or []]
            rows.append({**self.where(r), "name": r["name"], "routes": "; ".join(routes) or "none",
                         "subnets": ", ".join(self.vnet_subnet(s.get("id")) for s in p.get("subnets") or []) or "unattached",
                         "bgpPropagationDisabled": p.get("disableBgpRoutePropagation")})
        return rows

    # -- exposure ------------------------------------------------------------

    def public_ips(self):
        rows = []
        rules = self.public_ip_rules()
        for r in self.of("microsoft.network/publicipaddresses"):
            p = r.get("properties") or {}
            ipc = lc(g(p, "ipConfiguration", "id"))
            owner, owner_type = "", ""
            if not ipc:
                # NAT gateways and some other services reference the IP rather than the reverse
                refs = [x["from"] for x in self.graph.referrers(lc(r["id"])) if self.type_of(x["from"]) not in (T_SUBNET, T_VNET)]
                if refs:
                    owner, owner_type = self.label(refs[0]), self.type_of(refs[0])
            if ipc:
                top = self.resolve(ipc)
                owner_type = self.type_of(top) or "/".join(top.split("/")[6:8])
                owner = self.label(top)
                if owner_type == T_NIC:
                    vms = [self.label(e["from"]) for e in self.graph.referrers(top, T_VM)]
                    if vms:
                        owner, owner_type = ", ".join(vms), T_VM
            rows.append({**self.where(r), "name": r["name"], "ip": p.get("ipAddress") or "", "sku": g(r, "sku", "name", default=""),
                         "attachedTo": owner or "unattached", "attachedService": cat.friendly(owner_type) if owner_type else "",
                         "dns": g(p, "dnsSettings", "fqdn", default=""),
                         "nsgRules": "; ".join(
                             f"{x['rule']} ({x['priority']}, {x['protocol']} {x['ports']}): {x['effective']}"
                             + (f" - {x['closedBy']}" if x["closedBy"] else "") + (f"; remaining {x['remaining']}" if x["remaining"] else "")
                             for x in rules.get(lc(p.get("ipAddress")), []))})
        return rows

    def paas_network_access(self):
        rows = []
        for r in self.resources:
            t = lc(r["type"])
            p = r.get("properties") or {}
            if cat.category(t) not in ("Data", "Storage", "Web", "Containers", "Integration", "Security & Identity", "AI", "Monitoring & Management"):
                continue
            pna = p.get("publicNetworkAccess")
            acl = g(p, "networkAcls", "defaultAction") or g(p, "networkRuleSet", "defaultAction")
            if pna is None and acl is None:
                continue
            if lc(pna) == "disabled":
                stance = "private only"
            elif lc(acl) == "deny":
                stance = "public, restricted to selected networks"
            else:
                stance = "public, all networks"
            rows.append({**self.where(r), "name": r["name"], "service": cat.friendly(t),
                         "publicNetworkAccess": pna or "", "defaultAction": acl or "", "stance": stance,
                         "privateEndpoints": len(p.get("privateEndpointConnections") or [])})
        return sorted(rows, key=lambda x: (x["stance"] != "public, all networks", x["service"], x["name"]))

    def _nic_nsg_paths(self):
        """For each NSG: the other NSGs that also filter the same traffic. A NIC-level NSG sits behind
        the subnet-level NSG of the NIC's subnet, and the other way round."""
        also = defaultdict(set)
        for nic in self.of(T_NIC):
            np_ = name_of(g(nic, "properties", "networkSecurityGroup", "id"))
            if not np_:
                continue
            for sid in self.graph.subnets_of(lc(nic["id"])):
                sn = name_of(g(self.nodes.get(sid), "properties", "networkSecurityGroup", "id"))
                if sn and sn != np_:
                    also[np_].add(sn)
                    also[sn].add(np_)
        return also

    def open_nsg_rules(self):
        """Inbound allow rules whose source is any or the Internet, each evaluated in priority
        order against the same NSG's higher-priority deny rules (nsg.py)."""
        rows = []
        also = self._nic_nsg_paths()
        for r in self.of("microsoft.network/networksecuritygroups"):
            p = r.get("properties") or {}
            attached = [self.vnet_subnet(s.get("id")) for s in p.get("subnets") or []]
            attached += [name_of(lc(n.get("id"))) for n in p.get("networkInterfaces") or []]
            for rule, ev in nsgrules.inbound_allows_from_internet(p.get("securityRules") or []):
                ports = rule["ports_raw"]
                flagged = sorted({v for x in ports for k, v in MGMT_PORTS.items() if x == k or x == "*"})
                rows.append({**self.where(r), "nsg": r["name"], "rule": rule["name"], "priority": rule["priority"],
                             "ports": ", ".join(ports), "protocol": ", ".join(sorted(rule["protocols"])) if len(rule["protocols"]) < 5 else "*",
                             "destination": ", ".join(rule["dst"]),
                             "effective": ev["status"], "closedBy": nsgrules.names(ev["closedBy"]), "remaining": ev["remaining"],
                             "alsoFilteredBy": ", ".join(sorted(also.get(r["name"], []))),
                             "managementPorts": ", ".join(flagged), "attachedTo": ", ".join(attached) or "unattached"})
        order = {"open": 0, "partly closed": 1, "closed": 2}
        return sorted(rows, key=lambda x: (order[x["effective"]], not x["managementPorts"], x["nsg"], x["priority"] or 0))

    def public_ip_rules(self):
        """Per public IP address: the open-source allow rules that name the address as their
        destination, and what the priority-order evaluation makes of each."""
        by_ip = defaultdict(list)
        for x in self.open_nsg_rules():
            for d in x["destination"].split(", "):
                d = d.removesuffix("/32")
                if d and d not in ("*", "any", "0.0.0.0/0"):
                    by_ip[d].append(x)
        return by_ip

    def waf_policies(self):
        rows = []
        for r in self.of("microsoft.network/applicationgatewaywebapplicationfirewallpolicies",
                         "microsoft.network/frontdoorwebapplicationfirewallpolicies"):
            p = r.get("properties") or {}
            rs = [f"{m.get('ruleSetType')} {m.get('ruleSetVersion')}" for m in g(p, "managedRules", "managedRuleSets", default=[]) or []]
            rows.append({**self.where(r), "name": r["name"], "service": cat.friendly(r["type"]),
                         "mode": g(p, "policySettings", "mode", default=""), "state": g(p, "policySettings", "state", default=""),
                         "ruleSets": ", ".join(rs), "customRules": self._custom_rules(p),
                         "attachedTo": ", ".join(sorted({self.label(e["from"]) for e in self.graph.referrers(lc(r["id"]))}))})
        return rows

    @staticmethod
    def _custom_rules(p):
        c = p.get("customRules")
        return len(c.get("rules") or []) if isinstance(c, dict) else len(c or [])

    def _waf_text(self, gw):
        """WAF mode and rule set for an Application Gateway: from its policy when it has one, else the legacy configuration."""
        p = gw.get("properties") or {}
        pols = {g(p, "firewallPolicy", "id")} | {g(l, "properties", "firewallPolicy", "id") for l in p.get("httpListeners") or []}
        out = []
        for pid in sorted(x for x in pols if x):
            pol = self.nodes.get(lc(pid))
            if pol:
                ps = pol.get("properties") or {}
                rs = ", ".join(f"{m.get('ruleSetType')} {m.get('ruleSetVersion')}" for m in g(ps, "managedRules", "managedRuleSets", default=[]) or [])
                out.append(f"policy {pol['name']}: {g(ps, 'policySettings', 'mode', default='mode not reported')} mode"
                           + (f", {g(ps, 'policySettings', 'state')}" if g(ps, "policySettings", "state") else "") + (f", {rs}" if rs else ""))
            else:
                out.append(f"policy {name_of(pid)} (not in this capture)")
        legacy = p.get("webApplicationFirewallConfiguration")
        if legacy:
            out.append(f"legacy configuration: {legacy.get('firewallMode')} mode, {legacy.get('ruleSetType')} {legacy.get('ruleSetVersion')}"
                       + ("" if legacy.get("enabled") else ", disabled"))
        return "; ".join(out) or "none"

    # -- compute -------------------------------------------------------------

    def nic_facts(self, nic_id):
        n = self.nodes.get(lc(nic_id)) or {}
        ips, pips = [], []
        for c in g(n, "properties", "ipConfigurations", default=[]) or []:
            cp = c.get("properties") or {}
            if cp.get("privateIPAddress"):
                ips.append(cp["privateIPAddress"])
            pip = self.nodes.get(lc(g(cp, "publicIPAddress", "id")))
            if pip:
                pips.append(g(pip, "properties", "ipAddress") or pip["name"])
        return ips, pips

    def vms(self):
        sqlvm = {lc(g(s, "properties", "virtualMachineResourceId")): s for s in self.of("microsoft.sqlvirtualmachine/sqlvirtualmachines")}
        rows = []
        for r in self.of(T_VM):
            p = r.get("properties") or {}
            rid = lc(r["id"])
            sp = p.get("storageProfile") or {}
            img = sp.get("imageReference") or {}
            image = " ".join(filter(None, [img.get("offer"), img.get("sku")])) or ("gallery/custom image" if img.get("id") else "attached disk")
            ips, pips = [], []
            for e in self.graph.refs(rid, T_NIC):
                a, b = self.nic_facts(e["to"])
                ips += a
                pips += b
            bk = self.backup_by_src.get(rid)
            asr = self.asr_by_name.get(lc(r["name"]))
            sv = sqlvm.get(rid)
            rows.append({**self.where(r), "name": r["name"], "size": g(p, "hardwareProfile", "vmSize"),
                         "os": g(sp, "osDisk", "osType"), "image": image,
                         "osDisk": g(sp, "osDisk", "managedDisk", "storageAccountType")
                                   or g(self.nodes.get(lc(g(sp, "osDisk", "managedDisk", "id"))), "sku", "name", default=""),
                         "dataDisks": len(sp.get("dataDisks") or []),
                         "dataDiskGB": sum(int(d.get("diskSizeGB") or 0) for d in sp.get("dataDisks") or []),
                         "zone": ",".join(r.get("zones") or []), "availabilitySet": name_of(g(p, "availabilitySet", "id")),
                         "power": g(p, "extended", "instanceView", "powerState", "displayStatus", default=""),
                         "licenceType": p.get("licenseType") or "",
                         "subnet": ", ".join(self.vnet_subnet(s) for s in self.graph.subnets_of(rid)),
                         "privateIp": ", ".join(ips), "publicIp": ", ".join(pips),
                         "backup": f"{bk.get('policyName')} ({bk.get('lastBackupStatus') or 'no status'})" if bk else "not protected",
                         "asr": f"{asr.get('health')} -> {asr.get('recovery')}" if asr else "",
                         "sql": f"SQL {g(sv, 'properties', 'sqlImageSku', default='')} {g(sv, 'properties', 'sqlServerLicenseType', default='')}".strip() if sv else ""})
        return sorted(rows, key=lambda x: (x["subscription"], x["name"]))

    def scale_sets(self):
        rows = []
        for r in self.of("microsoft.compute/virtualmachinescalesets"):
            p = r.get("properties") or {}
            rows.append({**self.where(r), "name": r["name"], "sku": g(r, "sku", "name"), "capacity": g(r, "sku", "capacity"),
                         "mode": p.get("orchestrationMode"), "zones": ",".join(r.get("zones") or []),
                         "os": g(p, "virtualMachineProfile", "storageProfile", "osDisk", "osType", default=""),
                         "subnet": ", ".join(self.vnet_subnet(s) for s in self.graph.subnets_of(lc(r["id"])))})
        return rows

    def aks(self):
        rows = []
        for r in self.of("microsoft.containerservice/managedclusters"):
            p = r.get("properties") or {}
            pools = "; ".join(f"{a.get('name')} {a.get('vmSize')} x{a.get('count')} {a.get('mode', '')}"
                              + (f" (auto {a.get('minCount')}-{a.get('maxCount')})" if a.get("enableAutoScaling") else "")
                              for a in p.get("agentPoolProfiles") or [])
            rows.append({**self.where(r), "name": r["name"], "version": p.get("kubernetesVersion"),
                         "tier": g(r, "sku", "tier"), "pools": pools,
                         "network": f"{g(p, 'networkProfile', 'networkPlugin')} {g(p, 'networkProfile', 'networkPluginMode', default='')}".strip(),
                         "private": bool(g(p, "apiServerAccessProfile", "enablePrivateCluster")),
                         "ingress": self.aks_facts(r)["ingress"], "authorisedRanges": self.aks_facts(r)["authorisedRanges"],
                         "networkPolicy": self.aks_facts(r)["policy"],
                         "subnet": ", ".join(self.vnet_subnet(s) for s in self.graph.subnets_of(lc(r["id"])))})
        return rows

    def avd(self):
        return [{**self.where(r), "name": r["name"], "type": g(r, "properties", "hostPoolType"),
                 "loadBalancing": g(r, "properties", "loadBalancerType"), "maxSessions": g(r, "properties", "maxSessionLimit")}
                for r in self.of("microsoft.desktopvirtualization/hostpools")]

    # -- web and containers --------------------------------------------------

    def app_service(self):
        plans, apps = [], []
        for r in self.of("microsoft.web/serverfarms"):
            p = r.get("properties") or {}
            plans.append({**self.where(r), "name": r["name"], "sku": g(r, "sku", "name"), "tier": g(r, "sku", "tier"),
                          "workers": g(r, "sku", "capacity"), "os": "Linux" if p.get("reserved") else "Windows",
                          "zoneRedundant": p.get("zoneRedundant"), "apps": p.get("numberOfSites")})
        for r in self.of("microsoft.web/sites"):
            p = r.get("properties") or {}
            hosts = [h for h in p.get("hostNames") or [] if not lc(h).endswith(".azurewebsites.net")]
            apps.append({**self.where(r), "name": r["name"], "kind": r.get("kind"), "plan": name_of(p.get("serverFarmId")),
                         "state": p.get("state"), "httpsOnly": p.get("httpsOnly"),
                         "publicNetworkAccess": p.get("publicNetworkAccess") or "",
                         "vnetIntegration": self.vnet_subnet(p["virtualNetworkSubnetId"]) if p.get("virtualNetworkSubnetId") else "",
                         "customDomains": ", ".join(hosts),
                         "privateEndpoints": len(p.get("privateEndpointConnections") or [])})
        return {"plans": plans, "apps": sorted(apps, key=lambda x: (x["subscription"], x["name"]))}

    def container_apps(self):
        envs = [{**self.where(r), "name": r["name"],
                 "internal": g(r, "properties", "vnetConfiguration", "internal"),
                 "subnet": self.vnet_subnet(g(r, "properties", "vnetConfiguration", "infrastructureSubnetId"))
                 if g(r, "properties", "vnetConfiguration", "infrastructureSubnetId") else "",
                 "profiles": ", ".join(w.get("workloadProfileType", "") for w in g(r, "properties", "workloadProfiles", default=[]) or [])}
                for r in self.of("microsoft.app/managedenvironments")]
        apps = [{**self.where(r), "name": r["name"], "environment": name_of(g(r, "properties", "managedEnvironmentId") or g(r, "properties", "environmentId")),
                 "ingress": ("external" if g(r, "properties", "configuration", "ingress", "external") else "internal")
                 if g(r, "properties", "configuration", "ingress") else "none",
                 "targetPort": g(r, "properties", "configuration", "ingress", "targetPort", default="")}
                for r in self.of("microsoft.app/containerapps")]
        return {"environments": envs, "apps": apps}

    # -- data ----------------------------------------------------------------

    def data(self):
        rows = []
        for r in self.of("microsoft.sql/servers"):
            p = r.get("properties") or {}
            dbs = [d for d in self.of("microsoft.sql/servers/databases")
                   if lc(d["id"]).startswith(lc(r["id"]) + "/") and lc(d["name"]) != "master"]
            rows.append({**self.where(r), "name": r["name"], "service": "Azure SQL Server",
                         "detail": f"v{p.get('version')}; public access {p.get('publicNetworkAccess')}; min TLS {p.get('minimalTlsVersion')}; "
                                   f"Entra admin {'yes' if g(p, 'administrators', 'login') else 'no'}; {len(dbs)} database(s)"})
        for r in self.of("microsoft.sql/servers/databases"):
            if lc(r["name"]) == "master":
                continue
            p = r.get("properties") or {}
            rows.append({**self.where(r), "name": f"{lc(r['id']).split('/servers/')[1].split('/')[0]}/{r['name']}", "service": "Azure SQL Database",
                         "detail": f"{g(r, 'sku', 'name')} {g(r, 'sku', 'tier', default='')} cap {g(r, 'sku', 'capacity', default='')}; "
                                   f"max {gb(p.get('maxSizeBytes'))} GB; zone redundant {p.get('zoneRedundant')}; "
                                   f"backup redundancy {p.get('requestedBackupStorageRedundancy') or p.get('currentBackupStorageRedundancy')}"
                                   + (f"; pool {name_of(p.get('elasticPoolId'))}" if p.get("elasticPoolId") else "")})
        for r in self.of("microsoft.sql/managedinstances"):
            p = r.get("properties") or {}
            rows.append({**self.where(r), "name": r["name"], "service": "SQL Managed Instance",
                         "detail": f"{g(r, 'sku', 'name')}; {p.get('vCores')} vCores; {p.get('storageSizeInGB')} GB; "
                                   f"subnet {self.vnet_subnet(p.get('subnetId')) if p.get('subnetId') else '?'}"})
        for r in self.of("microsoft.documentdb/databaseaccounts"):
            p = r.get("properties") or {}
            caps = [c.get("name") for c in p.get("capabilities") or []]
            api = next((c.replace("Enable", "") for c in caps if c in ("EnableMongo", "EnableCassandra", "EnableTable", "EnableGremlin")), "NoSQL")
            rows.append({**self.where(r), "name": r["name"], "service": "Cosmos DB",
                         "detail": f"{api} API; {'serverless' if 'EnableServerless' in caps else 'provisioned'}; "
                                   f"consistency {g(p, 'consistencyPolicy', 'defaultConsistencyLevel')}; "
                                   f"regions {', '.join(l.get('locationName', '') for l in p.get('locations') or [])}; "
                                   f"multi-write {p.get('enableMultipleWriteLocations')}; backup {g(p, 'backupPolicy', 'type')}"})
        for r in self.of("microsoft.documentdb/mongoclusters"):
            p = r.get("properties") or {}
            rows.append({**self.where(r), "name": r["name"], "service": "Cosmos DB for MongoDB (vCore)",
                         "detail": f"v{p.get('serverVersion', '')}; tier {g(p, 'compute', 'tier') or g(p, 'nodeGroupSpecs', default=[{}])[0].get('sku', '')}; "
                                   f"{g(p, 'storage', 'sizeGb') or g(p, 'nodeGroupSpecs', default=[{}])[0].get('diskSizeGB', '')} GB; "
                                   f"HA {g(p, 'highAvailability', 'targetMode', default='n/a')}; public access {p.get('publicNetworkAccess', 'n/a')}"})
        for t in ("microsoft.dbforpostgresql/flexibleservers", "microsoft.dbformysql/flexibleservers"):
            for r in self.of(t):
                p = r.get("properties") or {}
                rows.append({**self.where(r), "name": r["name"], "service": cat.friendly(t),
                             "detail": f"v{p.get('version')}; {g(r, 'sku', 'name')} {g(r, 'sku', 'tier', default='')}; "
                                       f"{g(p, 'storage', 'storageSizeGB')} GB; HA {g(p, 'highAvailability', 'mode', default='Disabled')}; "
                                       f"network {'VNet-injected' if g(p, 'network', 'delegatedSubnetResourceId') else g(p, 'network', 'publicNetworkAccess', default='public')}"})
        for t in ("microsoft.cache/redis", "microsoft.cache/redisenterprise"):
            for r in self.of(t):
                p = r.get("properties") or {}
                sku = g(p, "sku") or r.get("sku") or {}
                rows.append({**self.where(r), "name": r["name"], "service": cat.friendly(t),
                             "detail": f"{sku.get('name')} {sku.get('family', '')}{sku.get('capacity', '')}; v{p.get('redisVersion', '')}"})
        for r in self.of("microsoft.storage/storageaccounts"):
            p = r.get("properties") or {}
            rows.append({**self.where(r), "name": r["name"], "service": "Storage Account",
                         "detail": f"{r.get('kind')} {g(r, 'sku', 'name')}; tier {p.get('accessTier', 'n/a')}; "
                                   f"HNS {bool(p.get('isHnsEnabled'))}; min TLS {p.get('minimumTlsVersion')}; "
                                   f"anonymous blob {p.get('allowBlobPublicAccess')}; shared key {p.get('allowSharedKeyAccess', True)}"})
        done = {"microsoft.sql/servers", "microsoft.sql/servers/databases", "microsoft.sql/managedinstances",
                "microsoft.documentdb/databaseaccounts", "microsoft.documentdb/mongoclusters", "microsoft.dbforpostgresql/flexibleservers",
                "microsoft.dbformysql/flexibleservers", "microsoft.cache/redis", "microsoft.cache/redisenterprise",
                "microsoft.storage/storageaccounts"}
        for r in self.resources:
            t = lc(r["type"])
            if cat.category(t) in ("Data", "Storage") and t not in done and t not in cat.SUPPORTING:
                rows.append({**self.where(r), "name": r["name"], "service": cat.friendly(t), "detail": self.sku_of(r)})
        return rows

    def other_services(self):
        """Integration, AI and anything uncatalogued, so nothing silently disappears."""
        rows = []
        for r in self.resources:
            t = lc(r["type"])
            if cat.category(t) in ("Integration", "AI", "Developer", "Other") and t not in cat.SUPPORTING:
                rows.append({**self.where(r), "name": r["name"], "service": cat.friendly(t), "sku": self.sku_of(r)})
        return sorted(rows, key=lambda x: (x["service"], x["name"]))

    # -- identity and access -------------------------------------------------

    def scope_label(self, scope):
        s = lc(scope)
        if "/providers/microsoft.management/managementgroups/" in s:
            return f"Management group {name_of(s)}"
        parts = s.split("/")
        if s.count("/") == 2:
            return f"Subscription {self.sub(parts[2])}"
        if "/resourcegroups/" in s and "/providers/" not in s:
            return f"Resource group {parts[4]}"
        if s == "/":
            return "Root"
        return f"Resource {name_of(s)} ({cat.friendly(self.type_of(s) or '/'.join(parts[6:8]))})"

    def access(self):
        rows = []
        for a in self.role_assignments:
            pr = self.principals.get(a.get("principalId")) or {}
            rd = self.role_defs.get(lc(a.get("roleDefinitionGuid"))) or {}
            a["roleName"] = a.get("roleName") or rd.get("roleName") or ""
            name = pr.get("displayName")
            if not name and lc(a.get("principalType")).startswith("foreign"):
                name = f"Partner tenant group ({a.get('principalId', '')[:8]}; CSP admin agents or GDAP)"
            rows.append({"scope": self.scope_label(a.get("scope")), "role": a["roleName"] or "(unresolved)",
                         "roleType": a.get("roleType") or rd.get("roleType") or "",
                         "principal": name or f"{a.get('principalId')} (not found in directory; possibly deleted)",
                         "principalType": a.get("principalType") or pr.get("type"),
                         "upn": pr.get("upn") or "", "privileged": lc(a.get("roleName")) in PRIVILEGED,
                         "_level": 0 if "management group" in self.scope_label(a.get("scope")).lower()
                         else 1 if self.scope_label(a.get("scope")).startswith("Subscription") else 2})
        rows.sort(key=lambda x: (not x["privileged"], x["_level"], x["role"], x["principal"] or ""))
        seen, out = set(), []
        for r in rows:
            k = (r["scope"], r["role"], r["principal"])
            if k not in seen:
                seen.add(k)
                out.append(r)
        return out

    def key_vaults(self):
        return [{**self.where(r), "name": r["name"], "rbac": g(r, "properties", "enableRbacAuthorization"),
                 "softDelete": g(r, "properties", "enableSoftDelete"), "purgeProtection": g(r, "properties", "enablePurgeProtection"),
                 "publicNetworkAccess": g(r, "properties", "publicNetworkAccess", default=""),
                 "defaultAction": g(r, "properties", "networkAcls", "defaultAction", default="")}
                for r in self.of("microsoft.keyvault/vaults")]

    def identities(self):
        system = sum(1 for r in self.resources if "systemassigned" in lc(g(r, "identity", "type")))
        user = len(self.of("microsoft.managedidentity/userassignedidentities"))
        return {"systemAssigned": system, "userAssigned": user}

    # -- governance ----------------------------------------------------------

    def policy(self):
        comp = {c["assignmentId"]: c for c in self.policy_compliance}
        rows = []
        for a in self.policy_assignments:
            c = comp.get(lc(a["id"])) or {}
            rows.append({"assignment": a.get("displayName") or a.get("name"), "scope": self.scope_label(a.get("scope")),
                         "enforcement": a.get("enforcementMode"),
                         "builtIn": lc(a.get("definitionId")).startswith("/providers/microsoft.authorization/"),
                         "initiative": "policysetdefinitions" in lc(a.get("definitionId")),
                         "nonCompliant": c.get("nonCompliant"), "evaluated": c.get("resources")})
        seen, out = set(), []
        for r in sorted(rows, key=lambda x: (-(x["nonCompliant"] or 0), x["assignment"])):
            k = (r["assignment"], r["scope"])
            if k not in seen:
                seen.add(k)
                out.append(r)
        return out

    def tags(self):
        primary = [r for r in self.resources if lc(r["type"]) not in cat.SUPPORTING]
        keys = Counter(k for r in primary for k in (r.get("tags") or {}))
        n = len(primary) or 1
        untagged = sum(1 for r in primary if not r.get("tags"))
        rg_keys = Counter(k for c in self.rgs for k in (c.get("tags") or {}))
        return {"resources": len(primary), "untagged": untagged,
                "keys": [(k, v, round(100 * v / n)) for k, v in keys.most_common(15)],
                "rgKeys": [(k, v, round(100 * v / (len(self.rgs) or 1))) for k, v in rg_keys.most_common(10)]}

    # -- resilience ----------------------------------------------------------

    def resilience(self):
        vms = self.vms()
        unprotected = [v["name"] for v in vms if v["backup"] == "not protected"]
        vaults = []
        for r in self.of("microsoft.recoveryservices/vaults"):
            p = r.get("properties") or {}
            items = [b for b in self.backup_items if b.get("vaultId") == lc(r["id"])]
            vaults.append({**self.where(r), "name": r["name"], "service": "Recovery Services Vault",
                           "redundancy": g(p, "redundancySettings", "standardTierStorageRedundancy", default=""),
                           "crossRegionRestore": g(p, "redundancySettings", "crossRegionRestore", default=""),
                           "softDelete": g(p, "securitySettings", "softDeleteSettings", "softDeleteState", default=""),
                           "immutability": g(p, "securitySettings", "immutabilitySettings", "state", default=""),
                           "items": ", ".join(f"{k} x{v}" for k, v in Counter(b.get("workloadType") for b in items).items()) or "none"})
        for r in self.of("microsoft.dataprotection/backupvaults"):
            p = r.get("properties") or {}
            vaults.append({**self.where(r), "name": r["name"], "service": "Backup Vault",
                           "redundancy": ", ".join(s.get("type", "") + " " + s.get("datastoreType", "") for s in p.get("storageSettings") or []),
                           "crossRegionRestore": "", "softDelete": g(p, "securitySettings", "softDeleteSettings", "state", default=""),
                           "immutability": g(p, "securitySettings", "immutabilitySettings", "state", default=""), "items": "see portal"})
        storage = Counter(g(r, "sku", "name") for r in self.of("microsoft.storage/storageaccounts"))
        zonal_vms = sum(1 for v in vms if v["zone"])
        return {"vmCount": len(vms), "vmProtected": len(vms) - len(unprotected), "unprotectedVms": unprotected,
                "vaults": vaults, "asr": self.asr_items, "storageRedundancy": storage, "zonalVms": zonal_vms,
                "avSetVms": sum(1 for v in vms if v["availabilitySet"]), "regions": self.regions()}

    # -- monitoring and security ---------------------------------------------

    def monitoring(self):
        laws = []
        for r in self.of("microsoft.operationalinsights/workspaces"):
            p = r.get("properties") or {}
            laws.append({**self.where(r), "name": r["name"], "sku": g(p, "sku", "name"), "retentionDays": p.get("retentionInDays"),
                         "dailyCapGB": g(p, "workspaceCapping", "dailyQuotaGb"),
                         "referencedBy": len({e["from"] for e in self.graph.referrers(lc(r["id"]))})})
        ai = [{**self.where(r), "name": r["name"], "workspace": name_of(g(r, "properties", "WorkspaceResourceId")) or "classic (no workspace)"}
              for r in self.of("microsoft.insights/components")]
        alerts = Counter(cat.friendly(r["type"]) for r in self.resources if lc(r["type"]) in (
            "microsoft.insights/metricalerts", "microsoft.insights/scheduledqueryrules", "microsoft.insights/activitylogalerts",
            "microsoft.alertsmanagement/smartdetectoralertrules", "microsoft.insights/actiongroups"))
        plans = defaultdict(list)
        for d in self.defender:
            if lc(d.get("tier")) == "standard":
                plans[self.sub(d["subscriptionId"])].append(d["plan"] + (f" ({d['subPlan']})" if d.get("subPlan") else ""))
        scores = {self.sub(s["subscriptionId"]): round(100 * (s.get("percentage") or 0)) for s in self.scores}
        return {"workspaces": laws, "appInsights": ai, "alerts": alerts, "defenderPlans": dict(plans),
                "secureScores": scores, "dcrCount": len(self.of("microsoft.insights/datacollectionrules"))}

    def advisor(self):
        by = Counter((a.get("category"), a.get("impact")) for a in self.advisor_recs)
        top = Counter((a.get("category"), a.get("impact"), a.get("problem")) for a in self.advisor_recs if a.get("impact") == "High")
        return {"counts": by, "high": [{"category": c, "problem": p, "resources": n} for (c, _, p), n in top.most_common(20)]}

    def security(self):
        sev = Counter(f.get("severity") for f in self.findings)
        top = Counter((f.get("severity"), f.get("finding")) for f in self.findings if f.get("severity") in ("High", "Medium"))
        return {"severity": sev, "top": [{"severity": s, "finding": f, "resources": n} for (s, f), n in top.most_common(20)]}

    # -- cost ----------------------------------------------------------------

    def cost_by_sub(self):
        out = defaultdict(float)
        for c in self.cost_rows:
            out[c["subscriptionId"]] += c["cost"]
        return {k: round(v, 2) for k, v in out.items()}

    def costs(self):
        cur = next((c.get("currency") for c in self.cost_rows if c.get("currency")), "")
        rg, svc = defaultdict(float), defaultdict(float)
        for c in self.cost_rows:
            rg[(self.sub(c["subscriptionId"]), c["resourceGroup"])] += c["cost"]
            svc[c["service"]] += c["cost"]
        total = sum(c["cost"] for c in self.cost_rows)
        return {"currency": cur, "days": self.manifest.get("costDays"), "total": round(total, 2),
                "bySub": {self.sub(k): v for k, v in self.cost_by_sub().items()},
                "topRgs": sorted(((k, round(v, 2)) for k, v in rg.items()), key=lambda x: -x[1])[:15],
                "topServices": sorted(((k, round(v, 2)) for k, v in svc.items()), key=lambda x: -x[1])[:15],
                "unavailable": self._cost_missing()}

    def _cost_missing(self):
        """Subscriptions where Cost Management refused, named once even if both cost queries failed."""
        out = []
        for key in (self.manifest.get("errors") or {}):
            kind, _, sub = key.partition(":")
            if kind in ("cost", "cost_months") and sub and sub not in out:
                out.append(sub)
        return out

    MONTH_NAMES = ("January", "February", "March", "April", "May", "June",
                   "July", "August", "September", "October", "November", "December")

    @staticmethod
    def _previous_month(month):
        y, m = int(month[:4]), int(month[5:7])
        return f"{y - 1}-12" if m == 1 else f"{y}-{m - 1:02d}"

    def captured_date(self):
        try:
            return dt.date.fromisoformat(str(self.manifest.get("capturedAt") or "")[:10])
        except ValueError:
            return None

    def spend(self):
        """Actual cost per calendar month, newest first, from the monthly Cost Management query.
        The capture month is part-month: it is marked incomplete and its change is not stated.
        Change is only given where both months are whole, so a part-month never distorts it."""
        cur = next((r.get("currency") for r in self.cost_month_rows if r.get("currency")), "")
        cap = self.captured_date()
        by = defaultdict(lambda: defaultdict(float))
        for r in self.cost_month_rows:
            if r.get("month"):
                by[r["month"]][r["subscriptionId"]] += r["cost"]
        rows = []
        for month in sorted(by, reverse=True):
            y, m = int(month[:4]), int(month[5:7])
            if cap and (y, m) > (cap.year, cap.month):
                continue                                  # a month that had not started at capture
            days_in = calendar.monthrange(y, m)[1]
            days = min(days_in, cap.day) if cap and (y, m) == (cap.year, cap.month) else days_in
            cost = round(sum(by[month].values()), 2)
            rows.append({"month": month, "label": f"{self.MONTH_NAMES[m - 1]} {y}", "cost": cost,
                         "days": days, "daysInMonth": days_in, "complete": days >= days_in,
                         "perDay": round(cost / days, 2) if days else None,
                         "bySub": {self.sub(k): round(v, 2) for k, v in sorted(by[month].items(), key=lambda x: -x[1])}})
        index = {r["month"]: r for r in rows}
        for r in rows:
            prev = index.get(self._previous_month(r["month"]))
            r["previous"] = prev["label"] if prev else ""
            r["change"] = (round((r["cost"] - prev["cost"]) / prev["cost"] * 100, 1)
                           if prev and prev["complete"] and r["complete"] and prev["cost"] else None)
        last_full = next((r for r in rows if r["complete"]), None)
        mtd = rows[0] if rows and not rows[0]["complete"] else None
        return {"currency": cur, "months": rows, "lastFull": last_full, "mtd": mtd,
                "unavailable": self._cost_missing()}

    # -- dependencies --------------------------------------------------------

    PLUMBING = {T_NIC, T_SUBNET, T_VNET, "microsoft.compute/disks", "microsoft.network/networksecuritygroups",
                "microsoft.network/publicipaddresses", "microsoft.network/routetables", "microsoft.compute/virtualmachines/extensions",
                "microsoft.network/privatednszones/virtualnetworklinks", "microsoft.network/privatednszones",
                "microsoft.compute/availabilitysets", "microsoft.compute/images", "microsoft.compute/galleries",
                "microsoft.insights/actiongroups", "microsoft.network/networkwatchers"}

    def dependencies(self):
        """Resource-to-resource references that say something about how the estate fits
        together. Network plumbing is shown elsewhere, so it is left out here."""
        rows, seen = [], set()
        for e in self.graph.edges:
            ft, tt = self.type_of(e["from"]), self.type_of(e["to"])
            if e["external"] or ft in self.PLUMBING or tt in self.PLUMBING or ft in cat.SUPPORTING:
                continue
            via = e["via"].replace("properties.", "", 1)
            key = frozenset((e["from"], e["to"]))     # A->B and B->A are one relationship
            if key in seen:
                continue
            seen.add(key)
            rows.append({"from": self.label(e["from"]), "fromService": cat.friendly(ft), "to": self.label(e["to"]),
                         "toService": cat.friendly(tt), "via": via,
                         "_rank": 1 if ft.startswith("microsoft.desktopvirtualization/") and tt.startswith("microsoft.desktopvirtualization/") else 0})
        # VNet integration and subnet placement for PaaS, which the filter above removes.
        for r in self.of("microsoft.web/sites"):
            sid = g(r, "properties", "virtualNetworkSubnetId")
            if sid:
                rows.append({"from": r["name"], "fromService": "App Service", "to": self.vnet_subnet(sid),
                             "toService": "Subnet", "via": "virtualNetworkSubnetId (VNet integration)", "_rank": 0})
        return sorted(rows, key=lambda x: (x["_rank"], x["fromService"], x["from"], x["toService"]))

    # Sources whose out-of-scope references are history or platform internals, not dependencies.
    HISTORY = {"microsoft.compute/galleries/images/versions", "microsoft.compute/snapshots", "microsoft.compute/disks",
               "microsoft.compute/restorepointcollections", "microsoft.compute/images",
               "microsoft.portal/dashboards", "microsoft.portalservices/dashboards"}

    def outside_scope(self):
        """References to resources outside the snapshot that may matter: another subscription or
        tenant the customer owns. Platform-managed internals (Bastion and gateway instances,
        Container Apps infrastructure) and references to deleted resources are left out."""
        out = Counter()
        in_scope_subs = set(self.sub_names)
        for e in self.graph.external_refs():
            parts = e["to"].split("/")
            src = self.nodes.get(e["from"]) or {}
            st, tt = self.type_of(e["from"]), "/".join(parts[6:8])
            if st in self.HISTORY or lc(src.get("resourceGroup")).startswith(("me_", "mc_")) or src.get("managedBy"):
                continue
            if tt == "microsoft.compute/virtualmachinescalesets" and st in (T_VNET, T_SUBNET):
                continue                          # Bastion / VPN gateway instances in Microsoft-owned subscriptions
            if st == "microsoft.app/managedenvironments" or (len(parts) > 2 and parts[2] in in_scope_subs and st != T_VNET):
                continue                          # Container Apps internals; deleted resources in in-scope subscriptions
            out[(self.label(e["from"]), cat.friendly(self.type_of(e["from"])), "/".join(parts[6:8]),
                 parts[-1], self.sub(parts[2]), parts[2], self.via_text(e["via"]))] += 1
        return [{"from": a, "fromService": b, "toType": c, "to": d, "subscription": s, "subscriptionId": sid, "via": v}
                for (a, b, c, d, s, sid, v) in sorted(out)]

    @staticmethod
    def via_text(via):
        """The property that carries a reference, in words where the property name is well known."""
        v = via.replace("properties.", "", 1).replace("[]", "")
        for key, text in (("remotevirtualnetwork", "VNet peering"), ("privatelinkserviceid", "private endpoint target"),
                          ("workspaceresourceid", "Log Analytics workspace"), ("serverfarmid", "App Service plan"),
                          ("subnet", "subnet"), ("storageaccount", "storage account"), ("keyvault", "Key Vault")):
            if key in lc(v):
                return text
        return v
