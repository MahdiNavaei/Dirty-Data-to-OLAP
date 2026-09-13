import { describe, expect, it } from "vitest";
import { ApiError } from "./client";

describe("API error semantics", () => {
  it("keeps delivery uncertainty visible", () => {
    const error = new ApiError(503, { error: { code: "DELIVERY_UNKNOWN", message: "reconcile", retryable: true } });
    expect(error.code).toBe("DELIVERY_UNKNOWN");
    expect(error.retryable).toBe(true);
    expect(error.message).toBe("reconcile");
  });
  it("does not reinterpret a validation conflict as success", () => {
    const error = new ApiError(409, { error: { code: "REVIEW_REVISION_CONFLICT", message: "stale" } });
    expect(error.status).toBe(409);
    expect(error.code).toBe("REVIEW_REVISION_CONFLICT");
  });
});
