# EstateSnapshot - improvement handoff

Date: 28 October 2026. Author: Isaac Rayner. Living version: [Claude Doc](https://claude.ai/artifact/DPhouBFWStvw5FhR547SBe).

## Purpose and context

On the Rascal ADS, most priority-1 findings came from data the snapshot did not collect, gathered by hand with the same Reader access. This handoff sets out the changes that would have produced those findings automatically, in the order to build them.

- **Captures used:** Dev and PreProd on 26/10/2026, Prod on 27/10/2026 (729 resources, 13 raw tables, no collection errors).
- **Hand-collected on top:** `supplementary-prod-extra.json` beside the Prod snapshot, 30 days of Azure Monitor metrics, NSG and Logic App checks, and Log Analytics usage queries.
- **Principle kept:** the snapshot states facts and the ADS interprets them. Every change below collects or derives facts; none scores or recommends.
- **Code read:** `README.md`, `estatesnapshot/collect.py` and the Rascal outputs (`manifest.json`, `estate.md` section 13), 28/10/2026.

## What we collected by hand

Eleven sets of facts were gathered outside the snapshot. Each one fed a recommendation in the ADS NOTES, and seven fed priority-1 or priority-2 items.

| Data collected by hand | What it showed at Rascal | ADS finding it fed | Snapshot gap |
|---|---|---|---|
| Cosmos DB account and database settings | Bounded Staleness on single-region accounts, periodic backup, key authentication on, 400 RU/s shared across seven containers | Throughput model, consistency, continuous backup (priority 1) | Resource Graph holds account properties but not database or container throughput |
| Cosmos DB metrics, 30 days | 42% of transaction and 59% of Prod vault requests throttled; normalised RU at 100% most days; vault about 322 GB | Vault throttling and time-to-live (priority 1) | No metrics stage |
| Service Bus entities and metrics | 41 topics, 53 queues, one partition, topics with no subscriptions, about 948,000 dead letters, test subscriptions in Prod | Concurrency, partitioning, dead-letter handling | Entities are child resources not in the resources table |
| App Service and Functions site configuration | 31 Prod function apps on .NET 8 (support ends 10/11/2026), 19 on 32-bit workers, no health checks, images pulled from the Dev registry | Runtime upgrade (priority 1), deployment safety, Prod registry | siteConfig needs a call per site |
| AKS profiles | Application routing NGINX ingress, kubenet, public API server with no authorised IP ranges | Ingress before November 2026, retirements | Partly in properties; not modelled |
| NSG rules in priority order | Deny rules at 402 to 404 closed the internet path that an auto-generated allow at 501 seemed to open | Edge exposure, stated correctly | Exposure model reads rules one at a time |
| Logic App definitions (connection targets only) | Prod Logic Apps forward test partners' files to PreProd | Test traffic on the Prod SFTP account | Not collected |
| Log Analytics retention, caps and table usage | About 415 GB a month into Prod, AKS audit logs dominant, 30-day retention, no daily caps | Logging fix, the first FinOps quick win | Listed in section 13 as not visible |
| Cost by subscription, including ones not captured | Non-production costs more than production; Pay-As-You-Go subscriptions alongside CSP | FinOps assessment | Cost is per captured subscription only; offer type not shown |
| Peering and remote references | Hub, BI and BUS subscriptions exist but were not visible | Hub resilience, Fabric, backup | Out-of-scope references are not reported |
| Comparison of Dev, PreProd and Prod | Prod sizing differs from PreProd; Prod depends on the Dev registry | Configuration drift, Prod hygiene | Captures cannot be compared |

## Recommendations

Ten changes, all read-only and within Reader unless stated. E1 and E2 together would have produced most of Rascal's priority-1 findings on day one, so build them first.

| ID | Change | Where in the code | Data source | Effort | ADS value |
|---|---|---|---|---|---|
| E1 | **Service configuration collectors.** Cosmos DB: consistency, backup type, disableLocalAuth, public access, and per database and container throughput (shared or dedicated, manual or autoscale, partition key paths). Service Bus: topics, subscriptions, queues, partition count, disableLocalAuth, topics with no subscriptions. Sites: runtime stack and version, use32BitWorkerProcess, alwaysOn, healthCheckPath, functionAppScaleLimit, linuxFxVersion with the registry it names. AKS: ingress profile (web app routing), network plugin, authorised IP ranges. Log Analytics: retention and daily cap. Save each as its own `raw/*.json`, as the existing collectors do | New `collect_config()` in `collect.py`; facts derived in `model.py` sections 5, 6 and 10 | ARM child resources (`sqlDatabases`, `containers`, `throughputSettings`; Service Bus `topics`, `subscriptions`, `queues`; `sites/config/web`) | M | Throughput model, consistency, runtime support, deployment safety, registry, ingress retirement |
| E2 | **Metrics stage.** A fixed 30-day set per type: Cosmos DB TotalRequests split by StatusCode, NormalizedRUConsumption, DataUsage; Service Bus IncomingMessages, DeadletteredMessages, ActiveMessages; App Service plans CpuPercentage and MemoryPercentage; SQL cpu_percent. Report percentages and peaks as facts, with sample coverage | New `metrics.py`; a "Measured load" subsection in `brief.py` | Azure Monitor `metrics:getBatch` (batched by subscription, region and type, which avoids the throttling of per-resource calls) | M | Vault throttling, Cosmos DB sizing, dead-letter handling, plan density |
| E3 | **Referenced but not captured.** List every resource ID in the graph that points to a subscription outside the capture (peerings, remote VNets, private endpoint targets, registries, diagnostic destinations), with the referencing resource | `graph.py` already extracts IDs; add the out-of-scope set to `model.py` and a section in `estate.md` | Existing graph | S | Shows the hub, BI and backup subscriptions before the workshop, so access can be requested |
| E4 | **Compare captures.** `estate_snapshot.py compare <a> <b>`: SKU and instance differences for resources matched by name pattern across environments, and drift between two captures of the same scope. Flag cross-environment dependencies as facts (a Prod site pulling from a non-Prod registry, a Logic App or private endpoint targeting another environment) | New `compare.py`; environment taken from subscription name, tags or a `--env` map | Two snapshot folders, offline | M | Configuration drift, Prod hygiene, test traffic in Prod |
| E5 | **NSG rules in priority order.** Evaluate inbound rules per NIC and subnet NSG in priority order, so an allow shadowed by a higher-priority deny is reported as closed. Add Application Gateway WAF policy mode and rule set version, and a count of sites with public network access | `model.py` section 3 | Existing resources table. The effective-rules API needs an action permission beyond Reader, so compute it offline | M | Edge exposure stated correctly; WAF in detection mode |
| E6 | **Diagnostic settings.** Every setting per resource, not just the last, with destination workspace, storage or event hub. Removes one section-13 item | New collector, limited to types that support settings | `providers/Microsoft.Insights/diagnosticSettings` per resource | M | Logging fix and telemetry volume |
| E7 | **Billing context.** Keep each subscription's quotaId (CSP, Pay-As-You-Go, EA) from the subscription list in `manifest.json` and show it beside cost; Log Analytics top tables by volume through a workspace query; optionally commitment coverage when cost access allows | `azure.py` subscription list; `brief.py` section 11 | Subscription policies; Log Analytics Usage table | S | FinOps assessment, Pay-As-You-Go clean-up, logging quick win |
| E8 | **Seed design for Azure Diagrammer.** Replace the full network drawing with a `component` seed: services grouped by resource group or function, function apps collapsed per plan as instances, private endpoints with `fronts`, stable ids, zone hints, and a `dataflow` view defined. The architect adds the flow | `design.py` | Existing model | M | Saves most of the hand authoring; Rascal went from 54 to 76 nodes by hand |
| E9 | **Shared retirement register and Entra collector with the AHC.** One maintained JSON of retirement dates (Functions .NET 8, application routing NGINX, kubenet, Dsv2, NSG flow logs) matched against the snapshot's facts; an opt-in Microsoft Graph collector for Conditional Access, directory roles and on-premises sync status | Shared file, read by `model.py`; optional `--entra` flag | Learn-sourced register; Graph read scopes | S then ongoing | Retirements table; identity deadlines |
| E10 | **Operability.** Progress output and resume after interruption (a Prod run exceeded a 60-second tool timeout); a one-page "Headline facts" section at the top of `estate.md`; keep any Retail Prices calls on the device, since the cloud workspace cannot reach that endpoint | `estate_snapshot.py`, `brief.py` | - | S | Faster reading of a 66 KB brief; fewer reruns |

Effort: S is days, M is one to three weeks for one engineer; indicative only.

## Build plan

Five phases in order, each closed by a test against the Rascal captures. No calendar dates until phase 1 has shown the cost of the per-resource calls on a real estate.

1. **Configuration and metrics (E1, E2, E10 progress and resume).** Gate: a fresh Rascal Prod run reproduces `supplementary-prod-extra.json` and the 30-day Cosmos DB and Service Bus figures without manual calls.
2. **Scope and comparison (E3, E4).** Gate: the Prod capture lists the hub, BI and backup subscriptions as referenced but not captured; `compare` against the PreProd capture reports the sizing differences and the Dev registry dependency.
3. **Exposure, diagnostics and billing (E5, E6, E7).** Gate: the AKS ingress allow rule is reported as closed by the 402 to 404 denies; diagnostic settings cover every resource that supports them; quotaId and the top Log Analytics tables appear in section 11.
4. **Diagram seed (E8).** Gate: the seed validates in Azure Diagrammer with no errors and needs only flows and labels adding to reach the Rascal rev3 design.
5. **Shared register and Entra (E9, headline facts).** Gate: the retirement table matches the AHC's for the same estate; Entra facts appear only when `--entra` is set and the scopes are granted.

Each phase updates `README.md` (access table and outputs) and `CLAUDE.md`, keeping the read-only rule and the no-customer-data-in-git rule.

## Acceptance tests against Rascal

The Rascal captures are the regression set. They are customer data, so tests read them from the PRESALES folder and the repository holds only sanitised fixtures.

- [ ] Cosmos DB facts: Bounded Staleness, periodic backup and key authentication on every account; vault and transactions at 400 RU/s shared; 429 percentages within a point of the hand-collected 42% and 59%.
- [ ] Service Bus facts: 41 topics, 53 queues, one partition, topics with no subscriptions named, dead-letter counts reported.
- [ ] Site facts: 31 Prod function apps on .NET 8, 19 on 32-bit workers, the apps with no health check, and the Prod sites whose images come from the Dev registry.
- [ ] AKS facts: web app routing on, kubenet, no authorised IP ranges.
- [ ] Exposure: the ingress public IP shown as closed by the deny rules, and the WAF policy in detection mode.
- [ ] Scope: hub, BI and backup subscriptions listed as referenced but not captured.
- [ ] Billing: quotaId shown per subscription; the largest Log Analytics tables listed with volumes.
- [ ] Seed: the design validates and contains every Prod service group drawn in rev3.
- [ ] Runtime: a Prod run completes with progress output, and an interrupted run resumes without recollecting finished tables.

## Open questions

- Should metrics (E2) be on by default, given the extra runtime on large estates, or behind a `--metrics` flag?
- Is a Log Analytics workspace query acceptable under Reader at every customer, or should table usage stay optional?
- Where should the shared retirement register live so that EstateSnapshot and the AHC both read it: one repo, or a small shared package?
- Should the full network drawing stay as a second output beside the component seed (E8)?

## Sources

- EstateSnapshot `README.md` and `estatesnapshot/collect.py`; Rascal snapshots (`manifest.json`, `estate.md` section 13, `supplementary-prod-extra.json`), read 28/10/2026.
- Rascal ADS NOTES.md (v24): measured load, Prod observations, completeness check, FinOps assessment.
- [Migrate from the metrics API to the getBatch API](https://learn.microsoft.com/azure/azure-monitor/metrics/migrate-to-batch-api)
- [Monitor normalised request units in Azure Cosmos DB](https://learn.microsoft.com/azure/cosmos-db/monitor-normalized-request-units)
- [How network security groups filter traffic](https://learn.microsoft.com/azure/virtual-network/network-security-group-how-it-works)
- [Diagnostic settings in Azure Monitor](https://learn.microsoft.com/azure/azure-monitor/essentials/diagnostic-settings)
