import { useEffect, useRef, useState } from "react";
import { Document, Page, pdfjs } from "react-pdf";
import "react-pdf/dist/Page/AnnotationLayer.css";
import "react-pdf/dist/Page/TextLayer.css";
import { useQuery } from "@tanstack/react-query";
import { getRunStatus, type BBoxEntry } from "@/lib/api";
import { getPDF, storePDF, clearPDF } from "@/lib/pdfStore";

// Configure pdf.js worker to use the reliable unpkg CDN for production viewing.
pdfjs.GlobalWorkerOptions.workerSrc = `//unpkg.com/pdfjs-dist@${pdfjs.version}/build/pdf.worker.min.mjs`;

// ── Colour palette — one colour per invoice field ─────────────────────────────
const FIELD_COLORS: Record<string, string> = {
  invoice_number:  "rgba(37, 99, 235,  0.35)",
  date:            "rgba(16,  185, 129, 0.35)",
  seller:          "rgba(217, 119, 6,  0.35)",
  buyer:           "rgba(220, 38, 38,  0.35)",
  total_amount:    "rgba(139,  92, 246, 0.35)",
  gross_weight_kg: "rgba(236,  72, 153, 0.35)",
  _default:        "rgba(107, 114, 128, 0.25)",
};

function fieldColor(name: string): string {
  return FIELD_COLORS[name] ?? FIELD_COLORS._default;
}

function fieldStroke(name: string): string {
  return fieldColor(name).replace("0.35", "0.85").replace("0.25", "0.70");
}

// ── Props ─────────────────────────────────────────────────────────────────────
interface PDFViewerPanelProps {
  runId:      string;
  pdfUrl:     string | null;
  source?:    "invoice" | "bl";
  pageWidth?: number;
}

// ── Component ─────────────────────────────────────────────────────────────────
export default function PDFViewerPanel({
  runId,
  pdfUrl,
  source    = "invoice",
  pageWidth = 700,
}: PDFViewerPanelProps) {
  const [numPages,      setNumPages]      = useState(0);
  const [pageNumber,    setPageNumber]    = useState(1);
  const [pdfDimensions, setPdfDimensions] = useState<{ width: number; height: number } | null>(null);
  // Tracks the current stored PDF data URL so the viewer re-renders on replace
  const [storedPdfKey,  setStoredPdfKey]  = useState(0);

  const overlayRef   = useRef<HTMLCanvasElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const replaceRef   = useRef<HTMLInputElement>(null);

  const [pdfLoadError, setPdfLoadError] = useState<string | null>(null);
  const [renderWidth, setRenderWidth] = useState(pageWidth);

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const observer = new ResizeObserver((entries) => {
      if (entries[0] && entries[0].contentRect.width > 0) {
        setRenderWidth(Math.min(entries[0].contentRect.width, pageWidth));
      }
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, [pageWidth]);

  const handleReplace = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    clearPDF(runId, source);
    await storePDF(runId, source, file);
    setPdfLoadError(null);
    setNumPages(0);
    setPageNumber(1);
    setStoredPdfKey((k) => k + 1);   // force Document to remount with new data
    e.target.value = "";              // allow re-picking same file
  };

  // ── Fetch status + bboxes ─────────────────────────────────────────────────
  const { data: statusData } = useQuery({
    queryKey: ["run-status", runId],
    queryFn:  () => getRunStatus(runId),
    refetchInterval: (query) => {
      const s = query.state.data?.status;
      return s === "completed" || s === "failed" || s === "blocked" ? false : 3000;
    },
    enabled: !!runId,
  });

  const bboxes: BBoxEntry[] = (statusData?.bboxes ?? []).filter(
    (b) => b.source === source && b.page === pageNumber,
  );

  // ── Canvas drawing ────────────────────────────────────────────────────────
  function drawOverlay() {
    const canvas    = overlayRef.current;
    const container = containerRef.current;
    if (!canvas || !container || !pdfDimensions) return;

    const cw = container.clientWidth;
    const ch = container.clientHeight;
    if (cw === 0 || ch === 0) return;

    canvas.width  = cw;
    canvas.height = ch;

    const scaleX = cw / pdfDimensions.width;
    const scaleY = ch / pdfDimensions.height;

    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    ctx.clearRect(0, 0, cw, ch);

    for (const b of bboxes) {
      const [x1, y1, x2, y2] = b.bbox;

      const rx = x1 * scaleX;
      const ry = y1 * scaleY;
      const rw = (x2 - x1) * scaleX;
      const rh = (y2 - y1) * scaleY;

      ctx.fillStyle   = fieldColor(b.field_name);
      ctx.strokeStyle = fieldStroke(b.field_name);
      ctx.lineWidth   = 1.5;
      ctx.fillRect(rx, ry, rw, rh);
      ctx.strokeRect(rx, ry, rw, rh);

      const label = b.field_name.replace(/_/g, " ");
      ctx.fillStyle = fieldStroke(b.field_name);
      ctx.font      = "bold 9px 'JetBrains Mono', monospace";
      ctx.fillText(label, rx + 2, Math.max(ry - 3, 10));
    }
  }

  useEffect(drawOverlay, [bboxes, pdfDimensions, pageNumber]);

  // ── Resolve PDF source: prop → API URL → sessionStorage ─────────────────
  if (!pdfUrl) {
    if (statusData?.document_id) {
      pdfUrl = getPDF(statusData.document_id, source);
    }
    if (!pdfUrl) {
      pdfUrl = getPDF(runId, source);
    }

    if (!pdfUrl) {
      if (source === "invoice" && statusData?.invoice_pdf_url) {
        pdfUrl = statusData.invoice_pdf_url;
      } else if (source === "bl" && statusData?.bl_pdf_url) {
        pdfUrl = statusData.bl_pdf_url;
      }
      
      // If we got a relative URL from the API, ensure it respects our base path
      if (pdfUrl && pdfUrl.startsWith("/uploads/")) {
        const _envBase = import.meta.env.VITE_API_BASE;
        if (_envBase) {
           pdfUrl = _envBase.replace(/\/api$/, "") + pdfUrl;
        } else if (window.location.hostname !== "localhost" && window.location.hostname !== "127.0.0.1") {
           // In production, root relative is fine if served by nginx
        } else {
           // In Dev, just let the Vite proxy handle it!
        }
      }
    }
  }

  // Ensure /uploads/... URLs resolve against the backend (port 8000), not Vite (port 3000).
  if (pdfUrl && pdfUrl.startsWith("/uploads/")) {
    const backendBase = import.meta.env.VITE_API_BASE
      ? import.meta.env.VITE_API_BASE.replace("/api/v1", "")
      : "http://localhost:8000";
    pdfUrl = `${backendBase}${pdfUrl}`;
  }

  if (!pdfUrl) {
    return (
      <div style={{
        display:        "flex",
        flexDirection:  "column",
        alignItems:     "center",
        justifyContent: "center",
        height:         "200px",
        border:         "1px dashed var(--border)",
        backgroundColor:"var(--bg-secondary)",
        gap:            "10px",
      }}>
        <p style={{
          fontFamily:  "'JetBrains Mono', monospace",
          fontSize:    "0.65rem",
          color:       "var(--text-muted)",
          letterSpacing: "0.08em",
        }}>
          NO PDF SOURCE AVAILABLE
        </p>
        <button
          onClick={() => replaceRef.current?.click()}
          style={{
            fontFamily:      "'JetBrains Mono', monospace",
            fontSize:        "0.58rem",
            fontWeight:      700,
            color:           "var(--accent-blue)",
            backgroundColor: "transparent",
            border:          "1px solid rgba(59,130,246,0.4)",
            padding:         "4px 12px",
            cursor:          "pointer",
            letterSpacing:   "0.06em",
          }}
        >
          + ATTACH PDF
        </button>
        <input
          ref={replaceRef}
          type="file"
          accept=".pdf,image/png,image/jpeg,image/tiff"
          style={{ display: "none" }}
          onChange={handleReplace}
        />
      </div>
    );
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
      {/* Hidden replace-file input */}
      <input
        ref={replaceRef}
        type="file"
        accept=".pdf,image/png,image/jpeg,image/tiff"
        style={{ display: "none" }}
        onChange={handleReplace}
      />

      {/* ✕ Replace button */}
      <div style={{ display: "flex", justifyContent: "flex-end" }}>
        <button
          onClick={() => replaceRef.current?.click()}
          style={{
            fontFamily:      "'JetBrains Mono', monospace",
            fontSize:        "0.55rem",
            fontWeight:      700,
            color:           "var(--text-secondary)",
            backgroundColor: "transparent",
            border:          "1px solid #1e293b",
            padding:         "2px 9px",
            cursor:          "pointer",
            letterSpacing:   "0.06em",
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
          ✕ Replace PDF
        </button>
      </div>
      {/* ── Page navigation ─────────────────────────────────────────────── */}
      {numPages > 1 && (
        <div style={{
          display:    "flex",
          alignItems: "center",
          gap:        "8px",
          fontFamily: "'JetBrains Mono', monospace",
          fontSize:   "0.65rem",
          color:      "var(--text-secondary)",
          userSelect: "none",
        }}>
          <button
            onClick={() => setPageNumber((p) => Math.max(1, p - 1))}
            disabled={pageNumber <= 1}
            style={{
              fontFamily:      "'JetBrains Mono', monospace",
              fontSize:        "0.7rem",
              padding:         "2px 8px",
              border:          "1px solid #1e293b",
              backgroundColor: "transparent",
              color:           "var(--accent-blue)",
              cursor:          pageNumber <= 1 ? "not-allowed" : "pointer",
              opacity:         pageNumber <= 1 ? 0.3 : 1,
            }}
            aria-label="Previous page"
          >
            ‹
          </button>
          <span>
            PAGE {pageNumber} / {numPages}
          </span>
          <button
            onClick={() => setPageNumber((p) => Math.min(numPages, p + 1))}
            disabled={pageNumber >= numPages}
            style={{
              fontFamily:      "'JetBrains Mono', monospace",
              fontSize:        "0.7rem",
              padding:         "2px 8px",
              border:          "1px solid #1e293b",
              backgroundColor: "transparent",
              color:           "var(--accent-blue)",
              cursor:          pageNumber >= numPages ? "not-allowed" : "pointer",
              opacity:         pageNumber >= numPages ? 0.3 : 1,
            }}
            aria-label="Next page"
          >
            ›
          </button>
        </div>
      )}

      {/* ── PDF canvas + bbox overlay ────────────────────────────────────── */}
      <div
        ref={containerRef}
        style={{
          position:        "relative",
          display:         "block",
          border:          "1px solid #1e293b",
          overflow:        "hidden",
          width:           "100%",
          maxWidth:        pageWidth,
          backgroundColor: "var(--bg-primary)",
        }}
      >
        <Document
          key={storedPdfKey}
          file={pdfUrl}
          onLoadSuccess={({ numPages: n }) => {
            setNumPages(n);
            setPageNumber(1);
            setPdfLoadError(null);
          }}
          onLoadError={(e) => {
            console.error("PDF load error:", e);
            setPdfLoadError(`Failed to load ${pdfUrl.split('/').pop()}`);
          }}
          loading={
            <div style={{
              display:        "flex",
              alignItems:     "center",
              justifyContent: "center",
              height:         "200px",
              fontFamily:     "'JetBrains Mono', monospace",
              fontSize:       "0.65rem",
              color:          "var(--text-muted)",
              letterSpacing:  "0.08em",
            }}>
              [ LOADING PDF... ]
            </div>
          }
        >
          {pdfLoadError ? (
            <div style={{
              display:        "flex",
              alignItems:     "center",
              justifyContent: "center",
              height:         "200px",
              fontFamily:     "'JetBrains Mono', monospace",
              fontSize:       "0.65rem",
              color:          "var(--accent-red)",
              letterSpacing:  "0.08em",
            }}>
              [ {pdfLoadError} ]
            </div>
          ) : (
            <Page
              pageNumber={pageNumber}
              width={renderWidth}
              renderTextLayer
              renderAnnotationLayer={false}
              onLoadSuccess={(page) => {
                const vp = page.getViewport({ scale: 1 });
                setPdfDimensions({ width: vp.width, height: vp.height });
              }}
              onRenderSuccess={drawOverlay}
            />
          )}
        </Document>

        {/* Transparent overlay canvas */}
        <canvas
          ref={overlayRef}
          style={{
            position:      "absolute",
            inset:         0,
            pointerEvents: "none",
          }}
        />
      </div>

      {/* ── Field legend ─────────────────────────────────────────────────── */}
      {bboxes.length > 0 && (
        <div style={{ display: "flex", flexWrap: "wrap", gap: "5px" }}>
          {bboxes.map((b) => (
            <span
              key={b.field_name}
              title={`confidence: ${(b.confidence * 100).toFixed(0)}%`}
              style={{
                display:         "inline-flex",
                alignItems:      "center",
                gap:             "5px",
                padding:         "2px 8px",
                fontSize:        "0.6rem",
                fontFamily:      "'JetBrains Mono', monospace",
                backgroundColor: fieldColor(b.field_name),
                border:          `1px solid ${fieldStroke(b.field_name)}`,
                color:           "var(--text-primary)",
              }}
            >
              <span>{b.field_name.replace(/_/g, " ")}</span>
              <span style={{ opacity: 0.65, maxWidth: "100px", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {b.value}
              </span>
            </span>
          ))}
        </div>
      )}
    </div>
  );
}
