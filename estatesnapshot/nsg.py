"""NSG inbound rules evaluated in priority order. A rule is only as open as the traffic
that no higher-priority deny has already taken: the first matching rule wins, so an allow at
priority 501 is closed by deny rules at 402 to 404 that cover the same traffic.

Pure functions, offline, no Azure calls. The effective-rules API needs an action beyond
Reader, so this reads the rules already held in Resource Graph.

The evaluation is deliberately conservative towards "open": a deny only shadows an allow when
it provably covers it. Rules that use application security groups, or address forms this module
does not understand, never shadow anything."""
import ipaddress

ANY = {"*", "any", "0.0.0.0/0"}
ALL_PROTOCOLS = ("tcp", "udp", "icmp", "esp", "ah")
OPEN_SOURCES = {"*", "internet", "0.0.0.0/0", "any"}


def lc(s):
    return str(s if s is not None else "").lower()


def _list(rp, one, many):
    return [x for x in [rp.get(one)] + list(rp.get(many) or []) if x not in (None, "")]


def parse_ports(items):
    """['80', '1000-2000', '*'] -> sorted, merged [(lo, hi)]. Unparseable entries are dropped."""
    out = []
    for it in items:
        it = str(it).strip()
        if it == "*":
            out.append((0, 65535))
            continue
        try:
            lo, _, hi = it.partition("-")
            out.append((int(lo), int(hi or lo)))
        except ValueError:
            continue
    return _merge(out)


def _merge(iv):
    iv = sorted(iv)
    out = []
    for lo, hi in iv:
        if out and lo <= out[-1][1] + 1:
            out[-1] = (out[-1][0], max(out[-1][1], hi))
        else:
            out.append((lo, hi))
    return out


def _subtract(iv, cut):
    """Intervals iv minus intervals cut (both merged)."""
    out = []
    for lo, hi in iv:
        cur = lo
        for clo, chi in cut:
            if chi < cur or clo > hi:
                continue
            if clo > cur:
                out.append((cur, clo - 1))
            cur = max(cur, chi + 1)
            if cur > hi:
                break
        if cur <= hi:
            out.append((cur, hi))
    return out


def fmt_ports(iv):
    return ",".join(str(lo) if lo == hi else ("*" if (lo, hi) == (0, 65535) else f"{lo}-{hi}") for lo, hi in iv)


def parse_rule(rule):
    rp = rule.get("properties") or {}
    proto = lc(rp.get("protocol")) or "*"
    return {
        "name": rule.get("name"), "priority": rp.get("priority") or 0,
        "direction": lc(rp.get("direction")), "access": lc(rp.get("access")),
        "protocols": set(ALL_PROTOCOLS) if proto == "*" else {proto},
        "ports": parse_ports(_list(rp, "destinationPortRange", "destinationPortRanges")),
        "ports_raw": [str(x) for x in _list(rp, "destinationPortRange", "destinationPortRanges")],
        "src": [lc(x) for x in _list(rp, "sourceAddressPrefix", "sourceAddressPrefixes")],
        "dst": [lc(x) for x in _list(rp, "destinationAddressPrefix", "destinationAddressPrefixes")],
        "src_asg": bool(rp.get("sourceApplicationSecurityGroups")),
        "dst_asg": bool(rp.get("destinationApplicationSecurityGroups")),
    }


def _net(tok):
    try:
        return ipaddress.ip_network(tok, strict=False)
    except ValueError:
        return None


def _covers_token(outer, inner):
    if outer in ANY or outer == inner:
        return True
    if outer == "internet":
        n = _net(inner)
        return bool(n) and not n.is_private
    a, b = _net(outer), _net(inner)
    return bool(a and b and a.version == b.version and b.subnet_of(a))


def covers(outer, inner):
    """Every address in `inner` is matched by some token in `outer`."""
    if not outer or not inner:
        return False
    return all(any(_covers_token(o, i) for o in outer) for i in inner)


def is_open_source(rule):
    return not rule["src_asg"] and any(s in OPEN_SOURCES for s in rule["src"])


def evaluate(allow, rules):
    """Status of one inbound allow rule against the same NSG's other rules (all parsed)."""
    remaining = {p: list(allow["ports"]) for p in allow["protocols"]}
    closers = []
    for d in sorted(rules, key=lambda r: r["priority"]):
        if d["direction"] != "inbound" or d["access"] != "deny" or d["priority"] >= allow["priority"]:
            continue
        if d["src_asg"] or d["dst_asg"] or not covers(d["src"], allow["src"]):
            continue
        if not covers(d["dst"], allow["dst"] if not allow["dst_asg"] else ["asg"]):
            continue
        hit = False
        for p in allow["protocols"] & d["protocols"]:
            left = _subtract(remaining[p], d["ports"])
            if left != remaining[p]:
                hit = True
            remaining[p] = left
        if hit:
            closers.append((d["name"], d["priority"]))
    left = {p: iv for p, iv in remaining.items() if iv}
    if not closers:
        return {"status": "open", "closedBy": [], "remaining": ""}
    if not left:
        return {"status": "closed", "closedBy": closers, "remaining": ""}
    groups = {}
    for p in ALL_PROTOCOLS:
        if p in left:
            groups.setdefault(fmt_ports(left[p]), []).append(p)
    rem = "; ".join(f"{'/'.join(ps)} {ports}" for ports, ps in groups.items())
    return {"status": "partly closed", "closedBy": closers, "remaining": rem}


def inbound_allows_from_internet(nsg_rules):
    """[(parsed_rule, evaluation)] for every inbound allow whose source is any/Internet."""
    parsed = [parse_rule(r) for r in nsg_rules]
    return [(a, evaluate(a, parsed)) for a in sorted(parsed, key=lambda r: r["priority"])
            if a["direction"] == "inbound" and a["access"] == "allow" and is_open_source(a)]


def names(closers):
    return ", ".join(f"{n} ({p})" for n, p in closers)
