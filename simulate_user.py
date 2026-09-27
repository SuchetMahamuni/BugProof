import time
import requests

API_BASE = "http://localhost:5000/api"

def simulate_workflow():
    print("1. Submitting bug...")
    payload = {
        "title": "Command correction fails for a valid command",
        "description": "The command correction tool does not correctly suggest a corrected command for a command that contains a typo.",
        "target_repository": "thefuck",
        "error_message": "",
        "extra_context": {"notes": "This is a test submission for BugProof. Reproduce the issue, investigate the root cause, propose a fix, and run the available verification tests."}
    }
    
    res = requests.post(f"{API_BASE}/bugs", json=payload)
    if res.status_code != 201:
        print(f"Failed to submit bug: {res.text}")
        return
    
    bug_id = res.json()["id"]
    print(f"Bug submitted successfully! ID: {bug_id}")
    
    print("2. Starting Investigation...")
    res = requests.post(f"{API_BASE}/bugs/{bug_id}/start")
    if res.status_code != 202:
        print(f"Failed to start investigation: {res.text}")
        return
        
    print("Waiting for workflow to reach WAITING_FOR_APPROVAL...")
    while True:
        res = requests.get(f"{API_BASE}/bugs/{bug_id}")
        status = res.json()["status"]
        print(f"Current Bug Status: {status}")
        if status in ["WAITING_FOR_APPROVAL", "FAILED", "COMPLETED", "FIX_APPLIED"]:
            break
        time.sleep(2)
        
    if status != "WAITING_FOR_APPROVAL":
        print("Workflow did not reach approval state.")
        return
        
    print("3. Fetching Investigation and Fix IDs...")
    res = requests.get(f"{API_BASE}/investigations/bug/{bug_id}")
    investigations = res.json()
    if not investigations:
        print("No investigations found.")
        return
    inv_id = investigations[0]["id"]
    
    res = requests.get(f"{API_BASE}/fixes/investigation/{inv_id}")
    fixes = res.json()
    if not fixes:
        print("No fixes found.")
        return
    fix_id = fixes[0]["id"]
    print(f"Fix ID: {fix_id} (Status: {fixes[0]['approval_status']})")
    
    print("4. Approving Fix...")
    res = requests.post(f"{API_BASE}/fixes/{fix_id}/approve", json={"approved_by": "Automated Tester"})
    if res.status_code != 200:
        print(f"Failed to approve fix: {res.text}")
        return
        
    print("5. Applying Fix...")
    res = requests.post(f"{API_BASE}/fixes/{fix_id}/apply")
    if res.status_code != 200:
        print(f"Failed to apply fix: {res.text}")
        return
        
    print("6. Generating Final Report...")
    res = requests.post(f"{API_BASE}/reports/bug/{bug_id}/generate")
    if res.status_code not in [200, 201]:
        print(f"Failed to generate report: {res.text}")
        return
        
    print("\n--- Final Report Summary ---")
    print(res.json()["summary"])

if __name__ == "__main__":
    simulate_workflow()
