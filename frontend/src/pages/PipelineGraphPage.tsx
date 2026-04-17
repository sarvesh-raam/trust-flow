import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ReactFlow,
  Background,
  Controls,
  Handle,
  Position,
  type Node,
  type Edge,
  type NodeProps,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { useQuery } from "@tanstack/react-query";
import { type WorkflowResponse, type WorkflowStep } from "@/lib/api";

// ── Pipeline topology ──────────────────────────────────────────────────────────
const NODES_DEF: { id: string; x: number; y: number; label: string }[] = [
  { id: "ingest",                 x: 140, y: 0,   label: "INGEST"                },
  { id: "preprocess",             x: 140, y: 80,  label: "PREPROCESS"            },
  { id: "ocr_extract",            x: 140, y: 160, label: "OCR_EXTRACT"           },
  { id: "vision_adjudication",    x: 0,   y: 240, label: "VISION_ADJUDICATION"   },
  { id: "field_extract",          x: 140, y: 320, label: "FIELD_EXTRACT"         },
  { id: "reconcile",              x: 140, y: 400, label: "RECONCILE"             },
  { id: "hs_rag",                 x: 140, y: 480, label: "HS_RAG"                },
  { id: "deterministic_validate", x: 140, y: 560, label: "DET_VALIDATE"          },
  { id: "interrupt_node",         x: 0,   y: 640, label: "INTERRUPT_NODE"        },
  { id: "country_validate",       x: 140, y: 720, label: "COUNTRY_VALIDATE"      },
  { id: "declaration_generate",   x: 140, y: 800, label: "DECL_GENERATE"         },
  { id: "audit_trace",            x: 140, y: 880, label: "AUDIT_TRACE"           },
];

const EDGES_DEF: { id: string; source: string; target: string; label?: string }[] = [
  { id: "e1",  source: "ingest",                 target: "preprocess"              },
  { id: "e2",  source: "preprocess",             target: "ocr_extract"             },
  { id: "e3",  source: "ocr_extract",            target: "vision_adjudication", label: "conf<0.7" },
  { id: "e4",  source: "ocr_extract",            target: "field_extract",       label: "conf≥0.7" },
  { id: "e5",  source: "vision_adjudication",    target: "field_extract"           },
  { id: "e6",  source: "field_extract",          target: "reconcile"               },
  { id: "e7",  source: "reconcile",              target: "hs_rag"                  },
  { id: "e8",  source: "hs_rag",                 target: "deterministic_validate"  },
  { id: "e9",  source: "deterministic_validate", target: "interrupt_node",      label: "BLOCK"    },
  { id: "e10", source: "deterministic_validate", target: "country_validate",    label: "OK"       },
  { id: "e11", source: "interrupt_node",         target: "country_validate"        },
  { id: "e12", source: "country_validate",       target: "declaration_generate"    },
  { id: "e13", source: "declaration_generate",   target: "audit_trace"             },
];

// ── Node status → visual style ─────────────────────────────────────────────────
type NodeStatus = WorkflowStep["status"] | "idle";

const STATUS_STYLE: Record<NodeStatus, { border: string; bg: string; color: string; glow?: string }> = {
  idle:        { border: "#1e293b", bg: "rgba(15,23,42,0.9)",    color: "#475569" },
  pending:     { border: "#1e293b", bg: "rgba(15,23,42,0.9)",    color: "#475569" },
  queued:      { border: "#1e3a5f", bg: "rgba(15,23,42,0.9)",    color: "#64748b" },
  running:     { border: "#3B82F6", bg: "rgba(37,99,235,0.14)",  color: "#3B82F6", glow: "0 0 10px rgba(59,130,246,0.4)" },
  completed:   { border: "#16a34a", bg: "rgba(22,163,74,0.1)",   color: "#22c55e" },
  blocked:     { border: "#ef4444", bg: "rgba(239,68,68,0.12)",  color: "#ef4444", glow: "0 0 10px rgba(239,68,68,0.4)" },
  interrupted: { border: "#f59e0b", bg: "rgba(245,158,11,0.12)", color: "#f59e0b" },
  failed:      { border: "#ef4444", bg: "rgba(239,68,68,0.12)",  color: "#ef4444" },
};

// ── Custom node component ──────────────────────────────────────────────────────
interface PipelineNodeData {
  label: string;
  status: NodeStatus;
  durationMs?: number;
  [key: string]: unknown;
}

function PipelineNode({ data }: NodeProps<Node<PipelineNodeData>>) {
  const s = STATUS_STYLE[data.status] ?? STATUS_STYLE.idle;
  return (
    <div
      style={{
        width: 164,
        padding: "7px 10px",
        border: `1px solid ${s.border}`,
        backgroundColor: s.bg,
        boxShadow: s.glow ?? "none",
        fontFamily: "'JetBrains Mono', monospace",
        fontSize: "0.58rem",
        letterSpacing: "0.07em",
        color: s.color,
        transition: "all 0.3s",
        animation: data.status === "running" ? "pulse-node 1.6s ease-in-out infinite" : "none",
      }}
    >
      <Handle type="target" position={Position.Top} style={{ background: s.border }} />
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <span style={{ fontWeight: 700 }}>{data.label}</span>
        <span style={{
          fontSize: "0.48rem",
          letterSpacing: "0.1em",
          color: s.color,
          opacity: 0.85,
          marginLeft: 4,
        }}>
          {data.status === "idle" ? "—" : data.status.toUpperCase()}
        </span>
      </div>
      {data.durationMs !== undefined && data.durationMs > 0 && (
        <div style={{ fontSize: "0.46rem", color: "#475569", marginTop: 2 }}>
          {(data.durationMs / 1000).toFixed(2)}s
        </div>
      )}
      <Handle type="source" position={Position.Bottom} style={{ background: s.border }} />
    </div>
  );
}

const nodeTypes = { pipelineNode: PipelineNode };

// ── Log level → colour ─────────────────────────────────────────────────────────
const LOG_COLORS: Record<string, string> = {
  debug:    "#374151",
  info:     "#64748b",
  warning:  "#f59e0b",
  warn:     "#f59e0b",
  error:    "#ef4444",
  critical: "#ef4444",
};

interface LogEntry {
  ts: string;
  level: string;
  event: string;
  node_name?: string;
  [key: string]: string | undefined;
}

// ── Helpers ────────────────────────────────────────────────────────────────────
function buildStepMap(steps: WorkflowStep[]): Record<string, WorkflowStep> {
  const m: Record<string, WorkflowStep> = {};
  for (const s of steps) m[s.name] = s;
  return m;
}

function stepDuration(step: WorkflowStep): number | undefined {
  if (step.started_at && step.completed_at) {
    return new Date(step.completed_at).getTime() - new Date(step.started_at).getTime();
  }
  return undefined;
}

// ── Main page ──────────────────────────────────────────────────────────────────
export default function PipelineGraphPage() {
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const logEndRef = useRef<HTMLDivElement>(null);
  const sseRef = useRef<EventSource | null>(null);

  // Poll workflow list
  const { data: workflows = [] } = useQuery<WorkflowResponse[]>({
    queryKey: ["workflows"],
    queryFn: () => import("@/lib/api").then((m) => m.listWorkflows()),
    refetchInterval: 3000,
  });

  // Auto-select latest running → latest overall
  useEffect(() => {
    if (workflows.length === 0) return;
    if (selectedRunId && workflows.some((w) => w.id === selectedRunId)) return;
    const running = workflows.find((w) => w.status === "running" || w.status === "blocked");
    setSelectedRunId(running?.id ?? workflows[0].id);
  }, [workflows, selectedRunId]);

  const selectedWorkflow = workflows.find((w) => w.id === selectedRunId);
  const stepMap = useMemo(
    () => (selectedWorkflow ? buildStepMap(selectedWorkflow.steps) : {}),
    [selectedWorkflow],
  );

  // Build ReactFlow nodes
  const rfNodes: Node<PipelineNodeData>[] = useMemo(() =>
    NODES_DEF.map((n) => {
      const step = stepMap[n.id];
      const status: NodeStatus = step?.status ?? "idle";
      return {
        id: n.id,
        type: "pipelineNode",
        position: { x: n.x, y: n.y },
        data: {
          label: n.label,
          status,
          durationMs: step ? stepDuration(step) : undefined,
        },
        draggable: false,
      };
    }),
    [stepMap],
  );

  // Build ReactFlow edges
  const rfEdges: Edge[] = useMemo(() =>
    EDGES_DEF.map((e) => ({
      id: e.id,
      source: e.source,
      target: e.target,
      label: e.label,
      style: { stroke: "#1e293b", strokeWidth: 1.5 },
      labelStyle: { fontFamily: "'JetBrains Mono', monospace", fontSize: 8, fill: "#475569" },
      labelBgStyle: { fill: "rgba(15,23,42,0.9)" },
      type: "smoothstep",
      animated: false,
    })),
    [],
  );

  // SSE log stream
  useEffect(() => {
    const token = localStorage.getItem("access_token");
    if (!token) return;
    const apiBase =
      (import.meta.env.VITE_API_BASE
        ? `${import.meta.env.VITE_API_BASE}/v1`
        : window.location.hostname === "localhost"
          ? "http://localhost:8000/api/v1"
          : "/api/v1");

    const es = new EventSource(`${apiBase}/logs/stream?token=${encodeURIComponent(token)}`);
    sseRef.current = es;

    es.onmessage = (ev) => {
      try {
        const entry: LogEntry = JSON.parse(ev.data);
        setLogs((prev) => [...prev.slice(-499), entry]);
      } catch {
        // ignore malformed frames
      }
    };
    return () => {
      es.close();
      sseRef.current = null;
    };
  }, []);

  // Auto-scroll log panel
  useEffect(() => {
    logEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [logs]);

  const clearLogs = useCallback(() => setLogs([]), []);

  return (
    <div style={{ display: "flex", height: "calc(100vh - 44px)", backgroundColor: "var(--bg-primary)" }}>

      {/* ── Left: Pipeline graph ──────────────────────────────────────────── */}
      <div style={{ width: "360px", flexShrink: 0, display: "flex", flexDirection: "column", borderRight: "1px solid #1e293b" }}>
        {/* Header */}
        <div style={{ padding: "10px 14px", borderBottom: "1px solid #1e293b", display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <span style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: "0.6rem", fontWeight: 700, letterSpacing: "0.14em", color: "var(--text-secondary)" }}>
            PIPELINE GRAPH
          </span>
          {/* Workflow selector */}
          <select
            value={selectedRunId ?? ""}
            onChange={(e) => setSelectedRunId(e.target.value)}
            style={{
              fontFamily: "'JetBrains Mono', monospace",
              fontSize: "0.55rem",
              backgroundColor: "var(--bg-card)",
              border: "1px solid #1e293b",
              color: "var(--text-secondary)",
              padding: "3px 6px",
              cursor: "pointer",
              maxWidth: "160px",
            }}
          >
            {workflows.length === 0 && <option value="">— no runs —</option>}
            {workflows.map((w) => (
              <option key={w.id} value={w.id}>
                {w.id.slice(0, 8)}… [{w.status}]
              </option>
            ))}
          </select>
        </div>

        {/* Legend */}
        <div style={{ padding: "6px 14px", borderBottom: "1px solid #1e293b", display: "flex", gap: 10, flexWrap: "wrap" }}>
          {(["idle", "running", "completed", "blocked", "failed"] as NodeStatus[]).map((s) => (
            <div key={s} style={{ display: "flex", alignItems: "center", gap: 4 }}>
              <span style={{ width: 7, height: 7, borderRadius: 2, backgroundColor: STATUS_STYLE[s].border, display: "inline-block" }} />
              <span style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: "0.48rem", color: STATUS_STYLE[s].color, letterSpacing: "0.06em" }}>
                {s.toUpperCase()}
              </span>
            </div>
          ))}
        </div>

        {/* ReactFlow canvas */}
        <div style={{ flex: 1, position: "relative" }}>
          <style>{`
            @keyframes pulse-node {
              0%, 100% { opacity: 1; }
              50% { opacity: 0.65; }
            }
            .react-flow__node { padding: 0 !important; border: none !important; background: none !important; }
            .react-flow__edge-label { font-size: 8px !important; }
          `}</style>
          <ReactFlow
            nodes={rfNodes}
            edges={rfEdges}
            nodeTypes={nodeTypes}
            fitView
            fitViewOptions={{ padding: 0.15 }}
            proOptions={{ hideAttribution: true }}
            minZoom={0.4}
            maxZoom={2}
            style={{ backgroundColor: "var(--bg-primary)" }}
          >
            <Background color="#1e293b" gap={24} size={1} />
            <Controls
              style={{ bottom: 10, left: 10, top: "auto" }}
              showInteractive={false}
            />
          </ReactFlow>
        </div>
      </div>

      {/* ── Right: Live log stream ────────────────────────────────────────── */}
      <div style={{ flex: 1, display: "flex", flexDirection: "column", minWidth: 0 }}>
        {/* Header */}
        <div style={{ padding: "10px 14px", borderBottom: "1px solid #1e293b", display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <span style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: "0.6rem", fontWeight: 700, letterSpacing: "0.14em", color: "var(--text-secondary)" }}>
              LIVE LOGS
            </span>
            <span style={{
              display: "inline-flex", alignItems: "center", gap: 5,
              fontFamily: "'JetBrains Mono', monospace", fontSize: "0.52rem",
              color: sseRef.current?.readyState === 1 ? "var(--accent-green)" : "#ef4444",
            }}>
              <span style={{ width: 5, height: 5, borderRadius: "50%", backgroundColor: "currentColor", display: "inline-block", animation: "pulse-dot 2s ease-in-out infinite" }} />
              {sseRef.current?.readyState === 1 ? "SSE CONNECTED" : "CONNECTING…"}
            </span>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <span style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: "0.52rem", color: "var(--text-muted)" }}>
              {logs.length} EVENTS
            </span>
            <button
              onClick={clearLogs}
              style={{
                fontFamily: "'JetBrains Mono', monospace", fontSize: "0.52rem", fontWeight: 700,
                letterSpacing: "0.08em", padding: "3px 10px",
                backgroundColor: "transparent", border: "1px solid #1e293b",
                color: "var(--text-secondary)", cursor: "pointer",
              }}
            >
              CLEAR
            </button>
          </div>
        </div>

        {/* Log lines */}
        <div style={{
          flex: 1, overflowY: "auto", padding: "8px 14px",
          fontFamily: "'JetBrains Mono', monospace", fontSize: "0.62rem",
          lineHeight: 1.65, backgroundColor: "var(--bg-primary)",
        }}>
          {logs.length === 0 && (
            <div style={{ color: "#374151", paddingTop: 20, textAlign: "center", letterSpacing: "0.12em", fontSize: "0.58rem" }}>
              [ WAITING FOR LOG EVENTS… ]
            </div>
          )}
          {logs.map((entry, i) => {
            const col = LOG_COLORS[entry.level] ?? "#64748b";
            const ts = entry.ts ? entry.ts.slice(11, 23) : "";
            const extra = Object.entries(entry)
              .filter(([k]) => !["ts", "level", "event"].includes(k))
              .map(([k, v]) => `${k}=${v}`)
              .join(" ");
            return (
              <div key={i} style={{ display: "flex", gap: 8, borderBottom: "1px solid rgba(30,41,59,0.4)", padding: "2px 0" }}>
                <span style={{ color: "#334155", flexShrink: 0, userSelect: "none" }}>{ts}</span>
                <span style={{
                  color: col, flexShrink: 0, fontWeight: 700,
                  minWidth: 48, letterSpacing: "0.06em",
                }}>
                  {entry.level?.toUpperCase().slice(0, 4)}
                </span>
                <span style={{ color: "var(--text-primary)", flexShrink: 0, minWidth: 0, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                  {entry.event}
                </span>
                {extra && (
                  <span style={{ color: "#475569", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {extra}
                  </span>
                )}
              </div>
            );
          })}
          <div ref={logEndRef} />
        </div>
      </div>
    </div>
  );
}
