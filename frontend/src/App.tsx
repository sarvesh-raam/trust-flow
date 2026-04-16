import { useState, useEffect, Component } from "react";
import type { ErrorInfo, ReactNode } from "react";
import UploadPage from "@/pages/UploadPage";
import WorkflowPage from "@/pages/WorkflowPage";
import DeclarationPage from "@/pages/DeclarationPage";
import LoginPage from "@/pages/LoginPage";
import { useDemoMode } from "@/demo/DemoContext";
import { useAuth } from "@/contexts/AuthContext";
import { signOutUser } from "@/lib/firebase";

// ── ErrorBoundary ──────────────────────────────────────────────────────────────
class ErrorBoundary extends Component<
  { children: ReactNode },
  { hasError: boolean; message: string }
> {
  constructor(props: { children: ReactNode }) {
    super(props);
    this.state = { hasError: false, message: "" };
  }
  static getDerivedStateFromError(err: Error) {
    return { hasError: true, message: err?.message ?? String(err) };
  }
  componentDidCatch(_err: Error, info: ErrorInfo) {
    console.error("[ErrorBoundary]", info.componentStack);
  }
  render() {
    if (!this.state.hasError) return this.props.children;
    return (
      <div style={{
        minHeight: "100vh",
        backgroundColor: "var(--bg-primary)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        padding: "32px 16px",
      }}>
        <div style={{
          maxWidth: "480px",
          width: "100%",
          backgroundColor: "var(--bg-card)",
          border: "1px solid rgba(239,68,68,0.3)",
          borderLeft: "3px solid #ef4444",
          padding: "24px 24px",
        }}>
          <p style={{ fontFamily: "'Space Grotesk', sans-serif", fontWeight: 700, fontSize: "0.9rem", color: "var(--accent-red)", marginBottom: "8px", letterSpacing: "0.04em" }}>
            SYSTEM ERROR
          </p>
          <p style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: "0.65rem", color: "var(--text-secondary)", marginBottom: "20px", lineHeight: 1.6, wordBreak: "break-all" }}>
            {this.state.message || "An unexpected error occurred."}
          </p>
          <div style={{ display: "flex", gap: "10px", flexWrap: "wrap" }}>
            <button
              onClick={() => window.location.assign("/?demo=true")}
              style={{
                fontFamily: "'JetBrains Mono', monospace",
                fontSize: "0.65rem",
                fontWeight: 700,
                letterSpacing: "0.12em",
                padding: "8px 18px",
                backgroundColor: "rgba(37, 99, 235, 0.12)",
                border: "1px solid #3B82F6",
                color: "var(--accent-blue)",
                cursor: "pointer",
              }}
            >
              LOAD DEMO MODE
            </button>
            <button
              onClick={() => this.setState({ hasError: false, message: "" })}
              style={{
                fontFamily: "'JetBrains Mono', monospace",
                fontSize: "0.65rem",
                fontWeight: 700,
                letterSpacing: "0.12em",
                padding: "8px 18px",
                backgroundColor: "transparent",
                border: "1px solid #1e293b",
                color: "var(--text-secondary)",
                cursor: "pointer",
              }}
            >
              RETRY
            </button>
          </div>
        </div>
      </div>
    );
  }
}

type Tab = "upload" | "workflows" | "declaration";

// ── Live clock ────────────────────────────────────────────────────────────────
function LiveClock() {
  const [time, setTime] = useState(new Date());
  useEffect(() => {
    const t = setInterval(() => setTime(new Date()), 1000);
    return () => clearInterval(t);
  }, []);
  return (
    <span style={{
      fontFamily: "'JetBrains Mono', monospace",
      fontSize: "0.7rem",
      color: "var(--text-muted)",
      letterSpacing: "0.05em",
      fontVariantNumeric: "tabular-nums",
    }}>
      {time.toLocaleTimeString("en-US", {
        hour12: false,
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
      })}
    </span>
  );
}

// ── Nav tab button ─────────────────────────────────────────────────────────────
function NavTab({
  label,
  active,
  onClick,
}: {
  label: string;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      style={{
        fontFamily:      "'JetBrains Mono', monospace",
        fontSize:        "0.65rem",
        fontWeight:      700,
        letterSpacing:   "0.12em",
        padding:         "8px 16px",
        background:      "transparent",
        border:          "none",
        borderBottom:    active ? "2px solid #3B82F6" : "2px solid transparent",
        color:           active ? "var(--accent-blue)" : "var(--text-secondary)",
        cursor:          "pointer",
        transition:      "color 0.15s, border-color 0.15s",
        textTransform:   "uppercase",
        whiteSpace:      "nowrap",
      }}
      onMouseEnter={(e) => {
        if (!active) (e.currentTarget as HTMLElement).style.color = "var(--text-muted)";
      }}
      onMouseLeave={(e) => {
        if (!active) (e.currentTarget as HTMLElement).style.color = "var(--text-secondary)";
      }}
    >
      {label}
    </button>
  );
}

// ── Demo Mode Banner ──────────────────────────────────────────────────────────
function DemoBanner() {
  return (
    <div
      style={{
        backgroundColor: "rgba(217, 119, 6, 0.08)",
        borderBottom:    "1px solid rgba(245,158,11,0.35)",
        padding:         "4px 16px",
        display:         "flex",
        alignItems:      "center",
        justifyContent:  "center",
        gap:             "10px",
      }}
    >
      <span
        style={{
          display:         "inline-block",
          width:           "6px",
          height:          "6px",
          borderRadius:    "50%",
          backgroundColor: "var(--accent-amber)",
          animation:       "pulse-dot 1.2s ease-in-out infinite",
          flexShrink:      0,
        }}
      />
      <span
        style={{
          fontFamily:    "'JetBrains Mono', monospace",
          fontSize:      "0.6rem",
          fontWeight:    700,
          color:         "var(--accent-amber)",
          letterSpacing: "0.14em",
        }}
      >
        DEMO MODE — Synthetic data | No backend required
      </span>
      <span
        style={{
          fontFamily:    "'JetBrains Mono', monospace",
          fontSize:      "0.52rem",
          color:         "#78350f",
          letterSpacing: "0.07em",
        }}
      >
        [Ctrl+Shift+D for script]
      </span>
    </div>
  );
}

// ── Demo Script Overlay (Ctrl+Shift+D) ────────────────────────────────────────
const DEMO_POINTS = [
  { n: "01", label: "TWO-DOC INGEST",      body: "Commercial invoice + bill of lading uploaded as one atomic request. Both files stored, paths committed to SQLite." },
  { n: "02", label: "DOCLING OCR",          body: "Docling + RapidOCR extracts text, tables and pixel-level bounding boxes from each PDF — no manual parsing." },
  { n: "03", label: "LLM FIELD EXTRACT",    body: "Groq llama-3.3-70b-versatile via Instructor populates strict Pydantic models: InvoiceDocument and BillOfLading." },
  { n: "04", label: "HS CODE RETRIEVAL",    body: "USITC HTS REST API first; falls back to semantic search over an embedded sample catalogue." },
  { n: "05", label: "COMPLIANCE REASON",    body: "LLM selects the best HS code per line item and flags dual-use or restricted goods." },
  { n: "06", label: "DETERMINISTIC CHECK",  body: "Rule engine validates: positive weights, B/L presence, vessel name — catches what the LLM might miss." },
  { n: "07", label: "WEIGHT CONFLICT",      body: "Invoice: 820 kg vs B/L: 860 kg → 4.9% delta exceeds 5% threshold → BLOCK issued, pipeline pauses." },
  { n: "08", label: "HITL RESUME",          body: "Operator corrects gross_weight_kg to 860 kg. State patch applied via LangGraph aupdate_state; pipeline re-enters at interrupt_node." },
  { n: "09", label: "FINAL DECLARATION",    body: "12-node audit trail committed. Declaration generated with HS codes, compliance status PASS/WARN, and PDF bbox highlights." },
] as const;

function DemoScriptOverlay({ onClose }: { onClose: () => void }) {
  return (
    <div
      style={{
        position:        "fixed",
        inset:           0,
        zIndex:          9999,
        display:         "flex",
        alignItems:      "center",
        justifyContent:  "center",
        backgroundColor: "rgba(6,6,11,0.88)",
        backdropFilter:  "blur(4px)",
      }}
      onClick={onClose}
    >
      <div
        style={{
          width:           "min(640px, calc(100vw - 32px))",
          backgroundColor: "var(--bg-card)",
          border:          "1px solid #1e293b",
          borderTop:       "2px solid #3B82F6",
          maxHeight:       "80vh",
          overflowY:       "auto",
          boxShadow:       "0 0 60px rgba(59,130,246,0.15)",
        }}
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div style={{
          padding:         "12px 20px",
          borderBottom:    "1px solid #1e293b",
          display:         "flex",
          alignItems:      "center",
          gap:             "10px",
        }}>
          <span style={{ fontFamily: "'Space Grotesk', sans-serif", fontWeight: 700, fontSize: "0.75rem", color: "var(--accent-blue)", letterSpacing: "0.06em" }}>
            DEMO SCRIPT
          </span>
          <span style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: "0.55rem", color: "var(--text-muted)", marginLeft: "auto" }}>
            ESC or click outside to close
          </span>
        </div>
        {/* Talking points */}
        <div style={{ padding: "16px 20px" }}>
          {DEMO_POINTS.map((pt) => (
            <div key={pt.n} style={{
              display:      "flex",
              gap:          "14px",
              marginBottom: "14px",
              alignItems:   "flex-start",
            }}>
              <span style={{
                fontFamily:    "'JetBrains Mono', monospace",
                fontSize:      "0.58rem",
                fontWeight:    700,
                color:         "var(--accent-blue)",
                backgroundColor: "rgba(37, 99, 235, 0.1)",
                border:        "1px solid rgba(59,130,246,0.3)",
                padding:       "2px 7px",
                flexShrink:    0,
                alignSelf:     "flex-start",
              }}>{pt.n}</span>
              <div>
                <p style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: "0.62rem", fontWeight: 700, color: "var(--text-primary)", letterSpacing: "0.06em", marginBottom: "3px" }}>
                  {pt.label}
                </p>
                <p style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: "0.6rem", color: "var(--text-secondary)", lineHeight: 1.6 }}>
                  {pt.body}
                </p>
              </div>
            </div>
          ))}
        </div>
        {/* Footer */}
        <div style={{
          borderTop:    "1px solid #1e293b",
          padding:      "10px 20px",
          display:      "flex",
          justifyContent: "flex-end",
        }}>
          <button
            onClick={onClose}
            style={{
              fontFamily:      "'JetBrains Mono', monospace",
              fontSize:        "0.62rem",
              fontWeight:      700,
              letterSpacing:   "0.1em",
              padding:         "6px 18px",
              backgroundColor: "transparent",
              border:          "1px solid #1e293b",
              color:           "var(--text-secondary)",
              cursor:          "pointer",
            }}
          >
            CLOSE [ESC]
          </button>
        </div>
      </div>
    </div>
  );
}

// ── User avatar + sign-out ────────────────────────────────────────────────────
function UserMenu() {
  const { user } = useAuth();
  if (!user) return null;
  return (
    <div style={{ display: "flex", alignItems: "center", gap: "8px", padding: "0 14px", borderLeft: "1px solid #1e293b" }}>
      {user.photoURL && (
        <img
          src={user.photoURL}
          alt={user.displayName ?? "user"}
          referrerPolicy="no-referrer"
          style={{ width: "24px", height: "24px", borderRadius: "50%", border: "1px solid #1e293b" }}
        />
      )}
      <span style={{
        fontFamily: "'JetBrains Mono', monospace",
        fontSize:   "0.58rem",
        color:      "var(--text-secondary)",
        maxWidth:   "100px",
        overflow:   "hidden",
        textOverflow: "ellipsis",
        whiteSpace: "nowrap",
      }}>
        {user.displayName?.split(" ")[0] ?? user.email?.split("@")[0]}
      </span>
      <button
        onClick={() => signOutUser()}
        style={{
          fontFamily:      "'JetBrains Mono', monospace",
          fontSize:        "0.52rem",
          fontWeight:      700,
          letterSpacing:   "0.08em",
          padding:         "3px 8px",
          backgroundColor: "transparent",
          border:          "1px solid #1e293b",
          color:           "var(--text-secondary)",
          cursor:          "pointer",
          transition:      "color 0.15s, border-color 0.15s",
        }}
        onMouseEnter={(e) => {
          (e.currentTarget as HTMLElement).style.color = "#ef4444";
          (e.currentTarget as HTMLElement).style.borderColor = "rgba(239,68,68,0.4)";
        }}
        onMouseLeave={(e) => {
          (e.currentTarget as HTMLElement).style.color = "var(--text-secondary)";
          (e.currentTarget as HTMLElement).style.borderColor = "#1e293b";
        }}
      >
        SIGN OUT
      </button>
    </div>
  );
}

// ── Root App ──────────────────────────────────────────────────────────────────
export default function App() {
  const { user } = useAuth();
  const { isDemoMode, demoRunId } = useDemoMode();
  const [tab, setTab] = useState<Tab>("upload");
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [scriptOpen, setScriptOpen] = useState(false);

  // Ctrl+Shift+D opens the demo script overlay; Escape closes it
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.ctrlKey && e.shiftKey && e.key === "D") {
        e.preventDefault();
        setScriptOpen((v) => !v);
      }
      if (e.key === "Escape") setScriptOpen(false);
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, []);

  const handleViewDeclaration = (runId: string) => {
    setSelectedRunId(isDemoMode ? demoRunId : runId);
    setTab("declaration");
  };

  const handleBack = () => {
    setTab("workflows");
    setSelectedRunId(null);
  };

  const switchTab = (t: Tab) => {
    setTab(t);
    if (t !== "declaration") setSelectedRunId(null);
  };

  // Show login page when not authenticated (demo mode bypasses auth)
  if (!user && !isDemoMode) {
    return (
      <ErrorBoundary>
        <LoginPage />
      </ErrorBoundary>
    );
  }

  return (
    <ErrorBoundary>
    <div
      style={{
        minHeight: "100vh",
        display: "flex",
        flexDirection: "column",
        backgroundColor: "var(--bg-primary)",
        color: "var(--text-primary)",
      }}
    >
      {/* ── Bloomberg Terminal Nav ──────────────────────────────────────────── */}
      <header
        style={{
          backgroundColor: "var(--header-bg)",
          borderBottom: "1px solid #1e293b",
          flexShrink: 0,
          position: "sticky",
          top: 0,
          zIndex: 100,
        }}
      >
        <div
          style={{
            display: "flex",
            alignItems: "stretch",
            height: "44px",
            paddingLeft: "16px",
            paddingRight: "16px",
          }}
        >
          {/* Left: Logo + pulse dot */}
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: "10px",
              paddingRight: "20px",
              borderRight: "1px solid #1e293b",
            }}
          >
            <span
              style={{
                fontFamily: "'Space Grotesk', sans-serif",
                fontWeight: 700,
                fontSize: "0.95rem",
                color: "var(--accent-blue)",
                letterSpacing: "0.02em",
                whiteSpace: "nowrap",
              }}
            >
              CUSTOMS DECLARATION
            </span>
            {/* Pulsing online indicator */}
            <span style={{ position: "relative", display: "inline-flex", height: "8px", width: "8px" }}>
              <span
                style={{
                  position: "absolute",
                  inset: 0,
                  borderRadius: "50%",
                  backgroundColor: "var(--accent-green)",
                  opacity: 0.7,
                  animation: "pulse-dot 2s ease-in-out infinite",
                }}
              />
              <span
                style={{
                  position: "relative",
                  display: "inline-flex",
                  borderRadius: "50%",
                  height: "8px",
                  width: "8px",
                  backgroundColor: "var(--accent-green)",
                }}
              />
            </span>
          </div>

          {/* Center: subtitle */}
          <div
            style={{
              flex: 1,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
            }}
          >
            <span
              style={{
                fontFamily: "'JetBrains Mono', monospace",
                fontSize: "0.6rem",
                color: "var(--text-secondary)",
                letterSpacing: "0.2em",
                textTransform: "uppercase",
              }}
            >
              HACKSTROM'26 | TRACK 3 | AUTONOMOUS COMPLIANCE AGENT
            </span>
          </div>

          {/* Right: nav tabs + clock + status badge */}
          <div
            style={{
              display: "flex",
              alignItems: "stretch",
              gap: "0",
              paddingLeft: "20px",
              borderLeft: "1px solid #1e293b",
            }}
          >
            <NavTab
              label="UPLOAD"
              active={tab === "upload"}
              onClick={() => switchTab("upload")}
            />
            <NavTab
              label="WORKFLOWS"
              active={tab === "workflows" || tab === "declaration"}
              onClick={() => switchTab("workflows")}
            />

            {/* Clock */}
            <div
              style={{
                display: "flex",
                alignItems: "center",
                padding: "0 14px",
                borderLeft: "1px solid #1e293b",
              }}
            >
              <LiveClock />
            </div>

            {/* System status badge */}
            <div
              style={{
                display: "flex",
                alignItems: "center",
                padding: "0 14px",
                borderLeft: "1px solid #1e293b",
              }}
            >
              <span
                style={{
                  fontFamily: "'JetBrains Mono', monospace",
                  fontSize: "0.55rem",
                  fontWeight: 700,
                  letterSpacing: "0.1em",
                  color: "var(--accent-green)",
                  backgroundColor: "rgba(22, 163, 74, 0.1)",
                  border: "1px solid rgba(34,197,94,0.3)",
                  padding: "3px 9px",
                  borderRadius: "9999px",
                }}
              >
                SYS:ONLINE
              </span>
            </div>

            {/* User avatar + sign-out */}
            <UserMenu />
          </div>
        </div>
      </header>

      {/* ── Demo mode amber banner ──────────────────────────────────────────── */}
      {isDemoMode && <DemoBanner />}

      {/* ── Main content ────────────────────────────────────────────────────── */}
      <main style={{ flex: 1, display: "flex", flexDirection: "column", overflow: "hidden" }}>
        {tab === "upload" && (
          <UploadPage
            onDemoLaunch={isDemoMode ? () => handleViewDeclaration(demoRunId) : undefined}
          />
        )}
        {tab === "workflows" && (
          <WorkflowPage onViewDeclaration={handleViewDeclaration} />
        )}
        {tab === "declaration" && selectedRunId && (
          <DeclarationPage runId={selectedRunId} onBack={handleBack} />
        )}
      </main>

      {/* ── Demo script overlay ──────────────────────────────────────────────── */}
      {scriptOpen && <DemoScriptOverlay onClose={() => setScriptOpen(false)} />}
    </div>
    </ErrorBoundary>
  );
}
