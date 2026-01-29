#!/usr/bin/env python3
"""
Create Tools - Interactive tool creation for Tool Builder
"""

import os
import json
import requests
from typing import List, Dict, Any, Optional, Tuple
from pathlib import Path
from time import sleep

# Try to import OpenAI, fall back gracefully
try:
    from openai import OpenAI  # type: ignore

    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False
    OpenAI = None

from cli.agents import select_agent_interactive, load_agents, save_agents
from cli.checkout_tools import checkout_tool_by_id


class APIManager:
    """Manages fetching and displaying APIs for tool creation"""

    def __init__(
        self,
        bearer_token: str,
        api_endpoint: Optional[str] = None,
        actions_endpoint: Optional[str] = None,
    ):
        self.bearer_token = bearer_token
        self.api_endpoint = api_endpoint or os.getenv(
            "ADOPT_API_ENDPOINT", "https://connect.adopt.ai"
        )
        self.actions_endpoint = actions_endpoint or os.getenv(
            "ADOPT_ACTIONS_ENDPOINT", "https://api.adopt.ai"
        )
        self.apis: List[Dict[str, Any]] = []

    def fetch_apis(self, page_size: int = 50) -> bool:
        """
        Fetch all available APIs from the AdoptAI API with pagination support.

        Args:
            page_size: Number of APIs to fetch per page (default: 50)

        Returns:
            True if APIs were fetched successfully
        """
        url = f"{self.api_endpoint}/v1/tools/apis"
        headers = {
            "Authorization": f"Bearer {self.bearer_token}",
            "Content-Type": "application/json",
        }

        all_apis = []
        page = 1
        total_fetched = 0

        try:
            print("⏳ Fetching available APIs...")

            while True:
                # Add pagination parameters
                params = {"page": page, "page_size": page_size}

                response = requests.get(url, headers=headers, params=params, timeout=30)

                if response.status_code != 200:
                    print(
                        f"❌ Failed to fetch APIs. Status code: {response.status_code}"
                    )
                    print(f"   Response: {response.text}")
                    return False

                json_response = response.json()

                # Handle different response formats
                page_apis = []
                has_more = False

                if isinstance(json_response, list):
                    # Simple list response
                    page_apis = json_response
                    has_more = len(page_apis) >= page_size
                elif isinstance(json_response, dict):
                    # Dictionary response with potential pagination info
                    # Check for items first (most common format)
                    if "items" in json_response:
                        page_apis = json_response["items"]
                    elif "apis" in json_response:
                        page_apis = json_response["apis"]
                    elif "data" in json_response:
                        page_apis = json_response["data"]
                    else:
                        # Try to use the response itself if it has typical API fields
                        page_apis = [json_response]

                    # Check for pagination indicators
                    # Use total_pages to determine if there are more pages
                    total_pages = json_response.get("total_pages")
                    current_page = json_response.get("page", page)
                    if total_pages is not None:
                        has_more = current_page < total_pages
                    else:
                        # Fallback to other pagination indicators
                        has_more = (
                            json_response.get("has_more", False)
                            or json_response.get("hasMore", False)
                            or json_response.get("has_next", False)
                            or len(page_apis) >= page_size
                        )

                    # Check if we have a total count to show progress
                    total = json_response.get("total") or json_response.get("count")
                    if total and page == 1:
                        print(f"   Total APIs available: {total}")
                else:
                    print(f"⚠️  Unexpected API response format: {type(json_response)}")
                    return False

                if not page_apis:
                    # No more results
                    if page == 1:
                        # Only warn on first page - might be a real issue
                        print(f"⚠️  No APIs found in page {page} response")
                        if isinstance(json_response, dict):
                            print(f"   Response keys: {list(json_response.keys())}")
                    break

                all_apis.extend(page_apis)
                total_fetched += len(page_apis)

                if page == 1:
                    print(f"   Fetched page {page}: {len(page_apis)} API(s)")
                else:
                    print(
                        f"   Fetched page {page}: {len(page_apis)} API(s) (total: {total_fetched})"
                    )

                # Check if we should continue pagination
                if not has_more or len(page_apis) < page_size:
                    break

                page += 1

            self.apis = all_apis
            print(f"✅ Fetched {len(self.apis)} APIs across {page} page(s)")
            return True

        except requests.exceptions.RequestException as e:
            print(f"❌ Network error while fetching APIs: {e}")
            return False

    def display_apis(self, page_size: int = 10) -> None:
        """
        Display APIs one page at a time with pagination.

        Args:
            page_size: Number of APIs to display per page (default: 10)
        """
        if not self.apis:
            print("\n⚠️  No APIs available")
            return

        total_apis = len(self.apis)
        total_pages = (total_apis + page_size - 1) // page_size
        current_page = 0

        while True:
            start_idx = current_page * page_size
            end_idx = min(start_idx + page_size, total_apis)
            page_apis = self.apis[start_idx:end_idx]

            # Display header
            print("\n" + "=" * 80)
            print(f"📚 AVAILABLE APIs - Page {current_page + 1} of {total_pages}")
            print(f"   Showing APIs {start_idx + 1}-{end_idx} of {total_apis}")
            print("=" * 80)

            # Display APIs on current page
            for idx, api in enumerate(page_apis, start=start_idx + 1):
                self._display_api(idx, api)

            print("=" * 80)

            # Navigation options
            nav_options = []
            if current_page > 0:
                nav_options.append("[P]revious")
            if current_page < total_pages - 1:
                nav_options.append("[N]ext")
            nav_options.append("[Q]uit")

            print(f"Navigation: {' | '.join(nav_options)}")
            print("=" * 80)

            # Get user input
            user_input = input("\n👉 Choose an option: ").strip().upper()

            if user_input == "Q":
                break
            elif user_input == "N" and current_page < total_pages - 1:
                current_page += 1
            elif user_input == "P" and current_page > 0:
                current_page -= 1
            else:
                print("⚠️  Invalid option. Please try again.")
                input("Press Enter to continue...")

    def _display_api(self, index: int, api: Dict[str, Any]) -> None:
        """
        Display a single API with formatted output.

        Args:
            index: The API number
            api: The API data dictionary
        """
        api_id = api.get("id", "N/A")
        api_name = api.get("name", "N/A")
        api_description = api.get("description", "No description available")

        print(f"\n{index}. {api_name}")
        print(f"   UUID: {api_id}")
        print(f"   Description: {api_description}")

        # Show base URL if available
        base_url = api.get("base_url", api.get("baseUrl"))
        if base_url:
            print(f"   Base URL: {base_url}")

        print("-" * 80)

    def find_api_by_uuid_fragment(self, fragment: str) -> Optional[Dict[str, Any]]:
        """
        Find an API by matching a UUID fragment.

        Args:
            fragment: A fragment of the UUID to search for

        Returns:
            The matching API or None if not found/ambiguous
        """
        fragment = fragment.strip().lower()

        if not fragment:
            return None

        # Find all APIs that contain the fragment
        matches = [api for api in self.apis if fragment in api.get("id", "").lower()]

        if len(matches) == 0:
            print(f"❌ No API found matching UUID fragment: '{fragment}'")
            return None
        elif len(matches) > 1:
            print(f"⚠️  Multiple APIs match UUID fragment '{fragment}':")
            for api in matches:
                print(f"   - {api.get('name', 'N/A')} ({api.get('id', 'N/A')})")
            print("   Please provide a more specific fragment")
            return None

        return matches[0]

    def fetch_api_details(self, api_id: str) -> Optional[Dict[str, Any]]:
        """
        Fetch detailed API information from the documented API endpoint.

        Args:
            api_id: The API UUID to fetch details for

        Returns:
            Dictionary containing detailed API information, or None if failed
        """
        url = f"{self.api_endpoint}/v1/tools/apis/{api_id}"
        headers = {
            "Authorization": f"Bearer {self.bearer_token}",
            "Content-Type": "application/json",
        }

        try:
            print(f"⏳ Fetching detailed API information for {api_id}...")
            response = requests.get(url, headers=headers, timeout=30)

            if response.status_code != 200:
                print(
                    f"❌ Failed to fetch API details. Status code: {response.status_code}"
                )
                print(f"   Response: {response.text}")
                return None

            api_details = response.json()
            print(f"✅ API details fetched successfully: {api_details}")
            return api_details

        except requests.exceptions.RequestException as e:
            print(f"❌ Network error while fetching API details: {e}")
            return None


class ToolCreator:
    """Manages tool creation and storage"""

    def __init__(
        self,
        bearer_token: str,
        api_endpoint: Optional[str] = None,
        actions_endpoint: Optional[str] = None,
    ):
        self.bearer_token = bearer_token
        self.api_endpoint = api_endpoint or os.getenv(
            "ADOPT_API_ENDPOINT", "https://connect.adopt.ai"
        )
        self.actions_endpoint = actions_endpoint or os.getenv(
            "ADAPT_ACTIONS_ENDPOINT", "https://api.adopt.ai"
        )
        self.guidelines_content = self._load_guidelines()
        self.widdle_guidelines_content = self._load_widdle_guidelines()
        self.openai_client = None
        self._initialize_openai()

    def _load_guidelines(self) -> str:
        """
        Load the tool description guidelines from tool_description.md

        Returns:
            Guidelines content as string, or empty string if file not found
        """
        try:
            guidelines_path = Path(__file__).parent / "prompts" / "templates" / "new_tool_description.md"
            if guidelines_path.exists():
                with open(guidelines_path, "r") as f:
                    return f.read().strip()
            else:
                print(
                    "⚠️  Warning: tool_description.md not found. Guidelines will not be prepended."
                )
                return ""
        except Exception as e:
            print(f"⚠️  Warning: Could not load tool_description.md: {e}")
            return ""

    def _load_widdle_guidelines(self) -> str:
        """
        Load the widdle generation guidelines

        Returns:
            Guidelines content as string, or empty string if file not found
        """
        try:
            guidelines_path = Path(__file__).parent / "prompts" / "templates" / "new_tool_description.md"
            if guidelines_path.exists():
                with open(guidelines_path, "r") as f:
                    return f.read().strip()
            else:
                return "Create a simple WDL with just two blocks. One for REST and one for OUTPUT_TEXT. Do not create any other blocks.\nCall the given API in the REST block and return the output from OUTPUT_TEXT as raw."
        except Exception:
            return "Create a simple WDL with just two blocks. One for REST and one for OUTPUT_TEXT. Do not create any other blocks.\nCall the given API in the REST block and return the output from OUTPUT_TEXT as raw."

    def _initialize_openai(self) -> None:
        """Initialize OpenAI client if API key is available"""
        if not OPENAI_AVAILABLE or OpenAI is None:
            return

        api_key = os.getenv("OPENAI_API_KEY", "")
        if api_key and api_key != "your_openai_api_key_here":
            try:
                self.openai_client = OpenAI(api_key=api_key)  # type: ignore
            except Exception as e:
                print(f"⚠️  Warning: Could not initialize OpenAI client: {e}")

    def generate_description_from_api(
        self, api_details: Dict[str, Any]
    ) -> Optional[str]:
        """
        Generate a tool description using LLM based on API details.

        Args:
            api_details: Detailed API information from the endpoint

        Returns:
            Generated description or None if generation failed
        """
        if not self.openai_client:
            print("⚠️  OpenAI client not available. Cannot generate description.")
            print("   Please set OPENAI_API_KEY in your environment or .env file.")
            return None

        try:
            # Extract relevant information from API details
            api_name = api_details.get("name", "Unknown API")
            api_description = api_details.get("description", "")
            api_base_url = api_details.get("base_url", api_details.get("baseUrl", ""))

            # Build context for the LLM
            context_parts = [f"API Name: {api_name}"]

            if api_description:
                context_parts.append(f"API Description: {api_description}")

            if api_base_url:
                context_parts.append(f"Base URL: {api_base_url}")

            # Include OpenAPI spec if available
            if "spec" in api_details or "openapi_spec" in api_details:
                spec = api_details.get("spec") or api_details.get("openapi_spec")
                if spec:
                    spec_str = (
                        json.dumps(spec, indent=2)
                        if isinstance(spec, dict)
                        else str(spec)
                    )
                    # Limit spec size to avoid token limits
                    if len(spec_str) > 3000:
                        spec_str = spec_str[:3000] + "\n... (truncated)"
                    context_parts.append(f"OpenAPI Specification:\n{spec_str}")

            # Include endpoints/operations if available
            if "endpoints" in api_details or "operations" in api_details:
                endpoints = api_details.get("endpoints") or api_details.get(
                    "operations"
                )
                if endpoints:
                    endpoints_str = (
                        json.dumps(endpoints, indent=2)
                        if isinstance(endpoints, dict)
                        else str(endpoints)
                    )
                    if len(endpoints_str) > 2000:
                        endpoints_str = endpoints_str[:2000] + "\n... (truncated)"
                    context_parts.append(f"Available Endpoints:\n{endpoints_str}")

            context = "\n\n".join(context_parts)

            # Create prompt for LLM
            prompt = f"""You are an expert at creating clear, concise descriptions for creating tools that call APIs.

Based on the following API information, generate a 240 character description that will be used for creating a tool that calls this API:
1. What are all the inputs needed to call the API?
2. What are the suggested default values for each input?
3. What are the suggested data types for each input?
4. What are the expected outputs and meaning of the data returned from the API?
5. What is the expected format of the outputs from the API?
6. What are the query parameters that can be used to filter the results from the API (if any)?
7. What are the headers that need to be set when calling the API (if any special/non standard ones)?
8. What is the payload/body/request body that needs to be sent to the API (if any)?

API Information:
{context}

Generate a tool description:"""

            print("⏳ Generating description using LLM...")

            response = self.openai_client.chat.completions.create(
                model="gpt-4o-mini",  # Using cost-effective model
                messages=[
                    {
                        "role": "system",
                        "content": "You are an expert technical writer creating tool descriptions for API integrations.",
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=0.7,
                max_tokens=800,
            )

            description = (
                response.choices[0].message.content.strip()
                if response.choices[0].message.content
                else None
            )
            print("✅ Description generated successfully")

            return description

        except Exception as e:
            print(f"❌ Error generating description: {e}")
            return None

    def fetch_tool_details(
        self, action_id: str
    ) -> Tuple[bool, Optional[Dict[str, Any]], str]:
        """
        Fetch tool details from the AdoptAI API with enhanced retry logic.

        Phase 1: Exponential backoff for ~5 minutes total
        Phase 2: Check every 30 seconds after that

        Args:
            action_id: The action ID to fetch details for

        Returns:
            Tuple of (success: bool, tool_data: dict or None, message: str)
        """
        # Phase 1: Exponential backoff for first 5 minutes
        phase1_wait_times = [30, 60, 90, 120]  # Total: 300 seconds = 5 minutes

        for i, wait_time in enumerate(phase1_wait_times, 1):
            try:
                url = f"{self.actions_endpoint}/v1/actions/{action_id}/current/"
                headers = {
                    "Authorization": f"Bearer {self.bearer_token}",
                    "Content-Type": "application/json",
                }
                response = requests.get(url, headers=headers)
                response_json = response.json()
                status = response_json.get("status", "error")
                is_regenerating = response_json.get("is_regenerating", True)
                if status == "error" or is_regenerating:
                    print(
                        f"❌ Tool details fetch failed. Status: {status}. Is regenerating: {is_regenerating}. Retrying in {wait_time} seconds... (attempt {i}/{len(phase1_wait_times)})"
                    )
                    sleep(wait_time)
                    continue
                return True, response_json, "Tool details fetched successfully"
            except Exception as e:
                print(f"❌ Error fetching tool details: {e}")
                if i < len(phase1_wait_times):
                    print(f"   Retrying in {wait_time} seconds...")
                    sleep(wait_time)
                    continue
                else:
                    # Don't return yet, continue to phase 2
                    break

        # Phase 2: Check every 30 seconds (for up to 5 more minutes)
        phase2_retries = 10
        phase2_wait_time = 30

        print(f"⏳ Switching to regular polling (every {phase2_wait_time} seconds)...")

        for i in range(phase2_retries):
            try:
                url = f"{self.actions_endpoint}/v1/actions/{action_id}/current/"
                headers = {
                    "Authorization": f"Bearer {self.bearer_token}",
                    "Content-Type": "application/json",
                }
                response = requests.get(url, headers=headers)
                response_json = response.json()
                status = response_json.get("status", "error")
                is_regenerating = response_json.get("is_regenerating", True)
                if status == "error" or is_regenerating:
                    print(
                        f"   Still processing... (polling attempt {i + 1}/{phase2_retries})"
                    )
                    sleep(phase2_wait_time)
                    continue
                return True, response_json, "Tool details fetched successfully"
            except Exception as e:
                print(f"❌ Error fetching tool details: {e}")
                return False, None, f"Error fetching tool details: {e}"

        return False, None, "Failed to fetch tool details after multiple retries"

    def list_existing_tools(self) -> Tuple[bool, Optional[List[Dict[str, Any]]], str]:
        """
        List all existing tools with execution_type set to "tool".

        Returns:
            Tuple of (success: bool, tools_list: list or None, message: str)
        """
        url = f"{self.api_endpoint}/v1/actions/list"
        headers = {
            "Authorization": f"Bearer {self.bearer_token}",
            "Content-Type": "application/json",
        }

        params = {"execution_type": "TOOL"}

        try:
            response = requests.get(url, headers=headers, params=params, timeout=30)

            if response.status_code != 200:
                error_msg = f"Failed to list tools. Status code: {response.status_code}\n   Response: {response.text}"
                return False, None, error_msg

            tools_data = response.json()

            # Handle different response formats
            if isinstance(tools_data, list):
                tools_list = tools_data
            elif isinstance(tools_data, dict):
                # API returns capabilities object which is a list of tools
                tools_list = tools_data.get(
                    "capabilities",
                    tools_data.get(
                        "data", tools_data.get("actions", tools_data.get("items", []))
                    ),
                )
            else:
                return False, None, "Unexpected response format from list tools API"

            return True, tools_list, "Tools listed successfully"

        except requests.exceptions.RequestException as e:
            return False, None, f"Network error while listing tools: {e}"

    def check_tool_exists_for_api(
        self, api_id: str
    ) -> Tuple[bool, Optional[Dict[str, Any]]]:
        """
        Check if a tool already exists for the given API ID.

        Args:
            api_id: The API ID to check

        Returns:
            Tuple of (exists: bool, existing_tool: dict or None)
        """
        success, tools_list, message = self.list_existing_tools()

        if not success:
            print(f"   ⚠️  Warning: Could not check for existing tools: {message}")
            return False, None

        if not tools_list:
            return False, None

        # Check if any tool uses this API ID
        for tool in tools_list:
            # Get api_ids from the tool (could be in different locations)
            tool_api_ids = tool.get("api_ids", [])
            if not isinstance(tool_api_ids, list):
                tool_api_ids = [tool_api_ids] if tool_api_ids else []

            # Also check in apis field if it exists
            if "apis" in tool:
                apis = tool.get("apis", [])
                if isinstance(apis, list):
                    for api in apis:
                        if isinstance(api, dict):
                            tool_api_ids.append(api.get("id", ""))
                        elif isinstance(api, str):
                            tool_api_ids.append(api)

            # Check if our API ID is in this tool's API IDs
            if api_id in tool_api_ids:
                return True, tool

        return False, None

    def set_deployment_rules(self, action_id: str) -> Tuple[bool, str]:
        """
        Set deployment rules for a tool to mark it as tool mode.

        Args:
            action_id: The action ID to set deployment rules for

        Returns:
            Tuple of (success: bool, message: str)
        """
        url = f"{self.actions_endpoint}/v1/actions/{action_id}/deployment-rules"
        headers = {
            "Authorization": f"Bearer {self.bearer_token}",
            "Content-Type": "application/json",
        }

        payload = {"rules": [], "is_visible_in_list": True, "is_tool_mode": True}

        try:
            print("   ⏳ Setting deployment rules for tool...")
            response = requests.post(url, headers=headers, json=payload, timeout=30)

            if response.status_code not in (200, 201):
                error_msg = f"Failed to set deployment rules. Status code: {response.status_code}\n   Response: {response.text}"
                return False, error_msg

            print("   ✅ Deployment rules set successfully")
            return True, "Deployment rules set successfully"

        except requests.exceptions.RequestException as e:
            return False, f"Network error while setting deployment rules: {e}"

    def regenerate_widdle(
        self,
        action_id: str,
        tool_title: str,
        tool_description: str,
        api_details: Dict[str, Any],
    ) -> Tuple[bool, Optional[List[Dict[str, Any]]], str]:
        """
        Regenerate widdle using Claude Sonnet based on API details and tool description.

        Args:
            action_id: The action ID
            tool_title: The tool title
            tool_description: The tool description
            api_details: Detailed API information

        Returns:
            Tuple of (success: bool, widdle_data: dict or None, message: str)
        """
        try:
            # Generate placeholder WDL based on API details
            # The agent should refine this using the simple_tool_template
            canonical_path = api_details.get("canonical_api_endpoint", "/api/endpoint") if api_details else "/api/endpoint"
            method = api_details.get("method", "GET") if api_details else "GET"
            
            wdl_blocks = [
                {
                    "required_inputs": {
                        "TODO_param": {
                            "type": "string",
                            "definition": "Agent should define based on API spec (mandatory)"
                        }
                    }
                },
                {
                    "id": "call_api",
                    "operation": "REST",
                    "method": method,
                    "canonical_api_endpoint": canonical_path,
                    "url": canonical_path,
                    "output_key": "api_response"
                },
                {
                    "id": "output",
                    "operation": "OUTPUT_TEXT",
                    "raw": True,
                    "inputs": {
                        "content": "{api_response}"
                    }
                }
            ]

            print("   ✅ Placeholder WDL generated (agent should refine)")
            return True, wdl_blocks, "Placeholder WDL generated"

        except Exception as e:
            return False, None, f"Error generating widdle: {e}"

    def publish_widdle(
        self, action_id: str, draft_id: str, widdle_data: List[Dict[str, Any]]
    ) -> Tuple[bool, str]:
        """
        Publish widdle to upstream service.

        Args:
            action_id: The action ID
            draft_id: The draft ID
            widdle_data: The widdle data to publish

        Returns:
            Tuple of (success: bool, message: str)
        """
        url = f"{self.actions_endpoint}/v1/actions/wdl-update"
        headers = {
            "Authorization": f"Bearer {self.bearer_token}",
            "Content-Type": "application/json",
        }

        payload = {"action_id": action_id, "draft_id": draft_id, "wdl": widdle_data}

        try:
            print("   ⏳ Publishing widdle to upstream...")
            response = requests.post(url, headers=headers, json=payload, timeout=30)

            if response.status_code not in (200, 201):
                error_msg = f"Failed to publish widdle. Status code: {response.status_code}\n   Response: {response.text}"
                return False, error_msg

            print(f"   ✅ Widdle published successfully: {widdle_data}")
            return True, "Widdle published successfully"

        except requests.exceptions.RequestException as e:
            return False, f"Network error while publishing widdle: {e}"

    def save_draft(
        self, action_id: str, draft_id: str
    ) -> Tuple[bool, Optional[str], str]:
        """
        Save draft action to get a version number.

        Args:
            action_id: The action ID
            draft_id: The draft ID

        Returns:
            Tuple of (success: bool, version_number: str or None, message: str)
        """
        url = f"{self.actions_endpoint}/v1/actions/{action_id}/draft/{draft_id}/save"
        headers = {
            "Authorization": f"Bearer {self.bearer_token}",
            "Content-Type": "application/json",
        }

        try:
            print("   ⏳ Saving draft action...")
            response = requests.post(url, headers=headers, timeout=30)

            if response.status_code not in (200, 201):
                error_msg = f"Failed to save draft. Status code: {response.status_code}\n   Response: {response.text}"
                return False, None, error_msg

            response_data = response.json()
            version_number = response_data.get("version_number")

            if not version_number:
                return False, None, "No version_number in response"

            print(
                f"   ✅ Draft saved successfully (version: {version_number}) (draft_id: {draft_id})"
            )
            return True, version_number, "Draft saved successfully"

        except requests.exceptions.RequestException as e:
            return False, None, f"Network error while saving draft: {e}"

    def approve_version(self, action_id: str, version_id: str) -> Tuple[bool, str]:
        """
        Approve a version of the action.

        Args:
            action_id: The action ID
            version_id: The version ID to approve

        Returns:
            Tuple of (success: bool, message: str)
        """
        url = f"{self.actions_endpoint}/v1/actions/{action_id}/version/{version_id}"
        headers = {
            "Authorization": f"Bearer {self.bearer_token}",
            "Content-Type": "application/json",
        }

        payload = {"status": "approved", "change_reason": "creating from scratch"}

        try:
            print(f"   ⏳ Approving version {version_id}...")
            response = requests.put(url, headers=headers, json=payload, timeout=30)

            if response.status_code not in (200, 201):
                error_msg = f"Failed to approve version. Status code: {response.status_code}\n   Response: {response.text}"
                return False, error_msg

            print("   ✅ Version approved successfully")
            return True, "Version approved successfully"

        except requests.exceptions.RequestException as e:
            return False, f"Network error while approving version: {e}"

    def create_tool(
        self,
        title: str,
        description: str,
        api_id: str,
        api_details: Optional[Dict[str, Any]] = None,
        auto_complete: bool = True,
    ) -> Tuple[bool, Optional[Dict[str, Any]], str]:
        """
        Create a new tool via the AdoptAI API and optionally complete all setup steps.

        Args:
            title: Tool title (max 64 chars)
            description: Tool description (will be prepended with guidelines)
            api_id: API UUID to associate with the tool
            api_details: Optional API details for widdle generation
            auto_complete: Whether to automatically complete all setup steps (deployment rules, widdle, approval)

        Returns:
            Tuple of (success: bool, tool_data: dict or None, message: str)
        """
        # Validate inputs
        if not title or not title.strip():
            return False, None, "Tool title cannot be empty"

        title = title.strip()
        if len(title) > 64:
            return False, None, f"Tool title too long ({len(title)} chars, max 64)"

        if not description or not description.strip():
            return False, None, "Tool description cannot be empty"

        if not api_id or not api_id.strip():
            return False, None, "API ID cannot be empty"

        # Prepend guidelines to the description
        full_description = description.strip()
        if self.guidelines_content:
            full_description = (
                f"{self.guidelines_content}\n\n---\n\n{description.strip()}"
            )
            print("   📋 Guidelines prepended to description")

        # Prepare the request
        url = f"{self.actions_endpoint}/v1/actions/"
        headers = {
            "Authorization": f"Bearer {self.bearer_token}",
            "Content-Type": "application/json",
        }

        payload = {
            "title": title,
            "description": full_description,
            "api_ids": [api_id.strip()],
        }

        try:
            print(f"\n⏳ Creating tool '{title}'...")
            response = requests.post(url, headers=headers, json=payload, timeout=30)

            if response.status_code not in (200, 201):
                error_msg = f"Failed to create tool. Status code: {response.status_code}\n   Response: {response.text}"
                return False, None, error_msg

            tool_data = response.json()
            action_id = tool_data.get("action_id", "")
            # Fetch final tool details
            success, tool_data, message = self.fetch_tool_details(action_id)

            print("✅ Tool created successfully!")
            action_id = tool_data.get("action_id", "") if tool_data else ""
            draft_id = (
                tool_data.get("id", tool_data.get("draft_id", "")) if tool_data else ""
            )

            if not action_id:
                raise Exception("Action ID not found in tool data")

            if not draft_id:
                raise Exception("Draft ID not found in tool data")

            # Auto-complete all setup steps if enabled
            if auto_complete:
                print("\n📋 Completing tool setup steps...")

                # Step 1: Set deployment rules
                success, message = self.set_deployment_rules(action_id)
                if not success:
                    print(f"   ⚠️  Warning: {message}")

                # Step 2: Regenerate widdle (if API details provided and OpenAI available)
                widdle_data = None
                if api_details and self.openai_client:
                    success, widdle_data, message = self.regenerate_widdle(
                        action_id, title, description, api_details
                    )
                    if not success:
                        print(f"   ⚠️  Warning: {message}")

                    # Step 3: Publish widdle (if we have widdle data and draft_id)
                    if success and widdle_data and draft_id:
                        success, message = self.publish_widdle(
                            action_id, draft_id, widdle_data
                        )
                        if not success:
                            print(f"   ⚠️  Warning: {message}")

                        success, tool_data, message = self.fetch_tool_details(action_id)
                        if not success:
                            print(f"   ⚠️  Warning: {message}")
                            return False, None, "Failed to fetch tool details"

                        draft_id = (
                            tool_data.get("id", tool_data.get("draft_id", ""))
                            if tool_data
                            else ""
                        )
                        if not draft_id:
                            raise Exception("Draft ID not found in tool data")

                        # Step 4: Save draft
                        if success:
                            success, version_number, message = self.save_draft(
                                action_id, draft_id
                            )
                            if not success:
                                print(f"   ⚠️  Warning: {message}")

                            success, tool_data, message = self.fetch_tool_details(
                                action_id
                            )
                            if not success:
                                print(f"   ⚠️  Warning: {message}")
                                return False, None, "Failed to fetch tool details"

                            draft_id = (
                                tool_data.get("id", tool_data.get("draft_id", ""))
                                if tool_data
                                else ""
                            )
                            if not draft_id:
                                raise Exception("Draft ID not found in tool data")

                            # Step 5: Approve version
                            if success and version_number:
                                success, message = self.approve_version(
                                    action_id, version_number
                                )
                                if not success:
                                    print(f"   ⚠️  Warning: {message}")
                                success, tool_data, message = self.fetch_tool_details(
                                    action_id
                                )
                                if not success:
                                    print(f"   ⚠️  Warning: {message}")
                                    return False, None, "Failed to fetch tool details"

                else:
                    if not api_details:
                        print(
                            "   ⚠️  Skipping widdle generation: No API details provided"
                        )
                    elif not self.openai_client:
                        print(
                            "   ⚠️  Skipping widdle generation: OpenAI client not available"
                        )

            # Final step: Verify the tool exists for this API ID
            print("\n🔍 Final verification: Checking if tool exists for API...")
            exists, found_tool = self.check_tool_exists_for_api(api_id.strip())

            if exists and found_tool:
                found_tool_id = found_tool.get("action_id", found_tool.get("id", "N/A"))
                found_tool_title = found_tool.get("title", "N/A")
                print("✅ Verification successful: Tool found!")
                print(f"   Tool: {found_tool_title}")
                print(f"   ID: {found_tool_id}")
                print(f"   API ID: {api_id}")
            else:
                print(
                    f"⚠️  Verification warning: Could not find tool with API ID {api_id}"
                )
                print(
                    "   The tool was created but may not be properly associated with the API"
                )
                # raise Exception("Tool not found for API ID when checking for tool existence right after creation")

            return True, tool_data, "Tool created successfully"

        except requests.exceptions.RequestException as e:
            return False, None, f"Network error while creating tool: {e}"

    def save_tool_to_agent(
        self,
        agent_name: str,
        agent_path: str,
        tool_data: Dict[str, Any],
        auto_checkout: bool = True,
    ) -> bool:
        """
        Save tool data to the agent folder and optionally checkout the current version.

        Args:
            agent_name: Name of the agent
            agent_path: Path to the agent directory
            tool_data: Tool data from API response (initial creation data)
            auto_checkout: Whether to automatically checkout the tool's current version (default: True)

        Returns:
            True if saved successfully
        """
        try:
            tool_id = tool_data.get("action_id", "unknown")

            # If auto_checkout is enabled, fetch the current version from the checkout endpoint
            if auto_checkout and tool_id and tool_id != "unknown":
                print("   📥 Auto-checkout enabled: fetching current tool version...")

                # Use the checkout functionality to get the latest version
                try:
                    if checkout_tool_by_id(
                        self.bearer_token, tool_id, agent_name, agent_path
                    ):
                        print(
                            "   ✅ Tool automatically checked out with current version"
                        )
                        return True
                    else:
                        print(
                            "   ⚠️  Auto-checkout failed, falling back to saving creation data..."
                        )
                except Exception as e:
                    print(
                        f"   ⚠️  Auto-checkout error: {e}, falling back to saving creation data..."
                    )

            # Fallback: Save the creation data if checkout is disabled or failed
            # Create tools directory if it doesn't exist
            tools_dir = Path(agent_path) / "tools"
            tools_dir.mkdir(parents=True, exist_ok=True)

            # Create a safe filename from the tool ID
            safe_filename = f"{tool_id}.json"

            # Save the tool data
            tool_file = tools_dir / safe_filename
            with open(tool_file, "w") as f:
                json.dump(tool_data, f, indent=2)

            print(f"   💾 Tool saved to: {tool_file}")

            # Update agent metadata
            agents = load_agents()
            if agent_name in agents:
                # Count tools in the agent
                tool_files = list(tools_dir.glob("*.json"))
                agents[agent_name]["tools_count"] = len(tool_files)
                from datetime import datetime

                agents[agent_name]["updated_at"] = datetime.now().isoformat()
                save_agents(agents)

            return True

        except Exception as e:
            print(f"⚠️  Warning: Could not save tool to agent: {e}")
            return False


def get_tool_name_input() -> Optional[str]:
    """
    Get tool name from user with validation.

    Returns:
        Tool name or None if cancelled
    """
    while True:
        name = input(
            "\n📝 Enter tool name (max 64 chars, or 'cancel' to stop): "
        ).strip()

        if name.lower() == "cancel":
            return None

        if not name:
            print("⚠️  Tool name cannot be empty. Please try again.")
            continue

        if len(name) > 64:
            print(
                f"⚠️  Tool name too long ({len(name)} chars, max 64). Please shorten it."
            )
            continue

        return name


def get_tool_description_input(
    initial_description: Optional[str] = None,
) -> Optional[str]:
    """
    Get tool description from user, optionally with a pre-filled initial description.

    Args:
        initial_description: Optional pre-generated description to show and edit

    Returns:
        Tool description or None if cancelled
    """
    if initial_description:
        print("\n📄 Generated tool description:")
        print("=" * 80)
        print(initial_description)
        print("=" * 80)

        while True:
            action = input("\n👉 (A)ccept, (E)dit, or (C)ancel? ").strip().upper()

            if action == "A":
                return initial_description
            elif action == "E":
                print("\n📝 Edit the description below (or 'cancel' to stop):")
                print("   (You can enter multiple lines. Press Enter twice to finish)")
                break
            elif action == "C":
                return None
            else:
                print("⚠️  Invalid option. Please choose A, E, or C.")
    else:
        print("\n📄 Enter tool description (or 'cancel' to stop):")
        print("   (You can enter multiple lines. Press Enter twice to finish)")

    lines = []
    empty_count = 0

    while True:
        line = input()

        if line.strip().lower() == "cancel":
            return None

        if not line.strip():
            empty_count += 1
            if empty_count >= 2:
                break
        else:
            empty_count = 0
            lines.append(line)

    description = " ".join(lines).strip()

    if not description:
        print("⚠️  Description cannot be empty.")
        return get_tool_description_input(initial_description)

    return description


def select_api_interactive(api_manager: APIManager) -> Optional[str]:
    """
    Interactive API selection with paginated display.

    Args:
        api_manager: APIManager instance with loaded APIs

    Returns:
        Selected API ID or None if cancelled
    """
    print("\n" + "=" * 80)
    print("📋 Browse through available APIs page by page")
    print("   You'll be able to select an API after viewing")
    print("=" * 80)
    input("Press Enter to start browsing...")

    # Display available APIs (paginated)
    api_manager.display_apis()

    print("\n" + "=" * 80)
    print("🔍 SELECT AN API")
    print("=" * 80)

    while True:
        fragment = input(
            "\n🔍 Paste UUID fragment for the API (or 'list' to browse again, 'cancel' to stop): "
        ).strip()

        if fragment.lower() == "cancel":
            return None

        if fragment.lower() == "list":
            api_manager.display_apis()
            continue

        if not fragment:
            print("⚠️  Please enter a UUID fragment.")
            continue

        # Find matching API
        api = api_manager.find_api_by_uuid_fragment(fragment)

        if api:
            api_id = api.get("id")
            api_name = api.get("name", "Unknown")
            print(f"\n✅ Selected API: {api_name}")
            print(f"   UUID: {api_id}")

            # Confirm selection
            confirm = input("   Is this correct? (y/n): ").strip().lower()
            if confirm in ("y", "yes", ""):
                return api_id
            else:
                print("   Selection cancelled, try again.")


def get_api_ids_from_paste() -> Optional[List[str]]:
    """
    Get a list of API IDs from user paste input.

    Returns:
        List of API IDs or None if cancelled
    """
    print("\n" + "=" * 80)
    print("📋 PASTE API IDs")
    print("=" * 80)
    print("Paste your API IDs (UUIDs) below, one per line or comma-separated.")
    print("Press Enter twice when done, or type 'cancel' to stop.")
    print("=" * 80)

    lines = []
    empty_count = 0

    while True:
        line = input()

        if line.strip().lower() == "cancel":
            return None

        if not line.strip():
            empty_count += 1
            if empty_count >= 2:
                break
        else:
            empty_count = 0
            lines.append(line)

    if not lines:
        print("⚠️  No API IDs provided.")
        return None

    # Parse the input - handle both newline and comma-separated
    api_ids = []
    for line in lines:
        # Split by comma, semicolon, or space
        parts = line.replace(",", " ").replace(";", " ").split()
        api_ids.extend([part.strip() for part in parts if part.strip()])

    if not api_ids:
        print("⚠️  No valid API IDs found.")
        return None

    # Remove duplicates while preserving order
    seen = set()
    unique_api_ids = []
    for api_id in api_ids:
        if api_id not in seen:
            seen.add(api_id)
            unique_api_ids.append(api_id)

    print(f"\n✅ Parsed {len(unique_api_ids)} unique API ID(s)")
    print("\nAPI IDs to use:")
    for idx, api_id in enumerate(unique_api_ids, 1):
        print(f"   {idx}. {api_id}")

    # Confirm
    confirm = input("\n   Use these API IDs? (y/n): ").strip().lower()
    if confirm in ("y", "yes", ""):
        return unique_api_ids
    else:
        print("   Cancelled.")
        return None


def create_tools_one_by_one(
    bearer_token: str,
    agent_name: str,
    agent_info: Dict[str, Any],
    api_manager: APIManager,
) -> int:
    """
    Create tools one by one with interactive API selection.
    New workflow: Select API → Use API title → Generate description → Confirm → Create

    Args:
        bearer_token: Authentication token
        agent_name: Name of the agent
        agent_info: Agent information dictionary
        api_manager: APIManager instance with loaded APIs

    Returns:
        Number of tools created
    """
    agent_path = agent_info.get("path", "")
    tool_creator = ToolCreator(bearer_token)
    tools_created = 0

    print("\n" + "=" * 80)
    print("🚀 START CREATING TOOLS (ONE BY ONE)")
    print("=" * 80)
    print(f"Agent: {agent_name}")
    print(f"Available APIs: {len(api_manager.apis)}")
    print("=" * 80)

    while True:
        print("\n" + "-" * 80)
        print(f"📝 CREATING TOOL #{tools_created + 1}")
        print("-" * 80)

        # Step 1: Select API first
        api_id = select_api_interactive(api_manager)
        if api_id is None:
            print("\n⚠️  Tool creation stopped by user.")
            break

        # Step 2: Check if a tool already exists for this API (before generating description)
        print(f"\n🔍 Checking if tool already exists for API: {api_id}...")
        exists, existing_tool = tool_creator.check_tool_exists_for_api(api_id)

        if exists and existing_tool:
            tool_title = existing_tool.get("title", "Unknown")
            tool_id = existing_tool.get("action_id", existing_tool.get("id", "Unknown"))
            print("⚠️  A tool already exists for this API!")
            print(f"   Tool: {tool_title}")
            print(f"   ID: {tool_id}")
            print("   Skipping to next tool...")

            # Ask if user wants to continue with another tool
            print("\n" + "-" * 80)
            continue_creating = input("🔄 Try another tool? (y/n): ").strip().lower()
            if continue_creating not in ("y", "yes", ""):
                break
            continue

        # Find the selected API to get its name
        selected_api = next(
            (api for api in api_manager.apis if api.get("id") == api_id), None
        )
        api_name = selected_api.get("title", "Tool") if selected_api else "Tool"

        # Step 3: Use API title as tool name (with option to edit)
        print(f"\n📝 Suggested tool name from API: {api_name}")
        use_suggested = (
            input("   Use this name? (y/n, or press Enter for yes): ").strip().lower()
        )

        if use_suggested in ("n", "no"):
            tool_name = get_tool_name_input()
            if tool_name is None:
                print("\n⚠️  Tool creation stopped by user.")
                break
        else:
            tool_name = api_name
            # Validate length
            if len(tool_name) > 64:
                print(f"⚠️  API name too long ({len(tool_name)} chars, max 64).")
                tool_name = get_tool_name_input()
                if tool_name is None:
                    print("\n⚠️  Tool creation stopped by user.")
                    break

        print(f"✅ Using tool name: {tool_name}")

        # Step 4: Fetch detailed API information
        api_details = api_manager.fetch_api_details(api_id)

        # Step 5: Generate description using LLM (if available)
        generated_description = None
        if api_details:
            generated_description = tool_creator.generate_description_from_api(
                api_details
            )

        # Step 6: Get user confirmation/editing of description
        if generated_description:
            tool_description = get_tool_description_input(
                initial_description=generated_description
            )
        else:
            print("\n⚠️  Could not generate description automatically.")
            tool_description = get_tool_description_input()

        if tool_description is None:
            print("\n⚠️  Tool creation stopped by user.")
            break

        # Step 7: Create the tool
        success, tool_data, message = tool_creator.create_tool(
            title=tool_name,
            description=tool_description,
            api_id=api_id,
            api_details=api_details,
            auto_complete=True,
        )

        if success and tool_data:
            tools_created += 1

            # Save to agent
            tool_creator.save_tool_to_agent(agent_name, agent_path, tool_data)

            print("\n" + "=" * 80)
            print("✅ TOOL CREATED SUCCESSFULLY!")
            print("=" * 80)
            print(f"   Title: {tool_data.get('title', 'N/A')}")
            print(f"   ID: {tool_data.get('id', 'N/A')}")
            print(f"   Agent: {agent_name}")
            print("=" * 80)
        else:
            print(f"\n❌ Failed to create tool: {message}")
            print("   Do you want to try again or continue?")

        # Ask if user wants to create another tool
        print("\n" + "-" * 80)
        continue_creating = input("🔄 Create another tool? (y/n): ").strip().lower()

        if continue_creating not in ("y", "yes", ""):
            break

    return tools_created


def create_tools_from_list(
    bearer_token: str, agent_name: str, agent_info: Dict[str, Any], api_ids: List[str]
) -> int:
    """
    Create tools from a list of API IDs.
    New workflow: Fetch API details → Use API title → Generate description → Confirm → Create

    Args:
        bearer_token: Authentication token
        agent_name: Name of the agent
        agent_info: Agent information dictionary
        api_ids: List of API IDs to create tools for

    Returns:
        Number of tools created
    """
    agent_path = agent_info.get("path", "")
    tool_creator = ToolCreator(bearer_token)
    api_manager = APIManager(bearer_token)
    tools_created = 0

    print("\n" + "=" * 80)
    print("🚀 START CREATING TOOLS FROM LIST")
    print("=" * 80)
    print(f"Agent: {agent_name}")
    print(f"API IDs to process: {len(api_ids)}")
    print("=" * 80)

    for idx, api_id in enumerate(api_ids, 1):
        print("\n" + "-" * 80)
        print(f"📝 CREATING TOOL #{idx} of {len(api_ids)}")
        print(f"   API ID: {api_id}")
        print("-" * 80)

        # Step 1: Check if a tool already exists for this API (before generating description)
        print(f"\n🔍 Checking if tool already exists for API: {api_id}...")
        exists, existing_tool = tool_creator.check_tool_exists_for_api(api_id)

        if exists and existing_tool:
            tool_title = existing_tool.get("title", "Unknown")
            tool_id = existing_tool.get("action_id", existing_tool.get("id", "Unknown"))
            print("⚠️  A tool already exists for this API!")
            print(f"   Tool: {tool_title}")
            print(f"   ID: {tool_id}")
            print("   Skipping to next tool...")
            continue

        # Step 2: Fetch detailed API information
        api_details = api_manager.fetch_api_details(api_id)

        # Step 3: Use API title as tool name (with option to edit)
        api_name = api_details.get("title", "Tool") if api_details else "Tool"

        print(f"\n📝 Suggested tool name from API: {api_name}")
        use_suggested = (
            input("   Use this name? (y/n, or press Enter for yes): ").strip().lower()
        )

        if use_suggested in ("n", "no"):
            tool_name = get_tool_name_input()
            if tool_name is None:
                print("\n⚠️  Skipping this tool...")
                continue
        else:
            tool_name = api_name
            # Validate length
            if len(tool_name) > 64:
                print(f"⚠️  API name too long ({len(tool_name)} chars, max 64).")
                tool_name = get_tool_name_input()
                if tool_name is None:
                    print("\n⚠️  Skipping this tool...")
                    continue

        print(f"✅ Using tool name: {tool_name}")

        # Step 4: Generate description using LLM (if available)
        generated_description = None
        if api_details:
            generated_description = tool_creator.generate_description_from_api(
                api_details
            )

        # Step 5: Get user confirmation/editing of description
        if generated_description:
            tool_description = get_tool_description_input(
                initial_description=generated_description
            )
        else:
            print("\n⚠️  Could not generate description automatically.")
            tool_description = get_tool_description_input()

        if tool_description is None:
            print("\n⚠️  Skipping this tool...")
            continue

        # Step 6: Create the tool
        success, tool_data, message = tool_creator.create_tool(
            title=tool_name,
            description=tool_description,
            api_id=api_id,
            api_details=api_details,
            auto_complete=True,
        )

        if success and tool_data:
            tools_created += 1

            # Save to agent
            tool_creator.save_tool_to_agent(agent_name, agent_path, tool_data)

            print("\n" + "=" * 80)
            print(f"✅ TOOL {idx}/{len(api_ids)} CREATED SUCCESSFULLY!")
            print("=" * 80)
            print(f"   Title: {tool_data.get('title', 'N/A')}")
            print(f"   ID: {tool_data.get('id', 'N/A')}")
            print(f"   API ID: {api_id}")
            print(f"   Agent: {agent_name}")
            print("=" * 80)
        else:
            print(f"\n❌ Failed to create tool: {message}")

            # Ask if user wants to continue
            continue_creating = (
                input("\n🔄 Continue with remaining API IDs? (y/n): ").strip().lower()
            )
            if continue_creating not in ("y", "yes", ""):
                print("\n⚠️  Stopping tool creation...")
                break

    return tools_created


def create_tool_non_interactive(
    bearer_token: str,
    agent_name: str,
    agent_path: str,
    api_id: str,
    tool_name: Optional[str] = None,
    tool_description: Optional[str] = None,
    auto_complete: bool = True,
) -> Tuple[bool, Optional[Dict[str, Any]], str]:
    """
    Create a tool without user interaction.

    Args:
        bearer_token: Authentication token
        agent_name: Name of the agent
        agent_path: Path to the agent directory
        api_id: API ID to create tool for
        tool_name: Optional tool name (will use API title if not provided)
        tool_description: Optional tool description (will be auto-generated if not provided)
        auto_complete: Whether to complete all setup steps automatically

    Returns:
        Tuple of (success: bool, tool_data: dict or None, message: str)
    """
    tool_creator = ToolCreator(bearer_token)
    api_manager = APIManager(bearer_token)

    print(f"\n📝 Creating tool for API: {api_id}")

    # Check if a tool already exists for this API (before generating description)
    print(f"🔍 Checking if tool already exists for API: {api_id}...")
    exists, existing_tool = tool_creator.check_tool_exists_for_api(api_id)

    if exists and existing_tool:
        tool_title = existing_tool.get("title", "Unknown")
        tool_id = existing_tool.get("action_id", existing_tool.get("id", "Unknown"))
        warning_msg = (
            f"A tool already exists for this API: {tool_title} (ID: {tool_id})"
        )
        print(f"⚠️  {warning_msg}")
        print("   Skipping creation...")
        return False, None, warning_msg

    # Fetch API details
    api_details = api_manager.fetch_api_details(api_id)
    if not api_details:
        return False, None, f"Failed to fetch API details for {api_id}"

    # Use API title as tool name if not provided
    if not tool_name:
        tool_name = api_details.get("title") or api_details.get("name") or "Tool"
        print(f"   Using API title as tool name: {tool_name}")

    # Validate tool name length
    if tool_name and len(tool_name) > 64:
        tool_name = tool_name[:64]
        print(f"   Tool name truncated to 64 chars: {tool_name}")

    # Ensure we have a valid tool name
    if not tool_name:
        tool_name = "Tool"

    # Generate description if not provided
    if not tool_description:
        if tool_creator.openai_client:
            print("   Generating tool description...")
            tool_description = tool_creator.generate_description_from_api(api_details)
            if not tool_description:
                # Fallback to API description
                fallback = api_details.get("description")
                tool_description = fallback if fallback else f"Tool for {tool_name}"
                if tool_description:
                    preview = (
                        tool_description[:100]
                        if len(tool_description) > 100
                        else tool_description
                    )
                    print(f"   Using fallback description: {preview}...")
        else:
            # Use API description as fallback
            fallback = api_details.get("description")
            tool_description = fallback if fallback else f"Tool for {tool_name}"
            if tool_description:
                preview = (
                    tool_description[:100]
                    if len(tool_description) > 100
                    else tool_description
                )
                print(f"   Using API description: {preview}...")

    # Ensure we have a valid description
    if not tool_description:
        tool_description = f"Tool for {tool_name}"

    # Create the tool
    success, tool_data, message = tool_creator.create_tool(
        title=tool_name,
        description=tool_description,
        api_id=api_id,
        api_details=api_details,
        auto_complete=auto_complete,
    )

    if success and tool_data:
        # Save to agent
        tool_creator.save_tool_to_agent(agent_name, agent_path, tool_data)
        print(f"✅ Tool created successfully: {tool_name}")
    else:
        print(f"❌ Failed to create tool: {message}")

    return success, tool_data, message


def create_tools_batch_non_interactive(
    bearer_token: str,
    agent_name: str,
    agent_path: str,
    api_ids: List[str],
    auto_complete: bool = True,
) -> Tuple[int, int]:
    """
    Create multiple tools in batch mode without user interaction.

    Args:
        bearer_token: Authentication token
        agent_name: Name of the agent
        agent_path: Path to the agent directory
        api_ids: List of API IDs to create tools for
        auto_complete: Whether to complete all setup steps automatically

    Returns:
        Tuple of (successful: int, failed: int)
    """
    successful = 0
    failed = 0

    print("\n" + "=" * 80)
    print("🚀 BATCH TOOL CREATION (NON-INTERACTIVE)")
    print("=" * 80)
    print(f"Agent: {agent_name}")
    print(f"API IDs to process: {len(api_ids)}")
    print("=" * 80)

    for idx, api_id in enumerate(api_ids, 1):
        print(f"\n[{idx}/{len(api_ids)}] Processing API: {api_id}")
        print("-" * 80)

        success, tool_data, message = create_tool_non_interactive(
            bearer_token=bearer_token,
            agent_name=agent_name,
            agent_path=agent_path,
            api_id=api_id,
            auto_complete=auto_complete,
        )

        if success:
            successful += 1
        else:
            failed += 1

    # Summary
    print("\n" + "=" * 80)
    print("📊 BATCH CREATION SUMMARY")
    print("=" * 80)
    print(f"Agent: {agent_name}")
    print(f"Tools created: {successful}")
    print(f"Failed: {failed}")
    print(f"Total processed: {len(api_ids)}")
    print("=" * 80)

    return successful, failed


def create_tools_interactive(bearer_token: str) -> None:
    """
    Interactive tool creation workflow.

    Args:
        bearer_token: The authentication bearer token
    """
    print("\n" + "=" * 80)
    print("🔨 CREATE TOOLS")
    print("=" * 80)
    print("Create custom tools by associating them with available APIs.")
    print("Each tool will be saved to your selected agent.")
    print("=" * 80)

    # Step 1: Select agent
    agent_name, agent_info = select_agent_interactive("create tools in")

    if not agent_name or not agent_info:
        print("\n⚠️  Tool creation cancelled - no agent selected.\n")
        return

    # Step 2: Choose creation mode
    print("\n" + "=" * 80)
    print("📋 CHOOSE CREATION MODE")
    print("=" * 80)
    print("1. One by one       - Browse and pick APIs interactively")
    print("2. From list        - Paste API IDs with confirmation for each")
    print("3. Batch (no input) - Paste API IDs, auto-create all without prompts")
    print("=" * 80)

    while True:
        mode = input("\n👉 Choose mode (1, 2, or 3): ").strip()

        if mode == "1":
            # Mode 1: One by one with API browsing
            print("\n📚 Fetching APIs for browsing...")
            api_manager = APIManager(bearer_token)

            if not api_manager.fetch_apis():
                print("\n❌ Failed to fetch APIs. Cannot proceed with tool creation.\n")
                return

            if not api_manager.apis:
                print("\n❌ No APIs available. Cannot create tools.\n")
                return

            tools_created = create_tools_one_by_one(
                bearer_token, agent_name, agent_info, api_manager
            )
            break

        elif mode == "2":
            # Mode 2: Paste list of API IDs with confirmation
            api_ids = get_api_ids_from_paste()

            if not api_ids:
                print("\n⚠️  No API IDs provided. Tool creation cancelled.\n")
                return

            tools_created = create_tools_from_list(
                bearer_token, agent_name, agent_info, api_ids
            )
            break

        elif mode == "3":
            # Mode 3: Batch non-interactive mode
            api_ids = get_api_ids_from_paste()

            if not api_ids:
                print("\n⚠️  No API IDs provided. Tool creation cancelled.\n")
                return

            print(
                "\n⚠️  BATCH MODE: Tools will be created automatically without further prompts."
            )
            print(
                "   - Tool names will use API titles (truncated to 64 chars if needed)"
            )
            print("   - Descriptions will be auto-generated using LLM")
            print("   - No manual confirmation or editing will be requested")

            confirm = input("\n👉 Proceed with batch creation? (y/n): ").strip().lower()
            if confirm not in ("y", "yes", ""):
                print("\n⚠️  Batch creation cancelled.\n")
                return

            agent_path = agent_info.get("path", "")
            successful, failed = create_tools_batch_non_interactive(
                bearer_token, agent_name, agent_path, api_ids
            )
            tools_created = successful
            break

        else:
            print("⚠️  Invalid choice. Please enter 1, 2, or 3.")

    # Summary
    print("\n" + "=" * 80)
    print("📊 TOOL CREATION SUMMARY")
    print("=" * 80)
    print(f"Agent: {agent_name}")
    print(f"Tools created this session: {tools_created}")
    print(f"Total tools in agent: {agent_info.get('tools_count', 0) + tools_created}")
    print("=" * 80 + "\n")


if __name__ == "__main__":
    # For testing purposes
    print("This module provides tool creation functionality for AdoptAI.")
    print("It is designed to be imported and used by tool_builder.py")
    print("\nTo use this feature, run: poetry run tool-builder")
