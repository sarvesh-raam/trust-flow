import { useRef, useState } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { uploadDocument, createWorkflow, type CountryCode } from "@/lib/api";
import { storePDF } from "@/lib/pdfStore";

function extractErrMsg(err: unknown): string {
  if (!err) return "Unknown error";
  const e = err as {
    response?: { data?: { detail?: string } };
    message?: string;
  };
  return e.response?.data?.detail ?? e.message ?? String(err);
}

const schema = z.object({
  country: z.enum(["us", "uae"] as const),
});
type FormValues = z.infer<typeof schema>;

function fmtBytes(n: number): string {
  if (n < 1024)         return `${n} B`;
  if (n < 1024 * 1024)  return `${(n / 1024).toFixed(1)} KB`;
  return                       `${(n / (1024 * 1024)).toFixed(2)} MB`;
}

// ── File drop zone ─────────────────────────────────────────────────────────────
function DropZone({
  label,
  file,
  onFile,
}: {
  label: string;
  file: File | null;
  onFile: (f: File) => void;
}) {
  const [isDragging, setIsDragging] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(false);
    const f = e.dataTransfer.files[0];
    if (f) onFile(f);
  };

  return (
    <div
      className={`dash-border${isDragging ? " dragging" : ""}`}
      onClick={() => inputRef.current?.click()}
      onDragOver={(e) => { e.preventDefault(); setIsDragging(true); }}
      onDragLeave={() => setIsDragging(false)}
      onDrop={handleDrop}
      style={{ padding: "20px 16px", textAlign: "center", cursor: "pointer", marginBottom: "12px" }}
    >
      {/* Upload icon */}
      <svg width="24" height="24" viewBox="0 0 24 24" fill="none"
        stroke={file ? "var(--accent-green)" : "var(--accent-blue)"} strokeWidth="1.5"
        strokeLinecap="square" strokeLinejoin="miter"
        style={{ display: "block", margin: "0 auto 8px" }}>
        <path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/>
        <polyline points="14 2 14 8 20 8"/>
        <line x1="12" y1="18" x2="12" y2="12"/>
        <line x1="9" y1="15" x2="15" y2="15"/>
      </svg>

      <div style={{
        fontFamily: "'JetBrains Mono', monospace",
        fontSize: "0.6rem",
        fontWeight: 700,
        color: "var(--text-secondary)",
        letterSpacing: "0.12em",
        marginBottom: "5px",
      }}>
        {label}
      </div>

      {file ? (
        <div>
          <p style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: "0.7rem", color: "var(--accent-green)", fontWeight: 700, wordBreak: "break-all" }}>
            {file.name}
          </p>
          <p style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: "0.58rem", color: "var(--text-secondary)", marginTop: "3px" }}>
            {fmtBytes(file.size)} · CLICK TO REPLACE
          </p>
        </div>
      ) : (
        <p style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: "0.65rem", color: "var(--text-muted)", letterSpacing: "0.04em" }}>
          DROP PDF HERE OR CLICK TO BROWSE
        </p>
      )}

      <input
        ref={inputRef}
        type="file"
        style={{ display: "none" }}
        accept=".pdf,image/png,image/jpeg,image/tiff"
        onChange={(e) => { const f = e.target.files?.[0]; if (f) onFile(f); }}
      />
    </div>
  );
}

function Label({ children }: { children: React.ReactNode }) {
  return (
    <div style={{
      fontFamily: "'JetBrains Mono', monospace",
      fontSize: "0.6rem",
      fontWeight: 700,
      color: "var(--text-secondary)",
      letterSpacing: "0.14em",
      marginBottom: "8px",
    }}>
      {children}
    </div>
  );
}

interface UploadPageProps {}

export default function UploadPage({}: UploadPageProps) {
  const [invoiceFile, setInvoiceFile] = useState<File | null>(null);
  const [blFile,      setBlFile]      = useState<File | null>(null);
  const [lastDocId,   setLastDocId]   = useState<string | null>(null);

  const qc = useQueryClient();

  const { register, handleSubmit } = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: { country: "us" },
  });

  const uploadMutation = useMutation({
    mutationFn: ({ inv, bl, country }: { inv: File; bl: File; country: CountryCode }) =>
      uploadDocument(inv, bl, country),
    onSuccess: (doc) => {
      setLastDocId(doc.id);
      qc.invalidateQueries({ queryKey: ["documents"] });
    },
  });

  const workflowMutation = useMutation({
    mutationFn: ({ docId, country }: { docId: string; country: CountryCode }) =>
      createWorkflow(docId, country),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["workflows"] });
    },
  });

  const onSubmit = async (values: FormValues) => {
    if (!invoiceFile || !blFile) return;

    // ── Real mode ────────────────────────────────────────────────────────────
    const doc = await uploadMutation.mutateAsync({
      inv: invoiceFile,
      bl: blFile,
      country: values.country,
    });
    // Persist PDFs in sessionStorage so the PDF viewer can display them
    await Promise.all([
      storePDF(doc.id, "invoice", invoiceFile),
      storePDF(doc.id, "bl",      blFile),
    ]);
    await workflowMutation.mutateAsync({ docId: doc.id, country: values.country });
  };

  const isPending = uploadMutation.isPending || workflowMutation.isPending;
  const hasError  = uploadMutation.isError || workflowMutation.isError;
  const errMsg    = hasError
    ? extractErrMsg(uploadMutation.error ?? workflowMutation.error)
    : "";

  const bothFilesReady = invoiceFile !== null && blFile !== null;

  return (
    <div style={{
      display: "flex",
      alignItems: "center",
      justifyContent: "center",
      minHeight: "100%",
      padding: "48px 16px",
      backgroundColor: "var(--bg-primary)",
    }}>
      {/* Terminal card */}
      <div style={{
        width: "100%",
        maxWidth: "520px",
        backgroundColor: "var(--bg-card)",
        border: "1px solid #1e293b",
        transition: "box-shadow 0.3s ease",
      }}
        onMouseEnter={(e) => {
          (e.currentTarget as HTMLElement).style.boxShadow =
            "0 0 30px rgba(59,130,246,0.1), inset 0 0 0 1px rgba(59,130,246,0.12)";
        }}
        onMouseLeave={(e) => {
          (e.currentTarget as HTMLElement).style.boxShadow = "none";
        }}
      >
        {/* Card header */}
        <div style={{ borderBottom: "1px solid #1e293b", padding: "14px 20px", display: "flex", alignItems: "center", gap: "10px" }}>
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="var(--accent-blue)" strokeWidth="2" strokeLinecap="square">
            <path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4"/>
            <polyline points="17 8 12 3 7 8"/>
            <line x1="12" y1="3" x2="12" y2="15"/>
          </svg>
          <span style={{ fontFamily: "'Space Grotesk', sans-serif", fontWeight: 700, fontSize: "0.8rem", color: "var(--text-primary)", letterSpacing: "0.04em" }}>
            DOCUMENT INGEST
          </span>
          <span style={{ marginLeft: "auto", fontFamily: "'JetBrains Mono', monospace", fontSize: "0.6rem", color: "var(--text-muted)", letterSpacing: "0.08em" }}>
            v1.0
          </span>
        </div>

        <div style={{ padding: "20px" }}>
          <p style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: "0.65rem", color: "var(--text-secondary)", marginBottom: "20px", letterSpacing: "0.05em" }}>
            UPLOAD BOTH DOCUMENTS — INVOICE AND BILL OF LADING
          </p>

          <form onSubmit={handleSubmit(onSubmit)}>
            {/* ── Invoice drop zone ──────────────────────────────────────────── */}
            <DropZone
              label="① COMMERCIAL INVOICE — PDF / IMAGE"
              file={invoiceFile}
              onFile={setInvoiceFile}
            />

            {/* ── BL drop zone ───────────────────────────────────────────────── */}
            <DropZone
              label="② BILL OF LADING — PDF / IMAGE"
              file={blFile}
              onFile={setBlFile}
            />

            {/* ── Jurisdiction selector ──────────────────────────────────────── */}
            <div style={{ marginBottom: "20px" }}>
              <Label>JURISDICTION</Label>
              <select {...register("country")} className="t-select">
                <option value="us">UNITED STATES — CBP / HTS</option>
                <option value="uae">UNITED ARAB EMIRATES — FCA</option>
              </select>
            </div>

            {/* ── File readiness indicator ───────────────────────────────────── */}
            {!bothFilesReady && (
              <div style={{ marginBottom: "12px", fontFamily: "'JetBrains Mono', monospace", fontSize: "0.6rem", color: "var(--text-muted)", letterSpacing: "0.06em" }}>
                {!invoiceFile && !blFile ? "[ ATTACH BOTH DOCUMENTS TO PROCEED ]"
                  : !invoiceFile         ? "[ MISSING: COMMERCIAL INVOICE ]"
                  :                        "[ MISSING: BILL OF LADING ]"}
              </div>
            )}

            {/* ── Submit button ──────────────────────────────────────────────── */}
            <button
              type="submit"
              disabled={!bothFilesReady || isPending}
              className="t-btn-primary"
            >
              {uploadMutation.isPending
                ? "[ UPLOADING... ]"
                : workflowMutation.isPending
                ? "[ INITIATING WORKFLOW... ]"
                : "INITIATE PROCESSING"}
            </button>

            {/* ── Success feedback ───────────────────────────────────────────── */}
            {lastDocId && !isPending && !hasError && (
              <div style={{ marginTop: "16px", padding: "10px 14px", backgroundColor: "rgba(22, 163, 74, 0.06)", border: "1px solid rgba(34,197,94,0.2)", borderLeft: "3px solid #22c55e" }}>
                <p style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: "0.65rem", color: "var(--accent-green)", fontWeight: 700 }}>
                  ✓ WORKFLOW INITIATED — CHECK WORKFLOWS TAB
                </p>
                <p style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: "0.6rem", color: "var(--text-secondary)", marginTop: "4px", wordBreak: "break-all" }}>
                  RUN_ID: {lastDocId}
                </p>
              </div>
            )}

            {/* ── Error feedback ─────────────────────────────────────────────── */}
            {hasError && (
              <div style={{ marginTop: "16px", padding: "10px 14px", backgroundColor: "rgba(220, 38, 38, 0.06)", border: "1px solid rgba(239,68,68,0.2)", borderLeft: "3px solid #ef4444" }}>
                <p style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: "0.65rem", color: "var(--accent-red)" }}>
                  ✗ ERROR: {errMsg}
                </p>
              </div>
            )}
          </form>
        </div>
      </div>
    </div>
  );
}
