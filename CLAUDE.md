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
- Each diagram is delivered with its PNG beside it (`design.png`, `design-seed.png`), exported through the diagrammer's `bin\export_png.py` and draw.io. Never substitute the preview SVG or a stale image; `--no-png` is for a machine without draw.io.
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
- D16: Spend is collected twice: `costs` (last 30 days, by resource group and service) and `cost_months` (monthly granularity, `--cost-months` whole months plus the current part-month). The brief states the last whole calendar month and the change on the month before; a part-month is labelled and never given a percentage. Both are non-fatal and explained like `costs` (D4).
- D17: Every emitted diagram is also exported as PNG by the diagrammer's `export_png.py`, because the brief and the customer documents embed an image, not a `.drawio`. The export is best effort: a missing draw.io is reported on the PNG line and never stops the build (`--no-png` turns it off). A PNG is customer data, so it stays with the capture.
- D18: The component seed draws a service that Azure deploys into a subnet (VM, scale set, Application Gateway, SQL Managed Instance, AKS and the like) inside that subnet, because the diagrammer treats the first four outside one as an error. Where the capture does not say which subnet, the service is left out and named in the seed note rather than drawn wrongly.
- D19: `overview.json` is a `context` diagram for someone who has never seen the estate: one box per service tier per subscription with a count, a subscription callout with its network share, and edges only where the snapshot holds a reference (internet ingress, on-premises route, cross-tier references, private endpoints). It states no flows it cannot prove and scores nothing; design.json remains the network topology. The `--no-png` note in D17 applies to it too.
- D20: Traffic is measured, never invented. The `flowlogs` stage reads each NSG flow log's newest files from its storage account with a SAS signed by the local az identity (`az storage container generate-sas`), never with an account key, and keeps only the flow tuple, the NSG rule and the decision as at the head of a V2 file. A flow with no address at either end (private endpoint traffic) is counted in the note, not guessed at, and a log that cannot be read is a stated gap (`--skip flowlogs` leaves the stage out). The network drawing is built from VNets, subnets and their NSGs, with measured paths as edges.
