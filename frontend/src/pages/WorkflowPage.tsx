import { useQuery } from "@tanstack/react-query";
import { listWorkflows, type WorkflowResponse, type WorkflowStatus } from "@/lib/api";
import { useDemoMode } from "@/demo/DemoContext";

// ── Status badge helper ────────────────────────────────────────────────────────
const STATUS_CONFIG: Record<WorkflowStatus, { label: string; cls: string }> = {
  completed:   { label: "PASS",    cls: "badge-pass"  },
  blocked:     { label: "BLOCK",   cls: "badge-block" },
  failed:      { label: "FAIL",    cls: "badge-block" },
  running:     { label: "RUNNING", cls: "badge-blue"  },
  queued:      { label: "QUEUED",  cls: "badge-muted" },
  uploaded:    { label: "UPLOADED", cls: "badge-muted" },
  interrupted: { label: "PAUSED",  cls: "badge-warn"  },
};

function StatusBadge({ status }: { status: WorkflowStatus }) {
  const cfg = STATUS_CONFIG[status] ?? STATUS_CONFIG.queued;
  return <span className={cfg.cls}>{cfg.label}</span>;
}

// ── Compliance badge (from result object) ──────────────────────────────────────
function ComplianceBadge({ result }: { result: Record<string, unknown> }) {
  const s = result?.compliance_status as string | undefined;
  if (!s) return <span style={{ color: "var(--text-muted)", fontFamily: "'JetBrains Mono', monospace", fontSize: "0.65rem" }}>—</span>;

  const map: Record<string, string> = {
    PASS:  "badge-pass",
    WARN:  "badge-warn",
    BLOCK: "badge-block",
  };
  const cls = map[s] ?? "badge-muted";
  return <span className={cls}>{s}</span>;
}

// ── Step count bar (mini pipeline progress) ────────────────────────────────────
const PIPELINE_STEPS = [
  "ingest",
  "preprocess",
  "ocr_extract",
  "vision_adjudication",
  "field_extract",
  "reconcile",
  "hs_rag",
  "deterministic_validate",
  "interrupt_node",
  "country_validate",
  "declaration_generate",
  "audit_trace",
];

function PipelineBar({ steps }: { steps: WorkflowResponse["steps"] }) {
  const done = new Set(
    steps
      .filter((s) => s.status === "completed")
      .map((s) => s.name)
  );
  const seen = new Set(steps.map((step) => step.name));
  const relevantSteps = PIPELINE_STEPS.filter((name) => seen.has(name));
  const total = relevantSteps.length || PIPELINE_STEPS.length;
  const count = (relevantSteps.length ? relevantSteps : PIPELINE_STEPS).filter((n) => done.has(n)).length;
  const pct   = Math.round((count / total) * 100);

  return (
    <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
      <div style={{
        width: "80px", height: "3px",
        backgroundColor: "var(--border)",
        position: "relative",
        flexShrink: 0,
      }}>
        <div style={{
          position: "absolute",
          left: 0, top: 0, bottom: 0,
          width: `${pct}%`,
          backgroundColor: pct === 100 ? "var(--accent-green)" : "var(--accent-blue)",
          transition: "width 0.4s ease",
        }} />
      </div>
      <span style={{
        fontFamily: "'JetBrains Mono', monospace",
        fontSize: "0.6rem",
        color: "var(--text-secondary)",
        whiteSpace: "nowrap",
      }}>
        {count}/{total}
      </span>
    </div>
  );
}

// ── View button ────────────────────────────────────────────────────────────────
function ViewBtn({ onClick }: { onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      style={{
        fontFamily:      "'JetBrains Mono', monospace",
        fontSize:        "0.65rem",
        fontWeight:      700,
        color:           "var(--accent-blue)",
        border:          "1px solid #1e3a5f",
        backgroundColor: "rgba(37, 99, 235, 0.06)",
        padding:         "5px 14px",
        cursor:          "pointer",
        letterSpacing:   "0.05em",
        transition:      "all 0.15s",
        whiteSpace:      "nowrap",
      }}
      onMouseEnter={(e) => {
        const el = e.currentTarget;
        el.style.backgroundColor = "rgba(37, 99, 235, 0.18)";
        el.style.borderColor = "var(--accent-blue)";
        el.style.boxShadow = "0 0 10px rgba(59,130,246,0.25)";
      }}
      onMouseLeave={(e) => {
        const el = e.currentTarget;
        el.style.backgroundColor = "rgba(37, 99, 235, 0.06)";
        el.style.borderColor = "var(--accent-blue)";
        el.style.boxShadow = "none";
      }}
    >
      VIEW&nbsp;→
    </button>
  );
}

// ── Table styles ───────────────────────────────────────────────────────────────
const TH: React.CSSProperties = {
  fontFamily:      "'JetBrains Mono', monospace",
  fontSize:        "0.58rem",
  fontWeight:      700,
  color:           "var(--text-secondary)",
  letterSpacing:   "0.16em",
  textAlign:       "left",
  padding:         "10px 16px",
  borderBottom:    "1px solid #1e293b",
  backgroundColor: "var(--header-bg)",
  whiteSpace:      "nowrap",
  userSelect:      "none",
};

const TD: React.CSSProperties = {
  fontFamily:   "'JetBrains Mono', monospace",
  fontSize:     "0.7rem",
  color:        "var(--text-muted)",
  padding:      "13px 16px",
  borderBottom: "1px solid #0f172a",
  verticalAlign:"middle",
  whiteSpace:   "nowrap",
};

// ── Props ──────────────────────────────────────────────────────────────────────
interface WorkflowPageProps {
  onViewDeclaration: (runId: string) => void;
}

export default function WorkflowPage({ onViewDeclaration }: WorkflowPageProps) {
  const { isDemoMode, demoWorkflows } = useDemoMode();

  const { data: realWorkflows = [], isLoading: realLoading } = useQuery({
    queryKey:        ["workflows"],
    queryFn:         listWorkflows,
    refetchInterval: 3000,
    enabled:         !isDemoMode,   // skip API when in demo mode
  });

  const workflows = isDemoMode ? demoWorkflows : realWorkflows;
  const isLoading = isDemoMode ? false : realLoading;

  return (
    <div style={{
      padding:         "24px",
      backgroundColor: "var(--bg-primary)",
      minHeight:       "100%",
    }}>
      {/* ── Page header ─────────────────────────────────────────────────── */}
      <div style={{
        display:        "flex",
        alignItems:     "flex-end",
        justifyContent: "space-between",
        marginBottom:   "20px",
      }}>
        <div>
          <h1 style={{
            fontFamily:    "'Space Grotesk', sans-serif",
            fontWeight:    700,
            fontSize:      "1rem",
            color:         "var(--text-primary)",
            letterSpacing: "0.04em",
            margin:        0,
          }}>
            WORKFLOW RUNS
          </h1>
          <p style={{
            fontFamily: "'JetBrains Mono', monospace",
            fontSize:   "0.62rem",
            color:      "var(--text-secondary)",
            marginTop:  "3px",
          }}>
            {workflows.length} RUN{workflows.length !== 1 ? "S" : ""}&nbsp;
            &nbsp;·&nbsp;&nbsp;AUTO-REFRESH 3s
          </p>
        </div>

        {/* Live indicator */}
        <div style={{
          display:         "flex",
          alignItems:      "center",
          gap:             "7px",
          backgroundColor: "rgba(37, 99, 235, 0.07)",
          border:          "1px solid #1e3a5f",
          padding:         "5px 14px",
        }}>
          <span style={{
            display:         "inline-block",
            width:           "6px",
            height:          "6px",
            borderRadius:    "50%",
            backgroundColor: "var(--accent-blue)",
            animation:       "pulse-dot 2s ease-in-out infinite",
          }} />
          <span style={{
            fontFamily:    "'JetBrains Mono', monospace",
            fontSize:      "0.58rem",
            fontWeight:    700,
            color:         "var(--accent-blue)",
            letterSpacing: "0.12em",
          }}>
            LIVE
          </span>
        </div>
      </div>

      {/* ── Loading state ───────────────────────────────────────────────── */}
      {isLoading && (
        <div style={{
          textAlign:   "center",
          padding:     "80px",
          color:       "var(--text-muted)",
          fontFamily:  "'JetBrains Mono', monospace",
          fontSize:    "0.72rem",
          letterSpacing: "0.08em",
          border:      "1px solid #1e293b",
          backgroundColor: "var(--bg-card)",
        }}>
          [ FETCHING WORKFLOWS... ]
        </div>
      )}

      {/* ── Empty state ──────────────────────────────────────────────────── */}
      {!isLoading && workflows.length === 0 && (
        <div style={{
          textAlign:       "center",
          padding:         "80px 20px",
          border:          "1px solid #1e293b",
          backgroundColor: "var(--bg-card)",
        }}>
          <p style={{
            fontFamily:    "'JetBrains Mono', monospace",
            fontSize:      "0.72rem",
            color:         "var(--text-muted)",
            letterSpacing: "0.12em",
          }}>
            NO ACTIVE WORKFLOWS — UPLOAD DOCUMENTS TO BEGIN
          </p>
        </div>
      )}

      {/* ── Workflow table ───────────────────────────────────────────────── */}
      {!isLoading && workflows.length > 0 && (
        <div style={{
          border:          "1px solid #1e293b",
          backgroundColor: "var(--bg-card)",
          overflow:        "auto",
        }}>
          <table style={{ width: "100%", borderCollapse: "collapse", minWidth: "700px" }}>
            <thead>
              <tr>
                {[
                  "RUN_ID",
                  "STATUS",
                  "COMPLIANCE",
                  "PIPELINE",
                  "COUNTRY",
                  "TIMESTAMP",
                  "ACTIONS",
                ].map((h) => (
                  <th key={h} style={TH}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {workflows.map((wf: WorkflowResponse) => (
                <tr
                  key={wf.id}
                  style={{ transition: "background 0.12s" }}
                  onMouseEnter={(e) => {
                    (e.currentTarget as HTMLElement).style.backgroundColor =
                      "#f1f5f9";
                  }}
                  onMouseLeave={(e) => {
                    (e.currentTarget as HTMLElement).style.backgroundColor =
                      "transparent";
                  }}
                >
                  {/* RUN_ID */}
                  <td style={TD}>
                    <span style={{ color: "var(--accent-blue)", fontSize: "0.65rem", fontFamily: "'JetBrains Mono', monospace" }}>
                      {wf.id.slice(0, 8)}
                    </span>
                    <span style={{ color: "var(--text-muted)", fontSize: "0.65rem" }}>…</span>
                    <span style={{ color: "var(--accent-blue)", fontSize: "0.65rem", fontFamily: "'JetBrains Mono', monospace" }}>
                      {wf.id.slice(-4)}
                    </span>
                  </td>

                  {/* STATUS */}
                  <td style={TD}>
                    <StatusBadge status={wf.status} />
                  </td>

                  {/* COMPLIANCE */}
                  <td style={TD}>
                    <ComplianceBadge result={wf.result} />
                  </td>

                  {/* PIPELINE */}
                  <td style={TD}>
                    <PipelineBar steps={wf.steps} />
                  </td>

                  {/* COUNTRY */}
                  <td style={TD}>
                    <span style={{ color: "var(--text-muted)", fontSize: "0.65rem", letterSpacing: "0.06em" }}>
                      {wf.country.toUpperCase()}
                    </span>
                  </td>

                  {/* TIMESTAMP */}
                  <td style={TD}>
                    <span style={{ color: "var(--text-secondary)", fontSize: "0.63rem" }}>
                      {new Date(wf.created_at).toLocaleString("en-US", {
                        month:   "2-digit",
                        day:     "2-digit",
                        hour:    "2-digit",
                        minute:  "2-digit",
                        second:  "2-digit",
                        hour12:  false,
                      })}
                    </span>
                  </td>

                  {/* ACTIONS */}
                  <td style={TD}>
                    <ViewBtn onClick={() => onViewDeclaration(wf.id)} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
