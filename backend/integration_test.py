import sys
import os
from time import sleep

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from backend.app import create_app
from backend.database.connection import db

app = create_app()
app.testing = True
client = app.test_client()

def run_integration():
    print("=== STARTING E2E INTEGRATION TEST ===")
    with app.app_context():
        db.create_all()

    # 1. Create a bug
    bug_payload = {
        "title": "thefuck doesn't correctly parse sudo with git",
        "description": "When I run `sudo git checkout`, it fails, and thefuck suggests the wrong command.",
        "target_repository": "thefuck",
        "error_message": "fatal: not a git repository",
    }
    print("1. Creating Bug...")
    res = client.post("/api/bugs", json=bug_payload)
    if res.status_code != 201:
        print(f"FAILED to create bug: {res.status_code} {res.text}")
        return
    bug_data = res.get_json()
    bug_id = bug_data['id']
    print(f"Bug created with ID: {bug_id}")

    # 2. Retrieve bug
    res = client.get(f"/api/bugs/{bug_id}")
    if res.status_code != 200:
        print(f"FAILED to retrieve bug: {res.status_code}")
        return

    # 3. Start investigation
    print("3. Starting investigation...")
    res = client.post(f"/api/bugs/{bug_id}/start")
    if res.status_code != 202:
        print(f"FAILED to start investigation: {res.status_code} {res.text}")
        return

    # Wait for investigation to finish (it's run synchronously in background threading or something? 
    # Oh wait, orchestrator might be running in a thread if it's the real app. Let's check how `/start` works.
    # Actually, in testing it might be blocking or we need to wait.
    print("Waiting for investigation/fix generation to complete...")
    for _ in range(20):
        res = client.get(f"/api/bugs/{bug_id}")
        status = res.get_json()['status']
        print(f"Bug status: {status}")
        if status in ("WAITING_FOR_APPROVAL", "FAILED", "COMPLETED"):
            break
        sleep(2)

    # 4. Check investigations
    res = client.get(f"/api/investigations/bug/{bug_id}")
    inv_data = res.get_json()
    if not inv_data:
        print("FAILED: No investigations found.")
        return
    inv_id = inv_data[0]['id']
    print(f"Investigation ID: {inv_id}")

    # 5. Check fixes
    res = client.get(f"/api/fixes/investigation/{inv_id}")
    fixes = res.get_json()
    if not fixes:
        print("FAILED: No fixes found.")
        return
    fix_id = fixes[0]['id']
    print(f"Fix ID: {fix_id}, Status: {fixes[0]['approval_status']}")

    # 6. Trigger bug reproduction test run (Member 2 integration)
    print("6. Triggering bug reproduction test...")
    res = client.post("/api/verification/run", json={"bug_id": bug_id, "kind": "BUG_REPRODUCTION"})
    if res.status_code != 202:
        print(f"FAILED to trigger reproduction run: {res.status_code} {res.text}")
    else:
        run_data = res.get_json()
        print(f"Reproduction run status: {run_data['status']}")
        if run_data['status'] == 'ERROR':
            print(f"Run output: {run_data.get('output')}")

    # 8. Impact analysis
    print("8. Running impact analysis...")
    res = client.post("/api/verification/impact/analyse", json={"fix_id": fix_id})
    if res.status_code != 202:
        print(f"FAILED impact analysis: {res.status_code} {res.text}")
    else:
        print("Impact analysis SUCCESS")

    # 9. Generate regression tests
    print("9. Generating regression tests...")
    res = client.post("/api/verification/regression/generate", json={"fix_id": fix_id})
    if res.status_code != 202:
        print(f"FAILED regression generation: {res.status_code} {res.text}")
    else:
        print("Regression generation SUCCESS")

    # 13. Approve fix
    print("13. Approving fix...")

    res = client.post(f"/api/fixes/{fix_id}/approve", json={"approved_by": "Test Engineer"})
    if res.status_code != 200:
        print(f"FAILED to approve fix: {res.status_code} {res.text}")
        return
    print("Fix approved.")

    # 14. Apply fix
    print("14. Applying fix...")
    res = client.post(f"/api/fixes/{fix_id}/apply")
    if res.status_code not in (200, 422):
        print(f"FAILED to apply fix: {res.status_code} {res.text}")
        return
    print(f"Apply fix result: {res.status_code}")
    if res.status_code == 422:
        print("Patch validation might have failed. Response:")
        print(res.text)

    # 15. Regression tests execute (Trigger REGRESSION)
    print("15. Executing post-fix regression tests...")
    res = client.post("/api/verification/run", json={"fix_id": fix_id, "kind": "REGRESSION"})
    if res.status_code != 202:
        print(f"FAILED to trigger regression run: {res.status_code} {res.text}")
    else:
        run_data = res.get_json()
        print(f"Regression run status: {run_data['status']}")
        if run_data['status'] == 'ERROR':
            print(f"Run output: {run_data.get('output')}")

    # 16. Generate Final Report (Member 3 Integration)
    print("16. Generating Final Report...")
    res = client.post(f"/api/reports/bug/{bug_id}/generate")
    if res.status_code not in (200, 201):
        print(f"FAILED to generate report: {res.status_code} {res.text}")
    else:
        print(f"Report generated successfully. ID: {res.get_json()['id']}")

    print("=== INTEGRATION TEST FINISHED ===")


if __name__ == "__main__":
    run_integration()
