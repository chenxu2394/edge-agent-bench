import { readFileSync } from "node:fs";
import { Type } from "typebox";
import { defineToolPlugin } from "openclaw/plugin-sdk/tool-plugin";

type Metric = {
  evidenceId: string;
  timestamp: string;
  name: string;
  value: number;
  unit: string;
};

type IncidentLog = {
  evidenceId: string;
  timestamp: string;
  service: string;
  level: string;
  message: string;
};

type Incident = {
  id: string;
  metrics: Metric[];
  logs: IncidentLog[];
};

type IncidentDataset = {
  version: number;
  incidents: Incident[];
};

const datasetUrl = new URL("../data/incidents.json", import.meta.url);

const dataset = JSON.parse(readFileSync(datasetUrl, "utf8")) as IncidentDataset;

const incidents = new Map(
  dataset.incidents.map((incident) => [incident.id, incident]),
);

function getIncident(incidentId: string): Incident {
  if (!/^INC-[0-9]{3}$/.test(incidentId)) {
    throw new Error("Invalid incident ID format");
  }

  const incident = incidents.get(incidentId);

  if (!incident) {
    throw new Error(`Unknown incident: ${incidentId}`);
  }

  return incident;
}

export function getIncidentMetrics(incidentId: string) {
  const incident = getIncident(incidentId);

  return {
    incidentId,
    metrics: incident.metrics.map((metric) => ({
      ...metric,
    })),
  };
}

export function getIncidentLogs(incidentId: string) {
  const incident = getIncident(incidentId);

  return {
    incidentId,
    logs: incident.logs.map((entry) => ({
      ...entry,
    })),
  };
}

const incidentParameters = Type.Object(
  {
    incidentId: Type.String({
      pattern: "^INC-[0-9]{3}$",
      description: "Incident identifier, for example INC-001.",
    }),
  },
  {
    additionalProperties: false,
  },
);

export default defineToolPlugin({
  id: "edge-agent-bench",
  name: "EdgeAgentBench",
  description: "Read-only tools for deterministic IT incident evaluation.",

  tools: (tool) => [
    tool({
      name: "get_incident_metrics",
      optional: true,
      description:
        "Retrieve deterministic device and telemetry metrics for an incident. Each observation includes an evidence ID.",
      parameters: incidentParameters,
      execute: async ({ incidentId }) => getIncidentMetrics(incidentId),
    }),

    tool({
      name: "get_incident_logs",
      optional: true,
      description:
        "Retrieve deterministic application and service logs for an incident. Each log entry includes an evidence ID.",
      parameters: incidentParameters,
      execute: async ({ incidentId }) => getIncidentLogs(incidentId),
    }),
  ],
});
