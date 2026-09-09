---
doc_type: knowledge_base_index
project: "Dirty Data to OLAP"
repository: "Dirty-Data-to-OLAP"
version: "1.0"
---

# Dirty Data to OLAP — Codex Specialist Knowledge Base

This bundle is designed to act as a **retrieval / RAG source for Codex** while building Dirty Data to OLAP. It contains one specialist playbook for every discipline identified in the full engineering-team design, plus shared routing and execution rules.

## What this pack is for

A prompt such as:

- "fix hidden FK discovery"
- "review our SCD2 model"
- "stress-test workers"
- "check SSRF in database connectors"
- "improve entity resolution"
- "validate source-to-fact revenue"

should retrieve the relevant specialist document and give Codex a project-specific operating manual rather than a generic persona.

## Directory layout

```text
Dirty-Data-to-OLAP_Codex_Specialist_Knowledge_Base/
├── 00_README.md
├── 01_SPECIALIST_ROUTING_MATRIX.md
├── 02_GLOBAL_CODEX_EXECUTION_PROTOCOL.md
├── 03_SHARED_PROJECT_INVARIANTS.md
├── 04_SPECIALIST_ACTIVATION_TEMPLATE.md
├── 99_REVIEW_AND_QUALITY_AUDIT.md
├── base_reports/
│   ├── 01_PROJECT_SCOPE_AND_REQUIREMENTS.md
│   ├── ...
│   └── 08_BENCHMARK_AND_VALIDATION_PLAN.md
└── specialists/
    ├── 01_TECHNICAL_LEAD.md
    ├── ...
    └── 41_BUSINESS_DOMAIN_DATA_EXPERT.md
```

## Retrieval priority

1. Retrieve the **specialist playbook** matching the task.
2. Retrieve the relevant **base report(s)** for authoritative architecture/contract/scope details.
3. If the task crosses specialties, retrieve at most the few roles that own the disputed boundary.
4. Inspect actual repository code/tests before editing. Documentation is an operating contract, not proof that implementation currently follows it.

## Naming note

The base reports bundled here have been normalized to the final project name **Dirty Data to OLAP** so Codex sees a single consistent system identity.

## Core product invariant

> Fragmented / dirty tabular sources → evidence-backed understanding → reviewed canonical model → explicit fact/dimension grain → validated OLAP-ready analytical layer.

V1 is **not** a full CDC/streaming platform, arbitrary unstructured-data platform, or fully autonomous destructive data-cleaning system.

## Specialist count

This pack contains **41 specialist playbooks**. One human or one Codex session may cover multiple roles, but the responsibility boundaries remain separate so reviews and RAG retrieval stay precise.
