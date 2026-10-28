"""Emitter - design.json for AzureDiagrammer: the current-state network diagram.
Follows the diagrammer's modelling rules: private endpoints live in subnets and
front PaaS services that sit outside the VNet; IaaS and VNet-injected services
sit in their subnet; PaaS with VNet integration stays in its resource group with
an edge to the subnet. The model decides what connects; the diagrammer lays out."""
import datetime as dt
import re
from collections import Counter

from . import catalogue as cat
from .model import T_VM, g, lc, name_of

CIDR = re.compile(r"^\d{1,3}(\.\d{1,3}){3}/\d{1,2}$")

# Types drawn inside the subnet they are attached to (IaaS or VNet-injected).
IN_SUBNET = {
    "microsoft.compute/virtualmachines", "microsoft.compute/virtualmachinescalesets",
    "microsoft.network/applicationgateways", "microsoft.network/azurefirewalls", "microsoft.network/bastionhosts",
    "microsoft.network/virtualnetworkgateways", "microsoft.network/loadbalancers",
    "microsoft.containerservice/managedclusters", "microsoft.apimanagement/service", "microsoft.sql/managedinstances",
    "microsoft.app/managedenvironments", "microsoft.containerinstance/containergroups",
    "microsoft.dbforpostgresql/flexibleservers", "microsoft.dbformysql/flexibleservers", "microsoft.cache/redis",
}
REGION_NAMES = {"uksouth": "UK South", "ukwest": "UK West", "northeurope": "North Europe", "westeurope": "West Europe",
                "eastus": "East US", "eastus2": "East US 2", "centralus": "Central US", "westus2": "West US 2"}
MAX_INSTANCES = 8


def icon_for(rtype, kind=""):
    if lc(rtype) == "microsoft.web/sites" and "functionapp" in lc(kind):
        return "function"
    return cat.icon(rtype)


class _Ids:
    def __init__(self):
        self.used, self.by_key = set(), {}

    def get(self, key, prefix, name):
        if key in self.by_key:
            return self.by_key[key]
        base = (prefix + "_" + re.sub(r"[^a-z0-9]+", "_", lc(name)).strip("_"))[:36].rstrip("_")
        i, n = base, 2
        while i in self.used:
            i = f"{base[:33]}_{n}"
            n += 1
        self.used.add(i)
        self.by_key[key] = i
        return i


def _zone(name):
    n = lc(name)
    if any(k in n for k in ("hub", "connectivity", "platform", "management", "identity", "shared", "landing")):
        return "landing_zone"
    if "prod" in n and "pre" not in n and "non" not in n:
        return "production"
    return "operations"


def build(e):
    ids = _Ids()
    nodes, edges, placed = [], [], {}
    node_by_id = {}
    omitted = Counter()
    edge_keys = set()

    def add(n):
        if n["id"] not in node_by_id:
            node_by_id[n["id"]] = n
            nodes.append(n)
        return n["id"]

    def edge(a, b, flow, label=None, **kw):
        k = (a, b, flow) if not kw.get("bidirectional") else (frozenset((a, b)), flow)
        if a == b or k in edge_keys:
            return
        edge_keys.add(k)
        d = {"from": a, "to": b, "flow": flow}
        if label:
            d["label"] = label
        d.update(kw)
        edges.append(d)

    def sub_node(sid, external=False):
        name = e.sub(sid)
        if external:
            name = f"Outside scope: {name if name != sid else sid[:8]}"
        n = {"id": ids.get(("sub", sid), "sub", name), "kind": "subscription", "name": name,
             "zone": "operations" if external else _zone(name)}
        return add(n)

    def rg_node(sid, rg, external=False):
        p = sub_node(sid, external)
        return add({"id": ids.get(("rg", sid, lc(rg)), "rg", rg), "kind": "resource_group", "name": rg, "parent": p})

    def service_node(rid, parent, name=None, detail=None):
        r = e.nodes.get(lc(rid)) or {}
        ic = icon_for(r.get("type"), r.get("kind"))
        if not ic:
            omitted[cat.friendly(r.get("type"))] += 1
            return None
        n = {"id": ids.get(("svc", lc(rid)), "svc", r.get("name") or name_of(rid)), "kind": "service",
             "name": name or r.get("name") or name_of(rid), "parent": parent, "icon": ic}
        det = detail if detail is not None else " ".join(filter(None, [cat.friendly(r.get("type")), e.sku_of(r) if r else ""]))
        if det:
            n["detail"] = det
        placed[lc(rid)] = n["id"]
        return add(n)

    nets = e.networks()
    subnet_ids = {}

    # VNets, subnets and what sits in them
    for v in nets:
        vr = e.nodes[v["_id"]]
        rg = rg_node(vr["subscriptionId"], vr["resourceGroup"])
        vn = {"id": ids.get(("vnet", v["_id"]), "vnet", v["name"]), "kind": "vnet", "name": v["name"], "parent": rg}
        prefixes = [a for a in v["addressSpace"] if CIDR.match(a)]
        if prefixes:
            vn["cidr"] = prefixes[0]
            if len(prefixes) > 1:
                vn["detail"] = "also " + ", ".join(prefixes[1:])
        add(vn)
        for s in v["subnets"]:
            sn = {"id": ids.get(("snet", s["_id"]), "snet", s["name"]), "kind": "subnet", "name": s["name"], "parent": vn["id"]}
            first = (s["prefix"] or "").split(",")[0].strip()
            if CIDR.match(first):
                sn["cidr"] = first
            bits = [f"NSG {s['nsg']}" if s["nsg"] else "", f"UDR {s['routeTable']}" if s["routeTable"] else "",
                    s["delegation"]]
            if any(bits):
                sn["detail"] = "; ".join(b for b in bits if b)
            subnet_ids[s["_id"]] = add(sn)

            occ = [(rid, t) for rid, t in s["_occupants"] if t in IN_SUBNET and lc(rid) not in placed]
            vms = [rid for rid, t in occ if t == T_VM]
            if len(vms) >= 2:
                rows = sorted(vms, key=lambda x: e.label(x))
                vm_id = ids.get(("vmgroup", s["_id"]), "vms", s["name"])
                n = {"id": vm_id, "kind": "service", "name": "Virtual Machines", "parent": sn["id"], "icon": "vm",
                     "detail": f"{len(vms)} VMs" + (f", first {MAX_INSTANCES} shown" if len(vms) > MAX_INSTANCES else ""),
                     "instances": [{"name": e.label(x), "detail": g(e.nodes[x], "properties", "hardwareProfile", "vmSize", default="")}
                                   for x in rows[:MAX_INSTANCES]]}
                add(n)
                for x in vms:
                    placed[lc(x)] = vm_id
                occ = [(rid, t) for rid, t in occ if t != T_VM]
            for rid, t in occ:
                service_node(rid, sn["id"])

    # Private endpoints: in their subnet, fronting a PaaS service drawn in its resource group
    for pe in e.private_endpoints():
        sn = subnet_ids.get(pe["_subnet"])
        tid = pe["_target"]
        if not sn or not tid:
            continue
        if tid in placed and node_by_id[placed[tid]].get("parent") in subnet_ids.values():
            omitted["private endpoint to a VNet-injected service"] += 1
            continue
        if tid not in placed:
            if tid in e.nodes:
                t = e.nodes[tid]
                if not service_node(tid, rg_node(t["subscriptionId"], t["resourceGroup"])):
                    omitted[f"private endpoint target ({pe['targetService']})"] += 1
                    continue
            else:
                parts = tid.split("/")
                ic = cat.icon("/".join(parts[6:8])) if len(parts) > 8 else None
                if not ic or len(parts) < 9:
                    omitted["private endpoint to an unidentified external target"] += 1
                    continue
                rg = rg_node(parts[2], parts[4], external=True)
                placed[tid] = add({"id": ids.get(("svc", tid), "svc", parts[8]), "kind": "service", "name": parts[8],
                                   "detail": cat.friendly("/".join(parts[6:8])) + " (outside scope)", "parent": rg, "icon": ic})
        group = f" ({pe['groupIds']})" if pe["groupIds"] else ""
        add({"id": ids.get(("pe", lc(pe["name"]), tid), "pe", pe["name"]), "kind": "private_endpoint",
             "name": f"PE: {e.label(tid)}{group}", "parent": sn, "fronts": placed[tid]})

    # Peering, including VNets outside the snapshot
    vnet_node = {v["_id"]: ids.by_key[("vnet", v["_id"])] for v in nets}
    for v in nets:
        for p in v["peerings"]:
            rid = p["remoteId"]
            if rid not in vnet_node:
                parts = rid.split("/")
                if len(parts) < 9:
                    continue
                rg = rg_node(parts[2], parts[4], external=True)
                n = {"id": ids.get(("vnet", rid), "vnet", parts[8]), "kind": "vnet", "name": parts[8], "parent": rg}
                first = (p["remotePrefixes"] or "").split(",")[0].strip()
                if CIDR.match(first):
                    n["cidr"] = first
                vnet_node[rid] = add(n)
            label = "VNet peering"
            if p["allowGatewayTransit"] or p["useRemoteGateways"]:
                label += ", gateway transit"
            if lc(p["state"]) != "connected":
                label += f" ({p['state']})"
            edge(vnet_node[v["_id"]], vnet_node[rid], "peering", label, bidirectional=True)

    # Hybrid connectivity
    for c in e.of("microsoft.network/connections"):
        p = c.get("properties") or {}
        gw = placed.get(lc(g(p, "virtualNetworkGateway1", "id")))
        if not gw:
            continue
        lng = e.nodes.get(lc(g(p, "localNetworkGateway2", "id")))
        circuit = e.nodes.get(lc(g(p, "peer", "id")))
        if lng:
            prefixes = ", ".join(g(lng, "properties", "localNetworkAddressSpace", "addressPrefixes", default=[]) or [])
            a = add({"id": ids.get(("onprem", lc(lng["id"])), "onprem", lng["name"]), "kind": "actor",
                     "name": f"On-premises: {lng['name']}", "icon": "onprem", "zone": "actors",
                     **({"detail": prefixes} if prefixes else {})})
            edge(gw, a, "egress", f"{p.get('connectionType', 'IPsec')} {p.get('connectionProtocol') or ''}".strip())
        elif circuit:
            cn = service_node(circuit["id"], rg_node(circuit["subscriptionId"], circuit["resourceGroup"]))
            if cn:
                edge(gw, cn, "sync", "ExpressRoute")

    # Internet ingress through public IPs, and Front Door / Traffic Manager
    internet = None

    def internet_node():
        nonlocal internet
        if not internet:
            internet = add({"id": "act_internet", "kind": "actor", "name": "Internet", "icon": "internet", "zone": "actors"})
        return internet

    for r in e.of("microsoft.network/publicipaddresses"):
        ipc = lc(g(r, "properties", "ipConfiguration", "id"))
        if not ipc:
            continue
        owner = e.resolve(ipc)
        if e.type_of(owner) == "microsoft.network/networkinterfaces":
            vms = [x["from"] for x in e.graph.referrers(owner, T_VM)]
            owner = vms[0] if vms else owner
        tgt = placed.get(owner)
        if not tgt or e.type_of(owner) == "microsoft.network/virtualnetworkgateways":
            continue
        ip = g(r, "properties", "ipAddress")
        edge(internet_node(), tgt, "ingress", f"Public IP {ip}" if ip else "Public IP")

    for r in e.of("microsoft.cdn/profiles", "microsoft.network/frontdoors", "microsoft.network/trafficmanagerprofiles"):
        n = service_node(r["id"], rg_node(r["subscriptionId"], r["resourceGroup"]))
        if n:
            edge(internet_node(), n, "ingress", "HTTPS")

    # PaaS with VNet integration: stays outside the VNet, with an edge to its subnet
    for r in e.of("microsoft.web/sites"):
        sid = lc(g(r, "properties", "virtualNetworkSubnetId"))
        if sid in subnet_ids:
            n = placed.get(lc(r["id"])) or service_node(r["id"], rg_node(r["subscriptionId"], r["resourceGroup"]))
            if n:
                edge(n, subnet_ids[sid], "sync", "VNet integration (outbound)")

    # NAT gateways: subnet egress
    for r in e.of("microsoft.network/natgateways"):
        attached = [lc(s.get("id")) for s in g(r, "properties", "subnets", default=[]) or []]
        attached = [subnet_ids[s] for s in attached if s in subnet_ids]
        if not attached:
            continue
        n = service_node(r["id"], rg_node(r["subscriptionId"], r["resourceGroup"]))
        for s in attached:
            edge(s, n, "egress", "Outbound via NAT")
        edge(n, internet_node(), "egress", "SNAT")

    if not nodes:
        return None

    m = e.manifest
    region = e.regions().most_common(1)
    region = REGION_NAMES.get(region[0][0], region[0][0]) if region else None
    note = (f"As-is network view generated by EstateSnapshot from Azure Resource Graph on {m['capturedAt'][:10]}. "
            f"{len(nets)} VNet(s) across {len(m['subscriptions'])} subscription(s). Shows VNets, subnets, IaaS and "
            "VNet-injected services, private endpoints with their PaaS targets, peering, hybrid links and public ingress. "
            "PaaS services without a network attachment are not drawn; see estate.md.")
    if omitted:
        note += " Not drawn (no icon or out of model): " + ", ".join(f"{k} x{v}" for k, v in omitted.most_common()) + "."
    meta = {"customer": m.get("customer") or "Customer", "scope": "Current State - Network Topology",
            "diagram_type": "network", "revision": "as-is 1", "date": dt.date.today().isoformat(),
            "author": "Climb Global Services", "source": f"EstateSnapshot {m['capturedAt']}", "note": note}
    if region:
        meta["region"] = region
    return {"schema_version": 1, "meta": meta, "nodes": nodes, "edges": edges}


# -- component seed (E8) -------------------------------------------------------------------------------

# Network plumbing that a component view leaves out; the network drawing carries it.
SEED_SKIP = {"microsoft.network/networksecuritygroups", "microsoft.network/routetables", "microsoft.network/privatednszones",
             "microsoft.network/dnszones", "microsoft.network/natgateways", "microsoft.network/ddosprotectionplans",
             "microsoft.network/privateendpoints", "microsoft.network/virtualnetworks"}
SEED_INSTANCES = 12
SEED_VIEWS = {"dataflow": {"hide_flows": ["mgmt", "peering", "egress", "replication"], "hide_private_endpoints": True}}


def seed(e):
    """A starting point for the target-state component diagram: the services that exist, grouped by
    resource group, with function apps, web apps, VMs and storage accounts collapsed into instances,
    private endpoints fronting their targets, stable ids, zone hints and a 'dataflow' view. It carries
    no data flows: those are architecture, and the architect adds them (with labels)."""
    ids = _Ids()
    nodes, by_id, placed, omitted = [], {}, {}, Counter()

    def add(n):
        if n["id"] not in by_id:
            by_id[n["id"]] = n
            nodes.append(n)
        return n["id"]

    def sub_node(sid):
        name = e.sub(sid)
        return add({"id": ids.get(("sub", sid), "sub", name), "kind": "subscription", "name": name, "zone": _zone(name)})

    def rg_node(sid, rg):
        return add({"id": ids.get(("rg", sid, lc(rg)), "rg", rg), "kind": "resource_group", "name": rg, "parent": sub_node(sid)})

    drawable = []
    for r in sorted(e.resources, key=lambda x: lc(x["id"])):
        t = lc(r["type"])
        if t in cat.SUPPORTING or t in SEED_SKIP or t.startswith("microsoft.network/virtualnetworks/"):
            continue
        if lc(r.get("resourceGroup")).startswith(("mc_", "me_")) or r.get("managedBy"):
            continue                          # platform-managed (AKS node group, managed environments)
        if not icon_for(t, r.get("kind")):
            if t not in ("microsoft.network/networkinterfaces", "microsoft.compute/disks", "microsoft.network/publicipaddresses"):
                omitted[cat.friendly(t)] += 1
            continue
        drawable.append(r)

    def group_key(r):
        t, p = lc(r["type"]), r.get("properties") or {}
        base = (r["subscriptionId"], lc(r["resourceGroup"]))
        if t == "microsoft.web/sites":
            plan = lc(name_of(p.get("serverFarmId")))
            return ("fn" if "functionapp" in lc(r.get("kind")) else "web", *base, plan)
        if t == T_VM:
            return ("vm", *base)
        if t == "microsoft.storage/storageaccounts":
            return ("storage", *base)
        return None

    members = {}
    for r in drawable:
        k = group_key(r)
        if k:
            members.setdefault(k, []).append(r)
    need = {"fn": 2, "web": 2, "vm": 2, "storage": 3}
    done = set()
    for r in drawable:
        parent = rg_node(r["subscriptionId"], r["resourceGroup"])
        k = group_key(r)
        if k and len(members[k]) >= need[k[0]]:
            if k in done:
                continue
            done.add(k)
            rs = members[k]
            kind_name = {"fn": "Function Apps", "web": "App Services", "vm": "Virtual Machines", "storage": "Storage Accounts"}[k[0]]
            plan = f" on plan {r['properties'].get('serverFarmId', '').split('/')[-1]}" if k[0] in ("fn", "web") and (r.get("properties") or {}).get("serverFarmId") else ""
            n = {"id": ids.get(("grp",) + k, "grp", f"{kind_name} {k[-1] if k[0] in ('fn', 'web') else r['resourceGroup']}"),
                 "kind": "service", "name": kind_name, "parent": parent, "icon": icon_for(r["type"], r.get("kind")),
                 "detail": f"{len(rs)} {kind_name.lower()}{plan}" + (f", first {SEED_INSTANCES} shown" if len(rs) > SEED_INSTANCES else ""),
                 "instances": [{"name": x["name"], **({"detail": e.sku_of(x)} if e.sku_of(x) else {})} for x in rs[:SEED_INSTANCES]]}
            add(n)
            for x in rs:
                placed[lc(x["id"])] = n["id"]
            continue
        det = " ".join(filter(None, [cat.friendly(r["type"]), e.sku_of(r)]))
        placed[lc(r["id"])] = add({"id": ids.get(("svc", lc(r["id"])), "svc", r["name"]), "kind": "service", "name": r["name"],
                                   "parent": parent, "icon": icon_for(r["type"], r.get("kind")), **({"detail": det} if det else {})})

    # Private endpoints sit in a subnet of a VNet and front a service drawn in its resource group.
    for pe in e.private_endpoints():
        tid, sid = pe["_target"], pe["_subnet"]
        sn = e.nodes.get(sid)
        if tid not in placed or not sn:
            omitted["private endpoint (target not drawn or outside the capture)"] += 1
            continue
        vnet = e.nodes.get(sn.get("parent"))
        if not vnet:
            continue
        vn = add({"id": ids.get(("vnet", vnet["_id"]), "vnet", vnet["name"]), "kind": "vnet", "name": vnet["name"],
                  "parent": rg_node(vnet["subscriptionId"], vnet["resourceGroup"]),
                  **({"cidr": a} if (a := next((x for x in g(vnet, "properties", "addressSpace", "addressPrefixes", default=[]) or [] if CIDR.match(x)), None)) else {})})
        pfx = (g(sn, "properties", "addressPrefix") or "").strip()
        snn = add({"id": ids.get(("snet", sid), "snet", sn["name"]), "kind": "subnet", "name": sn["name"], "parent": vn,
                   **({"cidr": pfx} if CIDR.match(pfx) else {})})
        group = f" ({pe['groupIds']})" if pe["groupIds"] else ""
        add({"id": ids.get(("pe", lc(pe["name"]), tid), "pe", pe["name"]), "kind": "private_endpoint",
             "name": f"PE: {e.label(tid)}{group}", "parent": snn, "fronts": placed[tid]})

    if not nodes:
        return None
    m = e.manifest
    note = (f"Component seed generated by EstateSnapshot from Azure Resource Graph on {m['capturedAt'][:10]}. Services are grouped by "
            "resource group; function apps, web apps, VMs and storage accounts are collapsed into instances; private endpoints front "
            "their targets. It carries no data flows: add them, with labels, and the 'dataflow' view will hide the management, "
            "peering and Private Link detail.")
    if omitted:
        note += " Not drawn (no icon, platform-managed or out of model): " + ", ".join(f"{k} x{v}" for k, v in omitted.most_common()) + "."
    region = e.regions().most_common(1)
    meta = {"customer": m.get("customer") or "Customer", "scope": "Current State - Components", "diagram_type": "component",
            "revision": "as-is seed 1", "date": dt.date.today().isoformat(), "author": "Climb Global Services",
            "source": f"EstateSnapshot {m['capturedAt']}", "note": note}
    if region:
        meta["region"] = REGION_NAMES.get(region[0][0], region[0][0])
    return {"schema_version": 1, "meta": meta, "nodes": nodes, "edges": [], "views": SEED_VIEWS}
