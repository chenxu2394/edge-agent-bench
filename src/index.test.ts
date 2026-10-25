import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { getToolPluginMetadata } from "openclaw/plugin-sdk/tool-plugin";

import plugin, { getIncidentMetrics, getIncidentLogs } from "./index.js";

describe("EdgeAgentBench plugin", () => {
  it("registers exactly two benchmark tools", () => {
    const metadata = getToolPluginMetadata(plugin);

    expect(metadata?.tools.map((tool) => tool.name)).toEqual([
      "get_incident_metrics",
      "get_incident_logs",
    ]);
  });

  it("returns deterministic metrics", () => {
    const first = getIncidentMetrics("INC-001");
    const second = getIncidentMetrics("INC-001");

    expect(first).toEqual(second);
    expect(first.incidentId).toBe("INC-001");

    const diskMetric = first.metrics.find(
      (metric) => metric.name === "disk_used_percent",
    );

    expect(diskMetric?.value).toBe(99);
    expect(diskMetric?.evidenceId).toBe("M-001");
  });

  it("returns deterministic logs", () => {
    const first = getIncidentLogs("INC-001");
    const second = getIncidentLogs("INC-001");

    expect(first).toEqual(second);
    expect(first.incidentId).toBe("INC-001");

    const errorLog = first.logs.find((entry) => entry.evidenceId === "L-002");

    expect(errorLog?.message).toContain("No space left on device");
  });

  it.each([
    {
      id: "INC-002",
      logIds: ["L-013", "L-015"],
      metricIds: ["M-013", "M-012"],
    },
    {
      id: "INC-003",
      logIds: ["L-022", "L-024"],
      metricIds: ["M-025", "M-023"],
    },
  ])(
    "serves $id deterministically with the required evidence",
    ({ id, logIds, metricIds }) => {
      const logs = getIncidentLogs(id);
      const metrics = getIncidentMetrics(id);

      expect(logs.incidentId).toBe(id);
      expect(metrics.incidentId).toBe(id);
      expect(logs).toEqual(getIncidentLogs(id));
      expect(metrics).toEqual(getIncidentMetrics(id));

      const logEvidence = new Set(logs.logs.map((entry) => entry.evidenceId));
      const metricEvidence = new Set(
        metrics.metrics.map((entry) => entry.evidenceId),
      );
      for (const evidenceId of logIds) {
        expect(logEvidence.has(evidenceId)).toBe(true);
      }
      for (const evidenceId of metricIds) {
        expect(metricEvidence.has(evidenceId)).toBe(true);
      }

      const output = JSON.stringify({ logs, metrics });
      expect(output).not.toContain("rootCauseCode");
      expect(output).not.toContain("rootCauseDescription");
      expect(output).not.toContain("requiredEvidenceIds");
    },
  );

  it("uses unique evidence identifiers across all configured incidents", () => {
    const fixtures = JSON.parse(
      readFileSync(new URL("../data/incidents.json", import.meta.url), "utf8"),
    ) as { incidents: Array<{ id: string }> };
    const seen = new Set<string>();
    let expectedCount = 0;
    for (const { id } of fixtures.incidents) {
      const logs = getIncidentLogs(id);
      const metrics = getIncidentMetrics(id);
      const records = [...logs.logs, ...metrics.metrics];
      expectedCount += records.length;
      for (const entry of records) {
        expect(seen.has(entry.evidenceId)).toBe(false);
        seen.add(entry.evidenceId);
      }
    }
    expect(seen.size).toBe(expectedCount);
  });

  it("rejects unknown incidents", () => {
    expect(() => {
      getIncidentMetrics("INC-999");
    }).toThrow("Unknown incident");

    expect(() => {
      getIncidentLogs("INC-999");
    }).toThrow("Unknown incident");
  });

  it("rejects malformed incident identifiers", () => {
    expect(() => {
      getIncidentMetrics("../INC-001");
    }).toThrow("Invalid incident ID format");
  });

  it("does not expose the answer key through tool results", () => {
    const metrics = getIncidentMetrics("INC-001");
    const logs = getIncidentLogs("INC-001");

    const output = JSON.stringify({
      metrics,
      logs,
    });

    expect(output).not.toContain("rootCauseCode");
    expect(output).not.toContain("rootCauseDescription");
    expect(output).not.toContain("requiredEvidenceIds");
  });

  it("serves INC-004 with consistent event-time evidence", () => {
    const incidentId = "INC-004";

    const logs = getIncidentLogs(incidentId);
    const metrics = getIncidentMetrics(incidentId);

    expect(logs.incidentId).toBe(incidentId);
    expect(metrics.incidentId).toBe(incidentId);

    expect(logs).toEqual(getIncidentLogs(incidentId));
    expect(metrics).toEqual(getIncidentMetrics(incidentId));

    const sample = logs.logs.find((entry) => entry.evidenceId === "L-033");

    const rejection = logs.logs.find((entry) => entry.evidenceId === "L-035");

    expect(sample).toBeDefined();
    expect(rejection).toBeDefined();

    const eventTime = sample?.message.match(/event_time=([0-9TZ:-]+)/)?.[1];

    const receivedAt = rejection?.message.match(
      /received at ([0-9TZ:-]+)/,
    )?.[1];

    const eventAgeSeconds =
      (Date.parse(receivedAt ?? "") - Date.parse(eventTime ?? "")) / 1000;

    expect(eventAgeSeconds).toBe(482);

    expect(rejection?.message).toContain("max_event_age_seconds=120");

    const metricValue = (evidenceId: string): number | undefined =>
      metrics.metrics.find((metric) => metric.evidenceId === evidenceId)?.value;

    expect(metricValue("M-035")).toBe(120);
    expect(metricValue("M-036")).toBe(0);
    expect(metricValue("M-037")).toBe(120);
    expect(metricValue("M-038")).toBe(-480);
    expect(metricValue("M-039")).toBeLessThan(1000);
    expect(metricValue("M-040")).toBe(0);
    expect(metricValue("M-041")).toBe(100);

    const toolOutput = JSON.stringify({ logs, metrics });

    expect(toolOutput).not.toContain("rootCauseCode");
    expect(toolOutput).not.toContain("rootCauseDescription");
    expect(toolOutput).not.toContain("requiredEvidenceIds");
  });
});
