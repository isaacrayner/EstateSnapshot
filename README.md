# EstateSnapshot

Read-only capture of an existing Azure estate, turned into current-state inputs for an ADS (Architectural Design Service).

**The snapshot states facts; the ADS interprets them.** Nothing here scores, rates or recommends.

## Pipeline

```
collect  ──►  raw/*.json  ──►  graph  ──►  model  ──►  estate.md          (current-state brief for the ADS)
(Reader)      (evidence)       (IDs →                  inventory.xlsx    (full lists, ADS appendix)
                               edges)                  design.json       network drawing  ──┐
                                                       design-seed.json  component seed   ──┴► AzureDiagrammer ──► .drawio

compare Dev=... Prod=...   (offline, any two or more captures)  ──►  compare.md
```

1. **Collect** (`estatesnapshot/collect.py`) - whole-table Azure Resource Graph dumps (resources, containers, role assignments, policy, Advisor, Defender, backup, Site Recovery), actual cost, cost by pricing model and actual cost per calendar month from Cost Management, built-in role names from ARM and principal names from Microsoft Graph. Then, per resource, the things Resource Graph does not hold (`config.py`): Cosmos DB databases, containers and throughput; Service Bus topics, subscriptions and queues; site configuration; AKS profile; Log Analytics tables; Logic App connection targets; every diagnostic setting. Then 30 days of Azure Monitor metrics (`metrics.py`) and Log Analytics ingestion by table. Optionally Entra ID (`entra.py`). Read-only; uses your `az login`.
2. **Graph** (`estatesnapshot/graph.py`) - every resource ID found inside another resource's properties becomes an edge. No per-type code, so new services are wired in automatically. Subnets are lifted out of VNets as nodes.
3. **Model** (`model.py`, `model_config.py`, `nsg.py`, `register.py`) - derives architect-level facts: topology, address overlaps, hybrid links, exposure (NSG rules evaluated in priority order), compute, data, throughput, measured load, access, governance, dated platform changes, resilience, monitoring, cost and dependencies.
4. **Emit** - `brief.py`, `inventory.py`, `design.py`.

## Usage

```powershell
az login                                   # Reader on the customer's subscriptions is enough
python estate_snapshot.py run --customer "Contoso" --tenant <tenant-id>
python estate_snapshot.py run --customer "Contoso" --tenant <tenant-id> --subscriptions "Prod,Dev"
python estate_snapshot.py run --customer "Contoso" --tenant <tenant-id> --out "<PRESALES>\Contoso\EstateSnapshot"
python estate_snapshot.py run ... --out <same folder> --resume     # carry on an interrupted run
python estate_snapshot.py run ... --skip metrics,usage              # leave stages out
python estate_snapshot.py run ... --entra                           # also read Entra ID (needs Graph read scopes)
python estate_snapshot.py build snapshots\Contoso-2026-10-23        # rebuild outputs offline, no Azure calls

python estate_snapshot.py compare Dev=snapshots\Contoso-Dev PreProd=snapshots\Contoso-PreProd Prod=snapshots\Contoso-Prod
python estate_snapshot.py compare --drift snapshots\Contoso-2026-09-01 snapshots\Contoso-2026-10-23
```

Default output: `snapshots\<customer>-<date>\`. Point `--out` at the customer's PRESALES folder and the ADS skill reads `estate.md` in Step 1 as a resource export.

A large estate can take longer than a tool timeout. Progress is printed per stage with elapsed time, and every finished stage is recorded in `manifest.json`, so `--resume` (same `--out`, same subscriptions) continues where the run stopped and only refetches resources that had no record. `--skip` takes `config`, `diagnostics`, `metrics` and `usage`; `--metrics-days` changes the 30-day window; `--cost-days` and `--cost-months` change the cost windows (30 days, and 6 whole calendar months plus the current part-month).

## Access needed

| Data | Permission | If missing |
|---|---|---|
| Resources, policy, Advisor, Defender, backup | Reader | Section empty, listed under collection gaps |
| Service configuration, diagnostic settings | Reader (GET reads only; no keys, secrets or app settings) | Stage fails per resource and is listed under collection gaps; `--resume` retries |
| Metrics | Reader, plus Monitoring Reader on the subscription for the batch API; Microsoft.Insights registered | Stage listed under collection gaps with the likely cause |
| Log Analytics ingestion | Read access to the workspace data (`Usage` table query) | Stage listed under collection gaps; `--skip usage` to leave it out |
| Cost, cost by pricing model, spend by month | Cost Management Reader (and cost visibility enabled for CSP subscriptions) | Section 11 says so |
| Principal names | Directory read in the tenant (any member user) | Role assignments show IDs; partner (CSP/GDAP) groups are labelled as such |
| Entra ID (`--entra`) | Graph read: Policy.Read.All, RoleManagement.Read.Directory or Directory.Read.All, Organization.Read.All | Each part listed as a gap; the rest is kept |

## Outputs

| File | Purpose |
|---|---|
| `estate.md` | Current-state brief: headline facts, then 13 sections; section 13 lists what the snapshot cannot see, as workshop prompts |
| `inventory.xlsx` | Every list behind the brief, one sheet each (config, load, diagnostics and Entra sheets exist when collected) |
| `design.json` / `design.drawio` | Current-state network diagram, validated by AzureDiagrammer |
| `design-seed.json` / `design-seed.drawio` | Component-layer starting point for the target state: services grouped by resource group, collapsed instances, private endpoints, stable ids, a `dataflow` view. No flows: the architect adds them |
| `graph.json` | Nodes and edges, for ad hoc questions |
| `raw/`, `manifest.json` | Evidence of what was seen, when, which stages finished, and any collection gaps |
| `compare.md` | From `compare`: paired resources that differ, resources with no counterpart, references that cross from one capture into another |

## What the brief now states that it did not before

- **Throughput and consistency:** Cosmos DB consistency, backup type, key authentication, and shared or dedicated, manual or autoscale throughput per database and container.
- **Messaging:** Service Bus topics, queues, partitioning, topics with no subscriptions, and dead-lettered messages.
- **Sites:** runtime stack and version, 32 or 64-bit workers, health check path, scale limit, and the container registry each image comes from (and whether it is in the capture). Function apps cannot be told apart as in-process or isolated: that is an app setting, which is not read.
- **Measured load:** 429 percentage by Cosmos DB container, normalised RU peaks and days at 100%, Service Bus dead letters, plan CPU and memory, SQL CPU, each with sample coverage.
- **Exposure:** inbound allow rules evaluated against higher-priority denies (closed, partly closed or open), the rules that name each public IP, WAF mode and rule set, and how many sites accept public network access.
- **Scope:** subscriptions outside the capture that resources point at (peerings, private endpoint targets, registries, diagnostic destinations).
- **Logging and billing:** every diagnostic setting, the largest Log Analytics tables, each subscription's offer (Pay-As-You-Go, EA, CSP), cost by pricing model, and actual spend for each of the last six whole calendar months plus the month so far, with the change on the month before.
- **Dated platform changes:** matched from `estatesnapshot/data/retirements.json`; each entry names the Learn page that states the date.

## Tests

```powershell
python -m pip install -r requirements-dev.txt
python -m pytest tests
```

The tests use a synthetic estate built in `tests\fixtures.py` and a fake client, so they need no Azure access and hold no customer data. Real captures stay in the customer's PRESALES folder or `snapshots\`.

## Data handling

Snapshots contain customer data, including names and UPNs of role holders. Keep them in the customer's PRESALES folder or `snapshots\` (excluded by `.gitignore`), never in source control. Logic App URIs are stored without query strings; site configuration, AKS and Cosmos DB reads are field-whitelisted.

## Requirements

Python 3.10+, Azure CLI, `openpyxl`. AzureDiagrammer at `C:\Code\AzureDiagrammer` (or set `AZURE_DIAGRAMMER`) for the `.drawio`.
