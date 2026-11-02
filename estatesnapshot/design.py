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
    """The as-is network drawing: every VNet as a box carrying its subnets, each subnet labelled with
    its NSG and what it holds, and, where NSG flow logs were collected, the measured paths between
    subnets and the internet. Services stay in the component seed; this drawing answers where traffic
    can go and, with flow logs, where it went."""
    nodes, edges = [], []
    by_id, edge_keys = {}, set()

    def add(n):
        if n["id"] not in by_id:
            by_id[n["id"]] = n
            nodes.append(n)
        return n["id"]

    def edge(a, b, flow, label=None, **kw):
        # both directions are real here: a subnet talks out to the internet and the internet talks in
        k = (a, b) if flow in ("ingress", "egress") else frozenset((a, b))
        if a == b or k in edge_keys or not label:
            return
        edge_keys.add(k)
        edges.append({"from": a, "to": b, "flow": flow, "label": label, **kw})

    # The model carries flows only in the drawing; its node list is topology
    for n in (e.network_overview() or ([], []))[0]:
        add(n)
    flows = e.flow_edges()
    # Internet sits in the actors column, ahead of the landing zone, when a measured flow
    # starts or ends outside the VNets. Its state keeps it out of the containers' column.
    if any(kind == "internet" for x in flows for kind, _ in (x["src"], x["dst"])):
        add({"id": "act_internet", "kind": "actor", "name": "Internet", "icon": "internet", "zone": "actors",
             "state": "focus"})
    for x in flows:
        (k1, r1), (k2, r2) = x["src"], x["dst"]
        a, b = _flow_end(k1, r1), _flow_end(k2, r2)
        if not (a in by_id and b in by_id) or a == b:
            continue
        if "None" in (a, b):
            continue
        row = x["row"]
        edge(a, b, FLOW_CLASS.get((k1, k2), "sync"), FLOW_LABEL.get((k1, k2), "Measured flow"),
             note="; ".join(filter(None, [
                 f"{row['connections']} new connection(s) over {row['protocols']}" if row["protocols"] else "",
                 f"port(s) {row['ports']}" if row["ports"] else "",
                 f"rule(s) {row['allowedBy']}" if row["allowedBy"] else "",
                 f"DENIED by {row['deniedBy']}" if row["deniedBy"] else ""])))

    if not nodes:
        return None
    m = e.manifest
    mf = e.measured_flows()
    region = e.regions().most_common(1)
    region = REGION_NAMES.get(region[0][0], region[0][0]) if region else None
    traced = Counter((x["src"][0], x["dst"][0]) for x in flows)
    note = (f"As-is network drawing generated by EstateSnapshot from Azure Resource Graph on {m['capturedAt'][:10]}: "
            f"{len(e.networks())} VNet(s) across {len(m['subscriptions'])} subscription(s), each subnet with its NSG "
            "and what it holds. ")
    if mf["collected"]:
        note += (f"Traffic is measured from {mf['logs']} NSG flow log(s): "
                 + ", ".join(f"{a} to {b} x{n}" for (a, b), n in sorted(traced.items())) + ". ")
        if not traced:
            note += "No measured flow could be placed on a subnet. "
    else:
        note += "No NSG flow logs were collected, so this shows where traffic can go, not where it went. "
    if mf["gaps"]:
        note += "Flow log gaps: " + "; ".join(f"{g['log']} ({g['target']}): {g['gap']}" for g in mf["gaps"]) + ". "
    meta = {"customer": m.get("customer") or "Customer", "scope": "Current State - Network Topology",
            "diagram_type": "network", "revision": "as-is 1", "date": dt.date.today().isoformat(),
            "author": "Climb Global Services", "source": f"EstateSnapshot {m['capturedAt']}", "note": note}
    if region:
        meta["region"] = region
    return {"schema_version": 1, "meta": meta, "nodes": nodes, "edges": edges}


FLOW_CLASS = {("internet", "subnet"): "ingress", ("subnet", "internet"): "egress"}
FLOW_LABEL = {("internet", "subnet"): "Measured ingress", ("subnet", "internet"): "Measured egress"}
VNET_REMINDER = ("The VNet, subnet and service detail is in design-seed.json and estate.md.")

def _flow_end(kind, ref):
    """A path end as the id the network overview gives it: a subnet id is a slug of the resource id."""
    if kind == "internet":
        return "act_internet"
    return ("snet_" + re.sub(r"[^a-z0-9]+", "_", lc(ref)).strip("_"))[:40].rstrip("_") if kind == "subnet" else None


# -- overview: the estate at a glance ----------------------------------------------------------------

# Which network tier a service belongs to, by type. Networking (firewalls, gateways, DNS) is left
# to the network drawing; naming the tier here is not a rating of anything.
OVERVIEW_TIER = {
    "microsoft.web/sites": "web", "microsoft.web/staticsites": "web", "microsoft.cdn/profiles": "web",
    "microsoft.network/frontdoors": "web", "microsoft.network/applicationgateways": "web",
    "microsoft.network/loadbalancers": "web", "microsoft.network/trafficmanagerprofiles": "web",
    "microsoft.apimanagement/service": "web",
    "microsoft.containerservice/managedclusters": "compute", "microsoft.compute/virtualmachines": "compute",
    "microsoft.compute/virtualmachinescalesets": "compute", "microsoft.app/managedenvironments": "compute",
    "microsoft.app/containerapps": "compute", "microsoft.app/jobs": "compute",
    "microsoft.containerinstance/containergroups": "compute", "microsoft.desktopvirtualization/hostpools": "compute",
    "microsoft.sql/servers": "data", "microsoft.sql/managedinstances": "data", "microsoft.documentdb/databaseaccounts": "data",
    "microsoft.dbforpostgresql/flexibleservers": "data", "microsoft.dbformysql/flexibleservers": "data",
    "microsoft.cache/redis": "data", "microsoft.cache/redisenterprise": "data", "microsoft.datafactory/factories": "data",
    "microsoft.synapse/workspaces": "data", "microsoft.databricks/workspaces": "data", "microsoft.kusto/clusters": "data",
    "microsoft.storage/storageaccounts": "storage", "microsoft.netapp/netappaccounts": "storage",
    "microsoft.servicebus/namespaces": "integration", "microsoft.eventhub/namespaces": "integration",
    "microsoft.eventgrid/topics": "integration", "microsoft.eventgrid/domains": "integration",
    "microsoft.logic/workflows": "integration",
    "microsoft.keyvault/vaults": "security", "microsoft.managedidentity/userassignedidentities": "security",
    "microsoft.operationalinsights/workspaces": "monitoring", "microsoft.insights/components": "monitoring",
    "microsoft.recoveryservices/vaults": "monitoring", "microsoft.dataprotection/backupvaults": "monitoring",
    "microsoft.automation/automationaccounts": "monitoring",
}
# One box per tier, titled and iconned.
OVERVIEW_TITLES = {"web": ("Web and ingress", "appservice"), "compute": ("Compute", "vm"),
                   "data": ("Data", "sqldb"), "storage": ("Storage", "storage"),
                   "integration": ("Integration", "servicebus"), "security": ("Security and identity", "keyvault"),
                   "monitoring": ("Monitoring and backup", "loganalytics")}


def sub_of(rid):
    """The subscription id in a resource id, or '' if it is not subscription scoped."""
    parts = lc(rid).split("/")
    return parts[2] if len(parts) > 2 and parts[1] == "subscriptions" else ""


def overview(e):
    """The estate at a glance, for someone who has never seen it: the internet and on-premises actors,
    each subscription with its network share, and one box per service tier with an edge wherever the
    snapshot holds a reference. Counting and linking only: no rating, no application flows."""
    ids = _Ids()
    by_id, nodes, edges, edge_keys = {}, [], [], set()

    def add(n):
        if n["id"] not in by_id:
            by_id[n["id"]] = n
            nodes.append(n)
        return n["id"]

    def edge(a, b, flow, label):
        k = frozenset((a, b))
        if a != b and k not in edge_keys:
            edge_keys.add(k)
            edges.append({"from": a, "to": b, "flow": flow, "label": label})

    def sub_node(sid):
        name = e.sub(sid)
        return add({"id": ids.get(("sub", sid), "sub", name), "kind": "subscription", "name": name, "zone": _zone(name)})

    # What each tier holds, by subscription, from the snapshot's own types.
    counts = Counter()
    for r in e.resources:
        t = OVERVIEW_TIER.get(lc(r["type"]))
        if t and lc(r["type"]) != "microsoft.web/sites" and icon_for(r["type"], r.get("kind")):
            counts[(lc(r["subscriptionId"]), t)] += 1
    # a site is a Web box on its own, a function app is Compute
    for r in e.of("microsoft.web/sites"):
        t = "compute" if "functionapp" in lc(r.get("kind")) else "web"
        counts[(lc(r["subscriptionId"]), t)] += 1

    def tier_node(sid, t):
        title, icon = OVERVIEW_TITLES[t]
        n = add({"id": ids.get(("tier", sid, t), "tier", title), "kind": "service", "name": title,
                 "parent": sub_node(sid), "icon": icon,
                 "detail": f"{counts[(sid, t)]} service{'s' if counts[(sid, t)] != 1 else ''}"})
        return n

    for sid, t in sorted(counts):
        tier_node(sid, t)

    def has_tier(sid, t):
        return (sid, t) in counts

    def tier_of(rid):
        return OVERVIEW_TIER.get(e.type_of(rid))

    # Internet ingress, through a public entry point that is in the capture.
    nets = e.networks()
    sites = e.of("microsoft.web/sites")
    appliances = e.of("microsoft.network/applicationgateways", "microsoft.cdn/profiles",
                      "microsoft.network/frontdoors", "microsoft.network/trafficmanagerprofiles",
                      "microsoft.network/loadbalancers")
    public_ips = [r for r in e.of("microsoft.network/publicipaddresses") if g(r, "properties", "ipConfiguration", "id")]
    if sites or appliances or public_ips:
        internet = add({"id": "act_internet", "kind": "actor", "name": "Internet", "icon": "internet", "zone": "actors"})
        for r in sites + appliances:
            sid = lc(r["subscriptionId"])
            if has_tier(sid, "web"):
                edge(internet, ids.by_key[("tier", sid, "web")], "ingress",
                     "HTTPS" if lc(r["type"]) in ("microsoft.web/sites", "microsoft.cdn/profiles") else "Public IP")
        for r in public_ips:
            sid = lc(r["subscriptionId"])
            if has_tier(sid, "web"):
                edge(internet, ids.by_key[("tier", sid, "web")], "ingress", "Public IP")

    # On-premises, where a gateway or circuit is in the capture.
    gateways = e.of("microsoft.network/virtualnetworkgateways", "microsoft.network/expressroutecircuits")
    if gateways:
        onprem = add({"id": "act_onprem", "kind": "actor", "name": "On-premises network", "icon": "onprem", "zone": "actors"})
        for sid in sorted({lc(sub_of(r["id"])) for r in gateways} - {""}):
            if ("sub", sid) in ids.by_key:
                edge(onprem, ids.by_key[("sub", sid)], "egress", "VPN or ExpressRoute")

    # References between tiers that the snapshot actually holds, within one subscription.
    for ed in e.graph.edges:
        ta, tb = tier_of(ed["from"]), tier_of(ed["to"])
        sa, sb = sub_of(ed["from"]), sub_of(ed["to"])
        if not ta or not tb or sa != sb or not has_tier(sa, ta) or not has_tier(sb, tb):
            continue
        edge(ids.by_key[("tier", sa, ta)], ids.by_key[("tier", sb, tb)], "sync", "Resource reference")

    # Private endpoints: a service reached over one, from a subnet in the same subscription.
    for pe in e.private_endpoints():
        tt = tier_of(pe["_target"])
        sid = sub_of(pe["_subnet"])
        if tt and has_tier(sid, tt):
            edge(ids.by_key[("tier", sid, tt)], sub_node(sid), "pe", "Private endpoint")

    # Each subscription carries its network share as a note beside it.
    pes = e.private_endpoints()
    for sub in e.manifest.get("subscriptions") or []:
        sid = lc(sub["id"])
        if ("sub", sid) not in ids.by_key:
            continue
        vnets = [v for v in nets if sub_of(v["_id"]) == sid]
        spaces = [a for v in vnets for a in v["addressSpace"]]
        by_id[ids.by_key[("sub", sid)]]["callout"] = {
            "title": "Network",
            # one fact per row: the note box is narrow, so a joined line would wrap and collide
            "facts": [{"label": "VNets", "value": str(len(vnets))},
                      {"label": "Private endpoints", "value": str(sum(1 for pe in pes if sub_of(pe["_subnet"]) == sid))},
                      {"label": "Address space", "value": ", ".join(spaces[:2]) + (" and more" if len(spaces) > 2 else "")
                                                     or "none"}]}

    if not nodes:
        return None
    m = e.manifest
    note = (f"Estate overview generated by EstateSnapshot from Azure Resource Graph on {m['capturedAt'][:10]}. "
            f"{len(e.resources)} resources across {len(e.manifest.get('subscriptions') or [])} subscription(s), "
            "grouped into service tiers by type, with a count in each. Edges are the references the snapshot holds "
            "between tiers, internet ingress, the on-premises route and private endpoints. It draws no application "
            "flows and scores nothing: estate.md holds the facts and design.json the network topology.")
    region = e.regions().most_common(1)
    meta = {"customer": m.get("customer") or "Customer", "scope": "Current State - Overview",
            "diagram_type": "context", "revision": "as-is overview 1", "date": dt.date.today().isoformat(),
            "author": "Climb Global Services", "source": f"EstateSnapshot {m['capturedAt']}", "note": note}
    if region:
        meta["region"] = REGION_NAMES.get(region[0][0], region[0][0])
    return {"schema_version": 1, "meta": meta, "nodes": nodes, "edges": edges}


# -- component seed (E8) -------------------------------------------------------------------------------

# Network plumbing that a component view leaves out; the network drawing carries it.
# VNets and subnets are added back only where a service is deployed into one.
SEED_SKIP = {"microsoft.network/networksecuritygroups", "microsoft.network/routetables", "microsoft.network/privatednszones",
             "microsoft.network/dnszones", "microsoft.network/natgateways", "microsoft.network/ddosprotectionplans",
             "microsoft.network/privateendpoints", "microsoft.network/virtualnetworks"}
SEED_INSTANCES = 12
SEED_VIEWS = {"dataflow": {"hide_flows": ["mgmt", "peering", "egress", "replication"], "hide_private_endpoints": True}}
# Azure deploys these into a subnet of a customer VNet. The diagrammer treats the first
# four outside every subnet as an error, and the rest as a prompt to check; either way the
# seed has to keep them where they run or it cannot be emitted.
SEED_IN_SUBNET = {"vm", "vmss", "appgw", "sqlmi", "aks", "bastion", "firewall", "vnetgw"}
# Of those, the ones the validator calls an error rather than a prompt. If the capture
# does not say which subnet one sits in, the seed leaves it out and says so.
SEED_SUBNET_ERROR = {"vm", "vmss", "appgw", "sqlmi"}


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

    # Which subnet each resource actually sits in, from the same occupants the network
    # drawing uses, so a service that Azure deploys into a subnet is drawn in it.
    subnet_of = {}
    for v in e.networks():
        for s in v["subnets"]:
            for rid, _t in s["_occupants"]:
                subnet_of[lc(rid)] = (v["_id"], s)

    def subnet_node(vid, s):
        vnet = e.nodes[vid]
        vn = add({"id": ids.get(("vnet", vid), "vnet", vnet["name"]), "kind": "vnet", "name": vnet["name"],
                  "parent": rg_node(vnet["subscriptionId"], vnet["resourceGroup"])})
        prefix = next((a for a in g(vnet, "properties", "addressSpace", "addressPrefixes", default=[]) or [] if CIDR.match(a)), None)
        if prefix:
            by_id[vn].setdefault("cidr", prefix)
        pfx = (s["prefix"] or "").split(",")[0].strip()
        sn = add({"id": ids.get(("snet", s["_id"]), "snet", s["name"]), "kind": "subnet", "name": s["name"], "parent": vn})
        if CIDR.match(pfx):
            by_id[sn].setdefault("cidr", pfx)
        return sn

    def parent_of(r):
        if icon_for(r["type"], r.get("kind")) in SEED_IN_SUBNET and lc(r["id"]) in subnet_of:
            return subnet_node(*subnet_of[lc(r["id"])])
        return rg_node(r["subscriptionId"], r["resourceGroup"])

    drawable = []
    for r in sorted(e.resources, key=lambda x: lc(x["id"])):
        t = lc(r["type"])
        if t in cat.SUPPORTING or t in SEED_SKIP or t.startswith("microsoft.network/virtualnetworks/"):
            continue
        if lc(r.get("resourceGroup")).startswith(("mc_", "me_")) or r.get("managedBy"):
            continue                          # platform-managed (AKS node group, managed environments)
        ic = icon_for(t, r.get("kind"))
        if not ic:
            if t not in ("microsoft.network/networkinterfaces", "microsoft.compute/disks", "microsoft.network/publicipaddresses"):
                omitted[cat.friendly(t)] += 1
            continue
        if ic in SEED_SUBNET_ERROR and lc(r["id"]) not in subnet_of:
            omitted[cat.friendly(t) + " (subnet not known)"] += 1
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
        parent = parent_of(r)
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
            "their targets; services deployed into a subnet keep the VNet and subnet they run in. It carries no data flows: add them, "
            "with labels, and the 'dataflow' view will hide the management, peering and Private Link detail.")
    if omitted:
        note += " Not drawn (no icon, platform-managed, subnet not known or out of model): " + ", ".join(f"{k} x{v}" for k, v in omitted.most_common()) + "."
    region = e.regions().most_common(1)
    meta = {"customer": m.get("customer") or "Customer", "scope": "Current State - Components", "diagram_type": "component",
            "revision": "as-is seed 1", "date": dt.date.today().isoformat(), "author": "Climb Global Services",
            "source": f"EstateSnapshot {m['capturedAt']}", "note": note}
    if region:
        meta["region"] = REGION_NAMES.get(region[0][0], region[0][0])
    return {"schema_version": 1, "meta": meta, "nodes": nodes, "edges": [], "views": SEED_VIEWS}
