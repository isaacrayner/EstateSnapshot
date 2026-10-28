"""Retirement register: dated platform changes matched against facts in a snapshot.
The register is data (data/retirements.json, or the file named by --retirements or the
ESTATESNAPSHOT_RETIREMENTS environment variable, so another tool can share it). The match
says which resources a dated change applies to and how far off the date is at capture; it
does not say what to do about it."""
import calendar
import datetime as dt
import json
import os
import re

from .util import g, lc

DEFAULT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "retirements.json")


def load(path=None):
    p = path or os.environ.get("ESTATESNAPSHOT_RETIREMENTS") or DEFAULT
    with open(p, encoding="utf-8") as f:
        d = json.load(f)
    d["_path"] = p
    return d


def _when(entry):
    """(display text, date used for the countdown). Month-only dates count to the end of the month."""
    s = entry["date"]
    if entry.get("precision") == "month":
        y, m = map(int, s.split("-"))
        return dt.date(y, m, 1).strftime("%B %Y"), dt.date(y, m, calendar.monthrange(y, m)[1])
    d = dt.date.fromisoformat(s)
    return d.strftime("%d/%m/%Y"), d


# -- matchers: each returns the names of the resources the entry applies to ------------------------

def _function_runtime(e, m):
    return [r["name"] for r in e.site_facts()
            if r["isFunction"] and r["stack"] == m["stack"] and r["major"] == m["major"]]


def _aks_nginx(e, m):
    return [r["name"] for r in e.of("microsoft.containerservice/managedclusters") if e.aks_facts(r)["appRoutingNginx"]]


def _aks_plugin(e, m):
    return [r["name"] for r in e.of("microsoft.containerservice/managedclusters")
            if lc(e.aks_facts(r)["plugin"]) == m["value"]]


def _aks_policy(e, m):
    out = []
    for r in e.of("microsoft.containerservice/managedclusters"):
        p = e.aks_props(r)
        linux = any(lc(a.get("osType")) != "windows" for a in p.get("agentPoolProfiles") or [])
        if lc(e.aks_facts(r)["policy"]) == m["value"] and linux:
            out.append(r["name"])
    return out


def _vm_size(e, m):
    rx = re.compile(m["pattern"])
    out = []
    for r in e.of("microsoft.compute/virtualmachines"):
        if rx.match(lc(g(r, "properties", "hardwareProfile", "vmSize", default=""))):
            out.append(r["name"])
    for r in e.of("microsoft.compute/virtualmachinescalesets"):
        if rx.match(lc(g(r, "sku", "name", default=""))):
            out.append(r["name"] + " (scale set)")
    for r in e.of("microsoft.containerservice/managedclusters"):
        for a in e.aks_props(r).get("agentPoolProfiles") or []:
            if rx.match(lc(a.get("vmSize"))):
                out.append(f"{r['name']}/{a.get('name')} (AKS pool)")
    return out


def _nsg_flow_log(e, m):
    return [r["name"] for r in e.of("microsoft.network/networkwatchers/flowlogs")
            if "/networksecuritygroups/" in lc(g(r, "properties", "targetResourceId", default=""))]


MATCHERS = {"function-runtime": _function_runtime, "aks-app-routing-nginx": _aks_nginx,
            "aks-network-plugin": _aks_plugin, "aks-network-policy": _aks_policy,
            "vm-size": _vm_size, "nsg-flow-log": _nsg_flow_log}


def match(estate, reg):
    """Register entries that apply to this estate, soonest first."""
    captured = dt.date.fromisoformat(estate.manifest["capturedAt"][:10])
    rows = []
    for ent in reg["entries"]:
        fn = MATCHERS.get(ent["match"]["kind"])
        names = sorted(set(fn(estate, ent["match"]))) if fn else []
        if not names:
            continue
        text, d = _when(ent)
        days = (d - captured).days
        rows.append({"change": ent["title"], "date": text, "daysFromCapture": days,
                     "status": "date passed at capture" if days < 0 else "ahead",
                     "affected": len(names), "resources": ", ".join(names[:12]) + (" ..." if len(names) > 12 else ""),
                     "source": ent["source"], "_sort": d})
    return sorted(rows, key=lambda r: r["_sort"])
