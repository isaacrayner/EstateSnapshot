import json
EM_DASH = chr(0x2014)
import os
import subprocess
import sys

import pytest

import fixtures
from estatesnapshot import brief, compare, design, inventory
from estatesnapshot.model import Estate

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIAGRAMMER = os.environ.get("AZURE_DIAGRAMMER", r"C:\Code\AzureDiagrammer")


# -- E4: compare ---------------------------------------------------------------------------------

def test_environment_words_and_name_patterns():
    assert compare.env_in("vmraddbprod01")[0] == "prod" and compare.env_in("rg-app-preprod-uks")[0] == "preprod"
    assert compare.env_in("latest-build")[0] is None and compare.env_in("product-api")[0] is None and compare.env_in("device")[0] is None
    assert compare.norm_name("vmraddbprod01") == compare.norm_name("vmraddbtest01") == "vmraddb{env}01"
    assert compare.norm_name("func-dev-a") == compare.norm_name("func-prod-a")
    assert compare.norm_name("acrdev") == "acrdev" and compare.norm_name("acrdev", "dev") == "acr{env}"    # own word only
    assert compare.norm_name("latest", "prod") == "latest"


def test_compare_reports_sizing_differences_and_cross_capture_dependencies(tmp_path):
    dev = fixtures.build(str(tmp_path / "dev"), "dev")
    prod = fixtures.build(str(tmp_path / "prod"), "prod")
    # make Prod's plan bigger than Dev's, and Prod's site c pull from Dev's registry (it does already)
    rp = os.path.join(prod, "raw", "resources.json")
    rs = json.load(open(rp))
    next(r for r in rs if r["type"] == "microsoft.web/serverfarms")["sku"] = {"name": "P3v3", "capacity": 4}
    json.dump(rs, open(rp, "w"))
    caps = compare.parse_targets([f"Dev={dev}", f"Prod={prod}"])
    res = compare.environments(caps)
    plan = next(m for m in res["matched"] if m["service"] == "App Service Plan")
    assert plan["differences"]["sku"] == ("P1v3", "P3v3") and plan["differences"]["capacity"] == (2, 4)
    assert any(m["service"] == "Container Registry" and m["aName"] == "acrdev" and m["bName"] == "acrprod" for m in res["matched"])
    fn = [m for m in res["matched"] if m["service"] == "App Service"]
    assert len(fn) == 3 and all(m["aName"].startswith("func-dev") and m["bName"].startswith("func-prod") for m in fn)
    crossing = [r for r in res["cross"] if r["fromEnv"] == "Prod" and r["toEnv"] == "Dev"]
    assert any("func-prod-c" in r["from"] and "acrdev" in r["to"] and "container image" in r["how"] for r in crossing)
    text = compare.render_environments(res, caps)
    assert "P1v3 -> P3v3" in text and "References that cross" in text and EM_DASH not in text


def test_compare_flags_logic_app_calling_another_captures_host(tmp_path):
    dev = fixtures.build(str(tmp_path / "dev"), "dev")
    prod = fixtures.build(str(tmp_path / "prod"), "prod")
    # Dev owns the host that Prod's Logic App connection points at
    rp = os.path.join(dev, "raw", "resources.json")
    rs = json.load(open(rp))
    rs.append(fixtures.res("rg-app-dev", "Microsoft.Storage/storageAccounts", "sftpdev", {"primaryEndpoints": {"blob": "https://sftp-test.partner.example.net/"}}))
    json.dump(rs, open(rp, "w"))
    # (host check uses azure endpoint suffixes, so point the connection at a Dev storage endpoint instead)
    rs[-1]["properties"] = {"primaryEndpoints": {"blob": "https://sftpdev.blob.core.windows.net/"}}
    json.dump(rs, open(rp, "w"))
    rp = os.path.join(prod, "raw", "resources.json")
    rs = json.load(open(rp))
    next(r for r in rs if r["type"] == "microsoft.web/connections")["properties"]["nonSecretParameterValues"]["hostName"] = "sftpdev.blob.core.windows.net"
    json.dump(rs, open(rp, "w"))
    res = compare.environments(compare.parse_targets([f"Dev={dev}", f"Prod={prod}"]))
    assert any(r["fromEnv"] == "Prod" and "logic-prod" in r["from"] and "sftpdev" in r["to"] and r["toEnv"] == "Dev" for r in res["cross"])


def test_drift_between_two_captures_of_one_scope(tmp_path):
    a = fixtures.build(str(tmp_path / "a"), "prod")
    b = fixtures.build(str(tmp_path / "b"), "prod")
    rp = os.path.join(b, "raw", "resources.json")
    rs = json.load(open(rp))
    rs = [r for r in rs if r["name"] != "func-prod-b"]
    next(r for r in rs if r["type"] == "microsoft.compute/virtualmachines")["properties"]["hardwareProfile"]["vmSize"] = "Standard_D4s_v5"
    rs.append(fixtures.res("rg-app-prod", "Microsoft.KeyVault/vaults", "kv-new"))
    json.dump(rs, open(rp, "w"))
    x, y = compare.parse_targets([f"Before={a}", f"After={b}"])
    d = compare.drift(x, y)
    assert [r["name"] for r in d["added"]] == ["kv-new"] and [r["name"] for r in d["removed"]] == ["func-prod-b"]
    assert d["changed"][0]["name"] == "vm-prod-01" and d["changed"][0]["differences"]["size"] == ("Standard_DS2_v2", "Standard_D4s_v5")
    assert "Drift: Before to After" in compare.render_drift(d, x, y)


# -- E8: seed -----------------------------------------------------------------------------------------

def test_seed_shape(prod):
    d = design.seed(prod)
    kinds = {n["id"]: n for n in d["nodes"]}
    assert d["meta"]["diagram_type"] == "component" and d["edges"] == [] and "dataflow" in d["views"]
    grp = next(n for n in d["nodes"] if n.get("instances") and "Function" in n["name"])
    assert [i["name"] for i in grp["instances"]] == ["func-prod-a", "func-prod-b", "func-prod-c"] and kinds[grp["parent"]]["kind"] == "resource_group"
    assert not any(n["name"] == "nsg-aks" for n in d["nodes"])              # network plumbing is left to the network drawing
    assert not any(EM_DASH in json.dumps(n, ensure_ascii=False) for n in d["nodes"])


def test_seed_ids_are_stable_between_runs(tmp_path):
    a = Estate(fixtures.build(str(tmp_path / "a"), "prod"))
    b = Estate(fixtures.build(str(tmp_path / "b"), "prod"))
    assert [n["id"] for n in design.seed(a)["nodes"]] == [n["id"] for n in design.seed(b)["nodes"]]


@pytest.mark.skipif(not os.path.isdir(DIAGRAMMER), reason="AzureDiagrammer not installed")
def test_seed_validates_in_azure_diagrammer(prod, tmp_path):
    p = tmp_path / "seed.json"
    p.write_text(json.dumps(design.seed(prod)))
    r = subprocess.run([sys.executable, os.path.join(DIAGRAMMER, "bin", "validate.py"), str(p)], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "0 error" in r.stdout


# -- backwards compatibility --------------------------------------------------------------------------------

def test_old_snapshot_without_new_raw_files_still_builds(tmp_path):
    d = fixtures.build(str(tmp_path / "old"), "prod", with_config=False)
    m = json.load(open(os.path.join(d, "manifest.json")))
    m.pop("stages")
    json.dump(m, open(os.path.join(d, "manifest.json"), "w"))
    e = Estate(d)
    text = brief.render(e)
    assert "Measured load: Azure Monitor metrics were not collected" in text and "Cosmos DB accounts and throughput" not in text
    inventory.write(e, str(tmp_path / "inv.xlsx"))
    assert e.site_summary()["collected"] == 0


def test_local_test_tenant_capture_still_builds_when_present(tmp_path):
    src = os.path.join(ROOT, "snapshots", "Test-Tenant-2026-10-23")
    if not os.path.isdir(src):
        pytest.skip("no local capture")
    import shutil
    dst = str(tmp_path / "t")
    shutil.copytree(src, dst)
    e = Estate(dst)
    assert "## Headline facts" in brief.render(e)
    assert design.seed(e) is not None
