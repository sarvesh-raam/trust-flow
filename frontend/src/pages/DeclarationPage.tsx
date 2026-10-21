import { useEffect, useState } from "react";
import axios from "axios";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { getRunStatus, resumeWorkflow, type BBoxEntry, type ComplianceIssue, type WorkflowStep } from "@/lib/api";
import { buildCorrections, correctionFieldsFor, currentValue, type CorrectionField } from "@/lib/corrections";
import PDFViewerPanel from "@/components/PDFViewerPanel";
import { useDemoMode } from "@/demo/DemoContext";
import { DEMO_WORKFLOW } from "@/demo/mockData";

// ── Node colour map removed for strict status-driven colours ──────────────────

// ── Compliance status colours ──────────────────────────────────────────────────
function compColor(s?: string) {
  if (s === "PASS")  return "var(--accent-green)";
  if (s === "WARN")  return "var(--accent-amber)";
  if (s === "BLOCK") return "var(--accent-red)";
  return "var(--text-muted)";
}

// ── Confidence badge ───────────────────────────────────────────────────────────
function ConfBadge({ v }: { v: number }) {
  const pct = Math.round(v * 100);
  const color = pct >= 90 ? "var(--accent-green)" : pct >= 70 ? "var(--accent-amber)" : "var(--accent-red)";
  return (
    <span style={{
      fontFamily:    "'JetBrains Mono', monospace",
      fontSize:      "0.58rem",
      fontWeight:    700,
      color,
      backgroundColor: `${color}18`,
      border:        `1px solid ${color}40`,
      padding:       "1px 6px",
      borderRadius:  "9999px",
      flexShrink:    0,
    }}>
      {pct}%
    </span>
  );
}

// ── Panel header bar ───────────────────────────────────────────────────────────
function PanelHeader({
  accent,
  title,
  sub,
  right,
}: {
  accent: string;
  title: string;
  sub?: string;
  right?: React.ReactNode;
}) {
  return (
    <div style={{
      backgroundColor: "var(--header-bg)",
      borderBottom:    "1px solid #1e293b",
      padding:         "10px 16px",
      flexShrink:      0,
      borderLeft:      `3px solid ${accent}`,
      display:         "flex",
      alignItems:      "center",
      gap:             "10px",
    }}>
      <span style={{
        fontFamily:    "'Space Grotesk', sans-serif",
        fontWeight:    700,
        fontSize:      "0.7rem",
        color:         "var(--text-muted)",
        letterSpacing: "0.1em",
      }}>
        {title}
      </span>
      {sub && (
        <span style={{
          fontFamily: "'JetBrains Mono', monospace",
          fontSize:   "0.58rem",
          color:      "var(--text-muted)",
        }}>
          {sub}
        </span>
      )}
      {right && <div style={{ marginLeft: "auto" }}>{right}</div>}
    </div>
  );
}

// ── Extracted field row ────────────────────────────────────────────────────────
function FieldRow({
  name,
  value,
  confidence,
  issueLevel,
}: {
  name: string;
  value: string;
  confidence?: number;
  issueLevel?: "warn" | "block" | null;
}) {
  const borderColor =
    issueLevel === "block" ? "var(--accent-red)" :
    issueLevel === "warn"  ? "var(--accent-amber)" :
    "transparent";
  const hasIssue = borderColor !== "transparent";

  return (
    <div style={{
      display:       "flex",
      alignItems:    "center",
      gap:           "10px",
      padding:       "9px 0",
      borderBottom:  "1px solid #0f172a",
      borderLeft:    hasIssue ? `2px solid ${borderColor}` : undefined,
      paddingLeft:   hasIssue ? "10px" : undefined,
      backgroundColor: issueLevel === "block" ? "rgba(220, 38, 38, 0.04)" : undefined,
      boxShadow:     issueLevel === "block" ? `inset 0 0 8px rgba(239,68,68,0.08)` : undefined,
    }}>
      <span style={{
        fontFamily:  "'JetBrains Mono', monospace",
        fontSize:    "0.62rem",
        color:       "var(--text-secondary)",
        flex:        "0 0 140px",
        flexShrink:  0,
        letterSpacing: "0.04em",
      }}>
        {name.replace(/_/g, "_").toUpperCase()}
      </span>
      <span style={{
        fontFamily:    "'JetBrains Mono', monospace",
        fontSize:      "0.7rem",
        color:         "var(--text-primary)",
        flex:          1,
        overflow:      "hidden",
        textOverflow:  "ellipsis",
        whiteSpace:    "nowrap",
      }}>
        {value || "—"}
      </span>
      {confidence !== undefined && <ConfBadge v={confidence} />}
    </div>
  );
}

// ── Agent trace node ───────────────────────────────────────────────────────────
function TraceNode({
  step,
  expanded,
  onToggle,
}: {
  step: WorkflowStep;
  expanded: boolean;
  onToggle: () => void;
}) {
  const hasOutput  = step.output && Object.keys(step.output).length > 0;
  const latencyMs  =
    step.started_at && step.completed_at
      ? Math.round(
          new Date(step.completed_at).getTime() -
          new Date(step.started_at).getTime()
        )
      : null;

  const statusColor =
    step.status === "completed" ? "var(--accent-green)" :
    step.status === "failed"    ? "var(--accent-red)" :
    step.status === "blocked"   ? "var(--accent-red)" :
    step.status === "running"   ? "var(--accent-blue)" :
    "var(--text-muted)";

  return (
    <div style={{ position: "relative", paddingLeft: "26px", marginBottom: "10px" }}>
      {/* Timeline dot */}
      <div style={{
        position:        "absolute",
        left:            "3px",
        top:             "7px",
        width:           "10px",
        height:          "10px",
        borderRadius:    "50%",
        backgroundColor: statusColor,
        boxShadow:       `0 0 7px ${statusColor}70`,
        flexShrink:      0,
        animation:       step.status === "running" ? "pulse-dot 2s ease-in-out infinite" : undefined,
      }} />

      <div
        onClick={() => hasOutput && onToggle()}
        style={{ cursor: hasOutput ? "pointer" : "default" }}
      >
        {/* Node header row */}
        <div style={{ display: "flex", alignItems: "center", gap: "7px", flexWrap: "wrap" }}>
          <span style={{
            fontFamily:    "'JetBrains Mono', monospace",
            fontSize:      "0.65rem",
            fontWeight:    700,
            color:         "var(--text-secondary)",
            letterSpacing: "0.04em",
          }}>
            {step.name.toUpperCase().replace(/_/g, "_")}
          </span>

          {/* Status micro badge */}
          <span style={{
            fontFamily:      "'JetBrains Mono', monospace",
            fontSize:        "0.52rem",
            fontWeight:      700,
            color:           statusColor,
            backgroundColor: `${statusColor}18`,
            border:          `1px solid ${statusColor}40`,
            padding:         "1px 5px",
            letterSpacing:   "0.08em",
          }}>
            {step.status.toUpperCase()}
          </span>

          {/* Latency badge */}
          {latencyMs !== null && (
            <span style={{
              fontFamily:      "'JetBrains Mono', monospace",
              fontSize:        "0.52rem",
              color:           "var(--text-secondary)",
              backgroundColor: "var(--bg-primary)",
              border:          "1px solid #1e293b",
              padding:         "1px 6px",
            }}>
              {latencyMs}ms
            </span>
          )}

          {/* Expand toggle */}
          {hasOutput && (
            <span style={{
              marginLeft: "auto",
              color:      "var(--text-muted)",
              fontSize:   "0.55rem",
              userSelect: "none",
            }}>
              {expanded ? "▲ HIDE" : "▼ JSON"}
            </span>
          )}
        </div>

        {/* Error message */}
        {step.error && (
          <p style={{
            fontFamily: "'JetBrains Mono', monospace",
            fontSize:   "0.6rem",
            color:      "var(--accent-red)",
            marginTop:  "3px",
          }}>
            ERR: {step.error}
          </p>
        )}
      </div>

      {/* Expanded JSON output */}
      {expanded && hasOutput && (
        <pre style={{
          marginTop:       "8px",
          padding:         "10px 12px",
          backgroundColor: "var(--header-bg)",
          border:          "1px solid #1e293b",
          fontFamily:      "'JetBrains Mono', monospace",
          fontSize:        "0.55rem",
          color:           "#64748b",
          overflow:        "auto",
          maxHeight:       "200px",
          lineHeight:      1.65,
          whiteSpace:      "pre-wrap",
          wordBreak:       "break-word",
        }}>
          {JSON.stringify(step.output, null, 2)}
        </pre>
      )}
    </div>
  );
}

// ── HITL Correction Panel ─────────────────────────────────────────────────────
function HitlPanel({
  phase,
  issues,
  fields,
  values,
  extracted,
  onChange,
  onSubmit,
  error,
  finalStatus,
  correctedCount,
}: {
  phase: "blocked" | "resuming" | "completed";
  /** Number of fields in the submitted corrections (the issues are gone once completed). */
  correctedCount: number;
  issues: ComplianceIssue[];
  fields: CorrectionField[];
  values: Record<string, string>;
  /** Originally extracted value per path, shown under each input. */
  extracted: Record<string, string>;
  onChange: (path: string, value: string) => void;
  onSubmit: () => void;
  error?: string | null;
  finalStatus?: string;
}) {
  const mono = "'JetBrains Mono', monospace";

  if (phase === "completed") {
    return (
      <div style={{
        marginTop:       "20px",
        borderTop:       "1px solid #1e293b",
        paddingTop:      "16px",
      }}>
        <div style={{
          padding:         "14px 16px",
          backgroundColor: "rgba(22, 163, 74, 0.07)",
          border:          "1px solid rgba(34,197,94,0.25)",
          borderLeft:      "3px solid #22c55e",
          display:         "flex",
          alignItems:      "center",
          gap:             "10px",
        }}>
          <span style={{ fontSize: "1rem" }}>✓</span>
          <div>
            <p style={{ fontFamily: mono, fontSize: "0.65rem", fontWeight: 700, color: "var(--accent-green)", margin: 0 }}>
              CORRECTIONS ACCEPTED — PIPELINE RESUMED
            </p>
            <p style={{ fontFamily: mono, fontSize: "0.6rem", color: "var(--text-secondary)", marginTop: "3px" }}>
              {correctedCount} field{correctedCount === 1 ? "" : "s"} corrected · Compliance status: {finalStatus ?? "—"}
            </p>
          </div>
        </div>
      </div>
    );
  }

  if (phase === "resuming") {
    return (
      <div style={{
        marginTop:       "20px",
        borderTop:       "1px solid #1e293b",
        paddingTop:      "16px",
      }}>
        <div style={{
          padding:    "14px 16px",
          border:     "1px solid #1e3a5f",
          backgroundColor: "rgba(37, 99, 235, 0.06)",
          borderLeft: "3px solid #3B82F6",
        }}>
          <p style={{ fontFamily: mono, fontSize: "0.65rem", fontWeight: 700, color: "var(--accent-blue)", marginBottom: "10px" }}>
            RESUMING PIPELINE…
          </p>
          {/* Animated progress bar */}
          <div style={{
            height:          "3px",
            backgroundColor: "var(--border)",
            position:        "relative",
            overflow:        "hidden",
          }}>
            <div style={{
              position:        "absolute",
              left:            "-40%",
              top:             0,
              bottom:          0,
              width:           "40%",
              backgroundColor: "var(--accent-blue)",
              animation:       "slide-bar 1s linear infinite",
            }} />
          </div>
          <p style={{ fontFamily: mono, fontSize: "0.6rem", color: "var(--text-secondary)", marginTop: "8px" }}>
            Applying correction · declaration_generate → audit_trace
          </p>
        </div>
        <style>{`
          @keyframes slide-bar {
            0%   { left: -40%; }
            100% { left: 100%; }
          }
        `}</style>
      </div>
    );
  }

  // phase === "blocked"
  return (
    <div style={{
      marginTop:  "20px",
      borderTop:  "1px solid #1e293b",
      paddingTop: "16px",
    }}>
      {/* Section label */}
      <div style={{
        fontFamily:    mono,
        fontSize:      "0.58rem",
        color:         "var(--accent-red)",
        letterSpacing: "0.14em",
        fontWeight:    700,
        marginBottom:  "12px",
        display:       "flex",
        alignItems:    "center",
        gap:           "8px",
      }}>
        <span style={{
          display:         "inline-block",
          width:           "6px",
          height:          "6px",
          borderRadius:    "50%",
          backgroundColor: "var(--accent-red)",
          animation:       "pulse-dot 1.2s ease-in-out infinite",
        }} />
        HUMAN REVIEW REQUIRED
      </div>

      {/* Issue cards */}
      {issues.map((issue, i) => (
        <div key={`${issue.field}-${i}`} style={{
          padding:         "10px 12px",
          backgroundColor: "rgba(220, 38, 38, 0.06)",
          border:          "1px solid rgba(239,68,68,0.2)",
          borderLeft:      "3px solid #ef4444",
          marginBottom:    "8px",
        }}>
          <p style={{ fontFamily: mono, fontSize: "0.6rem", fontWeight: 700, color: "var(--accent-red)", marginBottom: "4px" }}>
            [BLOCK] {issue.field.toUpperCase()}
          </p>
          <p style={{ fontFamily: mono, fontSize: "0.63rem", color: "var(--text-muted)", lineHeight: 1.5 }}>
            {issue.message}
          </p>
        </div>
      ))}

      {fields.length === 0 && (
        <p style={{ fontFamily: mono, fontSize: "0.62rem", color: "var(--text-secondary)", lineHeight: 1.5, margin: "6px 0 12px" }}>
          This issue can't be fixed by editing a field — start a new run with corrected documents.
        </p>
      )}

      {/* Correction inputs — one per field that resolves a blocking issue */}
      {fields.map((field) => (
      <div key={field.path} style={{ margin: "12px 0" }}>
        <label htmlFor={`fix-${field.path}`} style={{
          fontFamily:    mono,
          fontSize:      "0.58rem",
          fontWeight:    700,
          color:         "var(--text-secondary)",
          letterSpacing: "0.12em",
          display:       "block",
          marginBottom:  "6px",
        }}>
          {field.label}
        </label>
        <div style={{ display: "flex", gap: "8px", alignItems: "center" }}>
          <input
            id={`fix-${field.path}`}
            type={field.numeric ? "number" : "text"}
            value={values[field.path] ?? ""}
            onChange={(e) => onChange(field.path, e.target.value)}
            style={{
              flex:            1,
              backgroundColor: "var(--bg-primary)",
              border:          "1px solid #1e293b",
              color:           "var(--accent-blue)",
              fontFamily:      mono,
              fontSize:        "0.85rem",
              fontWeight:      700,
              padding:         "8px 12px",
              outline:         "none",
              letterSpacing:   "0.04em",
            }}
            placeholder={field.placeholder}
            onFocus={(e) => {
              e.currentTarget.style.borderColor = "var(--accent-blue)";
              e.currentTarget.style.boxShadow = "0 0 0 1px #3B82F6, 0 0 12px rgba(59,130,246,0.15)";
            }}
            onBlur={(e) => {
              e.currentTarget.style.borderColor = "var(--border)";
              e.currentTarget.style.boxShadow = "none";
            }}
          />
          {field.path.endsWith("gross_weight_kg") && (
            <span style={{ fontFamily: mono, fontSize: "0.65rem", color: "var(--text-secondary)" }}>kg</span>
          )}
        </div>
        <p style={{ fontFamily: mono, fontSize: "0.56rem", color: "var(--text-muted)", marginTop: "4px" }}>
          Extracted: {extracted[field.path] || "NOT SPECIFIED"}
        </p>
      </div>
      ))}

      {/* Submit button */}
      <button
        onClick={onSubmit}
        disabled={fields.length === 0}
        style={{
          width:           "100%",
          backgroundColor: "var(--accent-red)",
          border:          "1px solid #ef4444",
          color:           "#ffffff",
          fontFamily:      mono,
          fontSize:        "0.68rem",
          fontWeight:      700,
          letterSpacing:   "0.14em",
          textTransform:   "uppercase",
          padding:         "10px",
          cursor:          fields.length === 0 ? "not-allowed" : "pointer",
          opacity:         fields.length === 0 ? 0.5 : 1,
          transition:      "all 0.15s ease",
        }}
        onMouseEnter={(e) => {
          (e.currentTarget as HTMLElement).style.backgroundColor = "#dc2626";
          (e.currentTarget as HTMLElement).style.boxShadow =
            "0 0 16px rgba(239,68,68,0.4), 0 0 32px rgba(239,68,68,0.15)";
        }}
        onMouseLeave={(e) => {
          (e.currentTarget as HTMLElement).style.backgroundColor = "var(--accent-red)";
          (e.currentTarget as HTMLElement).style.boxShadow = "none";
        }}
      >
        SUBMIT CORRECTIONS →
      </button>

      {error && (
        <p role="alert" style={{ fontFamily: mono, fontSize: "0.62rem", color: "var(--accent-red)", marginTop: "10px", textAlign: "center" }}>
          {error}
        </p>
      )}
    </div>
  );
}

// ── Props ──────────────────────────────────────────────────────────────────────
interface DeclarationPageProps {
  runId: string;
  onBack: () => void;
}

export default function DeclarationPage({ runId, onBack }: DeclarationPageProps) {
  const [docSource,    setDocSource]    = useState<"invoice" | "bl">("invoice");
  const [expandedNode, setExpandedNode] = useState<string | null>(null);
  const [correctionValues, setCorrectionValues] = useState<Record<string, string>>({});
  const [correctedCount,   setCorrectedCount]   = useState(0);
  const [resumeSubmitting, setResumeSubmitting] = useState(false);
  const [resumeSubmitted,  setResumeSubmitted]  = useState(false);
  const [resumeError,      setResumeError]      = useState<string | null>(null);
  const queryClient = useQueryClient();

  const toggleNode = (name: string) =>
    setExpandedNode((prev) => (prev === name ? null : name));

  // ── Demo mode wiring ──────────────────────────────────────────────────────
  const { isDemoMode, demoStatus: demoData, demoPhase, submitCorrections } = useDemoMode();

  const { data: realData, isLoading: realLoading } = useQuery({
    queryKey:  ["run-status", runId],
    queryFn:   () => getRunStatus(runId),
    refetchInterval: (query) => {
      const s = query.state.data?.status;
      return s === "completed" || s === "failed" || s === "blocked" ? false : 2500;
    },
    enabled: !isDemoMode,
  });

  const data       = isDemoMode ? demoData    : realData;
  const isLoading  = isDemoMode ? false       : realLoading;

  // ── Derived data ─────────────────────────────────────────────────────────
  const bboxes: BBoxEntry[]         = data?.bboxes ?? [];
  const steps:  WorkflowStep[]      = data?.steps  ?? [];
  const result                      = data?.result ?? {};
  const compStatus                  = result.compliance_status as string | undefined;
  const declaration                 = result.declaration as Record<string, unknown> | undefined;
  // Completed runs carry compliance inside the declaration; blocked runs
  // expose their issues directly on the result.
  const compliance                  = (declaration?.compliance as {
    status: string;
    issues: ComplianceIssue[];
  } | undefined) ?? (Array.isArray(result.issues)
    ? { status: "BLOCK", issues: result.issues as ComplianceIssue[] }
    : undefined);
  const summaryText = result.summary as string | undefined;

  const cc = compColor(compStatus);

  // ── HITL: is human correction needed right now? ───────────────────────────
  const liveStatus = realData?.status;
  const realPhase: "blocked" | "resuming" | "completed" =
    resumeSubmitting || (resumeSubmitted && (liveStatus === "running" || liveStatus === "queued"))
      ? "resuming"
      : resumeSubmitted && liveStatus === "completed"
        ? "completed"
        : "blocked";
  const blockingIssues: ComplianceIssue[] = isDemoMode
    ? [...DEMO_WORKFLOW.compliance_issues]
    : ((result.blocking_issues ?? result.issues ?? []) as ComplianceIssue[]).filter((i) => i.severity === "block");
  const correctionFields = correctionFieldsFor(blockingIssues);
  // Values as extracted from the documents (demo data has no document objects).
  const extractedValues: Record<string, string> = isDemoMode
    ? { "invoice.gross_weight_kg": "820", "bill_of_lading.gross_weight_kg": "860" }
    : Object.fromEntries(correctionFields.map((f) => [f.path, currentValue(result, f.path)]));
  const shownInvoiceWeight = Number(extractedValues["invoice.gross_weight_kg"] ?? result.invoice?.gross_weight_kg) || undefined;
  const shownBlWeight      = Number(extractedValues["bill_of_lading.gross_weight_kg"] ?? result.bill_of_lading?.gross_weight_kg) || undefined;

  // Pre-fill each new input once with the extracted value. A missing weight
  // starts from the other document's weight, the most likely correct value.
  const fieldKey = correctionFields.map((f) => f.path).join("|");
  useEffect(() => {
    setCorrectionValues((prev) => {
      const next = { ...prev };
      for (const f of correctionFields) {
        if (f.path in next) continue;
        let v = extractedValues[f.path] ?? "";
        if (!v && f.path === "bill_of_lading.gross_weight_kg") v = extractedValues["invoice.gross_weight_kg"] ?? currentValue(result, "invoice.gross_weight_kg");
        if (!v && f.path === "invoice.gross_weight_kg") v = extractedValues["bill_of_lading.gross_weight_kg"] ?? currentValue(result, "bill_of_lading.gross_weight_kg");
        next[f.path] = v;
      }
      return next;
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fieldKey, data?.updated_at]);

  const submitRealCorrections = async () => {
    const built = buildCorrections(correctionFields, correctionValues);
    if ("error" in built) {
      setResumeError(built.error);
      return;
    }
    setResumeError(null);
    setResumeSubmitting(true);
    try {
      await resumeWorkflow(runId, built);
      setCorrectedCount(Object.keys(built.corrections).length);
      setResumeSubmitted(true);
      // Status is now "running" server-side; refetching restarts polling.
      await queryClient.invalidateQueries({ queryKey: ["run-status", runId] });
    } catch (e) {
      // FastAPI sends a string detail for our own errors, a list for body validation errors.
      const detail: unknown = axios.isAxiosError(e) ? e.response?.data?.detail : undefined;
      setResumeError(
        typeof detail === "string" ? detail
          : Array.isArray(detail) ? detail.map((d: { msg?: string }) => d?.msg ?? String(d)).join("; ")
          : "Could not resume the workflow — please try again.",
      );
    } finally {
      setResumeSubmitting(false);
    }
  };
  const needsHITL = (isDemoMode && demoPhase === "blocked") || (!isDemoMode && data?.status === "blocked");
  const isResuming = (isDemoMode && demoPhase === "resuming") || (!isDemoMode && realPhase === "resuming");
  const currentPhase = isDemoMode ? demoPhase : realPhase;

  // ── Full-height loading ───────────────────────────────────────────────────
  if (isLoading) {
    return (
      <div style={{
        display:        "flex",
        alignItems:     "center",
        justifyContent: "center",
        height:         "100%",
        fontFamily:     "'JetBrains Mono', monospace",
        color:          "var(--text-muted)",
        fontSize:       "0.72rem",
        letterSpacing:  "0.1em",
        backgroundColor: "var(--bg-primary)",
      }}>
        [ LOADING DECLARATION... ]
      </div>
    );
  }

  return (
    <div style={{
      display:         "flex",
      flexDirection:   "column",
      height:          "100%",
      backgroundColor: "var(--bg-primary)",
      overflow:        "hidden",
    }}>
      {/* ── Declaration sub-header ────────────────────────────────────────── */}
      <div style={{
        backgroundColor: "var(--header-bg)",
        borderBottom:    "1px solid #1e293b",
        padding:         "8px 16px",
        display:         "flex",
        alignItems:      "center",
        gap:             "12px",
        flexShrink:      0,
        overflowX:       "auto",
      }}>
        {/* Back button */}
        <button
          onClick={onBack}
          style={{
            fontFamily:      "'JetBrains Mono', monospace",
            fontSize:        "0.63rem",
            fontWeight:      700,
            color:           "var(--accent-blue)",
            border:          "1px solid #1e3a5f",
            backgroundColor: "transparent",
            padding:         "4px 12px",
            cursor:          "pointer",
            letterSpacing:   "0.06em",
            flexShrink:      0,
            transition:      "all 0.15s",
          }}
          onMouseEnter={(e) => {
            (e.currentTarget as HTMLElement).style.backgroundColor = "rgba(37, 99, 235, 0.12)";
          }}
          onMouseLeave={(e) => {
            (e.currentTarget as HTMLElement).style.backgroundColor = "transparent";
          }}
        >
          ← BACK
        </button>

        <div style={{ width: "1px", height: "18px", backgroundColor: "var(--border)", flexShrink: 0 }} />

        {/* Run ID */}
        <span style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: "0.6rem", color: "var(--text-secondary)", flexShrink: 0 }}>
          RUN_ID:
        </span>
        <span style={{
          fontFamily: "'JetBrains Mono', monospace",
          fontSize:   "0.63rem",
          color:      "var(--accent-blue)",
          flexShrink: 0,
        }}>
          {runId.slice(0, 8)}…{runId.slice(-8)}
        </span>

        {/* Workflow status badge */}
        {data?.status && (() => {
          const s = data.status;
          const cls = s === "completed" ? "badge-pass"
                    : s === "blocked"   ? "badge-block"
                    : s === "failed"    ? "badge-block"
                    : s === "running"   ? "badge-blue"
                    : "badge-muted";
          return <span className={cls}>{s.toUpperCase()}</span>;
        })()}

        {/* Compliance status badge */}
        {compStatus && (
          <span style={{
            fontFamily:      "'JetBrains Mono', monospace",
            fontSize:        "0.6rem",
            fontWeight:      700,
            color:           cc,
            backgroundColor: `${cc}18`,
            border:          `1px solid ${cc}40`,
            padding:         "3px 10px",
            borderRadius:    "9999px",
            flexShrink:      0,
          }}>
            {compStatus}
          </span>
        )}

        {/* Summary text (truncated) */}
        {summaryText && (
          <>
            <div style={{ width: "1px", height: "18px", backgroundColor: "var(--border)", flexShrink: 0 }} />
            <span style={{
              fontFamily:    "'JetBrains Mono', monospace",
              fontSize:      "0.58rem",
              color:         "var(--text-muted)",
              overflow:      "hidden",
              textOverflow:  "ellipsis",
              whiteSpace:    "nowrap",
              flex:          1,
              minWidth:      0,
            }}>
              {summaryText}
            </span>
          </>
        )}
      </div>

      {/* ── Three-panel body ──────────────────────────────────────────────── */}
      <div style={{ display: "flex", height: "calc(100vh - 120px)", overflow: "hidden" }}>

        {/* ═══════════════════════════════════════════════════════════════════
            LEFT PANEL — Source Document / PDF Viewer (35%)
        ═══════════════════════════════════════════════════════════════════ */}
        <div style={{
          width:        "35%",
          borderRight:  "1px solid #1e293b",
          display:      "flex",
          flexDirection:"column",
          overflow:     "hidden",
          flexShrink:   0,
        }}>
          <PanelHeader
            accent="var(--accent-blue)"
            title="SOURCE DOCUMENT"
            right={
              <div style={{ display: "flex", gap: "6px" }}>
                {(["invoice", "bl"] as const).map((src) => (
                  <button
                    key={src}
                    onClick={() => setDocSource(src)}
                    style={{
                      fontFamily:      "'JetBrains Mono', monospace",
                      fontSize:        "0.58rem",
                      fontWeight:      700,
                      letterSpacing:   "0.08em",
                      padding:         "3px 10px",
                      cursor:          "pointer",
                      backgroundColor: docSource === src ? "rgba(37, 99, 235, 0.15)" : "transparent",
                      border:          `1px solid ${docSource === src ? "var(--accent-blue)" : "var(--border)"}`,
                      color:           docSource === src ? "var(--accent-blue)" : "var(--text-secondary)",
                      transition:      "all 0.15s",
                    }}
                  >
                    {src === "invoice" ? "INVOICE" : "B/L"}
                  </button>
                ))}
              </div>
            }
          />

          <div style={{
            flex:            1,
            overflowY:       "auto",
            overflowX:       "hidden",
            padding:         "16px",
            backgroundColor: "var(--bg-card)",
          }}>
            <PDFViewerPanel
              runId={runId}
              pdfUrl={null}
              source={docSource}
              pageWidth={320}
            />
          </div>
        </div>

        {/* ═══════════════════════════════════════════════════════════════════
            CENTER PANEL — Extracted Fields (35%)
        ═══════════════════════════════════════════════════════════════════ */}
        <div style={{
          width:        "35%",
          borderRight:  "1px solid #1e293b",
          display:      "flex",
          flexDirection:"column",
          overflow:     "hidden",
          flexShrink:   0,
        }}>
          <PanelHeader
            accent="var(--accent-green)"
            title="EXTRACTED FIELDS"
            sub={bboxes.length > 0 ? `${bboxes.length} FIELDS` : undefined}
          />

          <div style={{
            flex:            1,
            overflowY:       "auto",
            padding:         "12px 16px",
            backgroundColor: "var(--bg-card)",
          }}>

            {/* ── Compliance issues ──────────────────────────────────────── */}
            {compliance?.issues && compliance.issues.length > 0 && (
              <div style={{ marginBottom: "18px" }}>
                <div style={{
                  fontFamily:    "'JetBrains Mono', monospace",
                  fontSize:      "0.58rem",
                  color:         "var(--text-muted)",
                  letterSpacing: "0.14em",
                  marginBottom:  "10px",
                  paddingBottom: "6px",
                  borderBottom:  "1px solid #111122",
                }}>
                  ─ COMPLIANCE ISSUES ({compliance.issues.length}) ─
                </div>
                {compliance.issues.map((issue, i) => {
                  const isBlock = issue.severity === "block";
                  const ic = isBlock ? "var(--accent-red)" : "var(--accent-amber)";
                  const isWeightConflict = issue.field.includes("gross_weight");
                  return (
                    <div
                      key={i}
                      style={{
                        borderLeft:      `2px solid ${ic}`,
                        padding:         "8px 10px",
                        marginBottom:    "6px",
                        backgroundColor: `${ic}08`,
                        boxShadow:       isBlock ? `0 0 12px ${ic}20, inset 0 0 0 1px ${ic}18` : undefined,
                      }}
                    >
                      <div style={{ display: "flex", alignItems: "center", gap: "8px", marginBottom: "3px" }}>
                        <span style={{
                          fontFamily:      "'JetBrains Mono', monospace",
                          fontSize:        "0.58rem",
                          fontWeight:      700,
                          color:           ic,
                          letterSpacing:   "0.08em",
                          animation:       isBlock ? "pulse-dot 1.4s ease-in-out infinite" : undefined,
                        }}>
                          [{issue.severity.toUpperCase()}]
                        </span>
                        <span style={{
                          fontFamily: "'JetBrains Mono', monospace",
                          fontSize:   "0.6rem",
                          color:      "var(--text-muted)",
                        }}>
                          {issue.field}
                        </span>
                      </div>
                      <p style={{
                        fontFamily: "'JetBrains Mono', monospace",
                        fontSize:   "0.63rem",
                        color:      "var(--text-secondary)",
                        lineHeight: 1.5,
                        margin:     0,
                      }}>
                        {issue.message}
                      </p>
                      {isWeightConflict && isBlock && !!shownInvoiceWeight && !!shownBlWeight && (
                        <div style={{
                          marginTop:       "6px",
                          padding:         "5px 8px",
                          backgroundColor: "rgba(220, 38, 38, 0.1)",
                          border:          "1px solid rgba(239,68,68,0.25)",
                          fontFamily:      "'JetBrains Mono', monospace",
                          fontSize:        "0.62rem",
                          color:           "#fca5a5",
                          letterSpacing:   "0.04em",
                        }}>
                          Invoice: {shownInvoiceWeight} kg&nbsp;&nbsp;≠&nbsp;&nbsp;B/L: {shownBlWeight} kg&nbsp;&nbsp;
                          <span style={{ color: "var(--accent-red)", fontWeight: 700 }}>
                            (Δ {Math.abs(shownInvoiceWeight - shownBlWeight)} kg)
                          </span>
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            )}

            {/* ── BBox-derived invoice fields ────────────────────────────── */}
            {bboxes.length > 0 && (
              <div>
                <div style={{
                  fontFamily:    "'JetBrains Mono', monospace",
                  fontSize:      "0.58rem",
                  color:         "var(--text-muted)",
                  letterSpacing: "0.14em",
                  marginBottom:  "6px",
                  paddingBottom: "6px",
                  borderBottom:  "1px solid #111122",
                }}>
                  ─ INVOICE FIELDS ─
                </div>
                {bboxes.map((b, i) => {
                  const issue = compliance?.issues?.find(
                    (iss) => iss.field.includes(b.field_name)
                  );
                  return (
                    <FieldRow
                      key={i}
                      name={b.field_name}
                      value={b.value}
                      confidence={b.confidence}
                      issueLevel={issue?.severity as "warn" | "block" | undefined}
                    />
                  );
                })}
              </div>
            )}

            {/* ── Declaration-derived invoice fields (fallback) ──────────── */}
            {bboxes.length === 0 && declaration?.invoice != null && (() => {
              const inv = declaration.invoice as Record<string, unknown>;
              const scalar = Object.entries(inv).filter(
                ([, v]) => v !== null && v !== undefined && typeof v !== "object"
              );
              return scalar.length > 0 ? (
                <div>
                  <div style={{
                    fontFamily:    "'JetBrains Mono', monospace",
                    fontSize:      "0.58rem",
                    color:         "var(--text-muted)",
                    letterSpacing: "0.14em",
                    marginBottom:  "6px",
                    paddingBottom: "6px",
                    borderBottom:  "1px solid #111122",
                  }}>
                    ─ INVOICE FIELDS ─
                  </div>
                  {scalar.map(([k, v]) => {
                    const issue = compliance?.issues?.find((iss) => iss.field.includes(k));
                    return (
                      <FieldRow
                        key={k}
                        name={k}
                        value={String(v)}
                        issueLevel={issue?.severity as "warn" | "block" | undefined}
                      />
                    );
                  })}
                </div>
              ) : null;
            })()}

            {/* ── Demo HS-code line items ────────────────────────────────── */}
            {isDemoMode && (
              <div style={{ marginTop: "16px" }}>
                <div style={{
                  fontFamily:    "'JetBrains Mono', monospace",
                  fontSize:      "0.58rem",
                  color:         "var(--text-muted)",
                  letterSpacing: "0.14em",
                  marginBottom:  "8px",
                  paddingBottom: "6px",
                  borderBottom:  "1px solid #111122",
                }}>
                  ─ LINE ITEMS / HS CLASSIFICATION ─
                </div>
                {DEMO_WORKFLOW.line_items.map((li, i) => (
                  <div key={i} style={{
                    padding:      "10px 0",
                    borderBottom: "1px solid #0f172a",
                  }}>
                    <div style={{ display: "flex", alignItems: "flex-start", gap: "10px", marginBottom: "5px" }}>
                      <span style={{
                        fontFamily:  "'JetBrains Mono', monospace",
                        fontSize:    "0.62rem",
                        color:       "var(--text-primary)",
                        flex:        1,
                        lineHeight:  1.4,
                      }}>
                        {li.description}
                      </span>
                    </div>
                    <div style={{ display: "flex", gap: "16px", flexWrap: "wrap" }}>
                      <span style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: "0.58rem", color: "var(--text-secondary)" }}>
                        QTY: <span style={{ color: "var(--text-muted)" }}>{li.quantity}</span>
                      </span>
                      <span style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: "0.58rem", color: "var(--text-secondary)" }}>
                        UNIT: <span style={{ color: "var(--text-muted)" }}>${li.unit_price}</span>
                      </span>
                      <span style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: "0.58rem", color: "var(--text-secondary)" }}>
                        TOTAL: <span style={{ color: "var(--text-primary)" }}>${li.total.toLocaleString()}</span>
                      </span>
                    </div>
                    {/* Best HS candidate */}
                    <div style={{ marginTop: "5px", display: "flex", alignItems: "center", gap: "8px" }}>
                      <span style={{
                        fontFamily:    "'JetBrains Mono', monospace",
                        fontSize:      "0.6rem",
                        color:         "var(--accent-blue)",
                        fontWeight:    700,
                        backgroundColor: "rgba(37, 99, 235, 0.1)",
                        border:        "1px solid rgba(59,130,246,0.3)",
                        padding:       "1px 7px",
                        letterSpacing: "0.05em",
                      }}>
                        HTS: {li.hs_code}
                      </span>
                      <span style={{
                        fontFamily: "'JetBrains Mono', monospace",
                        fontSize:   "0.58rem",
                        color:      "var(--text-secondary)",
                        overflow:   "hidden",
                        textOverflow: "ellipsis",
                        whiteSpace: "nowrap",
                      }}>
                        {li.hs_candidates[0].description}
                      </span>
                      <ConfBadge v={li.hs_candidates[0].confidence} />
                    </div>
                  </div>
                ))}
              </div>
            )}

            {/* ── Failure reason ─────────────────────────────────────────── */}
            {!isDemoMode && data?.status === "failed" && typeof result.error === "string" && (
              <div role="alert" style={{
                margin:          "16px 0",
                padding:         "12px 14px",
                backgroundColor: "rgba(220, 38, 38, 0.07)",
                border:          "1px solid rgba(239,68,68,0.25)",
                borderLeft:      "3px solid var(--accent-red)",
                fontFamily:      "'JetBrains Mono', monospace",
              }}>
                <p style={{ fontSize: "0.62rem", fontWeight: 700, color: "var(--accent-red)", letterSpacing: "0.08em", margin: 0 }}>
                  PIPELINE FAILED
                </p>
                <p style={{ fontSize: "0.63rem", color: "var(--text-secondary)", lineHeight: 1.5, marginTop: "6px", whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
                  {result.error}
                </p>
              </div>
            )}

            {/* ── Empty state ────────────────────────────────────────────── */}
            {bboxes.length === 0 && !declaration?.invoice && !isDemoMode && (
              <div style={{
                textAlign:  "center",
                padding:    "48px 16px",
                color:      "var(--text-muted)",
                fontFamily: "'JetBrains Mono', monospace",
                fontSize:   "0.68rem",
                letterSpacing: "0.08em",
              }}>
                {data?.status === "running" || data?.status === "queued"
                  ? "[ EXTRACTION IN PROGRESS... ]"
                  : "[ NO FIELDS AVAILABLE ]"}
              </div>
            )}

            {/* ── DEMO HITL: Human Review Panel ──────────────────────────── */}
            {(needsHITL || isResuming || (isDemoMode && demoPhase === "completed") || (!isDemoMode && realPhase === "completed")) && (
              <HitlPanel
                phase={currentPhase}
                issues={blockingIssues}
                fields={correctionFields}
                values={correctionValues}
                extracted={extractedValues}
                onChange={(path, value) => setCorrectionValues((prev) => ({ ...prev, [path]: value }))}
                error={isDemoMode ? null : resumeError}
                finalStatus={compStatus}
                correctedCount={isDemoMode ? 1 : correctedCount}
                onSubmit={async () => {
                  if (isDemoMode) {
                    submitCorrections(parseFloat(correctionValues["bill_of_lading.gross_weight_kg"]) || 860);
                  } else {
                    await submitRealCorrections();
                  }
                }}
              />
            )}
          </div>
        </div>

        {/* ═══════════════════════════════════════════════════════════════════
            RIGHT PANEL — Agent Trace (30%)
        ═══════════════════════════════════════════════════════════════════ */}
        <div style={{
          flex:         1,
          display:      "flex",
          flexDirection:"column",
          overflow:     "hidden",
          minWidth:     0,
        }}>
          <PanelHeader
            accent="#a78bfa"
            title="AGENT TRACE"
            sub={steps.length > 0 ? `${steps.length} NODES` : undefined}
          />

          <div style={{
            flex:            1,
            overflowY:       "auto",
            padding:         "14px 16px",
            backgroundColor: "var(--bg-card)",
          }}>
            {steps.length === 0 ? (
              <div style={{
                textAlign:  "center",
                padding:    "48px 16px",
                color:      "var(--text-muted)",
                fontFamily: "'JetBrains Mono', monospace",
                fontSize:   "0.68rem",
                letterSpacing: "0.08em",
              }}>
                {data?.status === "running" || data?.status === "queued"
                  ? "[ PIPELINE EXECUTING... ]"
                  : "[ NO TRACE DATA ]"}
              </div>
            ) : (
              <div style={{ position: "relative" }}>
                {/* Vertical timeline line */}
                <div style={{
                  position:        "absolute",
                  left:            "7px",
                  top:             "16px",
                  bottom:          0,
                  width:           "1px",
                  backgroundColor: "var(--border)",
                }} />

                {steps.map((step, i) => (
                  <TraceNode
                    key={i}
                    step={step}
                    expanded={expandedNode === step.name}
                    onToggle={() => toggleNode(step.name)}
                  />
                ))}
              </div>
            )}
          </div>
        </div>

      </div>
    </div>
  );
}
