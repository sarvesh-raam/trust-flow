import asyncio
from uuid import uuid4
from backend.models import WorkflowCreateRequest, ResumeRequest
from backend.routes.workflow import create_workflow, resume_workflow, _workflows
from fastapi import BackgroundTasks

async def test():
    req = WorkflowCreateRequest(document_id=uuid4(), country="us")
    bg = BackgroundTasks()
    res = await create_workflow(req, bg)
    run_id = str(res.id)
    print("Created:", run_id)
    # wait for background tasks
    for task in bg.tasks:
        await task.func(*task.args, **task.kwargs)
    
    # check status
    wf = _workflows[run_id]
    print("Status after run:", wf.status)
    if wf.result.get("issues"):
        print("Issues:")
        for iss in wf.result["issues"]:
            print(f"- {iss}")
    
    # resume
    if wf.status == "blocked":
        print("Resuming...")
        res_req = ResumeRequest(gross_weight_kg=820.0)
        bg2 = BackgroundTasks()
        await resume_workflow(run_id, res_req, bg2)
        for task in bg2.tasks:
            await task.func(*task.args, **task.kwargs)
        print("Status after resume:", wf.status)

        if wf.result.get("issues"):
            print("Issues after resume:")
            for iss in wf.result["issues"]:
                print(f"- {iss}")

if __name__ == "__main__":
    asyncio.run(test())
