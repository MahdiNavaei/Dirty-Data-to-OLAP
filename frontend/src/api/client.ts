import type { components } from "./generated";

export type ApiErrorBody = { error?: { code?: string; message?: string; status?: number; retryable?: boolean } };

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly retryable: boolean;

  constructor(status: number, body: ApiErrorBody) {
    super(body.error?.message ?? `Request failed with status ${status}`);
    this.name = "ApiError";
    this.status = status;
    this.code = body.error?.code ?? "HTTP_ERROR";
    this.retryable = body.error?.retryable ?? status >= 500;
  }
}

type ProductSummary = components["schemas"]["ProductSummary"];
type ProductOutput = Omit<ProductSummary["output"], "row_counts"> & { row_counts: Record<string, number> };
export type Summary = Omit<ProductSummary, "output"> & { output: ProductOutput };
export type Source = components["schemas"]["ProductSourceView"];
export type PendingReview = Summary["pending_reviews"][number];
export type ReviewActionMutation = components["schemas"]["ReviewActionMutationRequest"];
export type ReviewActionResult = components["schemas"]["ReviewActionResult"];
export type ReviewActionHistory = components["schemas"]["ReviewActionHistoryRecord"];

const baseUrl = (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? "http://127.0.0.1:8765";
const principal = "step29-browser-reviewer";

function mutationKey(prefix: string): string {
  const id = typeof crypto.randomUUID === "function" ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`;
  return `${prefix}-${id}`;
}

async function readBody(response: Response): Promise<unknown> {
  const text = await response.text();
  if (!text) return undefined;
  try { return JSON.parse(text) as unknown; } catch { return { error: { code: "INVALID_RESPONSE", message: "The server returned an invalid response" } }; }
}

async function request<T>(path: string, init: RequestInit = {}, options: { mutation?: boolean; key?: string } = {}): Promise<T> {
  const headers = new Headers(init.headers);
  headers.set("X-Local-Principal", principal);
  headers.set("Accept", "application/json");
  if (typeof init.body === "string" && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  if (options.mutation) headers.set("Idempotency-Key", options.key ?? mutationKey("step29"));
  const response = await fetch(`${baseUrl}${path}`, { ...init, headers });
  const body = await readBody(response);
  if (!response.ok) throw new ApiError(response.status, (body ?? {}) as ApiErrorBody);
  return body as T;
}

export class ApiClient {
  async sources(): Promise<Source[]> {
    return request<Source[]>("/api/v1/sources");
  }

  async importCsv(file: File, key = mutationKey("source-import")): Promise<Source> {
    return request<Source>("/api/v1/sources/import", { method: "POST", body: file, headers: { "Content-Type": "text/csv", "X-Source-Filename": file.name } }, { mutation: true, key });
  }

  async createRun(projectId: string, configurationFingerprint: string, key = mutationKey("run-create")): Promise<RunView> {
    return request<RunView>("/api/v1/runs", { method: "POST", body: JSON.stringify({ project_id: projectId, configuration_fingerprint: configurationFingerprint }) }, { mutation: true, key });
  }

  async health(): Promise<{ status: string }> {
    return request<{ status: string }>("/api/v1/health");
  }

  async configuration(): Promise<{ configuration_fingerprint: string; authentication_mode: string }> {
    return request<{ configuration_fingerprint: string; authentication_mode: string }>("/api/v1/product/configuration");
  }

  async bindSource(runId: string, registryId: string, key = mutationKey("source-bind")): Promise<Binding> {
    return request<Binding>(`/api/v1/runs/${encodeURIComponent(runId)}/source-selection`, { method: "POST", body: JSON.stringify({ registry_id: registryId, extraction: { chunk_size: 1000, max_rows: 10000, max_rows_scope: "SOURCE_WIDE", null_markers: [], preserve_raw_values: true }, scope: { included_objects: [], excluded_objects: [], include_views: false, included_columns: {} }, execution_context_id: `step29-browser-${runId}` }) }, { mutation: true, key });
  }

  async prepare(runId: string, key = mutationKey("plan-prepare")): Promise<Preparation> {
    return request<Preparation>(`/api/v1/runs/${encodeURIComponent(runId)}/execution/prepare`, { method: "POST", body: JSON.stringify({ intent: { cross_source_mapping_requested: false, entity_resolution_requested: false, optional_semantic_evidence_enabled: false, learned_evidence_enabled: false } }) }, { mutation: true, key });
  }

  async submit(runId: string, key = mutationKey("execution-submit")): Promise<Submission> {
    return request<Submission>(`/api/v1/runs/${encodeURIComponent(runId)}/execution`, { method: "POST" }, { mutation: true, key });
  }

  async resume(runId: string, key = mutationKey("execution-resume")): Promise<Submission> {
    return request<Submission>(`/api/v1/runs/${encodeURIComponent(runId)}/resume`, { method: "POST" }, { mutation: true, key });
  }

  async cancel(runId: string, key = mutationKey("execution-cancel")): Promise<Submission> {
    return request<Submission>(`/api/v1/runs/${encodeURIComponent(runId)}/cancel`, { method: "POST" }, { mutation: true, key });
  }

  async run(runId: string): Promise<RunView> {
    return request<RunView>(`/api/v1/runs/${encodeURIComponent(runId)}`);
  }

  async summary(runId: string): Promise<Summary> {
    return request<Summary>(`/api/v1/runs/${encodeURIComponent(runId)}/product-summary`);
  }

  async reviewActions(runId: string, review: PendingReview, payload: Omit<ReviewActionMutation, "subject_artifact_id" | "subject_content_hash" | "context" | "expected_revision"> & { expected_revision?: number }, key = mutationKey("review-action")): Promise<ReviewActionResult> {
    return request<ReviewActionResult>(`/api/v1/runs/${encodeURIComponent(runId)}/reviews/${encodeURIComponent(review.checkpoint)}/actions`, { method: "POST", body: JSON.stringify({ ...payload, subject_artifact_id: review.subject_artifact_id, subject_content_hash: review.subject_content_hash, expected_revision: payload.expected_revision ?? review.action_revision }) }, { mutation: true, key });
  }

  async reviewHistory(runId: string): Promise<ReviewActionHistory[]> {
    const page = await request<{ items: ReviewActionHistory[] }>(`/api/v1/runs/${encodeURIComponent(runId)}/reviews/actions?page_size=100`);
    return page.items;
  }

  async review(runId: string, review: PendingReview, rationale: string, key = mutationKey("review")): Promise<Record<string, unknown>> {
    return request<Record<string, unknown>>(`/api/v1/runs/${encodeURIComponent(runId)}/reviews/${encodeURIComponent(review.checkpoint)}`, { method: "POST", body: JSON.stringify({ subject_artifact_id: review.subject_artifact_id, subject_content_hash: review.subject_content_hash, decision: "ACCEPTED", rationale, expected_revision: review.revision }) }, { mutation: true, key });
  }
}

type RunView = components["schemas"]["RunView"];
type Binding = { run_id: string; registry_id: string; source_id?: string | null; source_display_name: string; selection_artifact_id: string; extraction_max_rows?: number | null; extraction_chunk_size: number };
type Preparation = { status: string; plan_id?: string | null; planning_phase: string; detail: string };
type Submission = { status: string; detail: string; submission_id?: string | null };
export const api = new ApiClient();
export { baseUrl, mutationKey };
