"""Prometheus metrics for the document pipeline (scraped at GET /metrics)."""
from prometheus_client import Counter, Histogram

PIPELINE_RUNS_TOTAL = Counter(
    "trustflow_pipeline_runs_total",
    "Pipeline executions by final outcome",
    ["status", "country"],
)
COMPLIANCE_STATUS_TOTAL = Counter(
    "trustflow_compliance_status_total",
    "Compliance verdicts issued on completed runs",
    ["status"],
)
HITL_INTERRUPTS_TOTAL = Counter(
    "trustflow_hitl_interrupts_total",
    "Human-in-the-loop pauses triggered",
)
HITL_RESUMES_TOTAL = Counter(
    "trustflow_hitl_resumes_total",
    "Blocked runs resumed with operator corrections",
)
OCR_CONFIDENCE = Histogram(
    "trustflow_ocr_confidence",
    "Docling conversion quality score per run",
    buckets=[0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 0.95, 1.0],
)
