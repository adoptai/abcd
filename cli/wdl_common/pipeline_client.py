"""Pipeline client for triggering pipeline test-runs on the Adopt platform."""

import json
import os
import sys
from pathlib import Path
from typing import Any

import requests

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from cli.auth import get_bearer_token


class PipelineClient:
    """Client for Adopt pipeline operations."""

    def __init__(self, bearer_token: str | None = None) -> None:
        self._bearer_token = bearer_token
        self.base_url = os.getenv(
            "ADOPT_ACTIONS_ENDPOINT",
            "https://staging-adopt-backend-adopt-dev-ws-8000.adopt.ai",
        )

    @property
    def bearer_token(self) -> str:
        if self._bearer_token is None:
            self._bearer_token = get_bearer_token()
        return self._bearer_token

    @property
    def headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.bearer_token}",
            "Content-Type": "application/json",
        }

    def test_run(
        self,
        pipeline_id: str,
        wdl: list[dict] | None = None,
        test_mode: bool = False,
        workflow_params: dict[str, Any] | None = None,
        workstream_id: str = "",
    ) -> dict[str, Any]:
        """Trigger a pipeline test-run.

        Args:
            pipeline_id: Remote pipeline ID.
            wdl: Optional WDL override (sent as-is to the API).
            test_mode: If True, runs in test mode.
            workflow_params: Optional dict of workflow_arguments values to bake
                into the WDL before sending (replaces {workflow_arguments.key}).
            workstream_id: Optional workstream ID to scope the run.

        Returns:
            API response dict with workflow_id, status, etc.
        """
        if wdl is not None and workflow_params:
            import copy, re
            wdl = copy.deepcopy(wdl)
            raw = json.dumps(wdl)
            for key, value in workflow_params.items():
                raw = raw.replace(f"{{workflow_arguments.{key}}}", str(value))
            wdl = json.loads(raw)

        url = f"{self.base_url}/v1/pipelines/workflows/test-run"
        payload: dict[str, Any] = {
            "pipeline_id": pipeline_id,
            "test_mode": test_mode,
        }
        if wdl is not None:
            payload["wdl"] = wdl
        if workstream_id:
            payload["workstream_id"] = workstream_id

        resp = requests.post(url, headers=self.headers, json=payload, timeout=120)
        resp.raise_for_status()
        return resp.json()


    def create_draft(self, pipeline_id: str, prompt: str = "") -> dict[str, Any]:
        """Create a new draft version for a pipeline."""
        url = f"{self.base_url}/v1/pipelines/workflows/draft"
        payload = {
            "pipeline_id": pipeline_id,
            "prompt": prompt,
            "sources": [],
            "destinations": [],
        }
        resp = requests.post(url, headers=self.headers, json=payload, timeout=60)
        resp.raise_for_status()
        return resp.json()

    def push_wdl(self, pipeline_id: str, version_id: str, wdl: list[dict]) -> dict[str, Any]:
        """Push WDL to an existing draft version."""
        url = f"{self.base_url}/v1/pipelines/workflows/draft"
        payload = {
            "pipeline_id": pipeline_id,
            "version_id": version_id,
            "wdl": wdl,
            "sources": [],
            "destinations": [],
        }
        resp = requests.put(url, headers=self.headers, json=payload, timeout=60)
        resp.raise_for_status()
        return resp.json()

    def publish_draft(self, pipeline_id: str, version_id: str) -> dict[str, Any]:
        """Publish a draft version."""
        url = f"{self.base_url}/v1/pipelines/workflows/draft/publish"
        payload = {"pipeline_id": pipeline_id, "version_id": version_id}
        resp = requests.post(url, headers=self.headers, json=payload, timeout=60)
        resp.raise_for_status()
        return resp.json()

    def update_and_publish(self, pipeline_id: str, wdl: list[dict],
                           description: str = "") -> dict[str, Any]:
        """Create draft, push WDL, and publish in one call."""
        import time
        draft = self.create_draft(pipeline_id, prompt=description)
        version_id = draft.get("version_id", draft.get("id"))
        self.push_wdl(pipeline_id, version_id, wdl)
        time.sleep(2)
        result = self.publish_draft(pipeline_id, version_id)
        return {"version_id": version_id, "publish_result": result}


def get_pipeline_client(bearer_token: str | None = None) -> PipelineClient:
    """Factory: create a PipelineClient using the active environment's credentials."""
    from cli.wdl_common.context import ensure_env
    ensure_env()
    return PipelineClient(bearer_token=bearer_token)
