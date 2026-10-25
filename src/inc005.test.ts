import { describe, expect, it } from "vitest";
import { getIncidentLogs, getIncidentMetrics } from "./index.js";

const incidentId = "INC-005";

function parseRecord(message: string) {
  const match = message.match(
    /device_id=([^,]+), sequence=(\d+), payload_hash=([a-f0-9]+)/,
  );

  if (!match) {
    throw new Error(`Cannot parse telemetry record: ${message}`);
  }

  return {
    deviceId: match[1],
    sequence: match[2],
    payloadHash: match[3],
  };
}

describe("INC-005 deduplication collision fixture", () => {
  const logs = getIncidentLogs(incidentId);
  const metrics = getIncidentMetrics(incidentId);

  function log(id: string): string {
    const entry = logs.logs.find((item) => item.evidenceId === id);

    if (!entry) {
      throw new Error(`Missing log: ${id}`);
    }

    return entry.message;
  }

  function metric(id: string): number {
    const entry = metrics.metrics.find((item) => item.evidenceId === id);

    if (!entry) {
      throw new Error(`Missing metric: ${id}`);
    }

    return entry.value;
  }

  it("serves deterministic incident data", () => {
    expect(logs.incidentId).toBe(incidentId);
    expect(metrics.incidentId).toBe(incidentId);

    expect(getIncidentLogs(incidentId)).toEqual(logs);
    expect(getIncidentMetrics(incidentId)).toEqual(metrics);

    const output = JSON.stringify({ logs, metrics });

    expect(output).not.toContain("rootCauseCode");
    expect(output).not.toContain("rootCauseDescription");
    expect(output).not.toContain("requiredEvidenceIds");
  });

  it("records the effective idempotency key change", () => {
    expect(log("L-040")).toContain("fields=device_id,sequence");

    expect(log("L-041")).toContain("fields=sequence");
  });

  it("shows distinct device records sharing one deduplication key", () => {
    const pairs = [
      ["L-043", "L-045"],
      ["L-046", "L-047"],
    ] as const;

    for (const [acceptedId, suppressedId] of pairs) {
      const acceptedLog = log(acceptedId);
      const suppressedLog = log(suppressedId);

      const accepted = parseRecord(acceptedLog);
      const suppressed = parseRecord(suppressedLog);

      expect(accepted.deviceId).not.toBe(suppressed.deviceId);

      expect(accepted.sequence).toBe(suppressed.sequence);

      expect(accepted.payloadHash).not.toBe(suppressed.payloadHash);

      const acceptedKey = acceptedLog.match(/dedupe_key=(\d+)/)?.[1];

      const suppressedKey = suppressedLog.match(/dedupe_key=(\d+)/)?.[1];

      expect(acceptedKey).toBe(accepted.sequence);
      expect(suppressedKey).toBe(acceptedKey);

      expect(acceptedLog).toContain("disposition=accepted");

      expect(suppressedLog).toContain("disposition=suppressed_duplicate");
    }
  });

  it("reconciles received, accepted, and suppressed counts", () => {
    expect(metric("M-042")).toBe(240);
    expect(metric("M-043")).toBe(240);

    const accepted = metric("M-045") + metric("M-046");

    const suppressed = metric("M-047") + metric("M-048");

    expect(accepted).toBe(120);
    expect(suppressed).toBe(120);

    expect(accepted + suppressed).toBe(metric("M-043"));

    expect(metric("M-046")).toBe(0);
    expect(metric("M-047")).toBe(120);

    expect(metric("M-049")).toBe(240);
    expect(metric("M-050")).toBe(120);
  });

  it("does not imply transport, replay, or sink failure", () => {
    expect(metric("M-044")).toBe(100);
    expect(metric("M-051")).toBe(0);
    expect(metric("M-052")).toBe(1);
    expect(metric("M-053")).toBe(0);
    expect(metric("M-056")).toBe(100);

    expect(Math.abs(metric("M-055"))).toBeLessThan(1);
  });
});
