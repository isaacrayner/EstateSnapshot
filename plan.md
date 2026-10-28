# EstateSnapshot - plan

Last updated: 28/10/2026. Source: `docs/Improvement-Handoff.md`. Decisions are logged in `CLAUDE.md` (D1 to D15).

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

42 tests pass (`python -m pytest tests`); `design.json` and `design-seed.json` validate with zero errors on the local Test-Tenant capture.

## Next - needs a real estate (cannot be done offline)

The handoff's gates are written against the Rascal captures, which are not reachable from this machine. Nothing below has been run against live Azure.

1. Run `run --resume` against Rascal Prod. Confirm the 30-day Cosmos DB 429 percentages are within a point of 42% and 59%, and compare the Service Bus counts with 41 topics and 53 queues.
2. Check the per-resource call cost on a real estate (the handoff held back calendar dates until this was known): elapsed time per stage is in the output.
3. Confirm Resource Graph returns `ingressProfile` for AKS (otherwise the direct ARM read in `aks_config` is what supplies it) and that the Rascal ingress rule set reads as closed. The synthetic case is partly closed because its allow rule is protocol `*`.
4. Confirm the getBatch calls succeed under the account used (Monitoring Reader on the subscription, Microsoft.Insights registered), and that the Log Analytics `Usage` query works under Reader at that customer.
5. Run `compare Dev=... PreProd=... Prod=...` on the three Rascal captures. Name pairing depends on the real naming convention: check the "no counterpart" list.
6. Run the seed through Azure Diagrammer and compare it with the Rascal rev3 design.
7. Entra: confirm which Graph scopes the Azure CLI token actually carries.

## Open decisions

- Should the retirement register move to a shared package for the AHC, or stay a file both tools point at (D11)?
- Should metrics stay default-on for very large estates (D8)?
- Function apps: in-process or isolated needs app settings (`FUNCTIONS_WORKER_RUNTIME` and friends), which are not read because the call returns secrets. Is a reduced read acceptable?
- Site slots, Event Hubs and Storage account service properties are not collected yet.
