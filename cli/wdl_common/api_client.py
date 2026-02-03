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
from typing import Any, Dict, List, Optional, Tuple

import requests

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from cli.auth import get_bearer_token


class AdoptAPIClient:
    """Client for Adopt API operations."""

    def __init__(self, bearer_token: Optional[str] = None) -> None:
        """
        Initialize API client.

        Args:
            bearer_token: Optional pre-fetched token. Will fetch if not provided.
        """
        self._bearer_token = bearer_token
        self.actions_endpoint = os.getenv(
            "ADOPT_ACTIONS_ENDPOINT", "https://api.adopt.ai"
        )
        self.api_endpoint = os.getenv("ADOPT_API_ENDPOINT", "https://connect.adopt.ai")

    @property
    def bearer_token(self) -> str:
        """Get bearer token, fetching if needed."""
        if self._bearer_token is None:
            self._bearer_token = get_bearer_token()
        return self._bearer_token

    @property
    def headers(self) -> Dict[str, str]:
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
        api_ids: Optional[List[str]] = None,
    ) -> Tuple[bool, Optional[Dict[str, Any]], str]:
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
            response = requests.post(
                url, headers=self.headers, json=payload, timeout=30
            )

            if response.status_code not in (200, 201):
                return False, None, f"Failed: {response.status_code} - {response.text}"

            return True, response.json(), "Action created successfully"

        except requests.exceptions.RequestException as e:
            return False, None, f"Network error: {e}"

    def get_action(self, action_id: str) -> Tuple[bool, Optional[Dict[str, Any]], str]:
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
        wdl: List[Dict[str, Any]],
        draft_id: Optional[str] = None,
    ) -> Tuple[bool, str]:
        """
        Publish WDL to an action.

        Returns:
            Tuple of (success, message)
        """
        url = f"{self.actions_endpoint}/v1/actions/wdl-update"
        payload = {"action_id": action_id, "draft_id": draft_id, "wdl": wdl}

        try:
            response = requests.post(
                url, headers=self.headers, json=payload, timeout=30
            )

            if response.status_code not in (200, 201):
                return False, f"Failed: {response.status_code} - {response.text}"

            return True, "WDL published successfully"

        except requests.exceptions.RequestException as e:
            return False, f"Network error: {e}"

    def update_description(
        self,
        action_id: str,
        new_description: str,
    ) -> Tuple[bool, str]:
        """Update action description."""
        url = f"{self.actions_endpoint}/v1/actions/{action_id}/description"
        payload = {"new_description": new_description}

        try:
            response = requests.patch(
                url, headers=self.headers, json=payload, timeout=30
            )

            if response.status_code not in (200, 201, 204):
                return False, f"Failed: {response.status_code} - {response.text}"

            return True, "Description updated"

        except requests.exceptions.RequestException as e:
            return False, f"Network error: {e}"

    def get_deployment_rules(
        self,
        action_id: str,
    ) -> Tuple[bool, Optional[Dict[str, Any]], str]:
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
        rules: Optional[List[Dict[str, Any]]] = None,
    ) -> Tuple[bool, Optional[Dict[str, Any]], str]:
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
            response = requests.post(
                url, headers=self.headers, json=payload, timeout=30
            )

            if response.status_code not in (200, 201):
                return False, None, f"Failed: {response.status_code} - {response.text}"

            return True, response.json(), "Deployment rules updated"

        except requests.exceptions.RequestException as e:
            return False, None, f"Network error: {e}"

    def populate_instructions(self, action_id: str) -> Tuple[bool, str]:
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
        change_reason: Optional[str] = None,
    ) -> Tuple[bool, Optional[str], str]:
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
                url, 
                headers=self.headers, 
                json=payload if payload else None,
                timeout=30
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
        change_reason: Optional[str] = None,
    ) -> Tuple[bool, str]:
        """
        Approve a version of the action.
        
        Args:
            action_id: Action ID
            version_id: Version ID to approve
            change_reason: Optional description of changes (defaults to generic message)
        """
        url = f"{self.actions_endpoint}/v1/actions/{action_id}/version/{version_id}"
        payload = {
            "status": "approved",
            "change_reason": change_reason or "WDL action update"
        }

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
        profile: Dict[str, Any],
        workflow_params: Optional[Dict[str, Any]] = None,
        version_number: Optional[int] = None,
        allow_draft: bool = False,
    ) -> Tuple[bool, Optional[Dict[str, Any]], str]:
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

        try:
            response = requests.post(
                url, headers=self.headers, json=payload, timeout=120
            )

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
    ) -> Tuple[bool, Optional[Dict[str, Any]], str]:
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
    ) -> Tuple[bool, Optional[List[Dict[str, Any]]], str]:
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
    ) -> Tuple[bool, Optional[Dict[str, Any]], str]:
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
    ) -> Tuple[bool, str]:
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
        for v in versions:
            if v.get("version_number") == version_number:
                target_version = v
                break
        
        if not target_version:
            return False, f"Version {version_id} not found"
        
        if not target_version.get("is_current_version", False):
            current_version = next((v.get("version_number") for v in versions if v.get("is_current_version")), None)
            return False, f"Version {version_id} is not current. Current version is {current_version}. Only current version can be checked out."
        
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
    # API Management Operations (for Diagnostics)
    # =========================================================================

    def list_apis(
        self,
        page: int = 1,
        page_size: int = 50,
    ) -> Tuple[bool, Optional[List[Dict[str, Any]]], str]:
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

    def get_api(self, api_id: str) -> Tuple[bool, Optional[Dict[str, Any]], str]:
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
    ) -> Tuple[bool, str]:
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
            response = requests.patch(
                url, headers=self.headers, json=payload, timeout=30
            )

            if response.status_code not in (200, 201, 204):
                return False, f"Failed: {response.status_code} - {response.text}"

            return True, "API path updated successfully"

        except requests.exceptions.RequestException as e:
            return False, f"Network error: {e}"

    # =========================================================================
    # Network Log Operations (for Diagnostics)
    # =========================================================================

    def fetch_network_logs(
        self,
        page: int = 1,
        page_size: int = 100,
        search: Optional[str] = None,
    ) -> Tuple[bool, Optional[List[Dict[str, Any]]], str]:
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
        params = {"page": page, "page_size": page_size}
        if search:
            params["search"] = search

        try:
            response = requests.get(
                url, headers=self.headers, params=params, timeout=60
            )

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
        search: Optional[str] = None,
        page_size: int = 100,
    ) -> Tuple[bool, List[Dict[str, Any]], str]:
        """
        Fetch all network logs with pagination.

        Args:
            max_logs: Maximum logs to fetch
            search: Optional URL search filter
            page_size: Page size

        Returns:
            Tuple of (success, logs_list, message)
        """
        all_logs = []
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
    ) -> Tuple[bool, Optional[List[Dict[str, Any]]], str]:
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
            response = requests.get(
                url, headers=self.headers, params=params, timeout=30
            )

            if response.status_code != 200:
                return False, None, f"Failed: {response.status_code} - {response.text}"

            data = response.json()
            tools = data.get("capabilities") or data.get("data") or []

            return True, tools, f"Found {len(tools)} tools"

        except requests.exceptions.RequestException as e:
            return False, None, f"Network error: {e}"


def get_api_client_for_env(env_name: Optional[str] = None, verbose: bool = False) -> AdoptAPIClient:
    """
    Get an API client with credentials loaded from the specified environment.

    This is the recommended way to get an API client in CLI scripts. It ensures
    that the correct environment credentials are loaded before creating the client.

    Args:
        env_name: Environment name. If None, uses active environment.
        verbose: Print verbose info about credential loading.

    Returns:
        AdoptAPIClient configured with environment credentials.

    Raises:
        ValueError: If no environment is available or .env is not configured.
    """
    from dotenv import load_dotenv
    from cli.wdl_common.workspace_manager import WORKSPACES_DIR, get_workspace_manager, DEFAULT_ENV

    manager = get_workspace_manager()

    # Determine environment
    env = env_name or manager.active_env or DEFAULT_ENV
    env_path = WORKSPACES_DIR / env

    if not env_path.exists():
        raise ValueError(
            f"Environment not found: {env}. "
            f"Create one with: python cli/workspace.py env create --id {env}"
        )

    # Load environment-specific .env file
    env_dotenv = env_path / ".env"
    if env_dotenv.exists():
        if verbose:
            print(f"[VERBOSE] Loading credentials from: {env_dotenv}", file=sys.stderr)
        load_dotenv(env_dotenv, override=True)

        # Check if credentials are configured (not placeholders)
        client_id = os.getenv("ADOPT_CLIENT_ID", "")
        client_secret = os.getenv("ADOPT_CLIENT_SECRET", "")

        if "your-" in client_id.lower() or not client_id:
            print(f"⚠️  Warning: ADOPT_CLIENT_ID is not configured in environment: {env}", file=sys.stderr)
            print(f"   Edit: {env_dotenv}", file=sys.stderr)
        if "your-" in client_secret.lower() or not client_secret:
            print(f"⚠️  Warning: ADOPT_CLIENT_SECRET is not configured in environment: {env}", file=sys.stderr)
            print(f"   Edit: {env_dotenv}", file=sys.stderr)
    else:
        print(f"⚠️  Warning: No .env file found in environment: {env}", file=sys.stderr)
        print(f"   Expected: {env_dotenv}", file=sys.stderr)
        print(f"   Using credentials from root .env or environment variables.", file=sys.stderr)

    return AdoptAPIClient()
