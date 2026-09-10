"""Project-owned dependency boundary for native or local Docker Desbordante."""
from __future__ import annotations

import base64, csv, json, math, shutil, subprocess, time
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol, Sequence

from dirty_data_to_olap.adapters.dependencies.staged import DependencyInputIntegrityError, DependencyStagedReader
from dirty_data_to_olap.domain.contracts.dependency import (
    DependencyAuthorization, DependencyCapability, DependencyCapabilityStatus,
    DependencyEvidenceState, DependencyFailure, DependencyFailureKind, DependencyKind,
    DependencyMetricObservation, DependencyObservationScope, DependencyProvenance,
    DependencyRequest, DependencyResult, DependencySearchStats, DependencyStageStatus,
    DependencyViolation, FunctionalDependencyEvidence, InclusionDependencyEvidence,
    KeyCandidate, NullPolicy, RelationshipCandidate, UniqueColumnCombinationEvidence,
    dependency_config_hash, dependency_id,
)
from dirty_data_to_olap.domain.contracts.source import AdapterReference, SourceCatalog, SourceSnapshotResult, TableObservationStatus


@dataclass(frozen=True)
class ProviderUCC:
    indices: tuple[int, ...]


@dataclass(frozen=True)
class ProviderFD:
    lhs_indices: tuple[int, ...]
    rhs_index: int
    native_metric_value: float | None = None


@dataclass(frozen=True)
class ProviderIND:
    left_table_index: int
    left_column_indices: tuple[int, ...]
    right_table_index: int
    right_column_indices: tuple[int, ...]
    native_metric_value: float | None = None


class ProviderTimeout(TimeoutError):
    pass


class DependencyEngine(Protocol):
    name: str
    version: str
    runtime_identity: str
    def discover_ucc(self, path: Path, *, max_arity: int) -> Sequence[ProviderUCC]: ...
    def discover_fd(self, path: Path, *, approximate: bool, max_error: float, max_lhs: int) -> Sequence[ProviderFD]: ...
    def discover_ind(self, paths: Sequence[Path], *, approximate: bool, max_error: float, max_arity: int) -> Sequence[ProviderIND]: ...


class DesbordantePythonEngine:
    name = "desbordante"
    runtime_identity = "local-python-binding"

    def __init__(self, module: Any) -> None:
        self._module = module
        self.version = str(getattr(module, "__version__", "2.4.1"))
        self.runtime_identity = f"python-binding:{self.version}"

    @classmethod
    def try_create(cls) -> "DesbordantePythonEngine | None":
        try:
            import desbordante  # type: ignore[import-not-found]
        except Exception:
            return None
        return cls(desbordante)

    @staticmethod
    def _table(path: Path) -> tuple[str, str, bool]:
        return str(path), ",", True

    def discover_ucc(self, path: Path, *, max_arity: int) -> Sequence[ProviderUCC]:
        algo = self._module.ucc.algorithms.Default()
        algo.load_data(table=self._table(path)); algo.execute(max_arity=max_arity)
        return tuple(ProviderUCC(tuple(int(v) for v in item.indices)) for item in algo.get_uccs())

    def discover_fd(self, path: Path, *, approximate: bool, max_error: float, max_lhs: int) -> Sequence[ProviderFD]:
        algo = self._module.afd.algorithms.Tane() if approximate else self._module.fd.algorithms.HyFD()
        algo.load_data(table=self._table(path))
        algo.execute(error=max_error, max_lhs=max_lhs) if approximate else algo.execute(max_lhs=max_lhs)
        output = []
        for item in algo.get_fds():
            getter = getattr(item, "get_error", None)
            output.append(ProviderFD(tuple(int(v) for v in item.lhs_indices), int(item.rhs_index), float(getter()) if callable(getter) else None))
        return tuple(output)

    def discover_ind(self, paths: Sequence[Path], *, approximate: bool, max_error: float, max_arity: int) -> Sequence[ProviderIND]:
        module = self._module.aind if approximate else self._module.ind
        algo = module.algorithms.Mind() if approximate else module.algorithms.Spider()
        algo.load_data(tables=[self._table(path) for path in paths])
        algo.execute(error=max_error, max_arity=max_arity) if approximate else algo.execute(max_arity=max_arity)
        output = []
        for item in algo.get_inds():
            lhs, rhs = item.get_lhs(), item.get_rhs(); getter = getattr(item, "get_error", None)
            output.append(ProviderIND(int(lhs.table_index), tuple(int(v) for v in lhs.column_indices), int(rhs.table_index), tuple(int(v) for v in rhs.column_indices), float(getter()) if callable(getter) else None))
        return tuple(output)


class DesbordanteDockerEngine:
    """Controlled local provider process; image is externally/local provisioned."""
    name = "desbordante-docker"
    image = "dirty-data-to-olap-desbordante-step12:latest"
    source_revision = "b211961f3f272ed8815ef1ffbda90573b11e1116"

    def __init__(self, image_digest: str) -> None:
        self.image_digest = image_digest; self.version = "2.4.1"; self.timeout_seconds = 120.0
        self.runtime_identity = f"image:{self.image}@{image_digest};source:{self.source_revision};binding:build/src/python_bindings"

    @classmethod
    def try_create(cls) -> "DesbordanteDockerEngine | None":
        try:
            result = subprocess.run(["docker", "image", "inspect", cls.image, "--format", "{{.Id}}"], capture_output=True, text=True, check=True, timeout=10)
        except (OSError, subprocess.SubprocessError):
            return None
        digest = result.stdout.strip()
        return cls(digest) if digest.startswith("sha256:") else None

    def _run(self, kind: str, paths: Sequence[Path], options: dict[str, Any]) -> list[dict[str, Any]]:
        root = paths[0].parent.resolve()
        container_paths = [f"/input/{path.name}" for path in paths]
        script = r'''import json,sys
sys.path.insert(0,"build/src/python_bindings")
import desbordante
kind=sys.argv[1]; paths=json.loads(sys.argv[2]); opts=json.loads(sys.argv[3])
def table(path): return (path,",",True)
out=[]
if kind=="ucc":
 a=desbordante.ucc.algorithms.Default(); a.load_data(table=table(paths[0])); a.execute(max_arity=opts["max_arity"])
 for x in a.get_uccs(): out.append({"indices":[int(v) for v in x.indices]})
elif kind=="fd":
 a=(desbordante.afd.algorithms.Tane() if opts["approximate"] else desbordante.fd.algorithms.HyFD()); a.load_data(table=table(paths[0]))
 if opts["approximate"]: a.execute(error=opts["max_error"],max_lhs=opts["max_lhs"])
 else: a.execute(max_lhs=opts["max_lhs"])
 for x in a.get_fds():
  fn=getattr(x,"get_error",None); out.append({"lhs_indices":[int(v) for v in x.lhs_indices],"rhs_index":int(x.rhs_index),"native_metric_value":float(fn()) if callable(fn) else None})
else:
 a=(desbordante.aind.algorithms.Mind() if opts["approximate"] else desbordante.ind.algorithms.Spider()); a.load_data(tables=[table(x) for x in paths])
 if opts["approximate"]: a.execute(error=opts["max_error"],max_arity=opts["max_arity"])
 else: a.execute(max_arity=opts["max_arity"])
 for x in a.get_inds():
  l=x.get_lhs(); r=x.get_rhs(); fn=getattr(x,"get_error",None); out.append({"left_table_index":int(l.table_index),"left_column_indices":[int(v) for v in l.column_indices],"right_table_index":int(r.table_index),"right_column_indices":[int(v) for v in r.column_indices],"native_metric_value":float(fn()) if callable(fn) else None})
print(json.dumps(out,separators=(",",":")))'''
        command = ["docker","run","--rm","--network","none","--read-only","--mount",f"type=bind,source={root},target=/input,readonly",self.image,"python","-c",script,kind,json.dumps(container_paths),json.dumps(options)]
        try:
            result = subprocess.run(command, capture_output=True, text=True, check=True, timeout=max(self.timeout_seconds, 0.1))
        except subprocess.TimeoutExpired as error:
            raise ProviderTimeout(f"Desbordante Docker provider exceeded {self.timeout_seconds:g}s") from error
        except subprocess.CalledProcessError as error:
            detail = (error.stderr or "provider process failed").strip().replace("\n", " ")
            raise RuntimeError(f"Desbordante Docker provider failed: {detail[:240]}") from error
        payload = json.loads(result.stdout.strip())
        if not isinstance(payload, list): raise RuntimeError("provider returned invalid observations")
        return payload

    def discover_ucc(self, path: Path, *, max_arity: int) -> Sequence[ProviderUCC]:
        return tuple(ProviderUCC(tuple(x["indices"])) for x in self._run("ucc",[path],{"max_arity":max_arity}))

    def discover_fd(self, path: Path, *, approximate: bool, max_error: float, max_lhs: int) -> Sequence[ProviderFD]:
        options = {"approximate":approximate,"max_error":max_error,"max_lhs":max_lhs}
        return tuple(ProviderFD(tuple(x["lhs_indices"]),x["rhs_index"],x.get("native_metric_value")) for x in self._run("fd",[path],options))

    def discover_ind(self, paths: Sequence[Path], *, approximate: bool, max_error: float, max_arity: int) -> Sequence[ProviderIND]:
        options = {"approximate":approximate,"max_error":max_error,"max_arity":max_arity}
        return tuple(ProviderIND(x["left_table_index"],tuple(x["left_column_indices"]),x["right_table_index"],tuple(x["right_column_indices"]),x.get("native_metric_value")) for x in self._run("ind",paths,options))


_AUTO_ENGINE = object()


class DesbordanteDependencyAdapter:
    name = "desbordante-adapter"
    adapter_reference = AdapterReference(name=name, version="2.0", config_fingerprint="dependency-adapter-v2")

    def __init__(self, *, project_root: Path, engine: DependencyEngine | None | object = _AUTO_ENGINE, reader: DependencyStagedReader | None = None, privacy_policy: Any | None = None) -> None:
        self.project_root = project_root.resolve()
        self.engine = (DesbordantePythonEngine.try_create() or DesbordanteDockerEngine.try_create()) if engine is _AUTO_ENGINE else engine
        self.reader = reader or DependencyStagedReader(); self.privacy_policy = privacy_policy
        self._provider_calls = 0; self._evaluated_pairs = 0

    def capability(self, request: DependencyRequest) -> DependencyCapability:
        if self.engine is None:
            return DependencyCapability(capability_id="dependencies.adapter",status=DependencyCapabilityStatus.UNAVAILABLE,engine="desbordante",requested_kinds=request.requested_kinds,detail="No importable Desbordante binding or provisioned local Docker image is available")
        return DependencyCapability(capability_id="dependencies.adapter",status=DependencyCapabilityStatus.AVAILABLE,engine=self.engine.name,engine_version=self.engine.version,requested_kinds=request.requested_kinds,detail=f"real provider boundary available ({getattr(self.engine, 'runtime_identity', 'test/provider-boundary')})")

    def discover(self, request: DependencyRequest, catalog: SourceCatalog, snapshot_result: SourceSnapshotResult, *, profiles: Any | None = None, artifact_root: Path | None = None, authorization: DependencyAuthorization | None = None) -> DependencyResult:
        try: self._validate_entry(request,catalog,snapshot_result,authorization)
        except ValueError as error:
            capability = self.capability(request)
            return self._failed_result(request, (), snapshot_result, [DependencyFailure(failure_id=dependency_id("dep_failure","entry"),request_id=request.request_id,kind=DependencyFailureKind.PRIVACY_BLOCKED if "authorization" in str(error) else DependencyFailureKind.INPUT_INVALID,detail=str(error))], capability)
        requested_tables = tuple(t for t in catalog.tables if t.table_id in request.selected_table_ids)
        selected = requested_tables[:request.search_policy.max_tables]
        all_columns = {t.table_id:tuple(c for c in catalog.columns if c.table_id == t.table_id) for t in requested_tables}
        columns_by_table = {t.table_id:all_columns[t.table_id][:request.search_policy.max_columns_per_table] for t in selected}
        rows_by_table, refs_by_table, failures = {}, {}, []
        try:
            for table in selected:
                rows, refs = [], []
                for item in self.reader.iter_table(snapshot_result,catalog,table,[c.physical_name for c in columns_by_table[table.table_id]],project_root=self.project_root):
                    rows.append(item.values); refs.append(item.record_ref)
                rows_by_table[table.table_id], refs_by_table[table.table_id] = rows, refs
        except DependencyInputIntegrityError as error:
            failures.append(DependencyFailure(failure_id=dependency_id("dep_failure",str(error)),request_id=request.request_id,kind=DependencyFailureKind.STAGED_INPUT_INTEGRITY_FAILED,detail=str(error)))
            return self._failed_result(request,selected,snapshot_result,failures,self.capability(request))
        scope = self._scope(request,snapshot_result,selected); stats = self._search_stats(request,requested_tables,selected,all_columns,columns_by_table); capability = self.capability(request)
        if stats.tables_excluded_by_bound or stats.columns_excluded_by_bound:
            failures.append(DependencyFailure(failure_id=dependency_id("dep_failure","search-scope-bound"),request_id=request.request_id,kind=DependencyFailureKind.BUDGET_EXCEEDED,detail="configured search bound excluded input scope; result is incomplete"))
        if stats.candidate_pairs_before_pruning > request.search_policy.max_column_pairs:
            failures.append(DependencyFailure(failure_id=dependency_id("dep_failure","column-pair-budget"),request_id=request.request_id,kind=DependencyFailureKind.BUDGET_EXCEEDED,detail="column-pair budget exceeded; IND search was not started"))
            stats=stats.model_copy(update={"pruned_by_budget":stats.candidate_pairs_before_pruning-request.search_policy.max_column_pairs,"pruned_column_pairs":stats.pruned_by_budget+stats.pruned_by_scope,"completeness":"INCOMPLETE"})
            return self._failed_result(request,selected,snapshot_result,failures,capability,scope=scope,stats=stats)
        if capability.status is not DependencyCapabilityStatus.AVAILABLE:
            failures.append(DependencyFailure(failure_id=dependency_id("dep_failure","capability"),request_id=request.request_id,kind=DependencyFailureKind.CAPABILITY_UNAVAILABLE,detail=capability.detail,retryable=True))
            return self._failed_result(request,selected,snapshot_result,failures,capability,scope=scope,stats=stats)
        temp_root = self._temp_root(request); temp_root.mkdir(parents=True,exist_ok=False); started=time.monotonic()
        self._provider_calls=0; self._evaluated_pairs=0
        if isinstance(self.engine,DesbordanteDockerEngine): self.engine.timeout_seconds=request.search_policy.max_runtime_seconds
        try:
            paths={t.table_id:self._write_engine_input(temp_root,t,columns_by_table[t.table_id],rows_by_table[t.table_id],request.null_policy) for t in selected}
            provenance=lambda algorithm,ids: DependencyProvenance(source_id=request.source_id,snapshot_id=request.snapshot_id,table_ids=tuple(ids),engine=self.engine.name,engine_version=self.engine.version,algorithm=algorithm,algorithm_config_hash=dependency_config_hash(request),adapter=self.adapter_reference,created_at=datetime.now(timezone.utc),null_semantics=self._null_semantics(request.null_policy),provider_runtime=getattr(self.engine, 'runtime_identity', 'test/provider-boundary'))
            ucc, keys=self._discover_keys(request,selected,columns_by_table,rows_by_table,paths,scope,provenance,failures)
            fds=self._discover_fds(request,selected,columns_by_table,rows_by_table,refs_by_table,paths,scope,provenance,failures)
            inds, rels=self._discover_inds(request,selected,columns_by_table,rows_by_table,refs_by_table,paths,scope,provenance,failures)
            emitted=len(ucc)+len(keys)+len(fds)+len(inds)+len(rels); runtime=self._runtime_exceeded(started,request)
            if runtime and not any(f.kind is DependencyFailureKind.BUDGET_EXCEEDED for f in failures): failures.append(self._budget_failure(request,"runtime-budget"))
            reasons=list(stats.truncation_reasons)
            if emitted>request.search_policy.max_output_candidates: reasons.append("max_output_candidates")
            if runtime: reasons.append("max_runtime_seconds")
            truncated=bool(stats.truncated or reasons)
            stats=stats.model_copy(update={"provider_calls":self._provider_calls,"evaluated_pairs":self._evaluated_pairs,"truncated":truncated,"output_truncation":emitted>request.search_policy.max_output_candidates,"runtime_timeout":runtime,"truncation_reasons":tuple(dict.fromkeys(reasons)),"emitted_candidates":min(emitted,request.search_policy.max_output_candidates),"completeness":"INCOMPLETE" if truncated or failures or not all(scope.complete_by_table.values()) else "COMPLETE"})
            remaining=request.search_policy.max_output_candidates
            capped_ucc=tuple(ucc[:remaining]); remaining-=len(capped_ucc); capped_keys=tuple(keys[:remaining]); remaining-=len(capped_keys); capped_fds=tuple(fds[:remaining]); remaining-=len(capped_fds); capped_inds=tuple(inds[:remaining]); remaining-=len(capped_inds); capped_rels=tuple(rels[:remaining])
            result=DependencyResult(request=request,observation_scope=scope,ucc_evidence=capped_ucc,key_candidates=capped_keys,functional_dependencies=capped_fds,inclusion_dependencies=capped_inds,relationship_candidates=capped_rels,failures=tuple(failures),capabilities=(capability,),search_stats=stats,status=DependencyStageStatus.INCOMPLETE if stats.completeness!="COMPLETE" else DependencyStageStatus.COMPLETE)
            if artifact_root is not None:
                from .artifacts import DependencyArtifactStore
                result=DependencyArtifactStore(self.project_root).publish(result,run_root=artifact_root)
            return result
        except ProviderTimeout:
            failures.append(self._budget_failure(request,"runtime-timeout"))
            return self._failed_result(request,selected,snapshot_result,failures,capability,scope=scope,stats=stats.model_copy(update={"provider_calls":self._provider_calls,"runtime_timeout":True,"truncated":True,"completeness":"INCOMPLETE","truncation_reasons":tuple(dict.fromkeys((*stats.truncation_reasons,"max_runtime_seconds")))}))
        except Exception:
            failures.append(DependencyFailure(failure_id=dependency_id("dep_failure","engine"),request_id=request.request_id,kind=DependencyFailureKind.ENGINE_FAILED,detail="dependency engine failed before a complete project-owned result was available"))
            return self._failed_result(request,selected,snapshot_result,failures,capability,scope=scope,stats=stats.model_copy(update={"provider_calls":self._provider_calls,"completeness":"INCOMPLETE"}))
        finally: shutil.rmtree(temp_root,ignore_errors=True)

    def _runtime_exceeded(self, started, request): return time.monotonic()-started > request.search_policy.max_runtime_seconds

    def _discover_keys(self,request,tables,columns_by_table,rows_by_table,paths,scope,provenance,failures):
        if DependencyKind.UCC not in request.requested_kinds: return [],[]
        evidence,keys=[],[]
        for table in tables:
            try:
                self._provider_calls+=1; native=self.engine.discover_ucc(paths[table.table_id],max_arity=request.search_policy.max_ucc_arity); columns=columns_by_table[table.table_id]
                for item in native:
                    indices=tuple(item.indices)
                    if not indices or len(indices)>request.search_policy.max_ucc_arity or any(i>=len(columns) for i in indices): continue
                    ids=tuple(columns[i].column_id for i in indices); physical=tuple(columns[i].physical_name for i in indices); rows=rows_by_table[table.table_id]; eligible=[r for r in rows if _row_eligible(r,columns,request.null_policy)]; distinct=len({tuple(r.get(c) for c in physical) for r in eligible}); ratio=distinct/len(eligible) if eligible else 0.0; evidence_id=dependency_id("ucc",[table.table_id,ids])
                    item=UniqueColumnCombinationEvidence(evidence_id=evidence_id,source_id=request.source_id,snapshot_id=request.snapshot_id,table_id=table.table_id,column_ids=ids,uniqueness_ratio=ratio,physical_missing_ratio=(len(rows)-len(eligible))/len(rows) if rows else 0.0,duplicate_count=max(len(eligible)-distinct,0),observation_scope=scope,state=DependencyEvidenceState.OBSERVED,provenance=provenance("UCC.Default",[table.table_id])); evidence.append(item)
                    keys.append(KeyCandidate(candidate_id=dependency_id("key",[table.table_id,ids]),source_id=request.source_id,snapshot_id=request.snapshot_id,table_id=table.table_id,columns=ids,ucc_evidence_id=evidence_id,uniqueness_ratio=ratio,physical_missing_ratio=item.physical_missing_ratio,duplicate_count=item.duplicate_count,observation_scope=scope,state=DependencyEvidenceState.OBSERVED,evidence_refs=(evidence_id,),provenance=item.provenance))
            except ProviderTimeout: raise
            except Exception: failures.append(DependencyFailure(failure_id=dependency_id("dep_failure",["ucc",table.table_id]),request_id=request.request_id,kind=DependencyFailureKind.ENGINE_FAILED,detail="UCC discovery failed for the selected table",table_id=table.table_id))
        return evidence,keys

    def _discover_fds(self,request,tables,columns_by_table,rows_by_table,refs_by_table,paths,scope,provenance,failures):
        output=[]
        for approximate,kind in ((False,DependencyKind.FD),(True,DependencyKind.AFD)):
            if kind not in request.requested_kinds: continue
            for table in tables:
                try:
                    self._provider_calls+=1; native=self.engine.discover_fd(paths[table.table_id],approximate=approximate,max_error=request.search_policy.max_error_ratio,max_lhs=request.search_policy.max_fd_lhs_arity); columns=columns_by_table[table.table_id]
                    for item in native:
                        lhs,rhs=tuple(item.lhs_indices),int(item.rhs_index)
                        if not lhs or len(lhs)>request.search_policy.max_fd_lhs_arity or any(i>=len(columns) for i in (*lhs,rhs)): continue
                        det=tuple(columns[i].column_id for i in lhs); dep=(columns[rhs].column_id,); pdet=tuple(columns[i].physical_name for i in lhs); pdep=(columns[rhs].physical_name,); metrics=_fd_metrics(rows_by_table[table.table_id],refs_by_table[table.table_id],columns,pdet,pdep,request.null_policy); observation=_native_metric(kind,item.native_metric_value,request.search_policy.max_error_ratio); violation=DependencyViolation(violation_id=dependency_id("violation",[table.table_id,det,dep]),kind=kind,record_refs=tuple(metrics["refs"][:20]),count=metrics["violations"],detail_code="FD_DETERMINANT_HAS_MULTIPLE_DEPENDENT_VALUES")
                        output.append(FunctionalDependencyEvidence(evidence_id=dependency_id("fd",[table.table_id,det,dep,approximate]),source_id=request.source_id,snapshot_id=request.snapshot_id,table_id=table.table_id,determinant=det,dependent=dep,approximate=approximate,support_ratio=metrics["support"],error_ratio=metrics["error"],violation_count=metrics["violations"],violations=(violation,) if metrics["violations"] else (),project_metric_name="distinct_rhs_alternatives_excess_per_eligible_row",project_metric_definition="sum(max(distinct dependent values per determinant - 1, 0)) / eligible rows",metric_observations=(observation,) if observation else (),null_policy=request.null_policy,observation_scope=scope,state=DependencyEvidenceState.OBSERVED,provenance=provenance("AFD.Tane" if approximate else "FD.HyFD",[table.table_id])))
                except ProviderTimeout: raise
                except Exception: failures.append(DependencyFailure(failure_id=dependency_id("dep_failure",["fd",table.table_id,approximate]),request_id=request.request_id,kind=DependencyFailureKind.ENGINE_FAILED,detail="functional dependency discovery failed for the selected table",table_id=table.table_id))
        return output

    def _discover_inds(self,request,tables,columns_by_table,rows_by_table,refs_by_table,paths,scope,provenance,failures):
        output,relationships=[],[]
        for approximate,kind in ((False,DependencyKind.IND),(True,DependencyKind.APPROXIMATE_IND)):
            if kind not in request.requested_kinds: continue
            try:
                self._evaluated_pairs = max(self._evaluated_pairs, sum(len(columns_by_table[left.table_id]) * len(columns_by_table[right.table_id]) for left in tables for right in tables if left.table_id != right.table_id))
                self._provider_calls+=1; native=self.engine.discover_ind(tuple(paths.values()),approximate=approximate,max_error=request.search_policy.max_error_ratio,max_arity=request.search_policy.max_ind_arity)
                for item in native:
                    left,right=tables[item.left_table_index],tables[item.right_table_index]
                    if left.table_id==right.table_id: continue
                    lc,rc=columns_by_table[left.table_id],columns_by_table[right.table_id]
                    if len(item.left_column_indices)!=len(item.right_column_indices) or not item.left_column_indices or len(item.left_column_indices)>request.search_policy.max_ind_arity: continue
                    if any(i>=len(lc) for i in item.left_column_indices) or any(i>=len(rc) for i in item.right_column_indices): continue
                    left_ids=tuple(lc[i].column_id for i in item.left_column_indices); right_ids=tuple(rc[i].column_id for i in item.right_column_indices); left_names=tuple(lc[i].physical_name for i in item.left_column_indices); right_names=tuple(rc[i].physical_name for i in item.right_column_indices); metrics=_ind_metrics(rows_by_table[left.table_id],refs_by_table[left.table_id],rows_by_table[right.table_id],lc,rc,left_names,right_names,request.null_policy); compatible=_types_compatible(lc,rc,item.left_column_indices,item.right_column_indices); risk=metrics["right_distinct"]<=request.search_policy.low_cardinality_distinct_limit or (metrics["right_rows"]>0 and metrics["right_distinct"]/metrics["right_rows"]<=request.search_policy.low_cardinality_distinct_ratio); evidence_id=dependency_id("ind",[left.table_id,left_ids,right.table_id,right_ids,approximate]); observation=_native_metric(kind,item.native_metric_value,request.search_policy.max_error_ratio); violation=DependencyViolation(violation_id=dependency_id("violation",evidence_id),kind=kind,record_refs=tuple(metrics["refs"][:20]),count=metrics["orphan_count"],detail_code="IND_LEFT_VALUE_NOT_IN_RIGHT_DOMAIN")
                    evidence=InclusionDependencyEvidence(evidence_id=evidence_id,source_id=request.source_id,snapshot_id=request.snapshot_id,left_table_id=left.table_id,left_columns=left_ids,right_table_id=right.table_id,right_columns=right_ids,approximate=approximate,coverage_ratio=metrics["coverage"],violation_ratio=metrics["violation_ratio"],left_distinct_count=metrics["left_distinct"],right_distinct_count=metrics["right_distinct"],orphan_count=metrics["orphan_count"],target_uniqueness_ratio=metrics["target_uniqueness"],type_compatible=compatible,low_cardinality_risk=risk,violations=(violation,) if metrics["orphan_count"] else (),metric_observations=(observation,) if observation else (),null_policy=request.null_policy,observation_scope=scope,state=DependencyEvidenceState.OBSERVED,provenance=provenance("AIND.Mind" if approximate else "IND.Spider",[left.table_id,right.table_id])); output.append(evidence)
                    if not risk and compatible and metrics["target_uniqueness"]>=1.0: relationships.append(RelationshipCandidate(candidate_id=dependency_id("rel",evidence_id),source_id=request.source_id,snapshot_id=request.snapshot_id,from_table=left.table_id,from_columns=left_ids,to_table=right.table_id,to_columns=right_ids,source_orphan_ratio=metrics["orphan_ratio"],target_uniqueness_ratio=metrics["target_uniqueness"],type_compatible=compatible,low_cardinality_risk=risk,evidence_refs=(evidence_id,)))
            except ProviderTimeout: raise
            except Exception: failures.append(DependencyFailure(failure_id=dependency_id("dep_failure",["ind",approximate]),request_id=request.request_id,kind=DependencyFailureKind.ENGINE_FAILED,detail="inclusion dependency discovery failed for the selected table set"))
        return output,relationships

    def _validate_entry(self,request,catalog,snapshot_result,authorization):
        if request.source_id!=catalog.source_id or request.snapshot_id!=snapshot_result.snapshot.snapshot_id: raise ValueError("dependency request is not bound to supplied source snapshot")
        if set(request.selected_table_ids)-{t.table_id for t in catalog.tables}: raise ValueError("dependency request selects an unknown table")
        artifact_ids=tuple(batch.batch_id for batch in snapshot_result.batches if batch.table_id in request.selected_table_ids)
        if not authorization or self.privacy_policy is None or not self.privacy_policy.verify_dependency_authorization(authorization,source_id=request.source_id,snapshot_id=request.snapshot_id,table_ids=request.selected_table_ids,artifact_ids=artifact_ids): raise ValueError("dependency execution requires a policy-issued authorization bound to this source snapshot and table scope")
        if authorization.purpose!=request.privacy_context.purpose or authorization.network_allowed or authorization.external_processing_allowed: raise ValueError("dependency authorization does not match local-only request")

    def _scope(self,request,snapshot_result,tables):
        ids={t.table_id for t in tables}; batches=tuple(b for b in snapshot_result.batches if b.table_id in ids); obs={o.table_id:o for o in snapshot_result.table_observations}; rows={t.table_id:sum(b.row_count for b in batches if b.table_id==t.table_id) for t in tables}
        return DependencyObservationScope(source_id=request.source_id,snapshot_id=request.snapshot_id,table_ids=tuple(t.table_id for t in tables),mode=snapshot_result.snapshot.observation_scope.mode,rows_by_table=rows,complete_by_table={t.table_id:obs.get(t.table_id) is not None and obs[t.table_id].status is TableObservationStatus.FULLY_OBSERVED for t in tables},input_batch_ids=tuple(b.batch_id for b in batches),input_batch_hashes=tuple(b.content_hash for b in batches),input_record_reference_count=sum(1 for r in snapshot_result.record_references if r.table_id in ids))

    def _search_stats(self,request,requested,selected,all_columns,columns):
        input_columns=sum(len(all_columns[t.table_id]) for t in requested)
        full_candidate=sum(len(all_columns[l.table_id])*len(all_columns[r.table_id]) for l in requested for r in requested if l.table_id!=r.table_id)
        candidate=sum(len(columns[l.table_id])*len(columns[r.table_id]) for l in selected for r in selected if l.table_id!=r.table_id)
        excluded=sum(len(all_columns[t.table_id])-len(columns.get(t.table_id,())) for t in requested)
        tables_excluded=len(requested)-len(selected)
        scope_pruned=max(full_candidate-candidate,0)
        budget=max(candidate-request.search_policy.max_column_pairs,0)
        determinant=sum(len(c)*sum(math.comb(len(c)-1,w) for w in range(1,min(request.search_policy.max_fd_lhs_arity,len(c)-1)+1)) for c in columns.values())
        reasons=tuple(x for x,v in (("max_columns_per_table",excluded),("max_tables",tables_excluded),("max_column_pairs",budget)) if v)
        return DependencySearchStats(input_tables=len(requested),input_columns=input_columns,columns_excluded_by_bound=excluded,tables_excluded_by_bound=tables_excluded,candidate_column_pairs=candidate,candidate_pairs_before_pruning=full_candidate,pruned_by_scope=scope_pruned,pruned_by_budget=budget,pruned_column_pairs=scope_pruned+budget,searched_determinants=determinant,ucc_search_arity=request.search_policy.max_ucc_arity,fd_search_arity=request.search_policy.max_fd_lhs_arity,ind_search_arity=request.search_policy.max_ind_arity,provider_calls=0,emitted_candidates=0,truncated=bool(reasons),completeness="INCOMPLETE" if reasons else "COMPLETE",truncation_reasons=reasons)

    def _temp_root(self,request):
        root=(self.project_root/request.privacy_context.project_temp_root/request.request_id).resolve(); root.relative_to(self.project_root); return root

    @staticmethod
    def _null_semantics(policy):
        return {NullPolicy.EXCLUDE_PHYSICAL_NULL:"physical null rows excluded from provider relation and project denominators",NullPolicy.NULLS_EQUAL:"physical null encoded as one tagged provider value and counted as equal",NullPolicy.NULLS_BREAK_DEPENDENCY:"physical null encoded as row-unique tagged provider value and breaks dependency",NullPolicy.LITERAL_NULL_IS_VALUE:"physical null encoded as one tagged value; literal source strings remain distinct"}[policy]

    @staticmethod
    def _write_engine_input(root,table,columns,rows,null_policy):
        path=root/f"{table.table_id}.csv"
        with path.open("w",encoding="utf-8",newline="") as stream:
            writer=csv.writer(stream); writer.writerow([c.physical_name for c in columns])
            for index,row in enumerate(rows):
                if null_policy is NullPolicy.EXCLUDE_PHYSICAL_NULL and any(row.get(c.physical_name) is None for c in columns): continue
                writer.writerow([_encode_cell(row.get(c.physical_name),null_policy,index,c.column_id) for c in columns])
        return path

    def _failed_result(self,request,tables,snapshot_result,failures,capability,*,scope=None,stats=None):
        scope=scope or self._scope(request,snapshot_result,tables); stats=stats or DependencySearchStats(input_tables=len(tables),input_columns=0,candidate_column_pairs=0,pruned_column_pairs=0,searched_determinants=0,emitted_candidates=0,completeness="INCOMPLETE")
        return DependencyResult(request=request,observation_scope=scope,failures=tuple(failures),capabilities=(capability,),search_stats=stats,status=DependencyStageStatus.FAILED)

    @staticmethod
    def _budget_failure(request,detail):
        return DependencyFailure(failure_id=dependency_id("dep_failure",detail),request_id=request.request_id,kind=DependencyFailureKind.BUDGET_EXCEEDED,detail="dependency search exceeded its configured wall-clock budget")


def _encode_cell(value,null_policy,row_index,column_id):
    if value is None: body="N|equal" if null_policy in (NullPolicy.NULLS_EQUAL,NullPolicy.LITERAL_NULL_IS_VALUE) else f"N|break|{row_index}|{column_id}"
    else: body=f"V|{type(value).__name__}|{value}"
    return "DDO1_"+base64.urlsafe_b64encode(body.encode()).decode().rstrip("=")


def _row_eligible(row,columns,policy):
    return policy is not NullPolicy.EXCLUDE_PHYSICAL_NULL or not any(row.get(c.physical_name) is None for c in columns)


def _metric_value(value,policy,index,column):
    return ("physical-null-row-unique",index,column.column_id) if value is None and policy is NullPolicy.NULLS_BREAK_DEPENDENCY else value


def _fd_metrics(rows,refs,columns,determinant,dependent,policy):
    groups=defaultdict(set); group_refs=defaultdict(list); eligible=0
    by_name={c.physical_name:c for c in columns}
    for index,row in enumerate(rows):
        if policy is NullPolicy.EXCLUDE_PHYSICAL_NULL and not _row_eligible(row,columns,policy): continue
        lhs=tuple(_metric_value(row.get(name),policy,index,by_name[name]) for name in determinant); rhs=tuple(_metric_value(row.get(name),policy,index,by_name[name]) for name in dependent); eligible+=1; groups[lhs].add(rhs); group_refs[lhs].append(refs[index])
    violations=sum(max(len(values)-1,0) for values in groups.values()); bad_refs=[ref for key,values in groups.items() if len(values)>1 for ref in group_refs[key]]; error=violations/eligible if eligible else 0.0
    return {"support":1.0-error,"error":error,"violations":violations,"refs":bad_refs}


def _ind_metrics(left_rows,left_refs,right_rows,left_columns,right_columns,left_cols,right_cols,policy):
    left_by_name={c.physical_name:c for c in left_columns}; right_by_name={c.physical_name:c for c in right_columns}
    def eligible(row,names,by_name,index):
        if policy is NullPolicy.EXCLUDE_PHYSICAL_NULL and any(row.get(name) is None for name in names): return None
        return tuple(_metric_value(row.get(name),policy,index,by_name[name]) for name in names)
    right_values={value for index,row in enumerate(right_rows) if (value:=eligible(row,right_cols,right_by_name,index)) is not None}; left_with_refs=[(value,left_refs[index]) for index,row in enumerate(left_rows) if (value:=eligible(row,left_cols,left_by_name,index)) is not None]; left_values=[value for value,_ in left_with_refs]; orphans=[(value,ref) for value,ref in left_with_refs if value not in right_values]; left_distinct,right_distinct=len(set(left_values)),len(right_values); right_rows_eligible=sum(1 for index,row in enumerate(right_rows) if eligible(row,right_cols,right_by_name,index) is not None); coverage=len(set(left_values)&right_values)/left_distinct if left_distinct else 1.0
    return {"coverage":coverage,"violation_ratio":len(orphans)/len(left_values) if left_values else 0.0,"orphan_count":len(orphans),"orphan_ratio":len(orphans)/len(left_values) if left_values else 0.0,"left_distinct":left_distinct,"right_distinct":right_distinct,"right_rows":right_rows_eligible,"target_uniqueness":right_distinct/right_rows_eligible if right_rows_eligible else 0.0,"refs":[ref for _,ref in orphans]}


def _types_compatible(left,right,left_indices,right_indices):
    for li,ri in zip(left_indices,right_indices):
        lt,rt=left[li].normalized_physical_type,right[ri].normalized_physical_type
        if lt!=rt and {lt,rt} not in ({"integer","numeric"},{"string","unknown"}): return False
    return True


def _native_metric(kind,value,threshold):
    if kind not in (DependencyKind.AFD,DependencyKind.APPROXIMATE_IND): return None
    if value is None: return DependencyMetricObservation(metric_name="provider_native_error_unavailable",metric_value=None,direction="lower_is_better",threshold=threshold,provider_algorithm=kind.value,definition="Provider API did not expose a native error metric; no value was invented.",project_computed=False)
    return DependencyMetricObservation(metric_name="provider_native_error",metric_value=value,direction="lower_is_better",threshold=threshold,provider_algorithm=kind.value,definition="Native provider error metric returned by the Desbordante result object.",project_computed=False)
