"""Compare captures, offline. Two uses:

  environments   Dev=snapshots\\Rascal-Dev PreProd=snapshots\\Rascal-PreProd Prod=snapshots\\Rascal-Prod
                 Resources are paired across captures by type and by name once the environment word is
                 taken out (vmraddbprod01 and vmraddbtest01 both become vmraddb{env}01). Reports sizing
                 differences between paired resources, resources with no counterpart, and references
                 that cross from one capture's environment into another's.
  drift          --drift <earlier> <later>
                 Two captures of the same scope, paired by resource id: added, removed and changed.

Facts only: a difference is stated, never rated. Nothing here talks to Azure."""
import os
import re
from collections import Counter, defaultdict

from . import catalogue as cat
from .model import Estate
from .util import g, lc

ENV_WORDS = {"preprod": "preprod", "pre-prod": "preprod", "nonprod": "nonprod", "non-prod": "nonprod",
             "production": "prod", "prod": "prod", "prd": "prod", "development": "dev", "dev": "dev",
             "staging": "staging", "stage": "staging", "stg": "staging", "test": "test", "tst": "test",
             "uat": "uat", "qa": "qa"}
EMBEDDABLE = {"preprod", "nonprod", "prod", "production", "prd", "uat", "stg", "staging"}   # safe at the end of a longer word
EMBED_BEFORE_DIGIT = {"dev", "test", "tst", "qa"}     # inside a longer word only when a digit follows (vmdbtest01, not latest-build)
_RX = re.compile(r"(?:(?<![a-z])|(?<=[a-z]))(" + "|".join(sorted((re.escape(k) for k in ENV_WORDS), key=len, reverse=True)) + r")(?![a-z])")


def env_in(name):
    """(canonical environment, span) for the environment word in a name, or (None, None)."""
    n = lc(name)
    for m in _RX.finditer(n):
        word = m.group(1)
        boundary = m.start() == 0 or not n[m.start() - 1].isalpha()
        digit_after = m.end() < len(n) and n[m.end()].isdigit()
        if boundary or word in EMBEDDABLE or (word in EMBED_BEFORE_DIGIT and digit_after):
            return ENV_WORDS[word], m.span()
    return None, None


def norm_name(name, own=None):
    """Name with its environment word replaced by {env}. `own` is the environment of the capture the
    name came from: that word is also accepted inside a longer word (acrdev in a Dev capture), which
    is too loose to allow for every word in every capture."""
    env, span = env_in(name)
    n = lc(name)
    if not span and own:
        words = [w for w, c in ENV_WORDS.items() if c == own]
        m = re.search(r"(" + "|".join(sorted(map(re.escape, words), key=len, reverse=True)) + r")(?![a-z])", n)
        span = m.span() if m else None
    return (n[:span[0]] + "{env}" + n[span[1]:]) if span else n


class Capture:
    def __init__(self, label, path):
        self.label, self.path = label, path
        self.env = ENV_WORDS.get(lc(label))
        self.e = Estate(path)
        self.site = {lc(r["name"]) + "|" + lc(r["resourceGroup"]): r for r in self.e.site_facts()}
        cz = {r["name"]: r for r in self.e.cosmos_config()["accounts"]}
        sb = {r["name"]: r for r in self.e.servicebus_config()["namespaces"]}
        self.cosmos, self.sb = cz, sb

    def attrs(self, r):
        """The attributes worth comparing between two paired resources."""
        t = lc(r["type"])
        p = r.get("properties") or {}
        a = {"sku": g(r, "sku", "name"), "tier": g(r, "sku", "tier"), "capacity": g(r, "sku", "capacity"),
             "kind": r.get("kind"), "zones": ",".join(r.get("zones") or []) or None}
        if t == "microsoft.compute/virtualmachines":
            a.update(size=g(p, "hardwareProfile", "vmSize"), dataDisks=len(g(p, "storageProfile", "dataDisks", default=[]) or []),
                     osDisk=g(p, "storageProfile", "osDisk", "managedDisk", "storageAccountType"))
        elif t == "microsoft.web/serverfarms":
            a.update(zoneRedundant=p.get("zoneRedundant"), os="Linux" if p.get("reserved") else "Windows")
        elif t == "microsoft.web/sites":
            s = self.site.get(lc(r["name"]) + "|" + lc(r["resourceGroup"]), {})
            a.update(runtime=s.get("runtime") or None, workers=s.get("workerBits") or None, alwaysOn=s.get("alwaysOn"),
                     healthCheck=s.get("healthCheckPath") or None, scaleLimit=s.get("scaleLimit"))
        elif t == "microsoft.documentdb/databaseaccounts":
            c = self.cosmos.get(r["name"], {})
            a.update(consistency=c.get("consistency"), backup=c.get("backup") or None, throughput=c.get("throughput"))
        elif t == "microsoft.servicebus/namespaces":
            c = self.sb.get(r["name"], {})
            a.update(topics=c.get("topics") or None, queues=c.get("queues") or None)
        elif t == "microsoft.containerservice/managedclusters":
            a.update(version=p.get("kubernetesVersion"), pools=" ".join(
                f"{x.get('name')}:{x.get('vmSize')}x{x.get('count')}" for x in p.get("agentPoolProfiles") or []) or None,
                plugin=g(p, "networkProfile", "networkPlugin"))
        elif t == "microsoft.sql/servers/databases":
            a.update(maxGB=round((p.get("maxSizeBytes") or 0) / 1024 ** 3) or None, zoneRedundant=p.get("zoneRedundant"),
                     backup=p.get("requestedBackupStorageRedundancy"))
        elif t == "microsoft.storage/storageaccounts":
            a.update(accessTier=p.get("accessTier"), hns=p.get("isHnsEnabled"))
        return {k: v for k, v in a.items() if v not in (None, "")}


def _primary(c):
    return [r for r in c.e.resources if lc(r["type"]) not in cat.SUPPORTING
            and lc(r["type"]) not in ("microsoft.network/networkinterfaces", "microsoft.compute/disks")]


def _diff(a, b):
    return {k: (a.get(k), b.get(k)) for k in sorted(set(a) | set(b)) if a.get(k) != b.get(k)}


# -- environments ------------------------------------------------------------------------------------

def hosts_of(r):
    """Host names a resource answers to, read from its properties (default host names, endpoints, login servers)."""
    out = set()

    def walk(o):
        if isinstance(o, dict):
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
        elif isinstance(o, str):
            m = re.match(r"^(?:https?://|sb://)?([a-z0-9][a-z0-9.-]*\.(?:azurewebsites\.net|windows\.net|azurecr\.io|azure\.net|azure-api\.net|"
                         r"servicebus\.windows\.net|documents\.azure\.com|azurefd\.net|trafficmanager\.net|cloudapp\.azure\.com))(?:[:/]|$)", o.lower())
            if m:
                out.add(m.group(1))
    walk(r.get("properties"))
    return out


def cross_references(caps):
    """References from a resource in one capture to a resource in another: resource ids in properties,
    container registries named by site images, and hosts called by Logic Apps."""
    idx, hostidx = {}, {}
    for c in caps:
        for nid, n in c.e.nodes.items():
            idx[nid] = (c, n)
        for r in c.e.resources:
            for h in hosts_of(r):
                hostidx.setdefault(h, (c, r))
    rows = []
    for c in caps:
        e = c.e
        for ed in e.graph.edges:
            tgt = idx.get(ed["to"])
            src = e.nodes.get(ed["from"])
            if not tgt or tgt[0] is c or not src:
                continue
            if lc(src.get("resourceGroup")).startswith(("me_", "mc_")) or lc(src["type"]) in e.HISTORY:
                continue
            rows.append({"from": f"{src['name']} ({cat.friendly(src['type'])})", "fromEnv": c.label,
                         "to": f"{tgt[1]['name']} ({cat.friendly(tgt[1]['type'])})", "toEnv": tgt[0].label,
                         "how": e.via_text(ed["via"])})
        for s in e.site_facts():
            h = hostidx.get(s["registry"]) if s["registry"] else None
            if h and h[0] is not c:
                rows.append({"from": f"{s['name']} (App Service)", "fromEnv": c.label, "to": f"{h[1]['name']} (Container Registry)",
                             "toEnv": h[0].label, "how": f"container image {s['image']}"})
        for rid, lh in e.logic_hosts().items():
            wf = e.nodes.get(rid)
            for host in lh["hosts"]:
                h = hostidx.get(host)
                if wf and h and h[0] is not c:
                    rows.append({"from": f"{wf['name']} (Logic App)", "fromEnv": c.label,
                                 "to": f"{h[1]['name']} ({cat.friendly(h[1]['type'])})", "toEnv": h[0].label,
                                 "how": f"calls {host}"})
    seen, out = set(), []
    for r in rows:
        k = tuple(r.values())
        if k not in seen:
            seen.add(k)
            out.append(r)
    return sorted(out, key=lambda r: (r["fromEnv"], r["toEnv"], r["from"]))


def pair(caps):
    """{key: {label: resource}} where key is (type, normalised name)."""
    by = defaultdict(lambda: defaultdict(list))
    for c in caps:
        for r in _primary(c):
            by[(lc(r["type"]), norm_name(r["name"], c.env))][c.label].append(r)
    return by


def environments(caps):
    by = pair(caps)
    labels = [c.label for c in caps]
    cap = {c.label: c for c in caps}
    matched, only, counts = [], defaultdict(lambda: defaultdict(list)), defaultdict(lambda: Counter())
    for (t, n), per in sorted(by.items()):
        for c in caps:
            counts[t][c.label] += len(per.get(c.label, []))
        present = [lb for lb in labels if per.get(lb)]
        if len(present) == 1:
            only[present[0]][t] += [r["name"] for r in per[present[0]]]
            continue
        for i in range(max(len(v) for v in per.values())):
            members = {lb: per[lb][i] for lb in present if i < len(per[lb])}
            if len(members) < 2:
                continue
            here = [lb for lb in present if lb in members]
            a_lb = here[0]
            base = cap[a_lb].attrs(members[a_lb])
            for lb in here[1:]:
                d = _diff(base, cap[lb].attrs(members[lb]))
                matched.append({"service": cat.friendly(t), "pattern": n, "a": a_lb, "aName": members[a_lb]["name"],
                                "b": lb, "bName": members[lb]["name"], "differences": d})
    return {"labels": labels, "matched": matched, "only": only, "counts": counts, "cross": cross_references(caps)}


# -- drift -------------------------------------------------------------------------------------------

def drift(a, b):
    ia = {lc(r["id"]): r for r in _primary(a)}
    ib = {lc(r["id"]): r for r in _primary(b)}
    changed = []
    for k in sorted(set(ia) & set(ib)):
        d = _diff(a.attrs(ia[k]), b.attrs(ib[k]))
        if d:
            changed.append({"service": cat.friendly(ia[k]["type"]), "name": ib[k]["name"], "differences": d})
    return {"added": [{"service": cat.friendly(ib[k]["type"]), "name": ib[k]["name"]} for k in sorted(set(ib) - set(ia))],
            "removed": [{"service": cat.friendly(ia[k]["type"]), "name": ia[k]["name"]} for k in sorted(set(ia) - set(ib))],
            "changed": changed}


# -- rendering ---------------------------------------------------------------------------------------

def _t(headers, rows, cap=60):
    if not rows:
        return "_None found._\n"
    out = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    for r in rows[:cap]:
        out.append("| " + " | ".join(str(c).replace("|", "/").replace("\n", " ") for c in r) + " |")
    if len(rows) > cap:
        out.append(f"\n_Showing {cap} of {len(rows)}._")
    return "\n".join(out) + "\n"


def _d(d):
    return "; ".join(f"{k}: {a if a is not None else '(none)'} -> {b if b is not None else '(none)'}" for k, (a, b) in d.items())


def render_environments(res, caps):
    L = ["# Environment comparison\n",
         "Resources are paired by type and by name with the environment word taken out. A difference is a fact about "
         "two configurations; nothing here says which is right.\n", "## Captures\n"]
    L.append(_t(["Environment", "Captured", "Subscriptions", "Resources", "Folder"],
                [[c.label, c.e.manifest["capturedAt"][:16].replace("T", " "), len(c.e.manifest["subscriptions"]),
                  len(c.e.resources), c.path] for c in caps]))
    L.append("## Instances by service\n")
    rows = [[cat.friendly(t)] + [v.get(lb, 0) for lb in res["labels"]] for t, v in sorted(res["counts"].items(), key=lambda kv: cat.friendly(kv[0]))]
    L.append(_t(["Service"] + res["labels"], rows, cap=80))
    diff = [m for m in res["matched"] if m["differences"]]
    same = len(res["matched"]) - len(diff)
    L.append(f"## Paired resources that differ ({len(diff)} of {len(res['matched'])} pairs; {same} identical on the attributes compared)\n")
    L.append(_t(["Service", "Pair", "Differences"],
                [[m["service"], f"{m['aName']} ({m['a']}) / {m['bName']} ({m['b']})", _d(m["differences"])] for m in diff], cap=120))
    L.append("## Resources with no counterpart\n")
    for lb in res["labels"]:
        o = res["only"].get(lb)
        if o:
            L.append(f"**Only in {lb}:** " + "; ".join(f"{cat.friendly(t)} ({len(n)}): {', '.join(n[:8])}{' ...' if len(n) > 8 else ''}"
                                                       for t, n in sorted(o.items(), key=lambda kv: -len(kv[1]))[:25]) + "\n")
    L.append("## References that cross from one capture into another\n")
    L.append("A reference from a resource in one environment's capture to a resource that is in another's: a resource id in "
             "properties, a container image from another capture's registry, or a Logic App call to another capture's host.\n")
    L.append(_t(["From", "In", "To", "In", "How"], [[r["from"], r["fromEnv"], r["to"], r["toEnv"], r["how"]] for r in res["cross"]], cap=80))
    return "\n".join(L)


def render_drift(res, a, b):
    L = [f"# Drift: {a.label} to {b.label}\n",
         f"Captured {a.e.manifest['capturedAt'][:16]} and {b.e.manifest['capturedAt'][:16]} (UTC). Resources are paired by id.\n",
         f"## Added ({len(res['added'])})\n", _t(["Service", "Name"], [[x["service"], x["name"]] for x in res["added"]], cap=100),
         f"## Removed ({len(res['removed'])})\n", _t(["Service", "Name"], [[x["service"], x["name"]] for x in res["removed"]], cap=100),
         f"## Changed ({len(res['changed'])})\n", _t(["Service", "Name", "Differences"], [[x["service"], x["name"], _d(x["differences"])] for x in res["changed"]], cap=100)]
    return "\n".join(L)


def parse_targets(items):
    """['Dev=path', 'path'] -> [Capture]. The label defaults to the environment word in the folder name."""
    caps = []
    for it in items:
        label, _, path = it.partition("=") if "=" in it and not os.path.exists(it) else ("", "", it)
        label = label or env_in(os.path.basename(os.path.normpath(path)))[0] or os.path.basename(os.path.normpath(path))
        caps.append(Capture(label, path))
    return caps
