#!/usr/bin/env python3
"""
Adopt API Client for WDL actions.

Consolidates API calls for create, test, update workflows.
"""

import json
import os
import sys
from datetime import datetime
from pathlib import Path
from time import sleep
from typing import Any

import requests

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from cli.auth import get_bearer_token


class AdoptAPIClient:
    """Client for Adopt API operations."""

    def __init__(self, bearer_token: str | None = None) -> None:
        """
        Initialize API client.

        Args:
            bearer_token: Optional pre-fetched token. Will fetch if not provided.
        """
        self._bearer_token = bearer_token
        self.actions_endpoint = os.getenv("ADOPT_ACTIONS_ENDPOINT", "https://api.adopt.ai")
        self.api_endpoint = os.getenv("ADOPT_API_ENDPOINT", "https://connect.adopt.ai")
        # ADOPT_LAMBDA_ENDPOINT separates lambda CRUD from action endpoints.
        # Falls back to actions_endpoint when not set.
        self.lambda_endpoint = os.getenv("ADOPT_LAMBDA_ENDPOINT", self.actions_endpoint)

    @property
    def bearer_token(self) -> str:
        """Get bearer token, fetching if needed."""
        if self._bearer_token is None:
            self._bearer_token = get_bearer_token()
        return self._bearer_token

    @property
    def headers(self) -> dict[str, str]:
        """Get standard headers for API calls."""
        return {
            "Authorization": f"Bearer {self.bearer_token}",
            "Content-Type": "application/json",
        }

    # =========================================================================
    # Action CRUD Operations
    # =========================================================================

    def create_action(
        self,
        title: str,
        description: str,
        api_ids: list[str] | None = None,
    ) -> tuple[bool, dict[str, Any] | None, str]:
        """
        Create a new action.

        Returns:
            Tuple of (success, action_data, message)
        """
        # Validate/truncate title
        title = title.strip()[:64]

        url = f"{self.actions_endpoint}/v1/actions/"
        payload = {
            "title": title,
            "description": description.strip(),
            "api_ids": api_ids or [],
        }

        try:
            response = requests.post(url, headers=self.headers, json=payload, timeout=30)

            if response.status_code not in (200, 201):
                return False, None, f"Failed: {response.status_code} - {response.text}"

            return True, response.json(), "Action created successfully"

        except requests.exceptions.RequestException as e:
            return False, None, f"Network error: {e}"

    def get_action(self, action_id: str) -> tuple[bool, dict[str, Any] | None, str]:
        """
        Fetch action details.

        Returns:
            Tuple of (success, action_data, message)
        """
        url = f"{self.actions_endpoint}/v1/actions/{action_id}/current/"

        try:
            response = requests.get(url, headers=self.headers, timeout=30)

            if response.status_code != 200:
                return False, None, f"Failed: {response.status_code} - {response.text}"

            return True, response.json(), "Action fetched successfully"

        except requests.exceptions.RequestException as e:
            return False, None, f"Network error: {e}"

    def publish_wdl(
        self,
        action_id: str,
        wdl: list[dict[str, Any]],
        draft_id: str | None = None,
    ) -> tuple[bool, str]:
        """
        Publish WDL to an action.

        Returns:
            Tuple of (success, message)
        """
        url = f"{self.actions_endpoint}/v1/actions/wdl-update"
        payload = {"action_id": action_id, "draft_id": draft_id, "wdl": wdl}

        try:
            response = requests.post(url, headers=self.headers, json=payload, timeout=30)

            if response.status_code not in (200, 201):
                return False, f"Failed: {response.status_code} - {response.text}"

            return True, "WDL published successfully"

        except requests.exceptions.RequestException as e:
            return False, f"Network error: {e}"

    def update_title(
        self,
        action_id: str,
        new_title: str,
    ) -> tuple[bool, str]:
        """Update action title."""
        url = f"{self.actions_endpoint}/v1/actions/{action_id}/title"
        payload = {"new_title": new_title.strip()[:64]}

        try:
            response = requests.patch(url, headers=self.headers, json=payload, timeout=30)

            if response.status_code not in (200, 201, 204):
                return False, f"Failed: {response.status_code} - {response.text}"

            return True, "Title updated"

        except requests.exceptions.RequestException as e:
            return False, f"Network error: {e}"

    def update_description(
        self,
        action_id: str,
        new_description: str,
    ) -> tuple[bool, str]:
        """Update action description."""
        url = f"{self.actions_endpoint}/v1/actions/{action_id}/description"
        payload = {"new_description": new_description}

        try:
            response = requests.patch(url, headers=self.headers, json=payload, timeout=30)

            if response.status_code not in (200, 201, 204):
                return False, f"Failed: {response.status_code} - {response.text}"

            return True, "Description updated"

        except requests.exceptions.RequestException as e:
            return False, f"Network error: {e}"

    def update_statement(
        self,
        action_id: str,
        statement: str,
        draft_id: str = "",
    ) -> tuple[bool, str]:
        """Update action statement (selection criteria)."""
        url = f"{self.actions_endpoint}/v1/actions/statement"
        payload: dict[str, Any] = {
            "action_id": action_id,
            "statement": statement,
            "draft_id": draft_id,
        }

        try:
            response = requests.post(url, headers=self.headers, json=payload, timeout=30)

            if response.status_code not in (200, 201, 204):
                return False, f"Failed: {response.status_code} - {response.text}"

            return True, "Statement updated"

        except requests.exceptions.RequestException as e:
            return False, f"Network error: {e}"

    def get_deployment_rules(
        self,
        action_id: str,
    ) -> tuple[bool, dict[str, Any] | None, str]:
        """
        Get deployment rules for action including tool mode status.

        Returns:
            Tuple of (success, deployment_rules_data, message)
        """
        url = f"{self.actions_endpoint}/v1/actions/{action_id}/deployment-rules"

        try:
            response = requests.get(url, headers=self.headers, timeout=30)

            if response.status_code != 200:
                return False, None, f"Failed: {response.status_code} - {response.text}"

            return True, response.json(), "Deployment rules fetched"

        except requests.exceptions.RequestException as e:
            return False, None, f"Network error: {e}"

    def set_deployment_rules(
        self,
        action_id: str,
        is_tool_mode: bool = False,
        is_visible_in_list: bool = True,
        rules: list[dict[str, Any]] | None = None,
    ) -> tuple[bool, dict[str, Any] | None, str]:
        """
        Set deployment rules for action.

        Args:
            action_id: Action ID
            is_tool_mode: Enable Tool Mode (bypass orchestrator)
            is_visible_in_list: Show in action list
            rules: Optional targeting rules (user properties, instance attributes)

        Returns:
            Tuple of (success, deployment_rules_data, message)
        """
        url = f"{self.actions_endpoint}/v1/actions/{action_id}/deployment-rules"
        payload = {
            "rules": rules or [],
            "is_visible_in_list": is_visible_in_list,
            "is_tool_mode": is_tool_mode,
        }

        try:
            response = requests.post(url, headers=self.headers, json=payload, timeout=30)

            if response.status_code not in (200, 201):
                return False, None, f"Failed: {response.status_code} - {response.text}"

            return True, response.json(), "Deployment rules updated"

        except requests.exceptions.RequestException as e:
            return False, None, f"Network error: {e}"

    def populate_instructions(self, action_id: str) -> tuple[bool, str]:
        """Trigger instruction population for action."""
        url = f"{self.actions_endpoint}/v1/actions/{action_id}/populate-instructions"

        try:
            response = requests.post(url, headers=self.headers, json={}, timeout=30)

            if response.status_code not in (200, 201):
                return False, f"Failed: {response.status_code} - {response.text}"

            return True, "Instructions populated"

        except requests.exceptions.RequestException as e:
            return False, f"Network error: {e}"

    def save_draft(
        self,
        action_id: str,
        draft_id: str,
        change_reason: str | None = None,
    ) -> tuple[bool, str | None, str]:
        """
        Save draft to get version number.

        Args:
            action_id: Action ID
            draft_id: Draft ID
            change_reason: Optional description of changes (may not be supported by API)

        Returns:
            Tuple of (success, version_number, message)
        """
        url = f"{self.actions_endpoint}/v1/actions/{action_id}/draft/{draft_id}/save"

        # Try to send change_reason if provided (API may or may not support it)
        payload = {}
        if change_reason:
            payload["change_reason"] = change_reason

        try:
            response = requests.post(
                url, headers=self.headers, json=payload if payload else None, timeout=30
            )

            if response.status_code not in (200, 201):
                return False, None, f"Failed: {response.status_code} - {response.text}"

            data = response.json()
            version = data.get("version_number")

            # If version not in response, try to get it from list_versions
            if not version:
                # Query versions to find the latest one
                success, versions_list, msg = self.list_versions(action_id)
                if success and versions_list:
                    # Find the highest version number
                    latest = max(versions_list, key=lambda v: v.get("version_number", 0))
                    version = str(latest.get("version_number", ""))
                    if version:
                        return True, version, "Draft saved (version retrieved from list)"

            if version:
                return True, version, "Draft saved"
            else:
                return False, None, "Draft saved but no version number in response"

        except requests.exceptions.RequestException as e:
            return False, None, f"Network error: {e}"

    def approve_version(
        self,
        action_id: str,
        version_id: str,
        change_reason: str | None = None,
    ) -> tuple[bool, str]:
        """
        Approve a version of the action.

        Args:
            action_id: Action ID
            version_id: Version ID to approve
            change_reason: Optional description of changes (defaults to generic message)
        """
        url = f"{self.actions_endpoint}/v1/actions/{action_id}/version/{version_id}"
        payload = {"status": "approved", "change_reason": change_reason or "WDL action update"}

        try:
            response = requests.put(url, headers=self.headers, json=payload, timeout=30)

            if response.status_code not in (200, 201):
                return False, f"Failed: {response.status_code} - {response.text}"

            return True, "Version approved"

        except requests.exceptions.RequestException as e:
            return False, f"Network error: {e}"

    # =========================================================================
    # Test/Run Operations
    # =========================================================================

    def run_action(
        self,
        action_id: str,
        user_input: str,
        profile: dict[str, Any],
        workflow_params: dict[str, Any] | None = None,
        version_number: int | None = None,
        allow_draft: bool = False,
        trace_id: str | None = None,
    ) -> tuple[bool, dict[str, Any] | None, str]:
        """
        Execute an action for testing.

        Args:
            action_id: Action to run
            user_input: Natural language input
            profile: Adopt profile with base_url, security_params, etc.
                     Can optionally include profiles_map for per-API profiles.
            workflow_params: Optional workflow parameters
            version_number: Optional version number to test (if None, uses latest published)
            allow_draft: Whether to allow testing draft versions

        Returns:
            Tuple of (success, response_data, message)
        """
        url = f"{self.api_endpoint}/v1/actions/run?include_trace=true"

        # Format message in langchain HumanMessage format
        message = {
            "type": "human",
            "content": user_input,
        }

        combined_params = {**profile.get("workflow_params", {})}
        if workflow_params:
            combined_params.update(workflow_params)

        payload = {
            "messages": [message],
            "action_id": action_id,
            "execution_type": "TOOL",
            "base_url": profile.get("base_url", ""),
            "application_base_url": profile.get("application_base_url", ""),
            "workflow_params": combined_params,
            "security_params": profile.get("security_params", {}),
        }

        # Add profiles_map if present in profile (for per-API/application profiles)
        # This allows different base_url and security_params for different APIs
        # Note: We map security_params -> security_headers for ProjectA3 compatibility
        profiles_map = profile.get("profiles_map")
        if profiles_map:
            # Convert security_params to security_headers in each profile entry
            # This allows users to use consistent naming (security_params) in adopt_profile.json
            converted_profiles_map = {}
            for key, entry in profiles_map.items():
                converted_entry = entry.copy() if isinstance(entry, dict) else entry
                if isinstance(converted_entry, dict) and "security_params" in converted_entry:
                    converted_entry["security_headers"] = converted_entry.pop("security_params")
                converted_profiles_map[key] = converted_entry
            payload["profiles_map"] = converted_profiles_map

        # Add mcp_profiles_map if present (for MCP integration profiles)
        # Same conversion: security_params -> security_headers
        mcp_profiles_map = profile.get("mcp_profiles_map")
        if mcp_profiles_map:
            converted_mcp_profiles_map = {}
            for key, entry in mcp_profiles_map.items():
                converted_entry = entry.copy() if isinstance(entry, dict) else entry
                if isinstance(converted_entry, dict) and "security_params" in converted_entry:
                    converted_entry["security_headers"] = converted_entry.pop("security_params")
                converted_mcp_profiles_map[key] = converted_entry
            payload["mcp_profiles_map"] = converted_mcp_profiles_map

        # Add version parameters if provided
        if version_number is not None:
            payload["version_number"] = version_number
        if allow_draft:
            payload["allow_draft"] = allow_draft
        if trace_id:
            payload["trace_id"] = trace_id

        try:
            response = requests.post(url, headers=self.headers, json=payload, timeout=120)

            if response.status_code != 200:
                # Try to parse the error response JSON to extract execution trace
                try:
                    error_data = response.json()
                    return (
                        False,
                        error_data,
                        f"Failed: {response.status_code} - {response.text}",
                    )
                except (ValueError, json.JSONDecodeError):
                    return (
                        False,
                        None,
                        f"Failed: {response.status_code} - {response.text}",
                    )

            data = response.json()

            if not data.get("status"):
                return False, data, "Action returned unsuccessful status"

            return True, data, "Action executed successfully"

        except requests.exceptions.RequestException as e:
            return False, None, f"Network error: {e}"

    def wait_for_update(
        self,
        action_id: str,
        previous_updated_at: str,
        max_retries: int = 20,
        poll_interval: int = 5,
    ) -> tuple[bool, dict[str, Any] | None, str]:
        """
        Poll until action's updated_at changes.

        Args:
            action_id: Action to poll
            previous_updated_at: Previous timestamp to compare against
            max_retries: Maximum poll attempts
            poll_interval: Seconds between polls

        Returns:
            Tuple of (success, action_data, message)
        """
        try:
            previous_ts = datetime.fromisoformat(previous_updated_at)
        except ValueError:
            return False, None, f"Invalid timestamp format: {previous_updated_at}"

        for _ in range(max_retries):
            success, data, msg = self.get_action(action_id)

            if not success:
                return False, None, msg

            current_updated_at = data.get("updated_at") if data else None
            if current_updated_at:
                try:
                    current_ts = datetime.fromisoformat(current_updated_at)
                    if current_ts > previous_ts:
                        return True, data, "Update detected"
                except ValueError:
                    pass

            sleep(poll_interval)

        return False, None, "Timeout waiting for update"

    # =========================================================================
    # Version Management Operations
    # =========================================================================

    def list_versions(
        self,
        action_id: str,
    ) -> tuple[bool, list[dict[str, Any]] | None, str]:
        """
        List all versions of an action.

        Returns:
            Tuple of (success, versions_list, message)
        """
        url = f"{self.actions_endpoint}/v1/actions/{action_id}/versions/"

        try:
            response = requests.get(url, headers=self.headers, timeout=30)

            if response.status_code != 200:
                return False, None, f"Failed: {response.status_code} - {response.text}"

            data = response.json()
            # Handle both list response and dict with "versions" key
            if isinstance(data, list):
                versions = data
            elif isinstance(data, dict):
                versions = data.get("versions", [])
            else:
                versions = []

            return True, versions, f"Found {len(versions)} versions"

        except requests.exceptions.RequestException as e:
            return False, None, f"Network error: {e}"

    def get_current_version(
        self,
        action_id: str,
    ) -> tuple[bool, dict[str, Any] | None, str]:
        """
        Get the current version's details including WDL.

        Returns:
            Tuple of (success, version_data, message)
        """
        url = f"{self.actions_endpoint}/v1/actions/{action_id}/current/"

        try:
            response = requests.get(url, headers=self.headers, timeout=30)

            if response.status_code != 200:
                return False, None, f"Failed: {response.status_code} - {response.text}"

            return True, response.json(), "Current version fetched successfully"

        except requests.exceptions.RequestException as e:
            return False, None, f"Network error: {e}"

    def checkout_version(
        self,
        action_id: str,
        version_id: str,
        workspace: Path,
    ) -> tuple[bool, str]:
        """
        Checkout a specific version to local workspace.

        Downloads the version's WDL and saves it locally.
        Only works for the current version (uses /current/ endpoint).

        Returns:
            Tuple of (success, message)
        """
        # Check if this version is current
        success, versions, msg = self.list_versions(action_id)
        if not success:
            return False, f"Failed to list versions: {msg}"

        version_number = int(version_id) if isinstance(version_id, str) else version_id
        target_version = None
        if versions:
            for v in versions:
                if v.get("version_number") == version_number:
                    target_version = v
                    break

        if not target_version:
            return False, f"Version {version_id} not found"

        if not target_version.get("is_current_version", False):
            current_version = next(
                (v.get("version_number") for v in (versions or []) if v.get("is_current_version")),
                None,
            )
            return (
                False,
                f"Version {version_id} is not current. Current version is {current_version}. Only current version can be checked out.",
            )

        # Get current version (includes WDL)
        success, version_data, msg = self.get_current_version(action_id)
        if not success or not version_data:
            return False, msg

        # Extract WDL from version
        wdl = version_data.get("wdl", version_data.get("widdle", []))

        # Save to workspace
        wdl_path = workspace / "widdle.json"
        wdl_path.write_text(json.dumps(wdl, indent=2))

        # Track which version is checked out
        version_file = workspace / "current_version.txt"
        version_file.write_text(f"version_id: {version_id}\n")

        # Save a copy in versions folder
        versions_dir = workspace / "versions"
        versions_dir.mkdir(exist_ok=True)
        version_copy = versions_dir / f"v{version_id}_widdle.json"
        version_copy.write_text(json.dumps(wdl, indent=2))

        return True, f"Checked out version {version_id}"

    # =========================================================================
    # WDL Validation Operations
    # =========================================================================

    def validate_wdl(
        self,
        wdl: list[dict[str, Any]],
    ) -> tuple[bool, dict[str, Any] | None, str]:
        """
        Validate WDL via the external-apis compiler endpoint.

        Args:
            wdl: The WDL workflow blocks to validate.

        Returns:
            Tuple of (success, response_json, message)
        """
        url = f"{self.api_endpoint}/v1/wdl/validate"
        payload = {"wdl": wdl}

        try:
            response = requests.post(url, headers=self.headers, json=payload, timeout=60)

            if response.status_code != 200:
                return (
                    False,
                    None,
                    f"Validation API failed: {response.status_code} - {response.text}",
                )

            return True, response.json(), "ok"

        except requests.exceptions.RequestException as e:
            return False, None, f"Network error calling validation API: {e}"

    def run_wdl_directly(
        self,
        wdl: list[dict[str, Any]],
        user_message: str,
        profile: dict[str, Any],
        title: str = "direct_wdl_execution",
        workflow_params: dict[str, Any] | None = None,
        inline_actions: dict[str, Any] | None = None,
        trace_id: str | None = None,
    ) -> tuple[bool, dict[str, Any] | None, str]:
        """
        Execute WDL payload directly via /run-wdl without saving to platform.

        Bypasses the need to create a remote action, publish WDL, and save
        as draft before testing. The WDL is sent directly for execution.

        Args:
            wdl: The WDL workflow steps (list of operation dicts).
            user_message: Natural language input / prompt for the execution.
            profile: Adopt profile with base_url, security_params, etc.
                     Can include profiles_map for per-API profiles.
            title: Display title for the execution (default: "direct_wdl_execution").
            workflow_params: Optional workflow parameters.
            inline_actions: Optional map of placeholder action IDs to inline WDL
                           definitions for uber agent testing without platform dependency.
            trace_id: Optional trace ID for multi-turn conversation tracking.
                     When provided, the server maintains conversation state across
                     calls sharing the same trace_id.

        Returns:
            Tuple of (success, response_data, message)
        """
        url = f"{self.api_endpoint}/v1/actions/run-wdl?include_trace=true"

        combined_params = {**profile.get("workflow_params", {})}
        if workflow_params:
            combined_params.update(workflow_params)

        payload: dict[str, Any] = {
            "wdl": wdl,
            "title": title,
            "user_message": user_message,
            "base_url": profile.get("base_url", ""),
            "application_base_url": profile.get("application_base_url", ""),
            "workflow_params": combined_params,
            "security_params": profile.get("security_params", {}),
            "include_execution_trace": True,
        }

        if trace_id:
            payload["trace_id"] = trace_id

        profiles_map = profile.get("profiles_map")
        if profiles_map:
            converted_profiles_map = {}
            for key, entry in profiles_map.items():
                converted_entry = entry.copy() if isinstance(entry, dict) else entry
                if isinstance(converted_entry, dict) and "security_params" in converted_entry:
                    converted_entry["security_headers"] = converted_entry.pop("security_params")
                converted_profiles_map[key] = converted_entry
            payload["profiles_map"] = converted_profiles_map

        mcp_profiles_map = profile.get("mcp_profiles_map")
        if mcp_profiles_map:
            converted_mcp_profiles_map = {}
            for key, entry in mcp_profiles_map.items():
                converted_entry = entry.copy() if isinstance(entry, dict) else entry
                if isinstance(converted_entry, dict) and "security_params" in converted_entry:
                    converted_entry["security_headers"] = converted_entry.pop("security_params")
                converted_mcp_profiles_map[key] = converted_entry
            payload["mcp_profiles_map"] = converted_mcp_profiles_map

        if inline_actions:
            payload["inline_actions"] = inline_actions

        try:
            response = requests.post(url, headers=self.headers, json=payload, timeout=120)

            if response.status_code != 200:
                try:
                    error_data = response.json()
                    return (
                        False,
                        error_data,
                        f"Failed: {response.status_code} - {response.text}",
                    )
                except (ValueError, json.JSONDecodeError):
                    return (
                        False,
                        None,
                        f"Failed: {response.status_code} - {response.text}",
                    )

            data = response.json()

            if not data.get("status"):
                return False, data, "WDL execution returned unsuccessful status"

            return True, data, "WDL executed successfully"

        except requests.exceptions.RequestException as e:
            return False, None, f"Network error: {e}"

    # =========================================================================
    # API Management Operations (for Diagnostics)
    # =========================================================================

    def list_apis(
        self,
        page: int = 1,
        page_size: int = 50,
    ) -> tuple[bool, list[dict[str, Any]] | None, str]:
        """
        List all APIs.

        Returns:
            Tuple of (success, apis_list, message)
        """
        url = f"{self.api_endpoint}/v1/tools/apis"

        try:
            response = requests.get(
                url,
                headers=self.headers,
                params={"page": page, "page_size": page_size},
                timeout=30,
            )

            if response.status_code != 200:
                return False, None, f"Failed: {response.status_code} - {response.text}"

            data = response.json()
            # Handle various response formats
            if isinstance(data, list):
                apis = data
            elif isinstance(data, dict):
                apis = data.get("apis") or data.get("data") or data.get("items") or []
            else:
                apis = []

            return True, apis, f"Found {len(apis)} APIs"

        except requests.exceptions.RequestException as e:
            return False, None, f"Network error: {e}"

    def get_api(self, api_id: str) -> tuple[bool, dict[str, Any] | None, str]:
        """
        Get API details.

        Returns:
            Tuple of (success, api_data, message)
        """
        url = f"{self.api_endpoint}/v1/tools/apis/{api_id}"

        try:
            response = requests.get(url, headers=self.headers, timeout=30)

            if response.status_code != 200:
                return False, None, f"Failed: {response.status_code} - {response.text}"

            return True, response.json(), "API fetched successfully"

        except requests.exceptions.RequestException as e:
            return False, None, f"Network error: {e}"

    def update_api_path(
        self,
        api_id: str,
        new_path: str,
    ) -> tuple[bool, str]:
        """
        Update API canonical path.

        Args:
            api_id: API ID
            new_path: New canonical path

        Returns:
            Tuple of (success, message)
        """
        url = f"{self.api_endpoint}/v1/tools/apis/{api_id}"
        payload = {"path": new_path}

        try:
            response = requests.patch(url, headers=self.headers, json=payload, timeout=30)

            if response.status_code not in (200, 201, 204):
                return False, f"Failed: {response.status_code} - {response.text}"

            return True, "API path updated successfully"

        except requests.exceptions.RequestException as e:
            return False, f"Network error: {e}"

    # =========================================================================
    # Playground Profile Operations
    # =========================================================================

    def list_playground_profiles(
        self,
        integration_id: str | None = None,
    ) -> tuple[bool, list[dict[str, Any]] | None, str]:
        """
        List playground profiles for the organization.

        Args:
            integration_id: Optional filter by integration ID

        Returns:
            Tuple of (success, profiles_list, message)
        """
        url = f"{self.actions_endpoint}/v1/settings/playground-profiles/"
        params: dict[str, Any] = {}
        if integration_id:
            params["integration_id"] = integration_id

        try:
            response = requests.get(url, headers=self.headers, params=params, timeout=30)

            if response.status_code != 200:
                return False, None, f"Failed: {response.status_code} - {response.text}"

            data = response.json()
            if isinstance(data, dict):
                profiles = data.get("profiles", [])
            elif isinstance(data, list):
                profiles = data
            else:
                profiles = []

            return True, profiles, f"Found {len(profiles)} profiles"

        except requests.exceptions.RequestException as e:
            return False, None, f"Network error: {e}"

    def get_playground_profile(
        self,
        profile_id: str,
    ) -> tuple[bool, dict[str, Any] | None, str]:
        """
        Get a specific playground profile by ID.

        Returns:
            Tuple of (success, profile_data, message)
        """
        url = f"{self.actions_endpoint}/v1/settings/playground-profiles/{profile_id}"

        try:
            response = requests.get(url, headers=self.headers, timeout=30)

            if response.status_code == 404:
                return False, None, f"Profile not found: {profile_id}"

            if response.status_code != 200:
                return False, None, f"Failed: {response.status_code} - {response.text}"

            return True, response.json(), "Profile fetched successfully"

        except requests.exceptions.RequestException as e:
            return False, None, f"Network error: {e}"

    def get_default_playground_profile(
        self,
    ) -> tuple[bool, dict[str, Any] | None, str]:
        """
        Get the default playground profile for the organization.

        Returns:
            Tuple of (success, profile_data, message)
        """
        url = f"{self.actions_endpoint}/v1/settings/playground-profiles/default/"

        try:
            response = requests.get(url, headers=self.headers, timeout=30)

            if response.status_code == 404:
                return False, None, "No default profile found"

            if response.status_code != 200:
                return False, None, f"Failed: {response.status_code} - {response.text}"

            return True, response.json(), "Default profile fetched successfully"

        except requests.exceptions.RequestException as e:
            return False, None, f"Network error: {e}"

    def create_playground_profile(
        self,
        profile_data: dict[str, Any],
    ) -> tuple[bool, dict[str, Any] | None, str]:
        """
        Create a new playground profile.

        Args:
            profile_data: Profile fields (profile_name, app_base_url, api_base_url, etc.)

        Returns:
            Tuple of (success, created_profile, message)
        """
        url = f"{self.actions_endpoint}/v1/settings/playground-profiles/"

        try:
            response = requests.post(url, headers=self.headers, json=profile_data, timeout=30)

            if response.status_code not in (200, 201):
                return False, None, f"Failed: {response.status_code} - {response.text}"

            return True, response.json(), "Profile created successfully"

        except requests.exceptions.RequestException as e:
            return False, None, f"Network error: {e}"

    def update_playground_profile(
        self,
        profile_id: str,
        profile_data: dict[str, Any],
    ) -> tuple[bool, dict[str, Any] | None, str]:
        """
        Update an existing playground profile.

        Args:
            profile_id: Profile ID to update
            profile_data: Fields to update

        Returns:
            Tuple of (success, updated_profile, message)
        """
        url = f"{self.actions_endpoint}/v1/settings/playground-profiles/update/{profile_id}"

        try:
            response = requests.post(url, headers=self.headers, json=profile_data, timeout=30)

            if response.status_code == 404:
                return False, None, f"Profile not found: {profile_id}"

            if response.status_code != 200:
                return False, None, f"Failed: {response.status_code} - {response.text}"

            return True, response.json(), "Profile updated successfully"

        except requests.exceptions.RequestException as e:
            return False, None, f"Network error: {e}"

    def delete_playground_profile(
        self,
        profile_id: str,
    ) -> tuple[bool, str]:
        """
        Soft delete a playground profile.

        Returns:
            Tuple of (success, message)
        """
        url = f"{self.actions_endpoint}/v1/settings/playground-profiles/delete/{profile_id}"

        try:
            response = requests.post(url, headers=self.headers, json={}, timeout=30)

            if response.status_code == 404:
                return False, f"Profile not found: {profile_id}"

            if response.status_code != 200:
                return False, f"Failed: {response.status_code} - {response.text}"

            return True, "Profile deleted successfully"

        except requests.exceptions.RequestException as e:
            return False, f"Network error: {e}"

    # =========================================================================
    # Lambda API Methods
    # =========================================================================

    def create_lambda(
        self,
        name: str,
        description: str = "",
        language: str = "python",
        entry_point: str = "script.py",
        resource_permissions: list[str] | None = None,
        timeout_seconds: int = 300,
        runtime_image: str | None = None,
        cpu_limit: str | None = None,
        memory_limit: str | None = None,
    ) -> tuple[bool, dict[str, Any] | None, str]:
        """Create a new lambda."""
        url = f"{self.lambda_endpoint}/v1/lambdas/"
        payload: dict[str, Any] = {
            "name": name,
            "description": description,
            "language": language,
            "entry_point": entry_point,
            "resource_permissions": resource_permissions or None,
            "timeout_seconds": timeout_seconds,
            "runtime_image": runtime_image or "adopt-lambda-runtime:latest",
            "cpu_limit": cpu_limit or "500m",
            "memory_limit": memory_limit or "512Mi",
        }

        try:
            response = requests.post(url, headers=self.headers, json=payload, timeout=30)

            if response.status_code not in (200, 201):
                return False, None, f"Failed: {response.status_code} - {response.text}"

            return True, response.json(), "Lambda created successfully"

        except requests.exceptions.RequestException as e:
            return False, None, f"Network error: {e}"

    def get_lambda(self, lambda_id: str) -> tuple[bool, dict[str, Any] | None, str]:
        """Get lambda details."""
        url = f"{self.lambda_endpoint}/v1/lambdas/{lambda_id}"

        try:
            response = requests.get(url, headers=self.headers, timeout=30)

            if response.status_code != 200:
                return False, None, f"Failed: {response.status_code} - {response.text}"

            return True, response.json(), "Lambda fetched successfully"

        except requests.exceptions.RequestException as e:
            return False, None, f"Network error: {e}"

    def list_lambdas(
        self,
        page: int = 1,
        page_size: int = 20,
        search: str = "",
        language: str = "",
        status: str = "",
    ) -> tuple[bool, list[dict[str, Any]] | None, str]:
        """List lambdas with filtering."""
        url = f"{self.lambda_endpoint}/v1/lambdas/"
        params: dict[str, Any] = {"page": page, "page_size": page_size}
        if search:
            params["search"] = search
        if language:
            params["language"] = language
        if status:
            params["status"] = status

        try:
            response = requests.get(url, headers=self.headers, params=params, timeout=30)

            if response.status_code != 200:
                return False, None, f"Failed: {response.status_code} - {response.text}"

            data = response.json()
            if isinstance(data, list):
                lambdas = data
            elif isinstance(data, dict):
                lambdas = data.get("lambdas") or data.get("data") or data.get("items") or []
            else:
                lambdas = []

            return True, lambdas, f"Found {len(lambdas)} lambdas"

        except requests.exceptions.RequestException as e:
            return False, None, f"Network error: {e}"

    def update_lambda(
        self,
        lambda_id: str,
        **kwargs: Any,
    ) -> tuple[bool, dict[str, Any] | None, str]:
        """Update lambda metadata."""
        url = f"{self.lambda_endpoint}/v1/lambdas/{lambda_id}"

        try:
            response = requests.patch(url, headers=self.headers, json=kwargs, timeout=30)

            if response.status_code not in (200, 201, 204):
                return False, None, f"Failed: {response.status_code} - {response.text}"

            return (
                True,
                response.json() if response.content else None,
                "Lambda updated successfully",
            )

        except requests.exceptions.RequestException as e:
            return False, None, f"Network error: {e}"

    def delete_lambda(self, lambda_id: str) -> tuple[bool, str]:
        """Delete a lambda."""
        url = f"{self.lambda_endpoint}/v1/lambdas/{lambda_id}"

        try:
            response = requests.delete(url, headers=self.headers, timeout=30)

            if response.status_code not in (200, 204):
                return False, f"Failed: {response.status_code} - {response.text}"

            return True, "Lambda deleted successfully"

        except requests.exceptions.RequestException as e:
            return False, f"Network error: {e}"

    def save_lambda_files(self, lambda_id: str, files: list[dict]) -> tuple[bool, dict | None, str]:
        """Save files to lambda (PUT to S3 via backend)."""
        url = f"{self.lambda_endpoint}/v1/lambdas/{lambda_id}/files"
        try:
            response = requests.put(url, headers=self.headers, json={"files": files}, timeout=30)
            if response.status_code != 200:
                return False, None, f"Failed: {response.status_code} - {response.text}"
            return True, response.json(), "Files saved"
        except Exception as e:
            return False, None, str(e)

    def get_lambda_file(self, lambda_id: str, path: str) -> tuple[bool, dict | None, str]:
        """Get file content from lambda."""
        url = f"{self.lambda_endpoint}/v1/lambdas/{lambda_id}/files/{path}"
        try:
            response = requests.get(url, headers=self.headers, timeout=30)
            if response.status_code != 200:
                return False, None, f"Failed: {response.status_code}"
            return True, response.json(), "OK"
        except Exception as e:
            return False, None, str(e)

    def list_lambda_files(self, lambda_id: str) -> tuple[bool, list | None, str]:
        """List files in lambda."""
        url = f"{self.lambda_endpoint}/v1/lambdas/{lambda_id}/files"
        try:
            response = requests.get(url, headers=self.headers, timeout=30)
            if response.status_code != 200:
                return False, None, f"Failed: {response.status_code}"
            return True, response.json(), "OK"
        except Exception as e:
            return False, None, str(e)

    def delete_lambda_file(self, lambda_id: str, file_path: str) -> tuple:
        """Delete a single file from a lambda."""
        try:
            from urllib.parse import quote as _url_quote

            encoded_path = _url_quote(file_path, safe="")
            url = f"{self.lambda_endpoint}/v1/lambdas/{lambda_id}/files/{encoded_path}"
            resp = requests.delete(url, headers=self.headers, timeout=30)
            if resp.status_code in (200, 204):
                return True, None, None
            return False, None, f"Delete failed: {resp.status_code} {resp.text}"
        except requests.exceptions.RequestException as e:
            return False, None, str(e)

    def test_lambda(
        self,
        lambda_id: str,
        input_data: dict[str, Any] | None = None,
        version_number: int | None = None,
    ) -> tuple[bool, dict[str, Any] | None, str]:
        """Test a lambda execution."""
        url = f"{self.lambda_endpoint}/v1/lambdas/{lambda_id}/test"
        payload: dict[str, Any] = {"input": input_data or {}}
        if version_number is not None:
            payload["version_number"] = version_number

        try:
            response = requests.post(url, headers=self.headers, json=payload, timeout=120)

            if response.status_code != 200:
                try:
                    error_data = response.json()
                    return False, error_data, f"Failed: {response.status_code} - {response.text}"
                except (ValueError, json.JSONDecodeError):
                    return False, None, f"Failed: {response.status_code} - {response.text}"

            return True, response.json(), "Lambda test executed successfully"

        except requests.exceptions.RequestException as e:
            return False, None, f"Network error: {e}"

    def list_lambda_executions(
        self,
        lambda_id: str,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[bool, list[dict[str, Any]] | None, str]:
        """List execution history for a lambda."""
        url = f"{self.lambda_endpoint}/v1/lambdas/{lambda_id}/executions"
        params: dict[str, Any] = {"page": page, "page_size": page_size}

        try:
            response = requests.get(url, headers=self.headers, params=params, timeout=30)

            if response.status_code != 200:
                return False, None, f"Failed: {response.status_code} - {response.text}"

            data = response.json()
            if isinstance(data, list):
                executions = data
            elif isinstance(data, dict):
                executions = data.get("executions") or data.get("data") or data.get("items") or []
            else:
                executions = []

            return True, executions, f"Found {len(executions)} executions"

        except requests.exceptions.RequestException as e:
            return False, None, f"Network error: {e}"

    def get_lambda_execution_log(
        self,
        lambda_id: str,
        execution_id: str,
    ) -> tuple[bool, dict[str, Any] | None, str]:
        """Get full execution log."""
        url = f"{self.lambda_endpoint}/v1/lambdas/{lambda_id}/executions/{execution_id}/logs"

        try:
            response = requests.get(url, headers=self.headers, timeout=30)

            if response.status_code != 200:
                return False, None, f"Failed: {response.status_code} - {response.text}"

            return True, response.json(), "Execution log fetched successfully"

        except requests.exceptions.RequestException as e:
            return False, None, f"Network error: {e}"

    # =========================================================================
    # Token Config Operations
    # =========================================================================

    def list_token_configs(
        self,
        page: int = 1,
        page_size: int = 50,
        search: str | None = None,
        is_published: bool | None = None,
        integration_id: str | None = None,
        sort_by: str | None = None,
        sort_order: str | None = None,
    ) -> tuple[bool, dict[str, Any] | None, str]:
        """
        List token configurations with pagination and filtering.

        Returns:
            Tuple of (success, paginated_response, message)
            paginated_response has 'items' and 'total' keys
        """
        url = f"{self.actions_endpoint}/v1/token-configs"
        params: dict[str, Any] = {"page": page, "page_size": page_size}
        if search:
            params["search"] = search
        if is_published is not None:
            params["is_published"] = is_published
        if integration_id:
            params["integration_id"] = integration_id
        if sort_by:
            params["sort_by"] = sort_by
        if sort_order:
            params["sort_order"] = sort_order

        try:
            response = requests.get(url, headers=self.headers, params=params, timeout=30)

            if response.status_code != 200:
                return False, None, f"Failed: {response.status_code} - {response.text}"

            return True, response.json(), "Token configs fetched successfully"

        except requests.exceptions.RequestException as e:
            return False, None, f"Network error: {e}"

    def get_token_config(
        self,
        token_id: str,
    ) -> tuple[bool, dict[str, Any] | None, str]:
        """
        Get a specific token configuration by ID.

        Returns:
            Tuple of (success, token_data, message)
        """
        url = f"{self.actions_endpoint}/v1/token-configs/{token_id}"

        try:
            response = requests.get(url, headers=self.headers, timeout=30)

            if response.status_code == 404:
                return False, None, f"Token config not found: {token_id}"

            if response.status_code != 200:
                return False, None, f"Failed: {response.status_code} - {response.text}"

            return True, response.json(), "Token config fetched successfully"

        except requests.exceptions.RequestException as e:
            return False, None, f"Network error: {e}"

    def create_token_config(
        self,
        token_data: dict[str, Any],
    ) -> tuple[bool, dict[str, Any] | None, str]:
        """
        Create a new token configuration.

        Args:
            token_data: Token config fields (name, domain_suffix, storage_type, etc.)

        Returns:
            Tuple of (success, created_token, message)
        """
        url = f"{self.actions_endpoint}/v1/token-configs"

        try:
            response = requests.post(url, headers=self.headers, json=token_data, timeout=30)

            if response.status_code not in (200, 201):
                return False, None, f"Failed: {response.status_code} - {response.text}"

            return True, response.json(), "Token config created successfully"

        except requests.exceptions.RequestException as e:
            return False, None, f"Network error: {e}"

    def update_token_config(
        self,
        token_id: str,
        token_data: dict[str, Any],
    ) -> tuple[bool, dict[str, Any] | None, str]:
        """
        Update an existing token configuration.

        Args:
            token_id: Token config ID to update
            token_data: Fields to update

        Returns:
            Tuple of (success, updated_token, message)
        """
        url = f"{self.actions_endpoint}/v1/token-configs/{token_id}"

        try:
            response = requests.post(url, headers=self.headers, json=token_data, timeout=30)

            if response.status_code == 404:
                return False, None, f"Token config not found: {token_id}"

            if response.status_code != 200:
                return False, None, f"Failed: {response.status_code} - {response.text}"

            return True, response.json(), "Token config updated successfully"

        except requests.exceptions.RequestException as e:
            return False, None, f"Network error: {e}"

    def delete_token_config(
        self,
        token_id: str,
    ) -> tuple[bool, str]:
        """
        Delete a token configuration.

        Returns:
            Tuple of (success, message)
        """
        url = f"{self.actions_endpoint}/v1/token-configs/{token_id}/delete"

        try:
            response = requests.post(url, headers=self.headers, json={}, timeout=30)

            if response.status_code == 404:
                return False, f"Token config not found: {token_id}"

            if response.status_code != 200:
                return False, f"Failed: {response.status_code} - {response.text}"

            return True, "Token config deleted successfully"

        except requests.exceptions.RequestException as e:
            return False, f"Network error: {e}"

    def batch_token_config_status(
        self,
        token_ids: list[str],
        is_published: bool,
    ) -> tuple[bool, str]:
        """
        Bulk update publish status for token configurations.

        Args:
            token_ids: List of token config IDs
            is_published: New publish status

        Returns:
            Tuple of (success, message)
        """
        url = f"{self.actions_endpoint}/v1/token-configs/batch/status"
        payload = {"token_ids": token_ids, "is_published": is_published}

        try:
            response = requests.post(url, headers=self.headers, json=payload, timeout=30)

            if response.status_code != 200:
                return False, f"Failed: {response.status_code} - {response.text}"

            data = response.json()
            count = data.get("updated_count", len(token_ids))
            status = "published" if is_published else "unpublished"
            return True, f"{count} token config(s) {status}"

        except requests.exceptions.RequestException as e:
            return False, f"Network error: {e}"

    # =========================================================================
    # Network Log Operations (for Diagnostics)
    # =========================================================================

    def fetch_network_logs(
        self,
        page: int = 1,
        page_size: int = 100,
        search: str | None = None,
    ) -> tuple[bool, list[dict[str, Any]] | None, str]:
        """
        Fetch HTTP network logs.

        Args:
            page: Page number
            page_size: Page size
            search: Optional URL search filter

        Returns:
            Tuple of (success, logs_list, message)
        """
        url = f"{self.api_endpoint}/v1/network-logs"
        params: dict[str, Any] = {"page": page, "page_size": page_size}
        if search:
            params["search"] = search

        try:
            response = requests.get(url, headers=self.headers, params=params, timeout=60)

            if response.status_code != 200:
                return False, None, f"Failed: {response.status_code} - {response.text}"

            data = response.json()
            # Handle various response formats
            if isinstance(data, list):
                logs = data
            elif isinstance(data, dict):
                logs = data.get("logs") or data.get("data") or data.get("items") or []
            else:
                logs = []

            return True, logs, f"Fetched {len(logs)} logs"

        except requests.exceptions.RequestException as e:
            return False, None, f"Network error: {e}"

    def fetch_all_network_logs(
        self,
        max_logs: int = 10000,
        search: str | None = None,
        page_size: int = 100,
    ) -> tuple[bool, list[dict[str, Any]], str]:
        """
        Fetch all network logs with pagination.

        Args:
            max_logs: Maximum logs to fetch
            search: Optional URL search filter
            page_size: Page size

        Returns:
            Tuple of (success, logs_list, message)
        """
        all_logs: list[Any] = []
        page = 1

        while len(all_logs) < max_logs:
            success, logs, msg = self.fetch_network_logs(
                page=page, page_size=page_size, search=search
            )

            if not success:
                if all_logs:
                    return True, all_logs, f"Partial fetch: {len(all_logs)} logs"
                return False, [], msg

            if not logs:
                break

            all_logs.extend(logs)

            if len(logs) < page_size:
                break

            page += 1

        return True, all_logs[:max_logs], f"Fetched {len(all_logs[:max_logs])} logs"

    # =========================================================================
    # Tool List Operations (for Diagnostics)
    # =========================================================================

    def list_tools(
        self,
        execution_type: str = "TOOL",
    ) -> tuple[bool, list[dict[str, Any]] | None, str]:
        """
        List all tools.

        Args:
            execution_type: Filter by execution type (TOOL, WORKFLOW, etc.)

        Returns:
            Tuple of (success, tools_list, message)
        """
        url = f"{self.actions_endpoint}/v1/actions/list"
        params = {"execution_type": execution_type}

        try:
            response = requests.get(url, headers=self.headers, params=params, timeout=30)

            if response.status_code != 200:
                return False, None, f"Failed: {response.status_code} - {response.text}"

            data = response.json()
            tools = data.get("capabilities") or data.get("data") or []

            return True, tools, f"Found {len(tools)} tools"

        except requests.exceptions.RequestException as e:
            return False, None, f"Network error: {e}"


def get_api_client_for_env(verbose: bool = False) -> AdoptAPIClient:
    """
    Get an API client with credentials from the active environment.

    This function:
    1. Uses the active environment
    2. Loads the environment's .env credentials
    3. Returns configured AdoptAPIClient

    Args:
        verbose: Print verbose info about credential loading.

    Returns:
        AdoptAPIClient configured with environment credentials.

    Raises:
        ValueError: If no active environment
    """
    from dotenv import load_dotenv

    from cli.wdl_common.workspace_manager import WORKSPACES_DIR, get_workspace_manager

    manager = get_workspace_manager()

    if not manager.active_env:
        raise ValueError(
            "No active environment. Set one with: python cli/workspace.py env use <env-id>"
        )

    env = manager.active_env
    env_path = WORKSPACES_DIR / env

    # Load environment-specific .env file
    env_dotenv = env_path / ".env"
    if env_dotenv.exists():
        if verbose:
            print(f"[VERBOSE] Loading credentials from: {env_dotenv}", file=sys.stderr)
        load_dotenv(env_dotenv, override=True)

        # Check if credentials are configured
        client_id = os.getenv("ADOPT_CLIENT_ID", "")
        client_secret = os.getenv("ADOPT_CLIENT_SECRET", "")

        if "your-" in client_id.lower() or not client_id:
            print(f"⚠️  Warning: ADOPT_CLIENT_ID not configured in: {env}", file=sys.stderr)
            print(f"   Edit: {env_dotenv}", file=sys.stderr)
        if "your-" in client_secret.lower() or not client_secret:
            print(f"⚠️  Warning: ADOPT_CLIENT_SECRET not configured in: {env}", file=sys.stderr)
            print(f"   Edit: {env_dotenv}", file=sys.stderr)
    else:
        print(f"⚠️  Warning: No .env file in environment: {env}", file=sys.stderr)
        print(f"   Expected: {env_dotenv}", file=sys.stderr)

    return AdoptAPIClient()
