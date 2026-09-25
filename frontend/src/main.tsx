import { StrictMode, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Link, Route, Routes, useNavigate, useParams } from "react-router-dom";
import { ApiError, api, type Source, type Summary } from "./api/client";
import { ReviewWorkspace } from "./ReviewWorkspace";
import "./styles.css";

function useSummary(runId: string | undefined) {
  const [data, setData] = useState<Summary | null>(null);
  const [error, setError] = useState<ApiError | Error | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  useEffect(() => {
    if (!runId) return;
    let active = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let inFlight = false;
    let terminal = false;
    let delay = 900;
    const poll = async () => {
      if (!active || inFlight) return;
      inFlight = true;
      setRefreshing(true);
      try {
        const next = await api.summary(runId);
        if (!active) return;
        setData(next);
        setError(null);
        terminal = next.status === "SUCCEEDED" || next.status === "FAILED" || next.status === "CANCELLED";
        delay = next.status === "SUCCEEDED" || next.status === "FAILED" || next.status === "CANCELLED" ? 4000 : Math.min(delay + 300, 2500);
      } catch (cause) {
        if (active) setError(cause instanceof Error ? cause : new Error("Could not reconcile run state"));
      } finally {
        inFlight = false;
        if (active && !terminal) timer = setTimeout(poll, document.hidden ? Math.max(delay, 3000) : delay);
        if (active) setRefreshing(false);
      }
    };
    void poll();
    const onVisibility = () => { if (!document.hidden && !timer) void poll(); };
    document.addEventListener("visibilitychange", onVisibility);
    return () => { active = false; if (timer) clearTimeout(timer); document.removeEventListener("visibilitychange", onVisibility); };
  }, [runId]);
  return { data, error, refreshing };
}

function ErrorNotice({ error }: { error: ApiError | Error | null }) {
  if (!error) return null;
  const apiError = error instanceof ApiError ? error : null;
  return <div className="notice error" role="alert"><strong>{apiError?.code ?? "NETWORK_ERROR"}</strong><span>{error.message}</span>{apiError?.code === "DELIVERY_UNKNOWN" && <span>Outcome uncertain. Refresh the run to reconcile the durable command.</span>}{apiError?.code === "REVIEW_REVISION_CONFLICT" && <span>This review is stale. The workspace is refreshing from the server-owned review state.</span>}</div>;
}

const uncertaintyStates = [
  ["INCOMPLETE", "wait for the missing prerequisite or source evidence"],
  ["PROVIDER_FAILED", "keep the run reviewable and show the failure for recovery"],
  ["PRIVACY_BLOCKED", "do not expose restricted evidence; request an authorized path"],
  ["NOT_EVALUATED", "do not infer a result from an absent check"],
  ["PARTIAL_SUCCESS", "retain completed evidence and reconcile the remaining work"],
  ["LONG_RUNNING", "continue bounded polling without duplicating commands"],
  ["STALE", "refresh server truth before allowing a review action"],
  ["VALIDATION_FAILED", "keep the output non-consumable and show the blocking gate"],
  ["CANCELLED", "keep the run terminal and do not present output as success"],
] as const;

function UncertaintyCoverage() {
  return <section className="panel product-section state-coverage" data-testid="uncertainty-coverage"><SectionTitle eyebrow="State semantics" title="No state is silently upgraded" /><p className="muted">The workspace preserves server-owned uncertainty and pairs each state with a safe next action.</p><div className="state-legend">{uncertaintyStates.map(([state, recovery]) => <div key={state}><span className="state-chip">{state}</span><small>{recovery}</small></div>)}</div></section>;
}

function AppShell() {
  return <div className="app-shell">
    <header className="topbar">
      <Link to="/" className="brand"><span className="brand-mark">A</span><span>Atlas <em>OLAP review</em></span></Link>
      <nav aria-label="Primary navigation"><Link to="/projects">Projects &amp; sources</Link><Link to="/">New run</Link></nav>
      <span className="auth-badge">Local reference auth</span>
    </header>
    <main><Routes><Route path="/" element={<SetupPage />} /><Route path="/projects" element={<ProjectsPage />} /><Route path="/runs/:runId" element={<ReviewWorkspace />} /><Route path="*" element={<NotFound />} /></Routes></main>
  </div>;
}

function SetupPage() {
  const navigate = useNavigate();
  const [projectId, setProjectId] = useState("demo-project");
  const [sources, setSources] = useState<Source[]>([]);
  const [selected, setSelected] = useState<Source | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<ApiError | Error | null>(null);
  const [configuration, setConfiguration] = useState<string | null>(null);
  useEffect(() => { void Promise.all([api.sources(), api.configuration()]).then(([items, config]) => { setSources(items); setSelected(items[0] ?? null); setConfiguration(config.configuration_fingerprint); }).catch((cause: unknown) => setError(cause instanceof Error ? cause : new Error("Product API is unavailable"))); }, []);
  const importSource = async () => {
    if (!file) return;
    setBusy(true); setError(null); setMessage(null);
    try { const source = await api.importCsv(file); setSources((current: Source[]) => [...current.filter((item: Source) => item.registry_id !== source.registry_id), source]); setSelected(source); setFile(null); setMessage("Source imported and registered in the managed project area."); }
    catch (cause) { setError(cause instanceof Error ? cause : new Error("Source import failed")); }
    finally { setBusy(false); }
  };
  const startRun = async () => {
    if (!selected || !configuration) return;
    setBusy(true); setError(null); setMessage(null);
    try { const run = await api.createRun(projectId.trim(), configuration); await api.bindSource(run.run_id, selected.registry_id); await api.prepare(run.run_id); await api.submit(run.run_id); navigate(`/runs/${encodeURIComponent(run.run_id)}`); }
    catch (cause) { setError(cause instanceof Error ? cause : new Error("Run could not be started")); }
    finally { setBusy(false); }
  };
  return <div className="page setup-page">
    <section className="hero compact"><p className="eyebrow">Evidence-led data preparation</p><h1>Turn a bounded source into a reviewed OLAP output.</h1><p className="lede">A local product path for discovery, uncertainty-aware review, canonical identity, analytical planning, materialization and validation.</p></section>
    <ErrorNotice error={error} />{message && <div className="notice success" role="status">{message}</div>}
    <div className="setup-grid">
      <section className="panel"><div className="section-heading"><div><p className="eyebrow">01 / source</p><h2>Connect a managed CSV</h2></div><span className="step-pill">Read-only after import</span></div><p className="muted">V1 expects order_id, customer_id, customer_id_ref, order_date, quantity and unit_price. Upload bytes are bounded to 5 MB.</p><label className="file-drop" htmlFor="source-file"><span className="upload-icon">↑</span><span><strong>{file ? file.name : "Choose a CSV file"}</strong><small>{file ? `${Math.ceil(file.size / 1024)} KB selected` : "The browser sends the file through the source API."}</small></span></label><input id="source-file" type="file" accept=".csv,text/csv" onChange={(event) => setFile(event.target.files?.[0] ?? null)} /><button className="button secondary" type="button" disabled={!file || busy} onClick={() => void importSource()}>{busy && file ? "Importing…" : "Import and register"}</button>{sources.length > 0 && <div className="source-list"><h3>Registered sources</h3>{sources.map((source) => <button type="button" className={`source-row ${selected?.registry_id === source.registry_id ? "selected" : ""}`} key={source.registry_id} onClick={() => setSelected(source)}><span className="source-dot" aria-hidden="true" /><span><strong>{source.display_name}</strong><small>{source.source_type.toUpperCase()} · {source.read_only ? "read-only" : "writable"}</small></span><span aria-hidden="true">{selected?.registry_id === source.registry_id ? "✓" : ""}</span></button>)}</div>}</section>
      <section className="panel start-panel"><div className="section-heading"><div><p className="eyebrow">02 / run</p><h2>Open a review workspace</h2></div><span className="step-pill">Durable execution</span></div><label htmlFor="project-id">Project context</label><input id="project-id" value={projectId} onChange={(event) => setProjectId(event.target.value)} /><div className="context-card"><span className="status-dot" aria-hidden="true" /><div><strong>{selected ? selected.display_name : "Select an imported source"}</strong><small>{selected ? "Selected source · opaque registry identity" : "Import a CSV to continue"}</small></div></div><div className="trust-note"><strong>Server-owned decisions</strong><span>Conditional stages, review subjects, validation status and G6 eligibility come from the API. The browser only presents and requests actions.</span></div><button className="button primary" type="button" disabled={!selected || !configuration || busy} onClick={() => void startRun()}>{busy && !file ? "Starting durable run…" : "Create run and start discovery"}<span aria-hidden="true">→</span></button>{!configuration && <p className="small-note">Connecting to the local product API…</p>}</section>
    </div>
  </div>;
}

function ProjectsPage() {
  return <div className="page"><section className="hero compact"><p className="eyebrow">Projects / sources</p><h1>Start from a source, then follow the evidence.</h1><p className="lede">The local reference workspace keeps source registration separate from each durable run.</p><Link className="button primary inline" to="/">Create a new run <span aria-hidden="true">→</span></Link></section></div>;
}

// Retained as a compatibility export for older local snapshots; the active
// route uses ReviewWorkspace above so all new actions cross the typed API.
export function RunWorkspace() {
  const { runId } = useParams();
  const { data, error, refreshing } = useSummary(runId);
  const [actionError, setActionError] = useState<ApiError | Error | null>(null);
  const [actionBusy, setActionBusy] = useState(false);
  const [rationale, setRationale] = useState("Reviewed the bounded evidence and its stated limitations.");
  const acceptReview = async (review: Summary["pending_reviews"][number]) => {
    if (!runId || review.decision === "ACCEPTED") return;
    setActionBusy(true); setActionError(null);
    try { await api.review(runId, review, rationale); await api.resume(runId); }
    catch (cause) { setActionError(cause instanceof Error ? cause : new Error("Review action failed")); }
    finally { setActionBusy(false); }
  };
  const cancelRun = async () => { if (!runId) return; setActionBusy(true); setActionError(null); try { await api.cancel(runId); } catch (cause) { setActionError(cause instanceof Error ? cause : new Error("Cancellation failed")); } finally { setActionBusy(false); } };
  if (!runId) return <NotFound />;
  return <div className="page workspace-page"><div className="workspace-header"><div><p className="eyebrow">Run workspace</p><h1>Review the path, then release the output.</h1><p className="muted mono">{runId}</p></div><div className="header-actions"><span className={`run-status status-${data?.status.toLowerCase() ?? "loading"}`} data-testid="run-status">{data?.status ?? "LOADING"}</span><button className="button ghost" type="button" disabled={actionBusy || ["SUCCEEDED", "CANCELLED", "FAILED"].includes(data?.status ?? "")} onClick={() => void cancelRun()}>Cancel run</button></div></div><ErrorNotice error={error ?? actionError} />{data && <><section className="workspace-summary"><div><span className="label">Current stage</span><strong>{data.current_stage ?? (data.status === "SUCCEEDED" ? "Validation complete" : "Durable coordinator")}</strong></div><div><span className="label">Planning phase</span><strong>{data.planning_phase ?? "BOOTSTRAP"}</strong></div><div><span className="label">Polling</span><strong>{refreshing ? "Reconciliating…" : "Connected"}</strong></div><div><span className="label">Project</span><strong>{data.project_id}</strong></div></section><div className="workspace-layout"><aside className="stage-nav" aria-label="Run sections"><a href="#source">Source &amp; snapshot</a><a href="#condition">Data condition</a><a href="#evidence">Evidence</a><a href="#identity">Canonical identity</a><a href="#analytical">Analytical / OLAP</a><a href="#materialization">Materialization</a><a href="#validation">Validation / G6</a><a href="#output">Final output</a></aside><div className="workspace-content"><section className="panel product-section" id="source" data-testid="source-section"><SectionTitle eyebrow="Source / snapshot" title="What entered the run" /><div className="definition-grid"><Definition label="Source" value={data.source?.display_name ?? "Awaiting discovery"} /><Definition label="Type" value={data.source?.source_type.toUpperCase() ?? "—"} /><Definition label="Binding" value={data.source_binding ? "Bound to this run" : "Not bound"} /><Definition label="Observation" value={data.data_condition.observation_scope} /></div></section><section className="panel product-section" id="condition" data-testid="data-condition"><SectionTitle eyebrow="Data condition" title="Observed source condition" /><div className="metric-row"><Metric value={data.data_condition.source_rows_observed} label="source rows observed" /><Metric value={data.data_condition.staged_rows} label="rows staged faithfully" /><Metric value={data.data_condition.column_count} label="columns catalogued" /><Metric value={data.data_condition.state} label="state" /></div><p className="muted">Observation scope is explicit: {data.data_condition.observation_scope}. Missing values are reported as condition evidence, not silently repaired.</p></section><section className="panel product-section" id="evidence" data-testid="evidence-section"><SectionTitle eyebrow="Evidence / relationships" title="Candidate evidence before acceptance" />{data.relationships.length === 0 ? <EmptyState text="No candidates are available yet." /> : <div className="table-wrap"><table><caption className="sr-only">Relationship evidence candidates</caption><thead><tr><th>Candidate</th><th>Evidence meaning</th><th>State</th></tr></thead><tbody>{data.relationships.map((item) => <tr key={item.decision_id}><td><strong>{item.from_table}.{item.from_columns.join(", ")} → {item.to_table}.{item.to_columns.join(", ")}</strong><small className="mono">{item.decision_id}</small></td><td><span>Uncalibrated decision score: {item.score_value ?? "not available"}</span><small>{item.score_semantics}. Not a probability.</small></td><td><span className="state-chip">{item.decision_state}</span></td></tr>)}</tbody></table></div>}</section><UncertaintyCoverage />{data.pending_reviews.length > 0 && <section className="panel review-panel" data-testid="review-card"><SectionTitle eyebrow="Reviewer action required" title="Resolve the current checkpoint" /><p className="muted">The server derived this subject from verified artifacts. Accepting it authorizes the next guarded stage; it does not rewrite the evidence.</p>{data.pending_reviews.map((review) => <article className="review-object" key={`${review.checkpoint}-${review.subject_artifact_id}`}><div className="review-object-heading"><span className="checkpoint">{review.checkpoint}</span><span className="state-chip warning">{review.state}</span></div><dl className="review-details"><Definition label="Subject" value={review.subject_semantic_id} /><Definition label="Freshness" value={`revision ${review.revision}`} /><Definition label="Scope" value={review.context.subject_stage} /><Definition label="Consequence" value="The next guarded stage remains paused until an accepted decision is durable." /></dl><label htmlFor={`rationale-${review.subject_artifact_id}`}>Review rationale</label><textarea id={`rationale-${review.subject_artifact_id}`} value={rationale} onChange={(event) => setRationale(event.target.value)} /><button className="button primary" type="button" disabled={actionBusy || !rationale.trim()} onClick={() => void acceptReview(review)}>Accept and resume <span aria-hidden="true">→</span></button></article>)}</section>}<section className="panel product-section" id="identity" data-testid="canonical-section"><SectionTitle eyebrow="Canonical / identity" title="Identity stays distinct from linkage" /><div className="definition-grid"><Definition label="State" value={data.canonical.state} /><Definition label="Model" value={data.canonical.model_id ?? "Pending finalization"} /><Definition label="Memberships" value={String(data.canonical.membership_count)} /><Definition label="Basis" value={data.canonical.identity_basis.join(", ") || "Not evaluated"} /></div><p className="muted">Source-local identity is shown as a declared basis. A cluster or candidate score would not be presented as canonical truth.</p></section><section className="panel product-section" id="analytical" data-testid="analytical-section"><SectionTitle eyebrow="Analytical / OLAP" title="A reviewed grain and measure plan" /><div className="definition-grid"><Definition label="Plan" value={data.analytical.plan_id ?? "Pending planning"} /><Definition label="Fact" value={data.analytical.fact_ids.join(", ") || "—"} /><Definition label="Dimensions" value={data.analytical.dimension_ids.join(", ") || "—"} /><Definition label="Measures" value={data.analytical.measure_ids.join(", ") || "—"} /><Definition label="Aggregation" value={data.analytical.measure_semantics.join(", ") || "Declared by server plan"} /><Definition label="State" value={data.analytical.state} /></div></section><section className="panel product-section" id="materialization" data-testid="materialization-section"><SectionTitle eyebrow="Materialization" title="Authorized output build" /><div className="definition-grid"><Definition label="Status" value={data.materialization.status} /><Definition label="Target" value={data.materialization.target_type ?? "Pending"} /><Definition label="Tables" value={data.materialization.table_names.join(", ") || "—"} /><Definition label="Usable" value={data.materialization.usable ? "Yes" : "Not yet"} /></div><p className="muted">Target paths and generated SQL remain outside the browser projection.</p></section><section className="panel product-section" id="validation" data-testid="validation-section"><SectionTitle eyebrow="Validation / G6" title="Correctness is an explicit gate" /><div className={`gate-card ${data.validation.g6_status === "PASS" && data.validation.g6_eligible ? "pass" : "pending"}`}><span className="gate-badge">G6</span><div><strong>{data.validation.g6_status} · {data.validation.g6_eligible ? "eligible" : "not eligible"}</strong><span>{data.validation.passed_check_count} checks passed · {data.validation.failed_check_count} failed · {data.validation.not_evaluated_check_count} pending or not evaluated</span></div></div></section><section className="panel product-section final-output" id="output" data-testid="output-section"><SectionTitle eyebrow="Final output" title="A consumable result, bound to this run" />{data.output.validated && data.status === "SUCCEEDED" ? <div className="final-result"><span className="success-icon" aria-hidden="true">✓</span><div><h3>Validated OLAP output available</h3><p>{Object.entries(data.output.row_counts).map(([name, count]) => `${name}: ${count} rows`).join(" · ")}</p><small>Materialization preceded success, and exact G6 is PASS and eligible.</small></div></div> : <div className="waiting-result"><span className="spinner" aria-hidden="true" /><div><strong>{data.status === "NEEDS_REVIEW" ? "Waiting for a reviewer" : "Output is not yet consumable"}</strong><span>Validation failure, missing evidence or an incomplete run cannot be presented as success.</span></div></div>}</section></div></div></>}</div>;
}

function SectionTitle({ eyebrow, title }: { eyebrow: string; title: string }) { return <div className="section-heading"><div><p className="eyebrow">{eyebrow}</p><h2>{title}</h2></div></div>; }
function Definition({ label, value }: { label: string; value: string }) { return <div className="definition"><dt>{label}</dt><dd>{value}</dd></div>; }
function Metric({ value, label }: { value: number | string; label: string }) { return <div className="metric"><strong>{value}</strong><span>{label}</span></div>; }
function EmptyState({ text }: { text: string }) { return <div className="empty-state"><strong>{text}</strong><span>Nothing is hidden or inferred from absence.</span></div>; }
function NotFound() { return <div className="page"><section className="panel empty-page"><p className="eyebrow">Not found</p><h1>This workspace is unavailable.</h1><Link className="button primary inline" to="/">Return to source setup</Link></section></div>; }

export default function Root() { return <StrictMode><BrowserRouter><AppShell /></BrowserRouter></StrictMode>; }

const root = document.getElementById("root");
if (root) createRoot(root).render(<Root />);
