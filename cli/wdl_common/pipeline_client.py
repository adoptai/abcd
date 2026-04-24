#!/usr/bin/env python3
"""
Shared Pipeline API Client.

Encapsulates all interactions with the /v1/pipelines and /v1/pipeline-connectors
endpoints. Use this instead of duplicating HTTP logic in one-off pipeline scripts.

Usage:
    from cli.wdl_common.pipeline_client import PipelineClient, get_pipeline_client

    client = get_pipeline_client()   # auto-loads active env credentials
    pipeline = client.create_pipeline(name, description, prompt, source)
"""

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import requests

# ---------------------------------------------------------------------------
# Bootstrap path
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class PipelineClient:
    """
    HTTP client for the Pipeline & Connector API.

    Instantiate via get_pipeline_client() so credentials are loaded
    automatically from the active environment's .env file.
    """

    def __init__(self, base_url: str, token: str) -> None:
        self._base = base_url.rstrip("/")
        self.bearer_token = token
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _req(self, method: str, path: str, **kwargs: Any) -> Any:
        url = f"{self._base}{path}"
        r = getattr(requests, method)(url, headers=self._headers, timeout=60, **kwargs)
        if r.status_code >= 400:
            raise RuntimeError(f"{method.upper()} {path} → {r.status_code}: {r.text[:500]}")
        return r.json()

    # ------------------------------------------------------------------
    # Pipeline CRUD
    # ------------------------------------------------------------------

    def list_pipelines(
        self,
        state: str | None = None,
        search: str | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> list[dict]:
        """List pipelines with optional filtering."""
        params: dict[str, Any] = {"page": page, "page_size": page_size}
        if state:
            params["state"] = state
        if search:
            params["search"] = search
        return self._req("get", "/v1/pipelines", params=params)

    def get_pipeline(self, pipeline_id: str, version_id: str | None = None) -> dict:
        """Get a single pipeline. Pass version_id to fetch a specific draft WDL."""
        params = {}
        if version_id:
            params["version_id"] = version_id
        return self._req("get", f"/v1/pipelines/{pipeline_id}", params=params)

    def create_pipeline(
        self,
        name: str,
        description: str,
        prompt: str,
        source: dict | None = None,
        destinations: list[dict] | None = None,
        schedule_type: str = "manual",
        cron_expr: str | None = None,
    ) -> dict:
        """
        Create a new pipeline (POST /v1/pipelines).

        source defaults to internal_data_store if not provided.
        Returns the created pipeline dict; save the returned `id`.
        """
        if source is None:
            source = {
                "integration_id": "internal_data_store",
                "integration_type": "internal",
                "integration_name": "Internal Data Store",
            }
        if destinations is None:
            destinations = [{"type": "internal_data_store", "label": "results"}]

        return self._req(
            "post",
            "/v1/pipelines",
            json={
                "name": name,
                "description": description,
                "prompt": prompt,
                "source": source,
                "destinations": destinations,
                "schedule_type": schedule_type,
                "cron_expr": cron_expr,
            },
        )

    def update_pipeline(self, pipeline_id: str, **fields: Any) -> dict:
        """Update pipeline fields (POST /v1/pipelines/{id}/update)."""
        return self._req("post", f"/v1/pipelines/{pipeline_id}/update", json=fields)

    def delete_pipeline(self, pipeline_id: str) -> dict:
        """Soft-delete a pipeline."""
        return self._req("delete", f"/v1/pipelines/{pipeline_id}")

    def activate_pipeline(self, pipeline_id: str) -> dict:
        """
        Set pipeline state to 'running'.

        Prerequisite: last_test_run_status must be 'passed'.
        """
        return self.update_pipeline(pipeline_id, state="running")

    def pause_pipeline(self, pipeline_id: str) -> dict:
        """Set pipeline state to 'paused'."""
        return self.update_pipeline(pipeline_id, state="paused")

    # ------------------------------------------------------------------
    # Draft workflow
    # ------------------------------------------------------------------

    def create_draft(self, pipeline_id: str, prompt: str) -> dict:
        """
        Create a WDL draft for the pipeline (POST /v1/pipelines/workflows/draft).

        This triggers async LLM generation; poll with poll_until_wdl_ready()
        before pushing a custom WDL.
        Returns dict with version_id (or id).
        """
        return self._req(
            "post",
            "/v1/pipelines/workflows/draft",
            json={
                "pipeline_id": pipeline_id,
                "prompt": prompt,
                "sources": [],
                "destinations": [],
            },
        )

    def push_wdl(
        self,
        pipeline_id: str,
        version_id: str,
        wdl: list[dict],
        prompt: str | None = None,
    ) -> dict:
        """
        Push WDL directly without triggering LLM (PUT /v1/pipelines/workflows/draft).

        Omit prompt (or keep it unchanged from the create_draft call) to skip LLM.
        Returns the updated draft dict.
        """
        payload: dict[str, Any] = {
            "pipeline_id": pipeline_id,
            "version_id": version_id,
            "wdl": wdl,
            "sources": [],
            "destinations": [],
        }
        if prompt is not None:
            payload["prompt"] = prompt
        return self._req("put", "/v1/pipelines/workflows/draft", json=payload)

    def poll_until_wdl_ready(
        self,
        pipeline_id: str,
        version_id: str,
        max_attempts: int = 30,
        interval: float = 2.0,
    ) -> dict:
        """
        Poll GET /v1/pipelines/{id}?version_id={vid} until wdl is non-null.

        Returns the pipeline dict when ready.
        Raises RuntimeError if not ready after max_attempts.
        """
        for _ in range(max_attempts):
            data = self._req(
                "get",
                f"/v1/pipelines/{pipeline_id}",
                params={"version_id": version_id},
            )
            if data.get("wdl"):
                return data
            time.sleep(interval)
        raise RuntimeError(
            f"LLM draft WDL not ready after {max_attempts * interval:.0f}s of polling"
        )

    def poll_until_wdl_confirmed(
        self,
        pipeline_id: str,
        version_id: str,
        first_step_id: str,
        max_attempts: int = 20,
        interval: float = 3.0,
    ) -> bool:
        """
        Poll until our pushed WDL appears (first step ID matches).

        Returns True if confirmed, False if timed out.
        """
        for _ in range(max_attempts):
            time.sleep(interval)
            data = self._req(
                "get",
                f"/v1/pipelines/{pipeline_id}",
                params={"version_id": version_id},
            )
            wdl = data.get("wdl") or []
            if wdl and wdl[0].get("id") == first_step_id:
                return True
        return False

    def mark_test_passed(self, pipeline_id: str) -> dict:
        """Mark pipeline test run as passed (POST /v1/pipelines/{id}/test-run-status)."""
        return self._req(
            "post",
            f"/v1/pipelines/{pipeline_id}/test-run-status",
            json={"status": "passed"},
        )

    def mark_test_failed(self, pipeline_id: str) -> dict:
        """Mark pipeline test run as failed."""
        return self._req(
            "post",
            f"/v1/pipelines/{pipeline_id}/test-run-status",
            json={"status": "failed"},
        )

    def publish_draft(self, pipeline_id: str, version_id: str) -> dict:
        """
        Publish the draft (POST /v1/pipelines/workflows/draft/publish).

        Finalizes the WDL version and copies it to the pipeline's wdl field.
        """
        return self._req(
            "post",
            "/v1/pipelines/workflows/draft/publish",
            json={"pipeline_id": pipeline_id, "version_id": version_id},
        )

    # ------------------------------------------------------------------
    # Test / execution
    # ------------------------------------------------------------------

    def test_run(
        self,
        pipeline_id: str,
        wdl: list[dict] | None = None,
        test_mode: bool = True,
        workflow_params: dict[str, Any] | None = None,
        workstream_id: str = "",
        allow_concurrent_runs: bool = False,
        max_concurrent_runs: int = 25,
    ) -> dict:
        """
        Trigger a pipeline run (POST /v1/pipelines/workflows/test-run).

        test_mode=True  → test run (no concurrent-run check, for development)
        test_mode=False → production run (409 if a run is already active)

        workflow_params, if provided, replaces {workflow_arguments.key} tokens
        in the WDL before sending (required because /test-run does NOT perform
        server-side workflow_arguments substitution).
        workstream_id scopes the run to a specific workstream (needed for HITL).
        allow_concurrent_runs / max_concurrent_runs control fan-out child dispatch.
        """
        import copy
        if wdl is not None and workflow_params:
            wdl = copy.deepcopy(wdl)
            raw = json.dumps(wdl)
            for key, value in workflow_params.items():
                raw = raw.replace(f"{{workflow_arguments.{key}}}", str(value))
            wdl = json.loads(raw)

        payload: dict[str, Any] = {
            "pipeline_id": pipeline_id,
            "test_mode": test_mode,
        }
        if wdl is not None:
            payload["wdl"] = wdl
        if workstream_id:
            payload["workstream_id"] = workstream_id
        if allow_concurrent_runs:
            payload["allow_concurrent_runs"] = True
            payload["max_concurrent_runs"] = max_concurrent_runs
        return self._req("post", "/v1/pipelines/workflows/test-run", json=payload)

    # ------------------------------------------------------------------
    # Runs & data
    # ------------------------------------------------------------------

    def list_runs(self, pipeline_id: str, page: int = 1, page_size: int = 20) -> dict:
        """List run history for a pipeline."""
        return self._req(
            "get",
            f"/v1/pipelines/{pipeline_id}/runs",
            params={"page": page, "page_size": page_size},
        )

    def get_run_data(self, pipeline_id: str, run_id: str | None = None) -> dict:
        """Get sync data for a pipeline, optionally scoped to a specific run."""
        params = {}
        if run_id:
            params["run_id"] = run_id
        return self._req("get", f"/v1/pipelines/{pipeline_id}/data", params=params)

    # ------------------------------------------------------------------
    # Connectors
    # ------------------------------------------------------------------

    def list_connectors(
        self,
        mode: str | None = None,
        provider_id: str | None = None,
        search: str | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> list[dict]:
        """List connector instances."""
        params: dict[str, Any] = {"page": page, "page_size": page_size}
        if mode:
            params["mode"] = mode
        if provider_id:
            params["provider_id"] = provider_id
        if search:
            params["search"] = search
        return self._req("get", "/v1/pipeline-connectors", params=params)

    def list_connector_catalog(self, mode: str | None = None) -> list[dict]:
        """List all connector providers from the catalog."""
        params = {}
        if mode:
            params["mode"] = mode
        return self._req("get", "/v1/pipeline-connectors/catalog", params=params)

    # ------------------------------------------------------------------
    # Full deploy flow (convenience)
    # ------------------------------------------------------------------

    def deploy_pipeline(
        self,
        name: str,
        description: str,
        prompt: str,
        wdl: list[dict],
        source: dict | None = None,
        destinations: list[dict] | None = None,
        activate: bool = False,
        verbose: bool = True,
    ) -> dict:
        """
        Run the full create → draft → push WDL → confirm → mark passed → publish flow.

        Returns a dict with pipeline_id, version_id, and state.
        Raises RuntimeError on any step failure.
        """

        def _log(msg: str) -> None:
            if verbose:
                print(f"   {msg}")

        _log(f"Creating pipeline: {name}")
        pipeline = self.create_pipeline(name, description, prompt, source, destinations)
        pipeline_id = pipeline["id"]
        _log(f"✅ Pipeline created: {pipeline_id}")

        _log("Creating draft (triggers async LLM)...")
        draft = self.create_draft(pipeline_id, prompt)
        version_id = draft.get("version_id") or draft.get("id")
        _log(f"✅ Draft started: version_id={version_id}")

        _log("Waiting for LLM draft to complete...")
        self.poll_until_wdl_ready(pipeline_id, version_id)
        _log("✅ LLM draft ready")

        _log("Pushing WDL (no LLM)...")
        push_result = self.push_wdl(pipeline_id, version_id, wdl)
        publish_version_id = push_result.get("version_id") or version_id
        _log(f"✅ WDL pushed (publish version: {publish_version_id})")

        _log("Polling for WDL confirmation...")
        confirmed = self.poll_until_wdl_confirmed(
            pipeline_id, publish_version_id, wdl[0]["id"]
        )
        if not confirmed:
            raise RuntimeError("WDL push did not appear after polling")
        _log(f"✅ WDL confirmed ({len(wdl)} steps)")

        _log("Marking test as passed...")
        self.mark_test_passed(pipeline_id)
        _log("✅ Test marked as passed")

        _log("Publishing draft...")
        self.publish_draft(pipeline_id, publish_version_id)
        _log("✅ Published!")

        if activate:
            _log("Activating pipeline...")
            self.activate_pipeline(pipeline_id)
            _log("✅ Pipeline activated (state=running)")

        return {
            "pipeline_id": pipeline_id,
            "version_id": publish_version_id,
            "state": "running" if activate else "draft",
            "name": name,
        }


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def get_pipeline_client() -> PipelineClient:
    """
    Get a PipelineClient configured with active-environment credentials.

    Loads the active environment's .env file automatically.
    """
    from cli.wdl_common.context import ensure_env
    from cli.wdl_common.workspace_manager import WORKSPACES_DIR

    env_name = ensure_env()
    env_path = WORKSPACES_DIR / env_name
    env_dotenv = env_path / ".env"

    if env_dotenv.exists():
        from dotenv import load_dotenv

        load_dotenv(env_dotenv, override=True)

    from cli.auth import get_bearer_token

    token = get_bearer_token()
    base_url = os.getenv(
        "ADOPT_ACTIONS_ENDPOINT", "https://api.adopt.ai"
    ).rstrip("/")

    return PipelineClient(base_url=base_url, token=token)
