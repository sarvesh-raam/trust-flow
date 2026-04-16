/**
 * DEMO MODE — synthetic workflow state for offline judge demos.
 * Matches the shape of the real API but needs no backend.
 */
import type { WorkflowResponse, StatusResponse, WorkflowStep, BBoxEntry } from "@/lib/api";

// ── Fixed identifiers ────────────────────────────────────────────────────────
export const DEMO_RUN_ID  = "demo-0000-0000-0000-000000000001";
export const DEMO_DOC_ID  = "demo-0000-0000-0000-000000000002";

// ── Raw spec (exactly as described in the blueprint) ────────────────────────
export const DEMO_WORKFLOW = {
  run_id:              "RUN-2026-0417-001",
  status:              "BLOCK",
  overall_compliance:  "BLOCK",

  invoice: {
    invoice_number: { value: "INV-2026-04-8821",                     confidence: 0.97 },
    date:           { value: "2026-04-10",                           confidence: 0.94 },
    seller:         { value: "Apex Electronics Co. Ltd, Shenzhen CN", confidence: 0.91 },
    buyer:          { value: "TechDist LLC, Houston TX 77002",        confidence: 0.89 },
    total_amount:   { value: "USD 45,200.00",                        confidence: 0.96 },
    currency:       { value: "USD",                                   confidence: 0.99 },
    gross_weight_kg:{ value: "820",                                   confidence: 0.88 },
  },

  bill_of_lading: {
    bl_number:         { value: "COSCO-SZX-20260410",       confidence: 0.93 },
    vessel:            { value: "COSCO SHIPPING UNIVERSE",  confidence: 0.95 },
    port_of_loading:   { value: "YANTIAN, SHENZHEN",        confidence: 0.92 },
    port_of_discharge: { value: "PORT OF HOUSTON",          confidence: 0.91 },
    gross_weight_kg:   { value: "860",                      confidence: 0.90 },  // ← THE CONFLICT
  },

  line_items: [
    {
      description: "Laptop Computer 15.6inch Intel i7",
      quantity: 50, unit_price: 680, total: 34000,
      hs_code: "8471300000",
      hs_candidates: [
        { code: "8471300000", description: "Portable automatic data processing machines",  confidence: 0.91, rationale: "Laptop computers are portable ADP machines per HTS chapter 84" },
        { code: "8471410000", description: "Other ADP machines comprising in the same housing", confidence: 0.61, rationale: null },
        { code: "8517620000", description: "Machines for reception/transmission of voice", confidence: 0.28, rationale: null },
      ],
    },
    {
      description: "USB-C Charging Adapter 65W",
      quantity: 200, unit_price: 8.50, total: 1700,
      hs_code: "8504401500",
      hs_candidates: [
        { code: "8504401500", description: "Static converters, for ADP machines",          confidence: 0.84, rationale: "65W USB-C adapter qualifies as static converter for ADP" },
        { code: "8504409500", description: "Other static converters",                      confidence: 0.54, rationale: null },
        { code: "8544422000", description: "Electric conductors fitted with connectors",   confidence: 0.31, rationale: null },
      ],
    },
  ],

  compliance_issues: [
    {
      field:    "gross_weight_kg",
      severity: "block" as const,
      message:  "Weight mismatch: Invoice=820kg vs B/L=860kg (delta=40kg, threshold=5%)",
    },
  ],

  agent_trace: [
    { node: "ingest",                 status: "done",    latency_ms: 12   },
    { node: "preprocess",             status: "done",    latency_ms: 234  },
    { node: "ocr_extract",            status: "done",    latency_ms: 1847 },
    { node: "field_extract",          status: "done",    latency_ms: 3201 },
    { node: "reconcile",              status: "done",    latency_ms: 45   },
    { node: "hs_rag",                 status: "done",    latency_ms: 892  },
    { node: "deterministic_validate", status: "done",    latency_ms: 18   },
    { node: "interrupt_node",         status: "blocked", latency_ms: null },
    { node: "country_validate",       status: "pending", latency_ms: null },
    { node: "declaration_generate",   status: "pending", latency_ms: null },
    { node: "audit_trace",            status: "pending", latency_ms: null },
  ],
} as const;

// ── Helpers ──────────────────────────────────────────────────────────────────

/** Build timestamps for steps by accumulating latencies from a base time. */
function buildStepTimestamps(
  traceStatus: "blocked" | "completed",
) {
  const BASE = new Date("2026-04-17T09:23:41.000Z");
  let cursor  = BASE.getTime();

  return DEMO_WORKFLOW.agent_trace.map((t) => {
    const started    = new Date(cursor).toISOString();
    const latMs      = t.latency_ms ?? 0;
    cursor           += latMs;
    const completed  = latMs > 0 ? new Date(cursor).toISOString() : null;

    // In "completed" mode every node is done; in "blocked" respect original status
    const nodeStatus =
      traceStatus === "completed"           ? "completed" :
      t.status    === "done"                ? "completed" :
      t.status    === "blocked"             ? "blocked"   :
      "queued";

    return {
      name:         t.node,
      status:       nodeStatus  as WorkflowStep["status"],
      started_at:   started,
      completed_at: completed,
      error:        null,
      output:       latMs ? { latency_ms: latMs } : {},
    } satisfies WorkflowStep;
  });
}

// ── WorkflowResponse builder ─────────────────────────────────────────────────
export function buildDemoWorkflowResponse(
  traceStatus: "blocked" | "completed",
): WorkflowResponse {
  const complianceStatus = traceStatus === "completed" ? "PASS" : "BLOCK";
  const issueCount       = traceStatus === "completed" ? 0 : 1;

  return {
    id:          DEMO_RUN_ID,
    document_id: DEMO_DOC_ID,
    country:     "us",
    status:      traceStatus === "completed" ? "completed" : "blocked",
    steps:       buildStepTimestamps(traceStatus),
    result: {
      compliance_status:  complianceStatus,
      compliance_issues:  issueCount,
      summary:
        `Declaration RUN-2026-0417-001 | US | Status: ${complianceStatus} | ` +
        `Issues: ${issueCount} | HS codes: 2/2 | Vessel: COSCO SHIPPING UNIVERSE | ` +
        `Total: USD 45,200.00`,
      audit_events: DEMO_WORKFLOW.agent_trace.filter((t) => t.status === "done").length,
      declaration: {
        run_id:       DEMO_RUN_ID,
        jurisdiction: "US",
        invoice: {
          invoice_number:  DEMO_WORKFLOW.invoice.invoice_number.value,
          date:            DEMO_WORKFLOW.invoice.date.value,
          seller:          DEMO_WORKFLOW.invoice.seller.value,
          buyer:           DEMO_WORKFLOW.invoice.buyer.value,
          total_amount:    45200,
          currency:        "USD",
          gross_weight_kg: traceStatus === "completed" ? 860 : 820,
          line_items:      DEMO_WORKFLOW.line_items,
        },
        bill_of_lading: {
          bl_number:         DEMO_WORKFLOW.bill_of_lading.bl_number.value,
          vessel:            DEMO_WORKFLOW.bill_of_lading.vessel.value,
          port_of_loading:   DEMO_WORKFLOW.bill_of_lading.port_of_loading.value,
          port_of_discharge: DEMO_WORKFLOW.bill_of_lading.port_of_discharge.value,
          gross_weight_kg:   860,
        },
        compliance: {
          status: complianceStatus,
          issues: traceStatus === "completed" ? [] : DEMO_WORKFLOW.compliance_issues,
        },
        hs_codes: DEMO_WORKFLOW.line_items.map((li) => ({
          description: li.description,
          hs_code:     li.hs_code,
        })),
      },
    },
    created_at: "2026-04-17T09:23:41.000Z",
    updated_at: "2026-04-17T09:24:55.000Z",
  };
}

// ── StatusResponse builder (adds bboxes) ─────────────────────────────────────
export function buildDemoStatusResponse(
  traceStatus: "blocked" | "completed",
): StatusResponse {
  const base = buildDemoWorkflowResponse(traceStatus);

  // Realistic bounding boxes in PDF-point space (letter page ≈ 612×792pt)
  const bboxes: BBoxEntry[] = [
    {
      field_name: "invoice_number",
      value:      DEMO_WORKFLOW.invoice.invoice_number.value,
      bbox:       [295, 142, 520, 160],
      page: 1, confidence: DEMO_WORKFLOW.invoice.invoice_number.confidence,
      source: "invoice",
    },
    {
      field_name: "date",
      value:      DEMO_WORKFLOW.invoice.date.value,
      bbox:       [295, 170, 410, 188],
      page: 1, confidence: DEMO_WORKFLOW.invoice.date.confidence,
      source: "invoice",
    },
    {
      field_name: "seller",
      value:      DEMO_WORKFLOW.invoice.seller.value,
      bbox:       [72, 230, 430, 248],
      page: 1, confidence: DEMO_WORKFLOW.invoice.seller.confidence,
      source: "invoice",
    },
    {
      field_name: "buyer",
      value:      DEMO_WORKFLOW.invoice.buyer.value,
      bbox:       [72, 270, 390, 288],
      page: 1, confidence: DEMO_WORKFLOW.invoice.buyer.confidence,
      source: "invoice",
    },
    {
      field_name: "total_amount",
      value:      DEMO_WORKFLOW.invoice.total_amount.value,
      bbox:       [400, 640, 540, 658],
      page: 1, confidence: DEMO_WORKFLOW.invoice.total_amount.confidence,
      source: "invoice",
    },
    {
      field_name: "gross_weight_kg",
      value:      traceStatus === "completed" ? "860" : DEMO_WORKFLOW.invoice.gross_weight_kg.value,
      bbox:       [72, 680, 200, 698],
      page: 1, confidence: DEMO_WORKFLOW.invoice.gross_weight_kg.confidence,
      source: "invoice",
    },
  ];

  return { 
    ...base, 
    bboxes,
    invoice_pdf_url: "mock_invoice.pdf",
    bl_pdf_url: "mock_bl.pdf"
  };
}
