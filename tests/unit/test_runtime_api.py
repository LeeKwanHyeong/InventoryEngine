"""Inventory Runtime HTTP ingress tests without a deployed service."""

import json
import unittest

from fastapi.testclient import TestClient

from dsio_inventory_engine.entrypoints.runtime_api import create_inventory_runtime_app
from tests.support.runtime_fixtures import RUN_ID, runtime_dispatch


class Submission:
    def __init__(self):
        self.requests = []

    async def submit(self, request):
        self.requests.append(request)
        return {"engine_run_id": request.engine_run_id, "status": "running", "replayed": False}


class RuntimeApiTests(unittest.TestCase):
    def test_authenticated_request_is_submitted(self):
        submission = Submission()
        client = TestClient(
            create_inventory_runtime_app(submission, bearer_token="internal-token")
        )

        response = client.post(
            "/api/v1/executions",
            json=runtime_dispatch(),
            headers={"Authorization": "Bearer internal-token"},
        )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["engine_run_id"], RUN_ID)
        self.assertEqual(len(submission.requests), 1)
        health = client.get("/health")
        self.assertIn("inventory-engine-execution-request-v1", health.json()["capabilities"])

    def test_auth_and_duplicate_json_keys_fail_closed(self):
        submission = Submission()
        client = TestClient(
            create_inventory_runtime_app(submission, bearer_token="internal-token")
        )
        denied = client.post("/api/v1/executions", json=runtime_dispatch())
        self.assertEqual(denied.status_code, 401)

        document = json.dumps(runtime_dispatch(), separators=(",", ":"))
        duplicated = document.replace(
            '"contract_version":"1.0.0"',
            '"contract_version":"1.0.0","contract_version":"1.0.0"',
            1,
        )
        rejected = client.post(
            "/api/v1/executions",
            content=duplicated,
            headers={
                "Authorization": "Bearer internal-token",
                "Content-Type": "application/json",
            },
        )
        self.assertEqual(rejected.status_code, 422)
        self.assertEqual(rejected.json()["detail"], "INVENTORY_RUNTIME_REQUEST_INVALID")
        self.assertEqual(submission.requests, [])


if __name__ == "__main__":
    unittest.main()
