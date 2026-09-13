import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import Root from "./main";

vi.mock("./api/client", () => ({
  api: {
    sources: vi.fn().mockResolvedValue([]),
    configuration: vi.fn().mockResolvedValue({ configuration_fingerprint: "cfg", authentication_mode: "local_test_reference_only" }),
  },
  ApiError: class ApiError extends Error {},
}));

describe("setup interaction", () => {
  it("has labelled source and project controls", async () => {
    render(<Root />);
    expect(await screen.findByRole("heading", { name: /managed csv/i })).toBeInTheDocument();
    expect(screen.getByLabelText(/choose a csv file/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/project context/i)).toBeInTheDocument();
  });
});
