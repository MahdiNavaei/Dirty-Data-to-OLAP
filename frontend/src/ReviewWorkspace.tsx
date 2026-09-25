import { useCallback, useEffect, useMemo, useState } from "react";
import { useParams } from "react-router-dom";
import { ApiError, api, type PendingReview, type ReviewActionHistory, type ReviewActionMutation, type Summary } from "./api/client";

type ActionName = ReviewActionMutation["action"];
type OverridePayload = NonNullable<ReviewActionMutation["override"]>;
type LabelPayload = NonNullable<ReviewActionMutation["label"]>;
type LockPayload = NonNullable<ReviewActionMutation["lock"]>;
type ActionDraft = Pick<ReviewActionMutation, "action" | "rationale"> & Partial<Pick<ReviewActionMutation, "override" | "label" | "lock">>;

const labelNamespaces = ["EVIDENCE", "IDENTITY", "ANALYTICAL", "MATERIALIZATION"] as const;
const labelValuesByNamespace: Record<LabelPayload["namespace"], readonly LabelPayload["value"][]> = {
  EVIDENCE: ["NEEDS_EVIDENCE", "DOMAIN_REVIEWED", "QUARANTINED"],
  IDENTITY: ["DO_NOT_MERGE", "DOMAIN_REVIEWED", "QUARANTINED"],
  ANALYTICAL: ["NON_ADDITIVE", "DOMAIN_REVIEWED"],
  MATERIALIZATION: ["CONTROLLED_TARGET", "DOMAIN_REVIEWED"],
};

function useReviewSummary(runId: string | undefined) {
  const [data, setData] = useState<Summary | null>(null);
  const [error, setError] = useState<ApiError | Error | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  useEffect(() => {
    if (!runId) return;
    let active = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let inFlight = false;
    let terminal = false;
    let delay = 700;
    const poll = async () => {
      if (!active || inFlight) return;
      inFlight = true;
      setRefreshing(true);
      try {
        const next = await api.summary(runId);
        if (!active) return;
        setData(next);
        setError(null);
        terminal = ["SUCCEEDED", "FAILED", "CANCELLED"].includes(next.status);
        delay = terminal ? 3500 : Math.min(delay + 250, 2200);
      } catch (cause) {
        if (active) setError(cause instanceof Error ? cause : new Error("Could not reconcile run state"));
      } finally {
        inFlight = false;
        if (active && !terminal) timer = setTimeout(poll, document.hidden ? Math.max(delay, 3000) : delay);
        if (active) setRefreshing(false);
      }
    };
    void poll();
    return () => { active = false; if (timer) clearTimeout(timer); };
  }, [runId]);
  return { data, error, refreshing };
}

function ReviewActionCard({ review, busy, onSubmit }: { review: PendingReview; busy: boolean; onSubmit: (draft: ActionDraft) => Promise<void> }) {
  const available = review.actions.filter((item) => item.available);
  const [action, setAction] = useState<ActionName>((available[0]?.action ?? "ACCEPT") as ActionName);
  const [rationale, setRationale] = useState("Reviewed the bounded evidence and its stated limitations.");
  const [target, setTarget] = useState<OverridePayload["target"]>("RELATIONSHIP_DISPOSITION" as OverridePayload["target"]);
  const [replacement, setReplacement] = useState<OverridePayload["replacement"]>("REQUIRE_REVISION" as OverridePayload["replacement"]);
  const [labelNamespace, setLabelNamespace] = useState<LabelPayload["namespace"]>("EVIDENCE" as LabelPayload["namespace"]);
  const [labelValue, setLabelValue] = useState<LabelPayload["value"]>("NEEDS_EVIDENCE" as LabelPayload["value"]);
  const [lockScope, setLockScope] = useState(`checkpoint-${review.checkpoint.toLowerCase()}`);
  const [confirmLock, setConfirmLock] = useState(false);
  const selected = review.actions.find((item) => item.action === action);
  const overrideTargets = useMemo(() => (review.actions.find((item) => item.action === "OVERRIDE")?.supported_override_targets ?? []) as OverridePayload["target"][], [review.actions]);
  const replacementOptions = useMemo(() => (review.actions.find((item) => item.action === "OVERRIDE")?.supported_override_replacements ?? []) as OverridePayload["replacement"][], [review.actions]);
  useEffect(() => {
    if (overrideTargets.length > 0 && !overrideTargets.includes(target)) setTarget(overrideTargets[0]);
  }, [overrideTargets, target]);
  const labelOptions = labelValuesByNamespace[labelNamespace];
  useEffect(() => {
    if (replacementOptions.length > 0 && !replacementOptions.includes(replacement)) setReplacement(replacementOptions[0]);
  }, [replacement, replacementOptions]);
  useEffect(() => {
    if (!labelOptions.includes(labelValue)) setLabelValue(labelOptions[0]);
  }, [labelOptions, labelValue]);
  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    const draft: ActionDraft = { action, rationale };
    if (action === "OVERRIDE") draft.override = { schema_version: "1.0", target, replacement, old_value_ref: review.subject_artifact_id };
    if (action === "LABEL") draft.label = { schema_version: "1.0", namespace: labelNamespace, value: labelValue };
    if (action === "LOCK") { const lock: LockPayload = { schema_version: "1.0", scope: lockScope, confirm: confirmLock }; draft.lock = lock; }
    await onSubmit(draft);
  };
  return <form className="review-actions" onSubmit={(event) => void submit(event)}>
    <div className="action-picker" role="group" aria-label="Permitted review actions">
      {review.actions.map((item) => <button key={item.action} type="button" className={`button action-button ${action === item.action ? "selected" : ""}`} disabled={!item.available || busy} title={item.available ? item.downstream_effect : item.reason_if_unavailable ?? "Unavailable"} onClick={() => setAction(item.action as ActionName)}>{item.action}</button>)}
    </div>
    <div className="action-help" role="status">{selected?.available ? selected.downstream_effect : selected?.reason_if_unavailable ?? "Select a server-permitted action."}</div>
    <label htmlFor={`rationale-${review.subject_artifact_id}`}>Rationale <span className="required">required</span></label>
    <textarea id={`rationale-${review.subject_artifact_id}`} value={rationale} onChange={(event) => setRationale(event.target.value)} required />
    {action === "OVERRIDE" && <div className="typed-action-fields">{overrideTargets.length > 0 ? <><label htmlFor={`override-target-${review.subject_artifact_id}`}>Supported override target</label><select id={`override-target-${review.subject_artifact_id}`} value={target} onChange={(event) => setTarget(event.target.value as OverridePayload["target"])}>{overrideTargets.map((value) => <option key={value} value={value}>{value.replaceAll("_", " ")}</option>)}</select><label htmlFor={`override-value-${review.subject_artifact_id}`}>Typed replacement</label><select id={`override-value-${review.subject_artifact_id}`} value={replacement} onChange={(event) => setReplacement(event.target.value as OverridePayload["replacement"])}>{replacementOptions.map((value) => <option key={value} value={value}>{value.replaceAll("_", " ")}</option>)}</select><small>Original subject remains immutable. A replacement needs a fresh compatible review and downstream recomputation.</small></> : <small>The server has no executable override target for this subject.</small>}</div>}
    {action === "LABEL" && <div className="typed-action-fields"><label htmlFor={`label-namespace-${review.subject_artifact_id}`}>Label namespace</label><select id={`label-namespace-${review.subject_artifact_id}`} value={labelNamespace} onChange={(event) => setLabelNamespace(event.target.value as LabelPayload["namespace"])}>{labelNamespaces.map((value) => <option key={value} value={value}>{value}</option>)}</select><label htmlFor={`label-value-${review.subject_artifact_id}`}>Supported label</label><select id={`label-value-${review.subject_artifact_id}`} value={labelValue} onChange={(event) => setLabelValue(event.target.value as LabelPayload["value"])}>{labelOptions.map((value) => <option key={value} value={value}>{value.replaceAll("_", " ")}</option>)}</select><small>Labels are audit evidence and never mean ACCEPT, MERGE or auto-approval.</small></div>}
    {action === "LOCK" && <div className="typed-action-fields"><label htmlFor={`lock-scope-${review.subject_artifact_id}`}>Lock scope</label><input id={`lock-scope-${review.subject_artifact_id}`} value={lockScope} onChange={(event) => setLockScope(event.target.value)} /><label className="checkbox-label"><input type="checkbox" checked={confirmLock} onChange={(event) => setConfirmLock(event.target.checked)} /> I understand this freezes the compatible decision.</label></div>}
    <button className="button primary" type="submit" disabled={busy || !selected?.available || !rationale.trim() || (action === "LOCK" && !confirmLock)}>{busy ? "Saving action…" : action === "ACCEPT" ? "Accept and resume when eligible" : `Save ${action}`}</button>
  </form>;
}

function ReviewHistory({ history }: { history: ReviewActionHistory[] }) {
  return <section className="panel product-section" data-testid="review-history"><ReviewTitle eyebrow="Durable review history" title="The audit trail remains inspectable" />{history.length === 0 ? <div className="empty-state"><strong>No reviewer actions have been saved for this run.</strong></div> : <div className="table-wrap"><table><caption className="sr-only">Durable review action history</caption><thead><tr><th>Action</th><th>Subject</th><th>Actor / revision</th><th>Effect</th></tr></thead><tbody>{history.map((item) => <tr key={item.history_id}><td><strong>{item.action.action}</strong><small>{item.action.recorded_at}</small></td><td><span>{item.action.checkpoint}</span><small className="mono">{item.action.subject_artifact_id}</small></td><td><span>{item.action.principal}</span><small>revision {item.revision} · rationale retained</small></td><td><span>{item.action.downstream_effect}</span><small>next: {item.action.next_required_action}</small></td></tr>)}</tbody></table></div>}</section>;
}

export function ReviewWorkspace() {
  const { runId } = useParams();
  const { data, error, refreshing } = useReviewSummary(runId);
  const [actionError, setActionError] = useState<ApiError | Error | null>(null);
  const [actionMessage, setActionMessage] = useState<string | null>(null);
  const [actionBusy, setActionBusy] = useState(false);
  const [history, setHistory] = useState<ReviewActionHistory[]>([]);
  const refreshHistory = useCallback(async () => { if (!runId) return; try { setHistory(await api.reviewHistory(runId)); } catch (cause) { if (cause instanceof Error) setActionError(cause); } }, [runId]);
  useEffect(() => { void refreshHistory(); }, [refreshHistory]);
  const cancelRun = async () => { if (!runId) return; setActionBusy(true); setActionError(null); try { await api.cancel(runId); } catch (cause) { setActionError(cause instanceof Error ? cause : new Error("Cancellation failed")); } finally { setActionBusy(false); } };
  if (!runId) return <div className="page"><section className="panel empty-page"><h1>This workspace is unavailable.</h1></section></div>;
  return <div className="page workspace-page"><div className="workspace-header"><div><p className="eyebrow">Run workspace</p><h1>Review the path, then release the output.</h1><p className="muted mono">{runId}</p></div><div className="header-actions"><span className={`run-status status-${data?.status.toLowerCase() ?? "loading"}`} data-testid="run-status">{data?.status ?? "LOADING"}</span><button className="button ghost" type="button" disabled={actionBusy || ["SUCCEEDED", "CANCELLED", "FAILED"].includes(data?.status ?? "")} onClick={() => void cancelRun()}>Cancel run</button></div></div>{(error ?? actionError) && <div className="notice error" role="alert"><strong>{error instanceof ApiError ? error.code : actionError instanceof ApiError ? actionError.code : "NETWORK_ERROR"}</strong><span>{(error ?? actionError)?.message}</span></div>}{actionMessage && <div className="notice success" role="status">{actionMessage}</div>}{data && <><section className="workspace-summary"><div><span className="label">Current stage</span><strong>{data.current_stage ?? (data.status === "SUCCEEDED" ? "Validation complete" : "Durable coordinator")}</strong></div><div><span className="label">Planning phase</span><strong>{data.planning_phase ?? "BOOTSTRAP"}</strong></div><div><span className="label">Polling</span><strong>{refreshing ? "Reconciling…" : "Connected"}</strong></div><div><span className="label">Project</span><strong>{data.project_id}</strong></div></section><section className="panel product-section" data-testid="review-subjects"><ReviewTitle eyebrow="Human review boundary" title="Server-owned subjects and consequences" /><p className="muted">Action applicability, evidence scope, compatibility and revision are supplied by the server. The browser never promotes a subject locally.</p>{data.pending_reviews.length === 0 ? <div className="waiting-result"><strong>{data.status === "SUCCEEDED" ? "No pending review subjects" : "Waiting for a review subject"}</strong><span>Run state remains authoritative; a missing subject is not an implicit acceptance.</span></div> : data.pending_reviews.map((review) => <article className="review-object" key={`${review.checkpoint}-${review.subject_artifact_id}`}><div className="review-object-heading"><span className="checkpoint">{review.checkpoint}</span><span className="state-chip warning">{review.state}</span></div><dl className="review-details"><ReviewDefinition label="Subject type" value={review.subject_type} /><ReviewDefinition label="Proposal" value={review.subject_description} /><ReviewDefinition label="Current state" value={review.state} /><ReviewDefinition label="Decision revision" value={`revision ${review.revision}`} /><ReviewDefinition label="Action revision" value={`revision ${review.action_revision}`} /><ReviewDefinition label="Scope" value={review.context.subject_stage} /></dl><div className="review-evidence"><div><strong>Evidence scope</strong><span>{review.source_scope.join(", ") || "Run-scoped registered evidence"}</span></div><div><strong>Confidence semantics</strong><span>{review.confidence_semantics}</span></div><div><strong>Supporting</strong><span>{review.supporting_evidence.join(" · ") || "None declared"}</span></div><div><strong>Missing or conflicting</strong><span>{[...review.missing_evidence, ...review.conflicts].join(" · ") || "None declared"}</span></div><div><strong>Provenance</strong><span>{review.provenance_summary.join(" · ")}</span></div><div><strong>Downstream effect</strong><span>{review.downstream_consequence}</span></div></div><ReviewActionCard review={review} busy={actionBusy} onSubmit={async (draft) => { if (!runId) return; setActionBusy(true); setActionError(null); try { const result = await api.reviewActions(runId, review, draft); setActionMessage(result.action === "ACCEPT" && result.execution_eligible ? "Action saved. The server confirmed resume eligibility; requesting resume…" : `Action saved: ${result.action}. ${result.next_required_action}.`); if (result.execution_eligible) { const resumed = await api.resume(runId); setActionMessage(resumed.status === "ACCEPTED" ? "Action saved. Run resumed by the durable execution boundary." : `Action saved. Resume result: ${resumed.status}.`); } await refreshHistory(); } catch (cause) { setActionError(cause instanceof Error ? cause : new Error("Review action failed")); } finally { setActionBusy(false); } }} /></article>)}</section><ReviewHistory history={history} /><section className="panel product-section" id="output" data-testid="output-section"><ReviewTitle eyebrow="Run outcome" title="Output readiness follows server gates" /><div className={`gate-card ${data.output.validated && data.status === "SUCCEEDED" ? "pass" : "pending"}`}><span className="gate-badge">G6</span><div><strong>{data.validation.g6_status} · {data.validation.g6_eligible ? "eligible" : "not eligible"}</strong><span>{data.output.validated && data.status === "SUCCEEDED" ? "Validated OLAP output available" : "Output is not yet consumable; a review POST alone is not success."}</span></div></div></section></>}</div>;
}

function ReviewTitle({ eyebrow, title }: { eyebrow: string; title: string }) { return <div className="section-heading"><div><p className="eyebrow">{eyebrow}</p><h2>{title}</h2></div></div>; }
function ReviewDefinition({ label, value }: { label: string; value: string }) { return <div className="definition"><dt>{label}</dt><dd>{value}</dd></div>; }
