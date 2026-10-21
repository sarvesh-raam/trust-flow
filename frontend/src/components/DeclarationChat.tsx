import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  actOnProposal,
  errorDetail,
  getChat,
  sendChat,
  type ChatMessage,
  type WorkflowStatus,
} from "@/lib/api";

const mono = "'JetBrains Mono', monospace";

const SUGGESTIONS = [
  "Why did this run get blocked?",
  "Summarise this declaration",
  "Compare the invoice and B/L weights",
];

/** Readable label for a field path, e.g. "bill_of_lading.gross_weight_kg" → "B/L · GROSS WEIGHT KG". */
function fieldLabel(path: string): string {
  const hs = /^line_items\.(\d+)\.hs_code$/.exec(path);
  if (hs) return `LINE ITEM ${Number(hs[1]) + 1} · HS CODE`;
  const [doc, field = ""] = path.split(".");
  return `${doc === "invoice" ? "INVOICE" : "B/L"} · ${field.replace(/_/g, " ").toUpperCase()}`;
}

function ProposalCard({
  message,
  canApply,
  busy,
  onAct,
}: {
  message: ChatMessage;
  canApply: boolean;
  busy: boolean;
  onAct: (action: "apply" | "dismiss") => void;
}) {
  const status = message.proposal_status;
  const accent = status === "applied" ? "var(--accent-green)" : status === "dismissed" ? "var(--text-muted)" : "var(--accent-blue)";
  return (
    <div style={{
      marginTop:       "8px",
      border:          "1px solid var(--border)",
      borderLeft:      `3px solid ${accent}`,
      padding:         "8px 10px",
      backgroundColor: "var(--bg-primary)",
    }}>
      <p style={{ fontFamily: mono, fontSize: "0.56rem", fontWeight: 700, color: accent, letterSpacing: "0.1em", margin: 0 }}>
        {status === "applied" ? "✓ APPLIED — VALIDATION RE-RAN" : status === "dismissed" ? "DISMISSED" : "PROPOSED CHANGES"}
      </p>
      {message.proposals.map((p) => (
        <div key={p.path} style={{ marginTop: "6px", fontFamily: mono, fontSize: "0.62rem" }}>
          <span style={{ color: "var(--text-secondary)" }}>{fieldLabel(p.path)}</span>
          <span style={{ color: "var(--text-muted)" }}> → </span>
          <span style={{ color: "var(--text-primary)", fontWeight: 700 }}>{String(p.value)}</span>
          {p.reason && (
            <div style={{ color: "var(--text-muted)", fontSize: "0.56rem", marginTop: "2px" }}>{p.reason}</div>
          )}
        </div>
      ))}
      {status === "pending" && (
        <div style={{ display: "flex", gap: "8px", marginTop: "10px", alignItems: "center" }}>
          <button
            onClick={() => onAct("apply")}
            disabled={!canApply || busy}
            style={{
              fontFamily: mono, fontSize: "0.6rem", fontWeight: 700, letterSpacing: "0.1em",
              padding: "5px 12px", border: "1px solid var(--accent-blue)",
              backgroundColor: "var(--accent-blue)", color: "#fff",
              cursor: !canApply || busy ? "not-allowed" : "pointer", opacity: !canApply || busy ? 0.5 : 1,
            }}
          >
            APPLY
          </button>
          <button
            onClick={() => onAct("dismiss")}
            disabled={busy}
            style={{
              fontFamily: mono, fontSize: "0.6rem", fontWeight: 700, letterSpacing: "0.1em",
              padding: "5px 12px", border: "1px solid var(--border)", backgroundColor: "transparent",
              color: "var(--text-secondary)", cursor: busy ? "not-allowed" : "pointer",
            }}
          >
            DISMISS
          </button>
          {!canApply && (
            <span style={{ fontFamily: mono, fontSize: "0.55rem", color: "var(--text-muted)" }}>
              wait for the run to finish
            </span>
          )}
        </div>
      )}
    </div>
  );
}

export default function DeclarationChat({
  runId,
  runStatus,
  demo,
}: {
  runId: string;
  runStatus?: WorkflowStatus;
  demo: boolean;
}) {
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState("");
  const [error, setError] = useState<string | null>(null);
  const endRef = useRef<HTMLDivElement>(null);
  const chatKey = ["chat", runId];

  const { data: messages = [], isLoading } = useQuery({
    queryKey: chatKey,
    queryFn:  () => getChat(runId),
    enabled:  !demo,
  });

  const send = useMutation({
    mutationFn: (text: string) => sendChat(runId, text),
    onSuccess:  (msgs) => {
      queryClient.setQueryData(chatKey, msgs);
      setDraft("");
      setError(null);
    },
    onError: (e) => setError(errorDetail(e, "Could not reach the assistant — please try again.")),
  });

  const act = useMutation({
    mutationFn: ({ index, action }: { index: number; action: "apply" | "dismiss" }) =>
      actOnProposal(runId, index, action),
    onSuccess: async (msgs) => {
      queryClient.setQueryData(chatKey, msgs);
      setError(null);
      // An applied edit re-runs validation; refetching restarts status polling.
      await queryClient.invalidateQueries({ queryKey: ["run-status", runId] });
    },
    onError: (e) => setError(errorDetail(e, "Could not apply the change — please try again.")),
  });

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages.length, send.isPending]);

  if (demo) {
    return (
      <p style={{ fontFamily: mono, fontSize: "0.65rem", color: "var(--text-muted)", padding: "24px 16px", textAlign: "center" }}>
        [ CHAT IS NOT AVAILABLE IN DEMO MODE ]
      </p>
    );
  }

  const ready = runStatus === "blocked" || runStatus === "completed";
  const submit = (text: string) => {
    const message = text.trim();
    if (message && !send.isPending) send.mutate(message);
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", minHeight: 0 }}>
      <div style={{ flex: 1, overflowY: "auto", padding: "14px 16px", backgroundColor: "var(--bg-card)" }}>
        {isLoading && (
          <p style={{ fontFamily: mono, fontSize: "0.62rem", color: "var(--text-muted)" }}>Loading chat…</p>
        )}

        {!isLoading && messages.length === 0 && (
          <div style={{ fontFamily: mono, fontSize: "0.62rem", color: "var(--text-muted)", lineHeight: 1.6 }}>
            <p style={{ margin: "0 0 10px" }}>
              Ask about this shipment, or ask to correct a field — e.g. “set the B/L gross weight to 850”.
              Edits are only proposed; nothing changes until you press APPLY.
            </p>
            {ready && SUGGESTIONS.map((s) => (
              <button
                key={s}
                onClick={() => submit(s)}
                disabled={send.isPending}
                style={{
                  display: "block", width: "100%", textAlign: "left", marginBottom: "6px",
                  fontFamily: mono, fontSize: "0.6rem", padding: "6px 8px",
                  border: "1px solid var(--border)", backgroundColor: "var(--bg-primary)",
                  color: "var(--text-secondary)", cursor: "pointer",
                }}
              >
                {s}
              </button>
            ))}
          </div>
        )}

        {messages.map((m, i) => (
          <div key={i} style={{ marginBottom: "12px", textAlign: m.role === "user" ? "right" : "left" }}>
            <div style={{
              display:         "inline-block",
              maxWidth:        "92%",
              textAlign:       "left",
              padding:         "8px 10px",
              fontFamily:      mono,
              fontSize:        "0.63rem",
              lineHeight:      1.55,
              whiteSpace:      "pre-wrap",
              wordBreak:       "break-word",
              color:           "var(--text-primary)",
              backgroundColor: m.role === "user" ? "rgba(37, 99, 235, 0.12)" : "var(--bg-primary)",
              border:          `1px solid ${m.role === "user" ? "rgba(59,130,246,0.35)" : "var(--border)"}`,
            }}>
              {m.content}
              {m.proposals.length > 0 && (
                <ProposalCard
                  message={m}
                  canApply={ready}
                  busy={act.isPending}
                  onAct={(action) => act.mutate({ index: i, action })}
                />
              )}
            </div>
          </div>
        ))}

        {send.isPending && (
          <p style={{ fontFamily: mono, fontSize: "0.6rem", color: "var(--text-muted)" }}>Assistant is thinking…</p>
        )}
        <div ref={endRef} />
      </div>

      {error && (
        <p role="alert" style={{ fontFamily: mono, fontSize: "0.6rem", color: "var(--accent-red)", padding: "6px 16px", margin: 0 }}>
          {error}
        </p>
      )}

      <form
        onSubmit={(e) => { e.preventDefault(); submit(draft); }}
        style={{ display: "flex", gap: "8px", padding: "10px 16px", borderTop: "1px solid var(--border)", backgroundColor: "var(--bg-card)" }}
      >
        <input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder={ready ? "Ask about this declaration…" : "Available once extraction finishes"}
          disabled={!ready || send.isPending}
          maxLength={2000}
          aria-label="Message the assistant"
          style={{
            flex: 1, fontFamily: mono, fontSize: "0.65rem", padding: "8px 10px",
            backgroundColor: "var(--bg-primary)", border: "1px solid var(--border)",
            color: "var(--text-primary)", outline: "none",
          }}
        />
        <button
          type="submit"
          disabled={!ready || send.isPending || !draft.trim()}
          style={{
            fontFamily: mono, fontSize: "0.62rem", fontWeight: 700, letterSpacing: "0.1em",
            padding: "8px 14px", border: "1px solid var(--accent-blue)",
            backgroundColor: "var(--accent-blue)", color: "#fff",
            cursor: !ready || send.isPending || !draft.trim() ? "not-allowed" : "pointer",
            opacity: !ready || send.isPending || !draft.trim() ? 0.5 : 1,
          }}
        >
          SEND
        </button>
      </form>
    </div>
  );
}
