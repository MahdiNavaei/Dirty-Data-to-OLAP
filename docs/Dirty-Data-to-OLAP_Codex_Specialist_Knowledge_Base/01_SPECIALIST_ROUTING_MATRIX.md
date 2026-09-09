---
doc_type: specialist_routing_matrix
project: "Dirty Data to OLAP"
---

# Specialist Routing Matrix

Use this file as the first retrieval router when the task does not explicitly name a specialist.

| role_id | Specialist | Strong retrieval cues | File |
|---|---|---|---|
| `technical_lead` | Technical Lead / Engineering Lead | technical lead, engineering lead, architecture governance, cross-cutting decisions, delivery | `01_TECHNICAL_LEAD.md` |
| `product_owner` | Product Manager / Technical Product Owner | product owner, requirements, scope, v1, user workflow | `02_PRODUCT_OWNER.md` |
| `principal_data_architect` | Principal Data Architect | data architecture, canonical model, business key, surrogate key, grain | `03_PRINCIPAL_DATA_ARCHITECT.md` |
| `solution_architect` | Software / Solution Architect | software architecture, solution architecture, module boundaries, adapters, control plane | `04_SOLUTION_ARCHITECT.md` |
| `senior_data_engineer` | Senior Data Engineer | data engineering, ingestion, dlt, staging, parquet | `05_SENIOR_DATA_ENGINEER.md` |
| `olap_engineer` | Data Warehouse / OLAP Engineer | olap, data warehouse, star schema, fact, dimension | `06_OLAP_ENGINEER.md` |
| `database_engineer` | Database Engineer / DBA | database, dba, postgresql, mysql, sql server | `07_DATABASE_ENGINEER_DBA.md` |
| `data_profiling` | Data Profiling Specialist | data profiling, dataprofiler, null ratio, cardinality, distribution | `08_DATA_PROFILING_SPECIALIST.md` |
| `data_quality` | Data Quality Engineer | data quality, completeness, validity, consistency, uniqueness | `09_DATA_QUALITY_ENGINEER.md` |
| `schema_matching` | Schema Matching / Data Integration Research Engineer | schema matching, valentine, column matching, coma, cupid | `10_SCHEMA_MATCHING_ENGINEER.md` |
| `dependency_discovery` | Dependency Discovery / Constraint Mining Engineer | dependency discovery, constraint mining, desbordante, functional dependency, inclusion dependency | `11_DEPENDENCY_DISCOVERY_ENGINEER.md` |
| `entity_resolution` | Entity Resolution / Record Linkage Engineer | entity resolution, record linkage, splink, deduplication, blocking | `12_ENTITY_RESOLUTION_ENGINEER.md` |
| `applied_ml` | Applied ML Engineer | applied ml, ranking, calibration, classification, active learning | `13_APPLIED_ML_ENGINEER.md` |
| `llm_semantic_ai` | LLM / Semantic AI Engineer | llm, semantic ai, business semantics, structured output, prompt | `14_LLM_SEMANTIC_AI_ENGINEER.md` |
| `evidence_fusion` | Evidence Fusion Engineer | evidence fusion, confidence, hypothesis, provenance, conflict resolution | `15_EVIDENCE_FUSION_ENGINEER.md` |
| `canonical_model` | Canonical Data Model Engineer | canonical model, golden record, survivorship, source precedence, canonical entity | `16_CANONICAL_DATA_MODEL_ENGINEER.md` |
| `semantic_layer` | Analytical Model / Semantic Layer Engineer | semantic layer, metrics, analytical model, measures, dimensions | `17_ANALYTICAL_SEMANTIC_LAYER_ENGINEER.md` |
| `backend` | Senior Backend Engineer | backend, fastapi, api, run manager, auth | `18_SENIOR_BACKEND_ENGINEER.md` |
| `distributed_jobs` | Distributed Systems / Job Processing Engineer | distributed systems, jobs, queue, worker, retry | `19_DISTRIBUTED_JOB_PROCESSING_ENGINEER.md` |
| `frontend` | Frontend Engineer | frontend, react, nextjs, ui, review | `20_FRONTEND_ENGINEER.md` |
| `ux_designer` | UX / Product Designer | ux, product design, human in the loop, review flow, uncertainty | `21_UX_PRODUCT_DESIGNER.md` |
| `data_visualization` | Data Visualization Engineer | data visualization, schema graph, lineage, heatmap, network graph | `22_DATA_VISUALIZATION_ENGINEER.md` |
| `qa_automation` | QA Automation Engineer | qa, test automation, integration test, e2e, regression | `23_QA_AUTOMATION_ENGINEER.md` |
| `data_qa` | Data QA Engineer | data qa, reconciliation, row counts, aggregate checks, referential integrity | `24_DATA_QA_ENGINEER.md` |
| `ml_evaluation` | ML Evaluation Engineer | ml evaluation, precision, recall, f1, calibration | `25_ML_EVALUATION_ENGINEER.md` |
| `performance` | Performance Engineer | performance, profiling, latency, throughput, memory | `26_PERFORMANCE_ENGINEER.md` |
| `load_stress` | Load / Stress Test Engineer | load test, stress test, k6, locust, concurrency | `27_LOAD_STRESS_TEST_ENGINEER.md` |
| `app_security` | Application Security Engineer | application security, auth, authorization, owasp, ssrf | `28_APPLICATION_SECURITY_ENGINEER.md` |
| `privacy` | Data Security / Privacy Engineer | privacy, pii, sensitive data, masking, redaction | `29_DATA_SECURITY_PRIVACY_ENGINEER.md` |
| `database_security` | Database Security Specialist | database security, read only, least privilege, sql guard, credentials | `30_DATABASE_SECURITY_SPECIALIST.md` |
| `penetration_tester` | Penetration Tester / Red Team | penetration test, red team, exploit, ssrf, rce | `31_PENETRATION_TESTER_RED_TEAM.md` |
| `devops` | DevOps Engineer | devops, ci cd, docker, build, release | `32_DEVOPS_ENGINEER.md` |
| `sre` | Site Reliability Engineer (SRE) | sre, reliability, slo, incident, availability | `33_SRE.md` |
| `observability` | Observability Engineer | observability, metrics, logs, traces, prometheus | `34_OBSERVABILITY_ENGINEER.md` |
| `data_platform` | Data Platform Engineer | data platform, object storage, metadata store, orchestration, storage | `35_DATA_PLATFORM_ENGINEER.md` |
| `distributed_data` | Distributed Data Engineer | distributed data, spark, ray, partitioning, large scale | `36_DISTRIBUTED_DATA_ENGINEER.md` |
| `chaos_resilience` | Chaos / Resilience Engineer | chaos, resilience, fault injection, worker crash, network failure | `37_CHAOS_RESILIENCE_ENGINEER.md` |
| `compatibility` | Compatibility Test Engineer | compatibility, postgres versions, mysql, sql server, oracle | `38_COMPATIBILITY_TEST_ENGINEER.md` |
| `technical_writer` | Technical Writer | documentation, readme, tutorial, architecture docs, troubleshooting | `39_TECHNICAL_WRITER.md` |
| `developer_experience` | Developer Experience (DX) Engineer | developer experience, dx, cli, setup, local development | `40_DEVELOPER_EXPERIENCE_ENGINEER.md` |
| `domain_expert` | Business / Domain Data Expert | domain expert, business semantics, banking, ecommerce, erp | `41_BUSINESS_DOMAIN_DATA_EXPERT.md` |

## High-value multi-role routes

| Task | Primary | Secondary / reviewer |
|---|---|---|
| Add a SQL source | Senior Data Engineer | DBA, Database Security, Compatibility, QA |
| Hidden FK / key discovery | Dependency Discovery | Profiling, Evidence Fusion, ML Evaluation, Data Architect |
| Cross-source column mapping | Schema Matching | Profiling, Evidence Fusion, Domain Expert |
| Deduplicate customers | Entity Resolution | Privacy, Canonical Model, Data QA, ML Evaluation |
| Build canonical Customer/Product/Order | Canonical Model | Principal Data Architect, Domain Expert, Evidence Fusion |
| Build fact/dim model | OLAP Engineer | Data Architect, Semantic Layer, Data QA, Domain Expert |
| Change confidence / auto-accept | Evidence Fusion | Applied ML, ML Evaluation, Product Owner, Data Architect |
| Add LLM semantic reasoning | LLM/Semantic AI | Privacy, Evidence Fusion, QA, Domain Expert |
| Add or change API | Backend | App Security, Frontend, QA, Distributed Jobs |
| Job retries/cancellation | Distributed Jobs | SRE, Chaos, Observability, QA |
| UI review flow | UX Designer | Frontend, Visualization, Product Owner, Evidence Fusion |
| Load/stress testing | Load Test | Performance, SRE, DBA, Observability |
| Security review | Application Security | DB Security, Privacy, Pen Test, DevOps |
| Production readiness | SRE | DevOps, Observability, Load, Chaos, Security |
| Data correctness release gate | Data QA | QA Automation, OLAP, Canonical, Domain Expert |
| Release / CI | DevOps | DX, QA, Security, Technical Lead |
| Documentation / discoverability | Technical Writer | Product Owner, DX, specialists owning the feature |

## Routing rules

- Prefer the **narrowest owner** for implementation and a second role for independent review when risk is high.
- Data correctness, security, privacy and source safety may block a change even if the feature owner approves it.
- The Technical Lead arbitrates software-boundary conflicts; the Principal Data Architect arbitrates semantic data-model conflicts; Product Owner arbitrates scope/product priority; Domain Expert supplies business truth where algorithms cannot.
