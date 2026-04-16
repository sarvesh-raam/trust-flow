import requests
import time

base = "http://localhost:8000"

with open("test_docs/weight_conflict_set/invoice.pdf", "rb") as inv, open("test_docs/weight_conflict_set/bl.pdf", "rb") as bl:
    files = {"invoice_pdf": inv, "bl_pdf": bl}
    data = {"country": "us"}
    r = requests.post(f"{base}/api/v1/upload/", files=files, data=data)
    r.raise_for_status()
    doc_id = r.json()["id"]

print("Doc ID:", doc_id)

r = requests.post(f"{base}/api/v1/workflow/", json={"document_id": doc_id, "country": "us"})
r.raise_for_status()
run_id = r.json()["id"]

print("Run ID:", run_id)

while True:
    r = requests.get(f"{base}/api/v1/workflow/status/{run_id}")
    r.raise_for_status()
    s = r.json()
    print("Status:", s["status"])
    if s["status"] in ["blocked", "completed", "failed"]:
        print("Issues:", s["result"].get("issues", []))
        break
    time.sleep(1.5)

if s["status"] == "blocked":
    print("Resuming...")
    r = requests.post(f"{base}/api/v1/workflow/resume/{run_id}", json={"gross_weight_kg": 820})
    r.raise_for_status()
    
    while True:
        r = requests.get(f"{base}/api/v1/workflow/status/{run_id}")
        r.raise_for_status()
        s = r.json()
        print("Status after resume:", s["status"])
        if s["status"] in ["blocked", "completed", "failed"]:
            print("Issues:", s["result"].get("issues", []))
            break
        time.sleep(1.5)

