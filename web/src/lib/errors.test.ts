import { describe, expect, it } from "vitest";
import { isAuthError } from "./errors";

describe("isAuthError", () => {
  it("recognises rejected or missing credentials", () => {
    expect(isAuthError("HTTP 401: Invalid API key provided")).toBe(true);
    expect(isAuthError("403 Forbidden")).toBe(true);
    expect(isAuthError("authentication_error: invalid x-api-key")).toBe(true);
  });

  it("leaves other failures to Retry alone", () => {
    expect(isAuthError("429 Too Many Requests: rate limit exceeded. Retry after 20s.")).toBe(false);
    expect(isAuthError("500 Internal Server Error")).toBe(false);
    expect(isAuthError("Overloaded")).toBe(false);
  });
});
