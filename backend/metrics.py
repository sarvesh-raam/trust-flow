from prometheus_client import Counter, Histogram, Gauge

PIPELINE_RUNS_TOTAL = Counter(
    "trustflow_pipeline_runs_total",
    "Total pipeline executions",
    ["status", "country"]
)
PIPELINE_DURATION_SECONDS = Histogram(
    "trustflow_pipeline_duration_seconds",
    "End-to-end pipeline wall time in seconds",
    ["country"],
    buckets=[2, 5, 10, 20, 30, 60, 90, 120, 180, 300]
)
NODE_LATENCY_SECONDS = Histogram(
    "trustflow_node_latency_seconds",
    "Per LangGraph node execution time in seconds",
    ["node_name"],
    buckets=[0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 20]
)
OCR_CONFIDENCE = Histogram(
    "trustflow_ocr_confidence",
    "OCR confidence score distribution",
    buckets=[0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 0.95, 1.0]
)
COMPLIANCE_STATUS_TOTAL = Counter(
    "trustflow_compliance_status_total",
    "Compliance verdicts issued",
    ["status"]
)
HITL_INTERRUPTS_TOTAL = Counter(
    "trustflow_hitl_interrupts_total",
    "Human-in-the-loop pauses triggered",
    ["reason"]
)
HITL_RESOLUTION_SECONDS = Histogram(
    "trustflow_hitl_resolution_seconds",
    "Time from HITL pause to operator resolution in seconds",
    buckets=[10, 30, 60, 120, 300, 600, 1800]
)
ACTIVE_CELERY_TASKS = Gauge(
    "trustflow_active_celery_tasks",
    "Currently executing Celery tasks"
)
QUEUE_DEPTH = Gauge(
    "trustflow_queue_depth",
    "Tasks waiting in Redis queue"
)
LLM_TOKEN_USAGE = Counter(
    "trustflow_llm_tokens_total",
    "Cumulative LLM token usage",
    ["model", "token_type"]
)
LLM_CALL_DURATION = Histogram(
    "trustflow_llm_call_duration_seconds",
    "LLM API call latency in seconds",
    ["model", "call_type"],
    buckets=[0.5, 1, 2, 5, 10, 20, 30, 60]
)
LLM_CALL_ERRORS = Counter(
    "trustflow_llm_call_errors_total",
    "Failed LLM API calls",
    ["model", "error_type"]
)
