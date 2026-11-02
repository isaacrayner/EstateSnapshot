# EstateSnapshot - plan

Last updated: 01/11/2026. Source: `docs/Improvement-Handoff.md`. Decisions are logged in `CLAUDE.md` (D1 to D20).

## Done (built and tested offline, 28/10/2026)

| ID | Change | Where |
|---|---|---|
| E1 | Service configuration collectors (Cosmos DB, Service Bus, sites, AKS, Log Analytics tables, Logic App targets) | `config.py`, `collect.py`, `model_config.py` |
| E2 | Metrics stage via metrics:getBatch, "Measured load" in the brief | `metrics.py`, `model_config.py`, `brief.py` |
| E3 | Referenced but not captured: summary by subscription plus every reference, including diagnostic destinations and registry hosts | `model_config.scope_gaps`, `model.outside_scope` |
| E4 | `compare` (environments and drift) with cross-capture references | `compare.py`, `estate_snapshot.py` |
| E5 | NSG rules in priority order, per-public-IP rule facts, WAF mode and rule set, public-access site count | `nsg.py`, `model.py` |
| E6 | Every diagnostic setting per resource, coverage by service | `config.diagnostics`, `model_config.diagnostic_settings` |
| E7 | Subscription offer (quotaId), cost by pricing model, Log Analytics ingestion by table | `collect.py`, `model_config.billing` |
| E8 | Component seed beside the network drawing | `design.seed` |
| E9 | Retirement register and opt-in Entra collector | `data/retirements.json`, `register.py`, `entra.py` |
| E10 | Progress output, `--resume`, headline facts | `collect.py`, `model_config.headline` |
| E11 | Spend by calendar month (last 6 whole months plus the current part-month), last month in the headline and section 11, `CostByMonth` sheet | `collect._cost_months`, `model.spend`, `brief.py`, `inventory.py` |
| E12 | PNG export beside every diagram (`export_png.py` and draw.io, best effort, `--no-png`), and a component seed that keeps subnet-deployed services in their subnet or leaves them out when the subnet is not known | `estate_snapshot.py`, `design.py` |
| E13 | High-level estate overview (overview.json / .drawio / .png): subscriptions with their network share, one counted box per service tier, edges only where the snapshot holds a reference | `design.overview` |
| E14 | Measured traffic: the flowlogs stage reads NSG flow log files with a local az SAS; the network drawing is now the VNets and subnets with their NSGs and the measured ingress, egress and subnet-to-subnet paths | `config.flow_log`, `model.measured_flows`, `model.network_overview`, `design.build` |

53 tests pass (`python -m pytest tests`); `overview.json`, `design.json` and `design-seed.json` validate with zero errors on the local Test-Tenant capture and on the synthetic estate. The PNGs export.

## Next - needs a real estate (cannot be done offline)

The handoff's gates are written against the Rascal captures, which are not reachable from this machine. Nothing below has been run against live Azure.

1. Run `run --resume` against Rascal Prod. Confirm the 30-day Cosmos DB 429 percentages are within a point of 42% and 59%, and compare the Service Bus counts with 41 topics and 53 queues.
2. Check the per-resource call cost on a real estate (the handoff held back calendar dates until this was known): elapsed time per stage is in the output.
3. Confirm Resource Graph returns `ingressProfile` for AKS (otherwise the direct ARM read in `aks_config` is what supplies it) and that the Rascal ingress rule set reads as closed. The synthetic case is partly closed because its allow rule is protocol `*`.
4. Confirm the getBatch calls succeed under the account used (Monitoring Reader on the subscription, Microsoft.Insights registered), and that the Log Analytics `Usage` query works under Reader at that customer.
5. Run `compare Dev=... PreProd=... Prod=...` on the three Rascal captures. Name pairing depends on the real naming convention: check the "no counterpart" list.
6. Run the seed and the overview through Azure Diagrammer and compare them with the Rascal rev3 design. Check the tier counts against the brief's section 2 and that the overview reads at one page on a 729-resource estate.
7. Entra: confirm which Graph scopes the Azure CLI token actually carries.

## Open decisions

- Should the retirement register move to a shared package for the AHC, or stay a file both tools point at (D11)?
- Should metrics stay default-on for very large estates (D8)?
- Function apps: in-process or isolated needs app settings (`FUNCTIONS_WORKER_RUNTIME` and friends), which are not read because the call returns secrets. Is a reduced read acceptable?
- Site slots, Event Hubs and Storage account service properties are not collected yet.
