# EstateSnapshot - working notes

Read-only Azure estate capture that produces ADS current-state inputs. See README.md for the pipeline and plan.md for what is built and what is next.

## Rules

- UK English spelling throughout (code comments, output text, docs).
- Never use em dashes in any output text, docs or comments. Use a hyphen, a spaced en dash, commas or parentheses.
- Collection is read-only. Never add a call that writes to Azure or Entra. The only POSTs are query-style (Resource Graph, Cost Management, Microsoft Graph getByIds, metrics getBatch, Log Analytics query). Never call a list-keys, list-secrets or app-settings action. `tests/test_collect.py` checks this.
- The brief states facts only. No scores, ratings or recommendations: interpretation belongs to the ADS skill.
- Collect stays dumb (whole tables, raw JSON). Interpretation lives in `model.py` and `model_config.py`, so `build` can be re-run offline.
- New resource types: add to `catalogue.py` (category, friendly name, AzureDiagrammer icon slug if one exists).
- New collectors write their own `raw/<name>.json`, record their stage in `manifest.json` (so `--resume` works) and drop any field that could hold a secret.
- Dated platform changes live in `estatesnapshot/data/retirements.json`, each with the Microsoft Learn page that states the date. Do not add a date you have not read.
- `design.json` and `design-seed.json` must pass `C:\Code\AzureDiagrammer\bin\validate.py` with zero errors.
- Snapshots hold customer data. Never commit `snapshots\`. Tests use only the synthetic estate in `tests\fixtures.py`.
- Keep any Retail Prices calls on the device: the cloud workspace cannot reach that endpoint. (None exist today.)
- Run `python -m pytest tests` and `python -m pyflakes estatesnapshot tests estate_snapshot.py` before saying something works.
- Do not initialise or touch git in this repo.
- Keep CLAUDE.md and .github/copilot-instructions.md identical, with the UK spelling and no-em-dash rules explicit.

## Decisions

- D1: Resource Graph whole-table dumps, not curated queries, so nothing needs re-collecting when a new question comes up.
- D2: Relationships come from resource IDs inside properties (generic graph), not per-type code.
- D3: Built-in role names come from the ARM role definitions API, because Resource Graph only holds custom roles.
- D4: Cost Management failures are non-fatal and explained (CSP cost visibility, permissions, throttling).
- D5: Out-of-scope references hide platform internals (Bastion and gateway instances, Container Apps infrastructure) and references to deleted resources.
- D6: Child resources (Cosmos DB databases and containers, Service Bus entities, site configuration, AKS, Log Analytics tables, Logic App targets, diagnostic settings) are read per resource from ARM by `config.py`, on a small thread pool, because Resource Graph does not hold them. Fetchers whitelist fields, so app settings, keys and service principal secrets are never stored.
- D7: Metrics use the metrics:getBatch data plane, grouped by subscription, region and type (50 per call), hourly points over 30 days, saved raw. Percentages and peaks are worked out in the model.
- D8: Configuration, diagnostics, metrics and Log Analytics usage run by default and are left out with `--skip`. Each is non-fatal. The handoff left metrics default-on or flagged as open; default-on was chosen because the Rascal priority-1 findings needed them, and `--skip metrics` is one flag.
- D9: Every stage is checkpointed in `manifest.json` (`stages`: done, partial or failed). `--resume` keeps finished stages and, inside a per-resource stage, only refetches resources with no record or an error record.
- D10: NSG exposure is computed offline in priority order (`nsg.py`) because the effective-rules API needs an action beyond Reader. A deny shadows an allow only when it provably covers it (source, destination, protocol, ports); application security groups never shadow, so doubt leaves a rule open.
- D11: The retirement register is a JSON file in this repo, overridable with `--retirements` or `ESTATESNAPSHOT_RETIREMENTS`, so the AHC can read the same file. Whether it should move to a shared package is still open.
- D12: `design-seed.json` (component layer, no flows) is written beside `design.json` (network drawing). The full network drawing stays as a second output.
- D13: Logic App collection stores connection targets only: URIs with the query string removed, connection names resolved to connection resource ids, and paths. Authentication blocks, headers and bodies are never read.
- D14: `compare` pairs resources across captures by type and by name with the environment word removed. A capture's own environment word is also accepted inside a longer word. Cross-capture references are stated as facts, never rated.
- D15: Entra collection is opt-in (`--entra`), each Graph call is independent, and a refusal is a listed gap. The Azure CLI token often lacks Policy.Read.All.
