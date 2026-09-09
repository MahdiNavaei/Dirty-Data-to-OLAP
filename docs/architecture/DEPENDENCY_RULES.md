# Dirty Data to OLAP — Dependency Rules

## 1. Layer order

From innermost semantic ownership to outer runtime wiring:

1. domain/contracts
2. ports
3. application/services
4. adapters and persistence
5. entrypoints
6. composition

The composition root is an outer wiring boundary and may reference concrete implementations. No inner layer may depend on it.

## 2. Allowed directions

- Entrypoints may call application services and submit StageExecutor requests.
- Application services may depend on domain contracts, ports and application-level policies.
- Ports may depend on domain contracts.
- Adapters may depend on project-owned ports/contracts and their own external engine.
- Persistence implementations may depend on repository ports/contracts and storage libraries.
- Validation may consume artifact/contract ports and materialized target references.
- Composition may import concrete adapters, persistence and executor implementations to wire them.

## 3. Forbidden directions

- Domain/contracts -> adapters, persistence, entrypoints or third-party engines.
- Core/application -> concrete adapters or third-party native types.
- Entrypoints -> Splink, Valentine, dlt, DataProfiler, Desbordante or DuckDB engine APIs directly.
- Cross-stage contracts containing native engine objects.
- Pickle transport for external objects.
- Runtime imports from research/oss.
- Materializer -> source-cleaning implementation or semantic redefinition.
- Compiler -> source mutation or execution.
- Control Store -> raw source/intermediate row ownership.

## 4. Composition rule

Concrete selection occurs once at a composition boundary using explicit configuration and capability results. Services receive ports as dependencies. There is no hidden global locator, singleton adapter registry or import-time optional-engine side effect.

## 5. Verification rule

Step 05 may turn these rules into import-boundary tests. Step 04 validates the logical graph and negative proposals without creating a package or import scaffold.
