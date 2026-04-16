import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  chatWithWorkflow,
  getRunStatus,
  resumeWorkflow,
  type StatusResponse,
} from "@/lib/api";
import PDFViewerPanel from "@/components/PDFViewerPanel";
import { useDemoMode } from "@/demo/DemoContext";

type Phase = "blocked" | "resuming" | "completed";
type AnyMap = Record<string, any>;

function msg(err: unknown) {
  const value = err as { response?: { data?: { detail?: string } }; message?: string };
  return value?.response?.data?.detail ?? value?.message ?? String(err);
}

function tone(status?: string) {
  if (status === "PASS") return "var(--accent-green)";
  if (status === "WARN") return "var(--accent-amber)";
  if (status === "BLOCK") return "var(--accent-red)";
  return "var(--text-muted)";
}

function label(value: string) {
  return value.replace(/_/g, " ").toUpperCase();
}

export default function DeclarationPage({
  runId,
  onBack,
}: {
  runId: string;
  onBack: () => void;
}) {
  const { isDemoMode, demoStatus, demoPhase, submitCorrections } = useDemoMode();
  const queryClient = useQueryClient();
  const [activePdf, setActivePdf] = useState<"invoice" | "bl">("invoice");
  const [correctedWeight, setCorrectedWeight] = useState("");
  const [phase, setPhase] = useState<Phase>("blocked");
  const [chatInput, setChatInput] = useState("");
  const [chatError, setChatError] = useState<string | null>(null);
  const [resumeError, setResumeError] = useState<string | null>(null);
  const [demoChat, setDemoChat] = useState([{ role: "assistant", content: "Ask for a summary, compare weights, or say 'change bill of lading gross weight to 860'." }]);
  const q = useQuery<StatusResponse>({
    queryKey: ["run-status", runId],
    queryFn: () => getRunStatus(runId),
    enabled: !isDemoMode,
    refetchInterval: (state) => {
      const status = state.state.data?.status;
      return status === "completed" || status === "failed" || status === "blocked" ? false : 2500;
    },
  });
  const data = isDemoMode ? demoStatus : q.data;
  const result = (data?.result ?? {}) as AnyMap;
  const declaration = (result.declaration ?? {}) as AnyMap;
  const invoice = (declaration.invoice ?? {}) as AnyMap;
  const bill = (declaration.bill_of_lading ?? {}) as AnyMap;
  const compliance = (declaration.compliance ?? {}) as AnyMap;
  const issues = useMemo(() => Array.isArray(compliance.issues) ? compliance.issues : Array.isArray(result.issues) ? result.issues : [], [compliance.issues, result.issues]);
  const fields = (entry: AnyMap) => Object.entries(entry).filter(([k, v]) => k !== "line_items" && v != null && typeof v !== "object");
  const lineItems = Array.isArray(invoice.line_items) ? invoice.line_items : [];
  const complianceStatus = typeof compliance.status === "string" ? compliance.status : result.compliance_status;
  const chatHistory = isDemoMode ? demoChat : Array.isArray(result.chat_history) ? result.chat_history : [];

  useEffect(() => {
    const next = bill.gross_weight_kg ?? invoice.gross_weight_kg;
    if ((correctedWeight === "" || correctedWeight === "0") && next != null) setCorrectedWeight(String(next));
  }, [bill.gross_weight_kg, correctedWeight, invoice.gross_weight_kg]);

  useEffect(() => {
    if (!isDemoMode) {
      if (data?.status === "completed") setPhase("completed");
      if (data?.status === "blocked") setPhase("blocked");
    }
  }, [data?.status, isDemoMode]);

  const resumeMutation = useMutation({
    mutationFn: (value: number) => resumeWorkflow(runId, value),
    onMutate: () => {
      setResumeError(null);
      setPhase("resuming");
    },
    onSuccess: (value) => {
      setPhase(value.status === "completed" ? "completed" : "blocked");
      queryClient.invalidateQueries({ queryKey: ["run-status", runId] });
      queryClient.invalidateQueries({ queryKey: ["workflows"] });
    },
    onError: (err) => {
      setPhase("blocked");
      setResumeError(msg(err));
    },
  });

  const chatMutation = useMutation({
    mutationFn: (value: string) => chatWithWorkflow(runId, value),
    onSuccess: (value) => {
      setChatInput("");
      setChatError(null);
      queryClient.setQueryData<StatusResponse | undefined>(["run-status", runId], (current) => current ? { ...current, result: { ...current.result, declaration: value.declaration ?? current.result.declaration, summary: value.summary ?? current.result.summary, chat_history: value.chat_history } } : current);
      queryClient.invalidateQueries({ queryKey: ["run-status", runId] });
      queryClient.invalidateQueries({ queryKey: ["workflows"] });
    },
    onError: (err) => setChatError(msg(err)),
  });

  function submitResume() {
    const next = Number(correctedWeight);
    if (!Number.isFinite(next) || next <= 0) {
      setResumeError("Enter a valid corrected gross weight.");
      return;
    }
    if (isDemoMode) {
      submitCorrections(next);
      return;
    }
    resumeMutation.mutate(next);
  }

  function sendChat() {
    const value = chatInput.trim();
    if (!value) return;
    setChatError(null);
    if (isDemoMode) {
      const lower = value.toLowerCase();
      const match = lower.match(/(?:change|set|update).*(?:weight|gross weight).*\bto\s+(\d+(?:\.\d+)?)/);
      let reply = "In demo mode I can summarize the shipment or accept a weight correction command.";
      let updated = false;
      let changes: string[] = [];
      if (lower.includes("summary") || lower.includes("status")) reply = String(demoStatus.result.summary ?? "");
      else if (lower.includes("issue") || lower.includes("compliance")) reply = "Active issue: invoice gross weight and bill of lading gross weight do not match.";
      else if (lower.includes("weight")) reply = `Invoice gross weight is ${invoice.gross_weight_kg ?? 820} kg and bill of lading gross weight is ${bill.gross_weight_kg ?? 860} kg.`;
      if (match) {
        const next = Number(match[1]);
        setCorrectedWeight(String(next));
        submitCorrections(next);
        reply = `Updated the bill of lading gross weight to ${next} kg and resumed validation.`;
        updated = true;
        changes = [`bill_of_lading.gross_weight_kg -> ${next}`];
      }
      setDemoChat((current) => [...current, { role: "user", content: value }, { role: "assistant", content: reply, updated, changes }]);
      setChatInput("");
      return;
    }
    chatMutation.mutate(value);
  }

  if (!data && q.isLoading) return <div style={{ padding: "32px", color: "var(--text-muted)", fontFamily: "'JetBrains Mono', monospace" }}>[ LOADING DECLARATION REVIEW... ]</div>;
  if (!data) return <div style={{ padding: "32px", color: "var(--accent-red)", fontFamily: "'JetBrains Mono', monospace" }}>{msg(q.error)}</div>;

  const currentPhase = isDemoMode ? demoPhase : phase;
  const box = { backgroundColor: "var(--bg-card)", border: "1px solid #1e293b" } as const;
  const badge = { fontFamily: "'JetBrains Mono', monospace", fontSize: "0.58rem", fontWeight: 700, letterSpacing: "0.08em", padding: "5px 8px" } as const;

  return (
    <div style={{ padding: "20px", backgroundColor: "var(--bg-primary)", minHeight: "100%" }}>
      <div style={{ display: "flex", flexWrap: "wrap", gap: "12px", alignItems: "center", marginBottom: "16px" }}>
        <button onClick={onBack} style={{ backgroundColor: "transparent", border: "1px solid #1e293b", color: "var(--text-secondary)", cursor: "pointer", fontFamily: "'JetBrains Mono', monospace", fontSize: "0.62rem", padding: "7px 12px" }}>BACK</button>
        <div style={{ flex: "1 1 260px" }}>
          <h1 style={{ margin: 0, color: "var(--text-primary)", fontFamily: "'Space Grotesk', sans-serif", fontSize: "1rem" }}>DECLARATION REVIEW</h1>
          <p style={{ margin: "3px 0 0", color: "var(--text-secondary)", fontFamily: "'JetBrains Mono', monospace", fontSize: "0.6rem" }}>RUN {runId.slice(0, 8)} | TWO-DOCUMENT ANALYSIS | {isDemoMode ? "DEMO" : "LIVE"}</p>
        </div>
        <span style={{ ...badge, color: "var(--accent-blue)", backgroundColor: "rgba(37,99,235,0.1)", border: "1px solid rgba(59,130,246,0.3)" }}>WORKFLOW {String(data.status).toUpperCase()}</span>
        <span style={{ ...badge, color: tone(complianceStatus), backgroundColor: `${tone(complianceStatus)}18`, border: `1px solid ${tone(complianceStatus)}40` }}>COMPLIANCE {String(complianceStatus ?? "pending").toUpperCase()}</span>
      </div>

      <div style={{ ...box, padding: "14px 16px", marginBottom: "16px", color: "var(--text-primary)", fontFamily: "'JetBrains Mono', monospace", fontSize: "0.67rem", lineHeight: 1.7 }}>
        {result.summary ?? "The declaration is still being assembled. Extracted data and trace details will appear here as the run progresses."}
      </div>

      <div style={{ display: "flex", flexWrap: "wrap", gap: "16px", alignItems: "stretch" }}>
        <div style={{ ...box, flex: "1.45 1 420px", minWidth: "320px", padding: "14px" }}>
          <div style={{ display: "flex", justifyContent: "space-between", gap: "8px", marginBottom: "12px", alignItems: "center" }}>
            <span style={{ color: "var(--text-muted)", fontFamily: "'Space Grotesk', sans-serif", fontSize: "0.75rem", fontWeight: 700 }}>DOCUMENT VIEWER</span>
            <div style={{ display: "flex", gap: "6px" }}>
              {(["invoice", "bl"] as const).map((name) => (
                <button key={name} onClick={() => setActivePdf(name)} style={{ backgroundColor: activePdf === name ? "rgba(37,99,235,0.16)" : "transparent", border: activePdf === name ? "1px solid rgba(59,130,246,0.45)" : "1px solid #1e293b", color: activePdf === name ? "var(--accent-blue)" : "var(--text-secondary)", cursor: "pointer", fontFamily: "'JetBrains Mono', monospace", fontSize: "0.56rem", padding: "4px 8px" }}>{name === "invoice" ? "INVOICE" : "B/L"}</button>
              ))}
            </div>
          </div>
          <PDFViewerPanel runId={runId} source={activePdf} pdfUrl={activePdf === "invoice" ? data.invoice_pdf_url ?? null : data.bl_pdf_url ?? null} pageWidth={700} />
        </div>

        <div style={{ display: "flex", flexDirection: "column", gap: "16px", flex: "1.1 1 340px", minWidth: "320px" }}>
          <div style={{ ...box, padding: "14px", maxHeight: "520px", overflowY: "auto" }}>
            <div style={{ color: "var(--text-muted)", fontFamily: "'Space Grotesk', sans-serif", fontSize: "0.75rem", fontWeight: 700, marginBottom: "12px" }}>DECLARATION DATA</div>
            {(currentPhase !== "blocked" || data.status === "blocked" || complianceStatus === "BLOCK") && (
              <div style={{ backgroundColor: currentPhase === "completed" ? "rgba(22,163,74,0.07)" : currentPhase === "resuming" ? "rgba(37,99,235,0.06)" : "rgba(220,38,38,0.05)", border: `1px solid ${currentPhase === "completed" ? "rgba(34,197,94,0.25)" : currentPhase === "resuming" ? "#1e3a5f" : "rgba(239,68,68,0.2)"}`, borderLeft: `3px solid ${currentPhase === "completed" ? "#22c55e" : currentPhase === "resuming" ? "#3B82F6" : "#ef4444"}`, marginBottom: "12px", padding: "12px" }}>
                <div style={{ color: currentPhase === "completed" ? "var(--accent-green)" : currentPhase === "resuming" ? "var(--accent-blue)" : "var(--accent-red)", fontFamily: "'JetBrains Mono', monospace", fontSize: "0.62rem", fontWeight: 700, marginBottom: "8px" }}>
                  {currentPhase === "completed" ? "CORRECTIONS ACCEPTED" : currentPhase === "resuming" ? "RESUMING PIPELINE" : "HUMAN REVIEW REQUIRED"}
                </div>
                {currentPhase === "blocked" && (
                  <>
                    <div style={{ color: "var(--text-secondary)", fontFamily: "'JetBrains Mono', monospace", fontSize: "0.62rem", lineHeight: 1.6, marginBottom: "8px" }}>{issues[0]?.message ?? "A blocking validation issue needs a corrected value before clearance can continue."}</div>
                    <input type="number" value={correctedWeight} onChange={(e) => setCorrectedWeight(e.target.value)} placeholder="Corrected gross weight" style={{ width: "100%", marginBottom: "8px", backgroundColor: "var(--bg-primary)", border: "1px solid #1e293b", color: "var(--accent-blue)", fontFamily: "'JetBrains Mono', monospace", fontSize: "0.72rem", padding: "8px 10px" }} />
                    <button onClick={submitResume} disabled={resumeMutation.isPending} style={{ width: "100%", backgroundColor: "var(--accent-red)", border: "1px solid #ef4444", color: "#fff", cursor: "pointer", fontFamily: "'JetBrains Mono', monospace", fontSize: "0.62rem", fontWeight: 700, padding: "9px" }}>SUBMIT CORRECTION</button>
                  </>
                )}
                {currentPhase !== "blocked" && <div style={{ color: "var(--text-secondary)", fontFamily: "'JetBrains Mono', monospace", fontSize: "0.62rem" }}>{currentPhase === "completed" ? "Validation finished with the corrected data." : "Re-validating the invoice and bill of lading."}</div>}
              </div>
            )}
            {resumeError && <div style={{ color: "#fca5a5", fontFamily: "'JetBrains Mono', monospace", fontSize: "0.6rem", marginBottom: "10px" }}>{resumeError}</div>}
            <div style={{ color: "var(--text-muted)", fontFamily: "'JetBrains Mono', monospace", fontSize: "0.57rem", marginBottom: "6px" }}>INVOICE FIELDS</div>
            {fields(invoice).map(([k, v]) => <div key={k} style={{ borderBottom: "1px solid #0f172a", padding: "7px 0", color: "var(--text-primary)", fontFamily: "'JetBrains Mono', monospace", fontSize: "0.65rem" }}>{label(k)}: <span style={{ color: "var(--text-secondary)" }}>{String(v)}</span></div>)}
            <div style={{ color: "var(--text-muted)", fontFamily: "'JetBrains Mono', monospace", fontSize: "0.57rem", margin: "14px 0 6px" }}>BILL OF LADING FIELDS</div>
            {fields(bill).map(([k, v]) => <div key={k} style={{ borderBottom: "1px solid #0f172a", padding: "7px 0", color: "var(--text-primary)", fontFamily: "'JetBrains Mono', monospace", fontSize: "0.65rem" }}>{label(k)}: <span style={{ color: "var(--text-secondary)" }}>{String(v)}</span></div>)}
            <div style={{ color: "var(--text-muted)", fontFamily: "'JetBrains Mono', monospace", fontSize: "0.57rem", margin: "14px 0 6px" }}>LINE ITEMS / HS CLASSIFICATION</div>
            {lineItems.length ? lineItems.map((item: AnyMap, i: number) => <div key={`${item.description}-${i}`} style={{ borderBottom: "1px solid #0f172a", padding: "8px 0" }}><div style={{ color: "var(--text-primary)", fontFamily: "'JetBrains Mono', monospace", fontSize: "0.64rem", marginBottom: "4px" }}>{item.description ?? `Line item ${i + 1}`}</div><div style={{ color: "var(--text-secondary)", fontFamily: "'JetBrains Mono', monospace", fontSize: "0.58rem" }}>QTY {item.quantity ?? "-"} | UNIT {item.unit_price ?? "-"} | HS {item.hs_code ?? "UNASSIGNED"}</div></div>) : <div style={{ color: "var(--text-muted)", fontFamily: "'JetBrains Mono', monospace", fontSize: "0.62rem" }}>No line items available yet.</div>}
          </div>

          <div style={{ ...box, padding: "14px", display: "flex", flexDirection: "column", minHeight: "320px" }}>
            <div style={{ color: "var(--text-muted)", fontFamily: "'Space Grotesk', sans-serif", fontSize: "0.75rem", fontWeight: 700, marginBottom: "12px" }}>BILL CHAT</div>
            <div style={{ flex: 1, overflowY: "auto", marginBottom: "10px" }}>
              {chatHistory.map((entry: AnyMap, i: number) => <div key={`${entry.role}-${i}`} style={{ backgroundColor: entry.role === "user" ? "rgba(15,23,42,0.7)" : "rgba(37,99,235,0.08)", border: `1px solid ${entry.role === "user" ? "#1e293b" : "rgba(59,130,246,0.2)"}`, borderLeft: `3px solid ${entry.role === "user" ? "#0f172a" : "#3B82F6"}`, marginBottom: "10px", padding: "10px 12px" }}><div style={{ color: entry.role === "user" ? "var(--text-secondary)" : "var(--accent-blue)", fontFamily: "'JetBrains Mono', monospace", fontSize: "0.54rem", fontWeight: 700, marginBottom: "5px" }}>{entry.role === "user" ? "YOU" : "APP"}</div><div style={{ color: "var(--text-primary)", fontFamily: "'JetBrains Mono', monospace", fontSize: "0.64rem", lineHeight: 1.6, whiteSpace: "pre-wrap" }}>{entry.content}</div>{entry.updated && entry.changes?.length ? <div style={{ marginTop: "6px", color: "var(--accent-green)", fontFamily: "'JetBrains Mono', monospace", fontSize: "0.56rem" }}>{entry.changes.join(" | ")}</div> : null}</div>)}
            </div>
            {chatError && <div style={{ color: "#fca5a5", fontFamily: "'JetBrains Mono', monospace", fontSize: "0.6rem", marginBottom: "8px" }}>{chatError}</div>}
            <div style={{ display: "flex", gap: "8px", flexWrap: "wrap", marginBottom: "10px" }}>
              {["Summarize the shipment", "What compliance issues are open?", "Change bill of lading gross weight to 860"].map((value) => <button key={value} onClick={() => setChatInput(value)} style={{ backgroundColor: "rgba(37,99,235,0.08)", border: "1px solid rgba(59,130,246,0.25)", color: "var(--accent-blue)", cursor: "pointer", fontFamily: "'JetBrains Mono', monospace", fontSize: "0.56rem", padding: "5px 8px" }}>{value}</button>)}
            </div>
            <textarea value={chatInput} onChange={(e) => setChatInput(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); sendChat(); } }} placeholder="Ask about the bills or request a field update." style={{ width: "100%", minHeight: "86px", backgroundColor: "var(--bg-primary)", border: "1px solid #1e293b", color: "var(--text-primary)", fontFamily: "'JetBrains Mono', monospace", fontSize: "0.64rem", padding: "10px 12px", resize: "vertical" }} />
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: "10px", marginTop: "10px" }}>
              <span style={{ color: "var(--text-muted)", fontFamily: "'JetBrains Mono', monospace", fontSize: "0.55rem" }}>Ctrl+Enter to send. Edits re-run validation.</span>
              <button onClick={sendChat} disabled={!chatInput.trim() || chatMutation.isPending} style={{ backgroundColor: !chatInput.trim() || chatMutation.isPending ? "rgba(37,99,235,0.35)" : "rgba(37,99,235,0.14)", border: "1px solid rgba(59,130,246,0.45)", color: "var(--accent-blue)", cursor: !chatInput.trim() || chatMutation.isPending ? "not-allowed" : "pointer", fontFamily: "'JetBrains Mono', monospace", fontSize: "0.62rem", fontWeight: 700, padding: "8px 14px" }}>{chatMutation.isPending ? "SENDING..." : "SEND"}</button>
            </div>
          </div>
        </div>

        <div style={{ ...box, flex: "0.9 1 300px", minWidth: "300px", padding: "14px", maxHeight: "860px", overflowY: "auto" }}>
          <div style={{ color: "var(--text-muted)", fontFamily: "'Space Grotesk', sans-serif", fontSize: "0.75rem", fontWeight: 700, marginBottom: "12px" }}>AGENT TRACE</div>
          {data.steps.length ? data.steps.map((step) => <div key={step.name} style={{ borderLeft: `3px solid ${step.status === "completed" ? "var(--accent-green)" : step.status === "blocked" || step.status === "failed" ? "var(--accent-red)" : step.status === "running" ? "var(--accent-blue)" : "var(--border)"}`, paddingLeft: "10px", marginBottom: "12px" }}><div style={{ color: "var(--text-primary)", fontFamily: "'JetBrains Mono', monospace", fontSize: "0.62rem", fontWeight: 700 }}>{label(step.name)}</div><div style={{ color: "var(--text-secondary)", fontFamily: "'JetBrains Mono', monospace", fontSize: "0.57rem", margin: "3px 0 5px" }}>{String(step.status).toUpperCase()}</div>{step.output?.reasoning_note ? <div style={{ color: "var(--text-secondary)", fontFamily: "'JetBrains Mono', monospace", fontSize: "0.61rem", lineHeight: 1.6 }}>{step.output.reasoning_note}</div> : null}</div>) : <div style={{ color: "var(--text-muted)", fontFamily: "'JetBrains Mono', monospace", fontSize: "0.62rem" }}>[ NO TRACE DATA ]</div>}
        </div>
      </div>
    </div>
  );
}
