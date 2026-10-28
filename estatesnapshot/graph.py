"""Stage 2a - the estate as a graph. ARM resources reference each other by
resource ID, so walking every property of every resource and resolving any
string that looks like a resource ID yields the relationships with no per-type
code. Subnets are lifted out of their VNets as nodes, because almost every
network relationship lands on a subnet."""
import re

ID_RE = re.compile(r"^/subscriptions/[0-9a-f-]{36}/.*?/providers/[^/]+/[^/]+/[^/]+", re.I)


def _walk(obj, path):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from _walk(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk(v, path + "[]")
    elif isinstance(obj, str):
        yield path, obj


def _top_level(rid):
    """/subscriptions/s/resourceGroups/g/providers/ns/type/name - drop child segments."""
    m = ID_RE.match(rid)
    return m.group(0).lower() if m else None


def _resolve(rid, nodes):
    s = rid.lower().rstrip("/")
    while "/providers/" in s:
        if s in nodes:
            return s, False
        s = s.rsplit("/", 2)[0]
    top = _top_level(rid)
    return (top, True) if top else (None, False)


def build(resources):
    nodes = {}
    for r in resources:
        n = dict(r)
        n["_id"] = r["id"].lower()
        nodes[n["_id"]] = n
    for r in resources:
        if r["type"].lower() == "microsoft.network/virtualnetworks":
            for s in (r.get("properties") or {}).get("subnets") or []:
                if not s.get("id"):
                    continue
                sid = s["id"].lower()
                nodes[sid] = {"_id": sid, "id": s["id"], "name": s.get("name"),
                              "type": "microsoft.network/virtualnetworks/subnets",
                              "resourceGroup": r.get("resourceGroup"), "subscriptionId": r.get("subscriptionId"),
                              "location": r.get("location"), "properties": s.get("properties") or {},
                              "parent": r["id"].lower(), "synthetic": True}

    edges, seen = [], set()
    for nid, n in nodes.items():
        for path, val in _walk(n.get("properties"), "properties"):
            if not val.lower().startswith("/subscriptions/") or "/providers/" not in val.lower():
                continue
            tgt, external = _resolve(val, nodes)
            if not tgt or tgt == nid or tgt.startswith(nid + "/"):
                continue                       # self or own child: containment, not a relationship
            key = (nid, tgt, path)
            if key in seen:
                continue
            seen.add(key)
            edges.append({"from": nid, "to": tgt, "via": path, "external": external})
    return nodes, edges


class Graph:
    def __init__(self, resources):
        self.nodes, self.edges = build(resources)
        self.out, self.inc = {}, {}
        for e in self.edges:
            self.out.setdefault(e["from"], []).append(e)
            self.inc.setdefault(e["to"], []).append(e)

    def refs(self, nid, to_type=None, via=None):
        """Outgoing references, optionally filtered by target type or property path fragment."""
        out = []
        for e in self.out.get(nid, []):
            t = self.nodes.get(e["to"], {}).get("type", "").lower()
            if to_type and t != to_type:
                continue
            if via and via not in e["via"].lower():
                continue
            out.append(e)
        return out

    def referrers(self, nid, from_type=None):
        out = []
        for e in self.inc.get(nid, []):
            t = self.nodes.get(e["from"], {}).get("type", "").lower()
            if from_type and t != from_type:
                continue
            out.append(e)
        return out

    def subnets_of(self, nid):
        """Subnets a resource sits in: direct reference, or through its NICs."""
        found = set()
        for e in self.refs(nid, "microsoft.network/virtualnetworks/subnets"):
            found.add(e["to"])
        for e in self.refs(nid, "microsoft.network/networkinterfaces"):
            for e2 in self.refs(e["to"], "microsoft.network/virtualnetworks/subnets"):
                found.add(e2["to"])
        return sorted(found)

    def external_refs(self):
        return [e for e in self.edges if e["external"]]
