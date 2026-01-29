#!/usr/bin/env python3
"""
Create a new ADOPT zaction from an API ID.

This script:
1. Takes an api_id as input
2. Downloads API details from connect.adopt.ai
3. Extracts the API title for the zaction title
4. Uses the content from new_tool_description.md as the description
5. Creates the zaction and completes all setup steps
"""

import os
import sys
import json
import requests
from pathlib import Path
from typing import Dict, Any, Optional, Tuple, List
from time import sleep

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from cli.auth import get_bearer_token

# Try to import Anthropic for WDL generation
try:
    from anthropic import Anthropic
    ANTHROPIC_AVAILABLE = True
except ImportError:
    ANTHROPIC_AVAILABLE = False
    Anthropic = None  # type: ignore


def load_description_from_file() -> str:
    """
    Load the description content from new_tool_description.md
    
    Returns:
        The description content as a string
    """
    try:
        description_path = Path(__file__).parent.parent / "prompts" / "templates" / "new_tool_description.md"
        if description_path.exists():
            with open(description_path, 'r') as f:
                return f.read().strip()
        else:
            # Fallback default description
            return "Create a simple WDL with just two blocks. One for REST and one for OUTPUT_TEXT. Do not create any other blocks.\nCall the given API in the REST block and return the output from OUTPUT_TEXT as raw."
    except Exception as e:
        print(f"⚠️  Warning: Could not load new_tool_description.md: {e}")
        return "Create a simple WDL with just two blocks. One for REST and one for OUTPUT_TEXT. Do not create any other blocks.\nCall the given API in the REST block and return the output from OUTPUT_TEXT as raw."


def load_tool_description_guidelines() -> str:
    """
    Load the tool description guidelines from tool_description.md
    
    Returns:
        The guidelines content as a string
    """
    try:
        guidelines_path = Path(__file__).parent.parent / "prompts" / "templates" / "tool_description.md"
        if guidelines_path.exists():
            with open(guidelines_path, 'r') as f:
                return f.read().strip()
        else:
            return "Generate a WDL following REST -> JQ_FILTER/EXTRACT/PROJECT -> OUTPUT_TEXT pattern with required_inputs."
    except Exception as e:
        print(f"⚠️  Warning: Could not read tool_description.md: {e}")
        return "Generate a WDL following REST -> JQ_FILTER/EXTRACT/PROJECT -> OUTPUT_TEXT pattern with required_inputs."


def generate_api_description(
    api_details: Dict[str, Any]
) -> Optional[str]:
    """
    Generate a short description of the API using Claude, focusing on inputs and outputs.
    
    Args:
        api_details: Dictionary containing API details
        
    Returns:
        A short description string (max 300 chars) or None if generation failed
    """
    if not ANTHROPIC_AVAILABLE or Anthropic is None:
        print("   ⚠️  Warning: Anthropic library not available. Cannot generate description.")
        return None
    
    api_key = os.getenv('ANTHROPIC_API_KEY')
    if not api_key:
        print("   ⚠️  Warning: ANTHROPIC_API_KEY not found. Cannot generate description.")
        return None
    
    # Build the prompt with raw API details
    api_details_json = json.dumps(api_details, indent=2, default=str)
    
    prompt = f"""Based on the following API details, generate a SHORT description (maximum 300 characters) that explains:
1. What inputs the API expects
2. What outputs it returns and their format

API Details:
{api_details_json}

IMPORTANT:
- Keep it under 300 characters
- Be concise and direct
- Focus only on inputs and outputs
- Do not include any markdown or formatting
- Return ONLY the description text, nothing else"""

    try:
        client = Anthropic(api_key=api_key)
        
        print("   🤖 Generating API description using Claude...")
        
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=200,
            temperature=0,
            messages=[
                {
                    "role": "user",
                    "content": prompt
                }
            ]
        )
        
        description = response.content[0].text.strip()  # type: ignore
        
        # Ensure it's under 300 characters
        if len(description) > 300:
            description = description[:297] + "..."
        
        print(f"   ✅ Description generated ({len(description)} chars)")
        return description
        
    except Exception as e:
        print(f"   ❌ Error generating description: {e}")
        return None


def update_zaction_description(
    bearer_token: str,
    action_id: str,
    new_description: str,
    actions_endpoint: Optional[str] = None
) -> Tuple[bool, str]:
    """
    Update the description of a zaction via PATCH endpoint.
    
    Args:
        bearer_token: The authentication bearer token
        action_id: The action ID to update
        new_description: The new description to set
        actions_endpoint: The actions API endpoint (defaults to ADAPT_ACTIONS_ENDPOINT env var)
        
    Returns:
        Tuple of (success: bool, message: str)
    """
    actions_endpoint = actions_endpoint or os.getenv('ADAPT_ACTIONS_ENDPOINT', 'https://api.adopt.ai')
    
    url = f"{actions_endpoint}/v1/actions/{action_id}/description"
    headers = {
        "Authorization": f"Bearer {bearer_token}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "new_description": new_description
    }
    
    try:
        print("   ⏳ Updating zaction description...")
        response = requests.patch(url, headers=headers, json=payload, timeout=30)
        
        if response.status_code not in (200, 201, 204):
            error_msg = f"Failed to update description. Status code: {response.status_code}\n   Response: {response.text}"
            return False, error_msg
        
        print("   ✅ Description updated successfully")
        return True, "Description updated successfully"
        
    except requests.exceptions.RequestException as e:
        return False, f"Network error while updating description: {e}"


def update_zaction_api_ids(
    bearer_token: str,
    action_id: str,
    api_ids: List[str],
    actions_endpoint: Optional[str] = None
) -> Tuple[bool, str]:
    """
    Forcefully associate a zaction with specific API IDs via PATCH endpoint.
    
    Args:
        bearer_token: The authentication bearer token
        action_id: The action ID to update
        api_ids: List of API IDs to associate with the zaction
        actions_endpoint: The actions API endpoint (defaults to ADAPT_ACTIONS_ENDPOINT env var)
        
    Returns:
        Tuple of (success: bool, message: str)
    """
    actions_endpoint = actions_endpoint or os.getenv('ADAPT_ACTIONS_ENDPOINT', 'https://api.adopt.ai')
    
    url = f"{actions_endpoint}/v1/actions/{action_id}/documented-api-ids"
    headers = {
        "Authorization": f"Bearer {bearer_token}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "api_ids": api_ids
    }
    
    try:
        print(f"   ⏳ Associating zaction with API ID(s): {api_ids}...")
        response = requests.patch(url, headers=headers, json=payload, timeout=30)
        
        if response.status_code not in (200, 201, 204):
            error_msg = f"Failed to update API IDs. Status code: {response.status_code}\n   Response: {response.text}"
            return False, error_msg
        
        print("   ✅ API ID(s) associated successfully")
        return True, "API IDs updated successfully"
        
    except requests.exceptions.RequestException as e:
        return False, f"Network error while updating API IDs: {e}"


def generate_wdl(
    tool_title: str,
    tool_description: str,
    api_details: Optional[Dict[str, Any]] = None
) -> List[Dict[str, Any]]:
    """
    Generate a WDL for a tool based on the tool data and api details using Claude Sonnet.
    
    Args:
        tool_title: The title of the tool
        tool_description: The description of the tool
        api_details: Optional dictionary containing API details
        
    Returns:
        List of dictionaries with the WDL blocks
    """
    if not ANTHROPIC_AVAILABLE or Anthropic is None:
        print("   ⚠️  Warning: Anthropic library not available. Using empty WDL.")
        return []
    
    # Get the tool_description.md content as the prompt guidelines
    guidelines = load_tool_description_guidelines()
    
    # Build the API details context
    api_context = ""
    if api_details:
        api_context = "\n## API Details\n\n"
        api_context += f"### API: {api_details.get('title', 'Untitled API')}\n\n"
        api_context += f"- **Base URL**: {api_details.get('base_url', 'N/A')}\n"
        api_context += f"- **Description**: {api_details.get('description', 'No description')}\n"
        
        # Include endpoints if available
        endpoints = api_details.get('endpoints', [])
        if endpoints:
            api_context += f"- **Endpoints** ({len(endpoints)} total):\n\n"
            for endpoint in endpoints[:5]:  # Limit to first 5 endpoints
                method = endpoint.get('method', 'GET')
                path = endpoint.get('path', '')
                ep_description = endpoint.get('description', '')
                api_context += f"  - `{method} {path}`: {ep_description}\n"
                
                # Include parameters if available
                parameters = endpoint.get('parameters', [])
                if parameters:
                    api_context += "    - Parameters:\n"
                    for param in parameters[:10]:  # Limit parameters
                        param_name = param.get('name', '')
                        param_type = param.get('type', '')
                        param_required = param.get('required', False)
                        param_desc = param.get('description', '')
                        required_text = "required" if param_required else "optional"
                        api_context += f"      - `{param_name}` ({param_type}, {required_text}): {param_desc}\n"
            
            if len(endpoints) > 5:
                api_context += f"  - ... and {len(endpoints) - 5} more endpoints\n"
        
        api_context += "\n"
    
    # Build the prompt
    prompt = f"""You are an expert at generating WDL (Workflow Definition Language) for tool actions.

{guidelines}

## Tool Information

**Title**: {tool_title}

**Description**: {tool_description}

{api_context}

## Task

Based on the guidelines above, the tool information, and the API details provided, generate a complete WDL that:

1. Uses exactly ONE REST operation to call the appropriate API endpoint
2. Optionally uses JQ_FILTER, EXTRACT, or PROJECT operations to process the response
3. Uses OUTPUT_TEXT with raw: true to display the results
4. Includes a required_inputs block listing all necessary inputs with proper types and definitions

**IMPORTANT**: 
- Return ONLY the valid JSON array representing the WDL blocks
- Do not include any markdown code fences, explanations, or additional text
- The output must be valid JSON that can be parsed directly
- Follow the exact format shown in the examples in the guidelines
- Make sure all string values in the JSON use proper escaping
- Use workflow_arguments for dynamic values like: {{{{workflow_arguments.parameter_name}}}}
- Mark inputs as (mandatory) or (optional) in their definitions
- Set default values for optional inputs
"""

    # Initialize Anthropic client
    api_key = os.getenv('ANTHROPIC_API_KEY')
    if not api_key:
        print("   ⚠️  Warning: ANTHROPIC_API_KEY not found. Using empty WDL.")
        return []
    
    try:
        client = Anthropic(api_key=api_key)
        
        print("   🤖 Generating WDL using Claude Sonnet...")
        
        # Call Claude API
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=4096,
            temperature=0,
            messages=[
                {
                    "role": "user",
                    "content": prompt
                }
            ]
        )
        
        # Extract the response
        response_text = response.content[0].text.strip()  # type: ignore
        
        # Try to parse the JSON response
        try:
            wdl_blocks = json.loads(response_text)
            
            if not isinstance(wdl_blocks, list):
                raise ValueError("WDL response is not a list")
            
            print(f"   ✅ WDL generated successfully with {len(wdl_blocks)} blocks")
            
            return wdl_blocks
            
        except json.JSONDecodeError as e:
            print(f"   ❌ Failed to parse WDL JSON: {e}")
            print(f"   📄 Response text (first 500 chars): {response_text[:500]}")
            
            return []
        
    except Exception as e:
        print(f"   ❌ Error calling Claude API: {e}")
        
        return []


def fetch_api_details(
    bearer_token: str,
    api_id: str,
    api_endpoint: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """
    Fetch detailed API information from connect.adopt.ai.
    
    Args:
        bearer_token: The authentication bearer token
        api_id: The API ID to fetch details for
        api_endpoint: The API endpoint (defaults to ADOPT_API_ENDPOINT env var)
        
    Returns:
        Dictionary containing detailed API information, or None if failed
    """
    api_endpoint = api_endpoint or os.getenv('ADAPT_ACTIONS_ENDPOINT', 'https://api.adopt.ai')
    
    url = f"{api_endpoint}/v1/documented-apis/{api_id}"
    headers = {
        "Authorization": f"Bearer {bearer_token}",
        "Content-Type": "application/json"
    }
    
    try:
        print(f"⏳ Fetching API details for {api_id}...")
        response = requests.get(url, headers=headers, timeout=30)
        
        if response.status_code != 200:
            print(f"❌ Failed to fetch API details. Status code: {response.status_code}")
            print(f"   Response: {response.text}")
            return None
        
        api_details = response.json()
        print("✅ API details fetched successfully")
        return api_details
        
    except requests.exceptions.RequestException as e:
        print(f"❌ Network error while fetching API details: {e}")
        return None


def fetch_tool_details(
    bearer_token: str,
    action_id: str,
    actions_endpoint: Optional[str] = None,
    check_updated_at: Optional[str] = None
) -> Tuple[bool, Optional[Dict[str, Any]], str]:
    """
    Fetch tool details from the AdoptAI API with enhanced retry logic.
    
    Phase 1: Exponential backoff for initial attempts
    Phase 2: Check every 30 seconds after that
    
    Args:
        bearer_token: The authentication bearer token
        action_id: The action ID to fetch details for
        actions_endpoint: The actions API endpoint (defaults to ADAPT_ACTIONS_ENDPOINT env var)
        check_updated_at: Optional previous updated_at timestamp (format: "2025-12-02T15:37:19").
                         If provided, will check if updated_at has changed and return early.
        
    Returns:
        Tuple of (success: bool, tool_data: dict or None, message: str)
    """
    from datetime import datetime
    
    actions_endpoint = actions_endpoint or os.getenv('ADAPT_ACTIONS_ENDPOINT', 'https://api.adopt.ai')
    
    # Parse previous timestamp if provided
    previous_timestamp = None
    if check_updated_at:
        try:
            previous_timestamp = datetime.fromisoformat(check_updated_at)
            print(f"   ⏳ Waiting for updated_at to change from: {check_updated_at}")
        except Exception as e:
            print(f"   ⚠️  Could not parse timestamp, using status-based checking: {e}")
            previous_timestamp = None
    
    # Phase 1: Exponential backoff for first attempts
    phase1_wait_times = [5, 10, 15, 20]
    
    for i, wait_time in enumerate(phase1_wait_times, 1):
        try:
            url = f"{actions_endpoint}/v1/actions/{action_id}/current/"
            headers = {
                "Authorization": f"Bearer {bearer_token}",
                "Content-Type": "application/json"
            }
            response = requests.get(url, headers=headers, timeout=30)
            response_json = response.json()
            status = response_json.get('status', 'error')
            is_regenerating = response_json.get('is_regenerating', False)
            current_updated_at = response_json.get('updated_at')
            
            # If check_updated_at provided, check if timestamp has changed
            if previous_timestamp and current_updated_at:
                try:
                    current_timestamp = datetime.fromisoformat(current_updated_at)
                    if current_timestamp > previous_timestamp:
                        print(f"   ✅ updated_at changed: {check_updated_at} → {current_updated_at}")
                        return True, response_json, "Tool details fetched successfully (updated_at changed)"
                    else:
                        print(f"   ⏳ updated_at unchanged, waiting... (attempt {i}/{len(phase1_wait_times)})")
                except Exception:
                    pass
            
            # Standard status checking
            if status == 'error' or is_regenerating:
                print(f"   ⚠️  Tool details fetch: Status={status}, Is regenerating={is_regenerating}. Retrying in {wait_time}s... (attempt {i}/{len(phase1_wait_times)})")
                sleep(wait_time)
                continue
            
            # Return on success if not checking updated_at
            if not previous_timestamp:
                return True, response_json, "Tool details fetched successfully"
            
            # If checking updated_at but it hasn't changed yet, wait
            sleep(wait_time)
            
        except Exception as e:
            print(f"   ❌ Error fetching tool details: {e}")
            if i < len(phase1_wait_times):
                print(f"      Retrying in {wait_time} seconds...")
                sleep(wait_time)
                continue
            else:
                break
    
    # Phase 2: Check every 30 seconds
    phase2_retries = 10
    phase2_wait_time = 30
    
    print(f"   ⏳ Switching to regular polling (every {phase2_wait_time} seconds)...")
    
    for i in range(phase2_retries):
        try:
            url = f"{actions_endpoint}/v1/actions/{action_id}/current/"
            headers = {
                "Authorization": f"Bearer {bearer_token}",
                "Content-Type": "application/json"
            }
            response = requests.get(url, headers=headers, timeout=30)
            response_json = response.json()
            status = response_json.get('status', 'error')
            is_regenerating = response_json.get('is_regenerating', True)
            current_updated_at = response_json.get('updated_at')
            
            # If check_updated_at provided, check if timestamp has changed
            if previous_timestamp and current_updated_at:
                try:
                    current_timestamp = datetime.fromisoformat(current_updated_at)
                    if current_timestamp > previous_timestamp:
                        print(f"   ✅ updated_at changed: {check_updated_at} → {current_updated_at}")
                        return True, response_json, "Tool details fetched successfully (updated_at changed)"
                except Exception:
                    pass
            
            # Standard status checking
            if status == 'error' or is_regenerating:
                print(f"      Still processing... (polling attempt {i+1}/{phase2_retries})")
                sleep(phase2_wait_time)
                continue
            
            # Return on success if not checking updated_at
            if not previous_timestamp:
                return True, response_json, "Tool details fetched successfully"
            
            sleep(phase2_wait_time)
            
        except Exception as e:
            print(f"   ❌ Error fetching tool details: {e}")
            return False, None, f"Error fetching tool details: {e}"
    
    return False, None, "Failed to fetch tool details after multiple retries"


def create_zaction(
    bearer_token: str,
    title: str,
    description: str,
    api_id: str,
    actions_endpoint: Optional[str] = None
) -> Tuple[bool, Optional[Dict[str, Any]], str]:
    """
    Create a new zaction via the AdoptAI API.
    
    Args:
        bearer_token: The authentication bearer token
        title: Tool title (max 64 chars)
        description: Tool description
        api_id: API UUID to associate with the tool
        actions_endpoint: The actions API endpoint (defaults to ADAPT_ACTIONS_ENDPOINT env var)
        
    Returns:
        Tuple of (success: bool, tool_data: dict or None, message: str)
    """
    actions_endpoint = actions_endpoint or os.getenv('ADAPT_ACTIONS_ENDPOINT', 'https://api.adopt.ai')
    
    # Validate inputs
    if not title or not title.strip():
        return False, None, "Tool title cannot be empty"
    
    title = title.strip()
    if len(title) > 64:
        title = title[:64]
        print(f"   ⚠️  Title truncated to 64 chars: {title}")
    
    if not description or not description.strip():
        return False, None, "Tool description cannot be empty"
    
    if not api_id or not api_id.strip():
        return False, None, "API ID cannot be empty"
    
    # Prepare the request
    url = f"{actions_endpoint}/v1/actions/"
    headers = {
        "Authorization": f"Bearer {bearer_token}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "title": title,
        "description": description.strip(),
        "api_ids": [api_id.strip()]
    }
    
    try:
        print(f"\n⏳ Creating zaction '{title}'...")
        response = requests.post(url, headers=headers, json=payload, timeout=30)
        
        if response.status_code not in (200, 201):
            error_msg = f"Failed to create zaction. Status code: {response.status_code}\n   Response: {response.text}"
            return False, None, error_msg
        
        tool_data = response.json()
        action_id = tool_data.get('action_id', "")
        
        print("✅ Zaction created successfully!")
        print(f"   Action ID: {action_id}")
        
        return True, tool_data, "Zaction created successfully"
        
    except requests.exceptions.RequestException as e:
        return False, None, f"Network error while creating zaction: {e}"


def set_deployment_rules(
    bearer_token: str,
    action_id: str,
    actions_endpoint: Optional[str] = None
) -> Tuple[bool, str]:
    """
    Set deployment rules for a tool to mark it as tool mode.
    
    Args:
        bearer_token: The authentication bearer token
        action_id: The action ID to set deployment rules for
        actions_endpoint: The actions API endpoint (defaults to ADAPT_ACTIONS_ENDPOINT env var)
        
    Returns:
        Tuple of (success: bool, message: str)
    """
    actions_endpoint = actions_endpoint or os.getenv('ADAPT_ACTIONS_ENDPOINT', 'https://api.adopt.ai')
    
    url = f"{actions_endpoint}/v1/actions/{action_id}/deployment-rules"
    headers = {
        "Authorization": f"Bearer {bearer_token}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "rules": [],
        "is_visible_in_list": True,
        "is_tool_mode": True
    }
    
    try:
        print("   ⏳ Setting deployment rules...")
        response = requests.post(url, headers=headers, json=payload, timeout=30)
        
        if response.status_code not in (200, 201):
            error_msg = f"Failed to set deployment rules. Status code: {response.status_code}\n   Response: {response.text}"
            return False, error_msg
        
        print("   ✅ Deployment rules set successfully")
        return True, "Deployment rules set successfully"
        
    except requests.exceptions.RequestException as e:
        return False, f"Network error while setting deployment rules: {e}"


def patch_wdl(wdl: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Patch the WDL to fix required_inputs field formatting.
    
    This function ensures that required_inputs JSON strings are properly formatted
    with double quotes (not single quotes) and consistent JSON structure.
    
    Args:
        wdl: The WDL data (list of blocks)
        
    Returns:
        The patched WDL with properly formatted required_inputs
    """
    for i, block in enumerate(wdl):
        if "required_inputs" in block and block['required_inputs']:
            new_required_inputs = []
            for input_str in block['required_inputs']:
                if "'" in input_str:
                    print(f"   🔨 Input string contains unescaped single quotes: {input_str}")
                    # Skip malformed input strings
                    continue
                try:
                    input_json = json.loads(input_str)
                    # Re-dump to normalize JSON formatting
                    new_required_inputs.append(json.dumps(input_json))
                    print(f"   🔨 Formatted required input: {list(input_json.keys())}")
                except json.JSONDecodeError as e:
                    print(f"   ⚠️  Could not parse required_input JSON: {e}")
                    # Keep original if parsing fails
                    new_required_inputs.append(input_str)
            
            if new_required_inputs:
                print(f"   🔨 Patched {len(new_required_inputs)} required input(s) in block {i}")
                wdl[i]['required_inputs'] = new_required_inputs
    
    return wdl


def publish_widdle(
    bearer_token: str,
    action_id: str,
    draft_id: Optional[str],
    widdle_data: List[Dict[str, Any]],
    actions_endpoint: Optional[str] = None
) -> Tuple[bool, str]:
    """
    Publish widdle to upstream service.
    
    Args:
        bearer_token: The authentication bearer token
        action_id: The action ID
        draft_id: The draft ID (can be None)
        widdle_data: The widdle data to publish
        actions_endpoint: The actions API endpoint (defaults to ADAPT_ACTIONS_ENDPOINT env var)
        
    Returns:
        Tuple of (success: bool, message: str)
    """
    actions_endpoint = actions_endpoint or os.getenv('ADAPT_ACTIONS_ENDPOINT', 'https://api.adopt.ai')
    
    url = f"{actions_endpoint}/v1/actions/wdl-update"
    headers = {
        "Authorization": f"Bearer {bearer_token}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "action_id": action_id,
        "draft_id": draft_id,
        "wdl": widdle_data
    }
    
    try:
        print("   ⏳ Publishing widdle to upstream...")
        response = requests.post(url, headers=headers, json=payload, timeout=30)
        
        if response.status_code not in (200, 201):
            error_msg = f"Failed to publish widdle. Status code: {response.status_code}\n   Response: {response.text}"
            return False, error_msg
        
        print("   ✅ Widdle published successfully")
        return True, "Widdle published successfully"
        
    except requests.exceptions.RequestException as e:
        return False, f"Network error while publishing widdle: {e}"


def populate_instructions(
    bearer_token: str,
    action_id: str,
    actions_endpoint: Optional[str] = None
) -> Tuple[bool, str]:
    """
    Populate instructions for an action.
    
    Args:
        bearer_token: The authentication bearer token
        action_id: The action ID
        actions_endpoint: The actions API endpoint (defaults to ADAPT_ACTIONS_ENDPOINT env var)
        
    Returns:
        Tuple of (success: bool, message: str)
    """
    actions_endpoint = actions_endpoint or os.getenv('ADAPT_ACTIONS_ENDPOINT', 'https://api.adopt.ai')
    
    url = f"{actions_endpoint}/v1/actions/{action_id}/populate-instructions"
    headers = {
        "Authorization": f"Bearer {bearer_token}",
        "Content-Type": "application/json"
    }
    
    payload = {}
    
    try:
        print("   ⏳ Populating instructions...")
        response = requests.post(url, headers=headers, json=payload, timeout=30)
        
        if response.status_code not in (200, 201):
            error_msg = f"Failed to populate instructions. Status code: {response.status_code}\n   Response: {response.text}"
            return False, error_msg
        
        print("   ✅ Instructions population triggered successfully")
        return True, "Instructions population triggered successfully"
        
    except requests.exceptions.RequestException as e:
        return False, f"Network error while populating instructions: {e}"


def save_draft(
    bearer_token: str,
    action_id: str,
    draft_id: str,
    actions_endpoint: Optional[str] = None
) -> Tuple[bool, Optional[str], str]:
    """
    Save draft action to get a version number.
    
    Args:
        bearer_token: The authentication bearer token
        action_id: The action ID
        draft_id: The draft ID
        actions_endpoint: The actions API endpoint (defaults to ADAPT_ACTIONS_ENDPOINT env var)
        
    Returns:
        Tuple of (success: bool, version_number: str or None, message: str)
    """
    actions_endpoint = actions_endpoint or os.getenv('ADAPT_ACTIONS_ENDPOINT', 'https://api.adopt.ai')
    
    url = f"{actions_endpoint}/v1/actions/{action_id}/draft/{draft_id}/save"
    headers = {
        "Authorization": f"Bearer {bearer_token}",
        "Content-Type": "application/json"
    }
    
    try:
        print("   ⏳ Saving draft action...")
        response = requests.post(url, headers=headers, timeout=30)
        
        if response.status_code not in (200, 201):
            error_msg = f"Failed to save draft. Status code: {response.status_code}\n   Response: {response.text}"
            return False, None, error_msg
        
        response_data = response.json()
        version_number = response_data.get('version_number')
        
        if not version_number:
            return False, None, "No version_number in response"
        
        print(f"   ✅ Draft saved successfully (version: {version_number})")
        return True, version_number, "Draft saved successfully"
        
    except requests.exceptions.RequestException as e:
        return False, None, f"Network error while saving draft: {e}"


def approve_version(
    bearer_token: str,
    action_id: str,
    version_id: str,
    actions_endpoint: Optional[str] = None
) -> Tuple[bool, str]:
    """
    Approve a version of the action.
    
    Args:
        bearer_token: The authentication bearer token
        action_id: The action ID
        version_id: The version ID to approve
        actions_endpoint: The actions API endpoint (defaults to ADAPT_ACTIONS_ENDPOINT env var)
        
    Returns:
        Tuple of (success: bool, message: str)
    """
    actions_endpoint = actions_endpoint or os.getenv('ADAPT_ACTIONS_ENDPOINT', 'https://api.adopt.ai')
    
    url = f"{actions_endpoint}/v1/actions/{action_id}/version/{version_id}"
    headers = {
        "Authorization": f"Bearer {bearer_token}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "status": "approved",
        "change_reason": "creating zaction from API"
    }
    
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


def create_adopt_zaction(api_id: str) -> bool:
    """
    Main workflow to create an ADOPT zaction from an API ID.
    
    This workflow:
    1. Fetches API details from connect.adopt.ai
    2. Extracts the API title for the zaction title
    3. Uses new_tool_description.md content as the description
    4. Creates the zaction
    5. Sets deployment rules
    6. Publishes widdle, populates instructions, saves draft, and approves version
    
    Args:
        api_id: The API ID to create a zaction for
        
    Returns:
        bool: True if successful, False otherwise
    """
    print("\n" + "=" * 80)
    print("🚀 CREATE ADOPT ZACTION")
    print("=" * 80)
    print(f"API ID: {api_id}")
    print("=" * 80)
    
    try:
        # Step 1: Get authentication token
        print("\n🔐 Authenticating with AdoptAI API...")
        bearer_token = get_bearer_token()
        print("✅ Authentication successful")
        
        # Step 2: Fetch API details
        print("\n📚 Step 1: Fetching API details...")
        api_details = fetch_api_details(bearer_token, api_id)
        
        if not api_details:
            print("❌ Failed to fetch API details")
            return False
        
        # Step 3: Extract title from API details
        api_title = api_details.get('title') or api_details.get('name') or 'Untitled'
        print(f"   API Title: {api_title}")
        
        # Step 4: Load description from new_tool_description.md
        description = load_description_from_file()
        print("   Description loaded from new_tool_description.md")
        
        # Step 5: Create the zaction
        print("\n🔧 Step 2: Creating zaction...")
        success, tool_data, message = create_zaction(
            bearer_token=bearer_token,
            title=api_title,
            description=description,
            api_id=api_id
        )
        
        if not success or not tool_data:
            print(f"❌ Failed to create zaction: {message}")
            return False
        
        action_id = tool_data.get('action_id', '')
        if not action_id:
            print("❌ No action_id returned from create zaction")
            return False
        
        # Step 6: Fetch initial tool details
        print("\n📋 Step 3: Fetching initial tool details...")
        success, tool_data, message = fetch_tool_details(bearer_token, action_id)
        
        if not success or not tool_data:
            print(f"❌ Failed to fetch tool details: {message}")
            return False
        
        draft_id = tool_data.get('id', tool_data.get('draft_id', ''))
        if not draft_id:
            print("❌ No draft_id found in tool data")
            return False
        
        print(f"   Draft ID: {draft_id}")
        
        # Step 7: Set deployment rules
        print("\n⚙️  Step 4: Setting deployment rules...")
        success, message = set_deployment_rules(bearer_token, action_id)
        if not success:
            print(f"   ⚠️  Warning: {message}")
        
        # Step 8: Regenerate WDL according to tool_description.md specs and publish
        print("\n📝 Step 5: Generating and publishing widdle...")
        
        # Generate WDL using Claude based on tool_description.md guidelines
        generated_wdl = generate_wdl(
            tool_title=api_title,
            tool_description=description,
            api_details=api_details
        )
        
        # If WDL generation failed, fall back to existing WDL (if any)
        if not generated_wdl:
            print("   ⚠️  WDL generation failed, using existing WDL (if any)...")
            generated_wdl = tool_data.get('wdl', tool_data.get('widdle', []))
        
        # Patch WDL to fix required_inputs formatting
        if generated_wdl:
            print("   🔨 Patching WDL required_inputs formatting...")
            generated_wdl = patch_wdl(generated_wdl)
        
        # Publish the generated WDL with draft_id=None (matching patch_url_params_workflow pattern)
        success, message = publish_widdle(bearer_token, action_id, None, generated_wdl)
        
        if not success:
            print(f"❌ Failed to publish widdle: {message}")
            return False
        
        # Step 9: Immediately fetch to get the post-publish updated_at
        print("\n⏳ Step 6: Fetching tool details immediately after publish...")
        success, tool_data, message = fetch_tool_details(bearer_token, action_id)
        
        if not success or not tool_data:
            print(f"❌ Failed to fetch tool details: {message}")
            return False
        
        # Get the current updated_at as baseline
        current_updated_at = tool_data.get('updated_at')
        print(f"   📌 Baseline updated_at after publish: {current_updated_at}")
        
        # Wait 3 seconds before starting to poll for changes
        print("   ⏳ Waiting 3 seconds before polling for changes...")
        sleep(3)
        
        # Now poll for updated_at to change
        print("   ⏳ Polling for updated_at to change...")
        success, tool_data, message = fetch_tool_details(
            bearer_token,
            action_id,
            check_updated_at=current_updated_at
        )
        
        if not success or not tool_data:
            print(f"❌ Failed to fetch tool details: {message}")
            return False
        
        draft_id = tool_data.get('id', tool_data.get('draft_id', ''))
        if not draft_id:
            print("❌ Draft ID not found after publish")
            return False
        
        # Step 10: Populate instructions (always regenerate after WDL update)
        # Capture current updated_at before populate
        current_updated_at_before_populate = tool_data.get('updated_at')
        
        print("\n📝 Step 7: Populating instructions from updated WDL...")
        success, message = populate_instructions(bearer_token, action_id)
        
        if not success:
            print(f"   ⚠️  Warning: Failed to populate instructions: {message}")
            # Don't fail the entire workflow, continue
        else:
            # Poll for updated_at to change
            print("   ⏳ Polling for updated_at to change...")
            success, tool_data, message = fetch_tool_details(
                bearer_token,
                action_id,
                check_updated_at=current_updated_at_before_populate
            )
            
            if not success or not tool_data:
                print(f"   ⚠️  Warning: Failed to fetch tool details after instructions: {message}")
                # Don't fail, continue with what we have
            else:
                # Update draft_id after populate instructions
                draft_id = tool_data.get('id', tool_data.get('draft_id', ''))
        
        # Step 11: Save draft
        print("\n💾 Step 8: Saving draft...")
        success, version_number, message = save_draft(bearer_token, action_id, draft_id)
        
        if not success:
            print(f"❌ Failed to save draft: {message}")
            return False
        
        # Step 12: Approve version
        if not version_number:
            print("❌ No version number returned from save draft")
            return False
        
        print("\n✅ Step 9: Approving version...")
        success, message = approve_version(bearer_token, action_id, version_number)
        
        if not success:
            print(f"❌ Failed to approve version: {message}")
            return False
        
        # Step 13: Generate and update description based on API details
        print("\n📝 Step 10: Generating and updating zaction description...")
        generated_description = generate_api_description(api_details)
        
        if generated_description:
            success, message = update_zaction_description(
                bearer_token=bearer_token,
                action_id=action_id,
                new_description=generated_description
            )
            if not success:
                print(f"   ⚠️  Warning: {message}")
        else:
            print("   ⚠️  Could not generate description, skipping update...")
        
        # Step 14: Forcefully associate zaction with the requested API ID
        print("\n🔗 Step 11: Associating zaction with API ID...")
        success, message = update_zaction_api_ids(
            bearer_token=bearer_token,
            action_id=action_id,
            api_ids=[api_id]
        )
        if not success:
            print(f"   ⚠️  Warning: {message}")
        
        # Final success message
        print("\n" + "=" * 80)
        print("🎉 ZACTION CREATED SUCCESSFULLY!")
        print("=" * 80)
        print(f"   Title: {api_title}")
        print(f"   Action ID: {action_id}")
        print(f"   Version: {version_number}")
        print(f"   API ID: {api_id}")
        print("=" * 80 + "\n")
        
        return True
        
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    """Main entry point for the create_adopt_zaction script."""
    print("\n" + "=" * 80)
    print("🔧 CREATE ADOPT ZACTION - AdoptAI Tool Builder")
    print("=" * 80)
    print("This script creates a new zaction from an API ID.")
    print("=" * 80 + "\n")
    
    # Get API ID from command line or prompt
    if len(sys.argv) > 1:
        api_id = sys.argv[1].strip()
    else:
        api_id = input("Enter the API ID: ").strip()
    
    if not api_id:
        print("❌ No API ID provided")
        sys.exit(1)
    
    # Create the zaction
    success = create_adopt_zaction(api_id)
    
    if success:
        print("✅ Zaction created successfully!")
        sys.exit(0)
    else:
        print("❌ Failed to create zaction")
        sys.exit(1)


if __name__ == "__main__":
    main()
