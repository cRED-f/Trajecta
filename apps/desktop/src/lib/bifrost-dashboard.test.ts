import { describe, expect, it } from "vitest";
import { bifrostDashboardUrl } from "./bifrost-dashboard";

describe("bifrostDashboardUrl", () => {
  it("uses the configured gateway host instead of assuming localhost", () => {
    expect(bifrostDashboardUrl("http://127.0.0.1:8080/v1"))
      .toBe("http://127.0.0.1:8080/");
    expect(bifrostDashboardUrl("https://gateway.example.test:8443/v1"))
      .toBe("https://gateway.example.test:8443/");
  });

  it("does not open invalid or non-web URLs", () => {
    expect(bifrostDashboardUrl(null)).toBeNull();
    expect(bifrostDashboardUrl("file:///tmp/secret")).toBeNull();
    expect(bifrostDashboardUrl("not a URL")).toBeNull();
  });
});
