"""EstateSnapshot - capture an Azure estate read-only and turn it into ADS inputs.

    python estate_snapshot.py run --customer "Contoso" --tenant <tenant-id> [--subscriptions a,b] [--out DIR]
    python estate_snapshot.py collect ...     stage 1 only (talks to Azure)
    python estate_snapshot.py run ... --resume   carry on an interrupted run in the same --out folder
    python estate_snapshot.py build DIR       stages 2-3 only (offline, re-runnable)
    python estate_snapshot.py compare Dev=DIR PreProd=DIR Prod=DIR     environments, offline
    python estate_snapshot.py compare --drift EARLIER_DIR LATER_DIR    the same scope at two times

Outputs in DIR: raw/ (evidence), graph.json, estate.md (ADS brief), inventory.xlsx (appendix),
design.json + design.drawio (current-state network diagram) and design-seed.json + design-seed.drawio
(component seed for the target-state diagram).
"""
import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys

from estatesnapshot import brief, collect, compare, design, inventory
from estatesnapshot.azure import AzureError
from estatesnapshot.model import Estate

HERE = os.path.dirname(os.path.abspath(__file__))
DIAGRAMMER = os.environ.get("AZURE_DIAGRAMMER", r"C:\Code\AzureDiagrammer")


def default_out(customer):
    slug = re.sub(r"[^A-Za-z0-9]+", "-", customer or "estate").strip("-")
    return os.path.join(HERE, "snapshots", f"{slug}-{dt.date.today().isoformat()}")


def do_collect(a):
    out = a.out or default_out(a.customer)
    subs = [s.strip() for s in a.subscriptions.split(",")] if a.subscriptions else None
    skip = [s.strip() for s in a.skip.split(",") if s.strip()] if a.skip else []
    print(f"Collecting into {out}")
    try:
        collect.run(out, tenant=a.tenant, subscription_ids=subs, customer=a.customer, cost_days=a.cost_days,
                    resume=a.resume, skip=skip, metrics_days=a.metrics_days, with_entra=a.entra,
                    cost_months=a.cost_months)
    except AzureError as e:
        sys.exit(f"Collection stopped: {e}")
    return out


def emit_design(d, out, base, label):
    """Write a design, validate it with AzureDiagrammer and, if it passes, emit the .drawio."""
    dpath = os.path.join(out, base + ".json")
    with open(dpath, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=1)
    print(f"  {base + '.json':<17} {len(d['nodes'])} nodes, {len(d['edges'])} edges")
    if not os.path.isdir(DIAGRAMMER):
        print(f"  {base + '.drawio':<17} skipped (AzureDiagrammer not found at {DIAGRAMMER})")
        return
    v = subprocess.run([sys.executable, os.path.join(DIAGRAMMER, "bin", "validate.py"), dpath], capture_output=True, text=True)
    print(f"  validate {label:<8} " + ("passed" if v.returncode == 0 else "FAILED"))
    if v.stdout.strip():
        print("    " + v.stdout.strip().replace("\n", "\n    "))
    if v.returncode == 0:
        r = subprocess.run([sys.executable, os.path.join(DIAGRAMMER, "bin", "emit_drawio.py"), dpath,
                            "--out", os.path.join(out, base + ".drawio")], capture_output=True, text=True)
        print(f"  {base + '.drawio':<17} " + ("written" if r.returncode == 0 else "FAILED: " + (r.stderr or r.stdout).strip()[-400:]))


def do_build(out, retirements=None):
    if retirements:
        os.environ["ESTATESNAPSHOT_RETIREMENTS"] = retirements
    print(f"Building outputs in {out}")
    e = Estate(out)
    with open(os.path.join(out, "graph.json"), "w", encoding="utf-8") as f:
        json.dump({"nodes": [{"id": k, "name": v.get("name"), "type": v.get("type"), "subscriptionId": v.get("subscriptionId"),
                              "resourceGroup": v.get("resourceGroup")} for k, v in e.nodes.items()],
                   "edges": e.graph.edges}, f, indent=1)
    print(f"  graph.json        {len(e.nodes)} nodes, {len(e.graph.edges)} edges")

    with open(os.path.join(out, "estate.md"), "w", encoding="utf-8") as f:
        f.write(brief.render(e))
    print("  estate.md         written")

    inventory.write(e, os.path.join(out, "inventory.xlsx"))
    print("  inventory.xlsx    written")

    d = design.build(e)
    if d:
        emit_design(d, out, "design", "network")
    else:
        print("  design.json       skipped (no networks in scope)")
    s = design.seed(e)
    if s:
        emit_design(s, out, "design-seed", "seed")
    else:
        print("  design-seed.json  skipped (nothing drawable)")


def do_compare(a):
    try:
        if a.drift:
            if len(a.targets) != 2:
                sys.exit("--drift takes exactly two folders: the earlier capture, then the later one")
            x, y = compare.parse_targets(a.targets)
            text = compare.render_drift(compare.drift(x, y), x, y)
        else:
            if len(a.targets) < 2:
                sys.exit("compare needs at least two captures, e.g. Dev=<folder> Prod=<folder>")
            caps = compare.parse_targets(a.targets)
            labels = [c.label for c in caps]
            if len(set(labels)) != len(labels):
                sys.exit(f"capture labels must differ ({', '.join(labels)}); name them with Label=<folder>")
            text = compare.render_environments(compare.environments(caps), caps)
    except (OSError, ValueError) as e:
        sys.exit(f"compare failed: {e}")
    out = a.out or os.path.join(HERE, "snapshots", "compare-" + dt.date.today().isoformat() + ".md")
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write(text)
    print(f"Comparison written to {out}")


def main():
    sys.stdout.reconfigure(line_buffering=True, encoding="utf-8")
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = p.add_subparsers(dest="cmd", required=True)
    for name in ("run", "collect"):
        c = sp.add_parser(name)
        c.add_argument("--customer", required=True, help="Customer name, used in titles and the output folder")
        c.add_argument("--tenant", help="Tenant ID (recommended when you are signed in to several tenants)")
        c.add_argument("--subscriptions", help="Comma-separated subscription IDs or names; default is all in the tenant")
        c.add_argument("--out", help="Output folder; default snapshots/<customer>-<date>")
        c.add_argument("--cost-days", type=int, default=30)
        c.add_argument("--cost-months", type=int, default=6,
                       help="Whole calendar months of spend to report, plus the current part-month (default 6)")
        c.add_argument("--metrics-days", type=int, default=30, help="Window for Azure Monitor metrics (default 30)")
        c.add_argument("--resume", action="store_true", help="Carry on an interrupted run in --out, keeping finished stages")
        c.add_argument("--skip", help="Comma-separated stages to leave out: " + ", ".join(collect.OPTIONAL))
        c.add_argument("--entra", action="store_true", help="Also read Conditional Access, directory roles and sync status from Microsoft Graph")
        c.add_argument("--retirements", help="Retirement register to match against (default: the one shipped in estatesnapshot/data)")
    b = sp.add_parser("build")
    b.add_argument("dir")
    b.add_argument("--retirements", help="Retirement register to match against")
    k = sp.add_parser("compare", help="Compare captures offline")
    k.add_argument("targets", nargs="+", help="[Label=]folder, two or more; with --drift exactly two (earlier, later)")
    k.add_argument("--drift", action="store_true", help="Treat the two folders as the same scope at two times, paired by resource id")
    k.add_argument("--out", help="Markdown file to write; default snapshots/compare-<date>.md")
    a = p.parse_args()

    if a.cmd == "build":
        do_build(a.dir, a.retirements)
    elif a.cmd == "compare":
        do_compare(a)
    elif a.cmd == "collect":
        do_collect(a)
    else:
        do_build(do_collect(a), a.retirements)


if __name__ == "__main__":
    main()
