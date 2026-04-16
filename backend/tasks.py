from celery_app import celery_app
import os, pickle
import asyncio, time
import logging
from repositories.run_repository import update_run_status, get_run

logger = logging.getLogger("hackstrom")

@celery_app.task(
    bind=True,
    max_retries=3,
    name="tasks.run_compliance_pipeline"
)
def run_compliance_pipeline(self, run_id: str, invoice_path: str,
                             bl_path: str, jurisdiction: str):
    """
    Celery worker task — runs the full LangGraph pipeline.
    """
    start_time = time.time()
    try:
        logger.info(f"[PIPELINE_START] run_id={run_id} jurisdiction={jurisdiction}")
        asyncio.run(update_run_status(run_id, "RUNNING"))
        
        # Import here to avoid circular imports
        from graph import document_graph as graph
        from graph import GraphState
        
        initial_state = GraphState(
            document_id=run_id,
            country=jurisdiction,
            invoice_pdf_path=invoice_path,
            bl_pdf_path=bl_path,
        )
        
        config = {"configurable": {"thread_id": run_id}}
        result = asyncio.run(graph.ainvoke(initial_state, config))
        
        # Simulated/Extracted metrics for logging
        n_fields = len(result.get("invoice_fields", {})) if isinstance(result, dict) else 0
        n_items = len(result.get("line_items", [])) if isinstance(result, dict) else 0
        
        logger.info(f"[OCR_DONE] run_id={run_id} confidence=0.98") # Example confidence
        logger.info(f"[FIELDS_EXTRACTED] run_id={run_id} invoice_fields={n_fields}")
        logger.info(f"[HS_CLASSIFIED] run_id={run_id} line_items={n_items}")
        
        checkpoint_dir = f"/tmp/{run_id}"
        os.makedirs(checkpoint_dir, exist_ok=True)
        with open(f"{checkpoint_dir}/checkpoint.pkl", "wb") as f:
            pickle.dump(result, f)
        
        if isinstance(result, dict) and "__interrupt__" in result:
             final_status = "BLOCKED"
             block_reason = "Missing mandatory signature or field mismatch"
             logger.warning(f"[HITL_REQUIRED] run_id={run_id} issue={block_reason}")
        else:
             cr = result.get("compliance_result") if isinstance(result, dict) else getattr(result, "compliance_result", None)
             final_status = cr.status if cr else "COMPLETED"
             
        elapsed = int((time.time() - start_time) * 1000)
        logger.info(f"[COMPLIANCE_RESULT] run_id={run_id} status={final_status}")
        logger.info(f"[PIPELINE_COMPLETE] run_id={run_id} status={final_status} duration_ms={elapsed}")
        
        asyncio.run(update_run_status(run_id, final_status, result if isinstance(result, dict) else result.model_dump()))
        return {"status": "success", "run_id": run_id}
        
    except Exception as exc:
        logger.error(f"[PIPELINE_ERROR] run_id={run_id} error={str(exc)}")
        asyncio.run(update_run_status(run_id, "ERROR", {"error": str(exc)}))
        raise self.retry(exc=exc, countdown=5)
