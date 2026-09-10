# Step14 — Entity Resolution Engineer Review

## Decision

Step14 is complete for bounded local probabilistic entity-resolution evidence.
The next handoff is Step15 Applied ML Engineer. Step15 was not started.

## Implemented boundary

The project now owns ER contracts, privacy authorization, the application
service, and the official Splink adapter. The adapter consumes only project
catalog/snapshot/staging contracts and never reconnects to a source or reruns
discovery/schema matching. Complete staged batches are hash-verified by the
existing staged reader. Raw identity values stay inside private process and
DuckDB work memory and are cleaned up after each run.

## Controls

Blocking rules and pair budgets are explicit and versioned. `LINK_ONLY`,
`DEDUPE_ONLY` and `LINK_AND_DEDUPE` are distinct. Null/placeholder values do
not create positive evidence. Normalization preserves original source data,
does not transliterate or guess meaning/country, and requires explicit phone
context for digit normalization. Edge and cluster outputs are candidate
evidence only; canonical IDs and accepted merges remain downstream review and
finalization responsibilities.

## Evaluation boundary

The benchmark fixture covers exact duplicate, cross-source spelling, email
case, explicit phone policy, common-name nonmatch, placeholder phone, shared
household, missing fields, Persian Unicode, same-source duplicate, transitive
bridge and a clear three-record cluster. Labels are synthetic benchmark labels
and not production truth. Pairwise precision/recall/F1, blocking recall,
false merges, contaminated clusters, largest cluster and threshold sensitivity
are the required evaluation dimensions; formal calibration is deferred to
later evaluation ownership.

## Step13 hardening included in this pass

Schema matching now reports available staged rows independently from instance
rows read and sampled rows. Schema-only mode does not read staging merely to
count rows. Type-pruning reports project-ineligible, provider-visible,
post-provider type-rejected and workload-avoided quantities without claiming
provider-visible pairs were pruned before the provider.

## Remaining limitations

The benchmark is synthetic, the ER output is not calibrated business truth,
and the runtime is optional. Formal G4 and later gates remain pending. The
current evidence is local-only and does not establish production deployment,
human identity acceptance, or downstream canonical finalization.
