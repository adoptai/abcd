#!/usr/bin/env python3
"""
Test an ADOPT zaction by downloading its details and running a test.

This script:
1. Takes an action_id as input
2. Creates a folder with the action_id as the name
3. Downloads the action details from the API
4. Extracts and stores the WDL in widdle.json
5. Extracts and stores the description in description.txt
6. Downloads and stores the API details
7. Copies the adopt_profile.json template for configuration
8. Runs a test with prompt.txt and compares output with API details
"""

import os
import sys
import json
import requests
from pathlib import Path
from typing import Optional, Dict, Any, List, Callable, Type
from pydantic import BaseModel, Field, create_model
from pydantic import SecretStr

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from cli.auth import get_bearer_token
from cli.create_adopt_zaction import fetch_tool_details, fetch_api_details

# =============================================================================
# Environment Configuration (copied from AdoptXchange/examples/read_env.py)
# =============================================================================

from dotenv import load_dotenv
env_path = Path(__file__).parent / '.env'
if env_path.exists():
    load_dotenv(env_path)
    print(f"✅ Loaded environment variables from {env_path}")
else:
    # Try loading from current directory or parent directories
    load_dotenv()

class AdoptAction(BaseModel):
    """Class to define the models for the Adopt API"""
    id: str = Field(default="", description="Action ID")
    title: str = Field(default="", description="Action name")
    description: str = Field(default="", description="Action description")
    required_inputs: List[str] = Field(default_factory=list, description="List of required input parameters")


# =============================================================================
# API Functions (copied from AdoptXchange/examples/action_api_samples/api_sample.py)
# =============================================================================

from langchain_core.messages import HumanMessage, AIMessage


def load_adopt_profile_from_path(profile_path: str) -> Dict[str, Any]:
    """Load the adopt profile configuration from a specified path."""
    try:
        with open(profile_path, 'r') as f:
            profile = json.load(f)
        return profile
    except FileNotFoundError:
        raise ValueError(f"Profile file not found at {profile_path}")
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON in profile file: {e}")


def run_action_by_id(
    action_id: str,
    user_input: str,
    profile: Dict[str, Any],
    workflow_params: Optional[Dict[str, Any]] = None,
    access_token: Optional[str] = None
) -> str:
    """Execute a specific action by its ID.
    
    This function is designed for tool calling scenarios where you want to
    execute a specific action directly by its ID.
    
    Args:
        action_id: The unique ID of the action to execute
        user_input: Natural language description of what to do
        profile: The adopt profile configuration
        adopt_env: Environment configuration
        workflow_params: Optional workflow parameters for actions with required inputs
        access_token: Optional authentication token to reuse
        
    Returns:
        The response from the action execution
    """
    # Get authentication token if not provided
    if access_token is None:
        access_token = get_bearer_token()
        
    url = f"{os.getenv('ADOPT_API_ENDPOINT', 'https://connect.adopt.ai')}/v1/actions/run?include_trace=true"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }
    
    # Create message with user input
    message = HumanMessage(content=user_input)
    
    # Merge workflow_params with profile workflow_params
    combined_workflow_params = {**profile.get("workflow_params", {})}
    if workflow_params:
        combined_workflow_params.update(workflow_params)
    
    # Build request payload with action_id and execution_type
    request_payload = {
        "messages": [message.model_dump()],
        "action_id": action_id,
        "execution_type": "TOOL",
        "base_url": profile.get("base_url", ""),
        "application_base_url": profile.get("application_base_url", ""),
        "workflow_params": combined_workflow_params,
        "security_params": profile.get("security_params", {})
    }
    
    # Debug logging
    print("\n" + "=" * 60)
    print("🔍 DEBUG: Request to Adopt API")
    print("=" * 60)
    print(f"   URL: {request_payload["url"]}")
    print(f"   Headers: {json.dumps({k: v[:50] + '...' if len(str(v)) > 50 else v for k, v in headers.items()}, indent=6)}")
    print(f"   Payload keys: {list(request_payload.keys())}")
    print(f"   action_id: {request_payload['action_id']}")
    print(f"   base_url: {request_payload['base_url']}")
    print(f"   workflow_params: {json.dumps(request_payload['workflow_params'], indent=6)}")
    security_params = request_payload['security_params']
    print(f"   security_params keys: {list(security_params.keys())}")
    # Show truncated cookie for debug
    if 'cookie' in security_params:
        cookie = security_params['cookie']
        print(f"   cookie length: {len(cookie)} chars")
        print(f"   cookie preview: {cookie[:20]}...")
    if 'x-csrftoken' in security_params:
        print(f"   x-csrftoken: {security_params['x-csrftoken'][:10]}")
    if 'referer' in security_params:
        print(f"   referer: {security_params['referer']}")
    print("=" * 60 + "\n")
    
    response = requests.post(url, headers=headers, json=request_payload)

    # Debug response
    print("🔍 DEBUG: Response from Adopt API")
    print(f"   Status code: {response.status_code}")
    print(f"   Response headers: {dict(response.headers)}")
    
    if response.status_code != 200:
        print(f"   Error response: {response.text[:500]}")
        raise ValueError(f"API request failed with status code {response.status_code}: {response.text}")
    
    json_response = response.json()
    print(f"   Response status: {json_response.get('status')}")
    
    if json_response.get("status") != True:
        raise ValueError(f"API returned unsuccessful status: {json_response}")
    
    # Check for expected content in response
    if "ai_message" not in json_response:
        raise ValueError(f"API response missing 'ai_message' field: {json_response}")
    
    ai_message = AIMessage(**json_response["ai_message"])
    if not isinstance(ai_message.content, list):
        raise ValueError(f"Action message content is not a list. It is: {type(ai_message.content)}")
    return "\n".join(str(item) for item in ai_message.content)


# =============================================================================
# Tool Factory (copied from AdoptXchange/examples/tool_calling_samples/tool_factory.py)
# =============================================================================

from langchain_core.tools import tool, ToolException


def sanitize_tool_name(title: str) -> str:
    """Convert action title to valid Python identifier."""
    name = title.lower()
    name = name.replace(' ', '_').replace('-', '_')
    name = ''.join(c for c in name if c.isalnum() or c == '_')
    if name and name[0].isdigit():
        name = 'tool_' + name
    return name


def parse_required_input(input_str: str) -> Dict[str, Dict[str, Any]]:
    """Parse a required_input JSON string into parameter metadata."""
    try:
        parsed = json.loads(input_str)
        if isinstance(parsed, dict):
            return parsed
        return {}
    except (json.JSONDecodeError, TypeError):
        return {}


def create_tool_schema(capability: AdoptAction) -> Optional[Type[BaseModel]]:
    """Create a dynamic Pydantic schema for a tool based on its required_inputs."""
    if not capability.required_inputs:
        return None
    
    # Type mapping from string to Python types
    type_mapping = {
        'int': int,
        'integer': int,
        'str': str,
        'string': str,
        'float': float,
        'double': float,
        'bool': bool,
        'boolean': bool,
    }
    
    field_definitions = {}
    
    for input_str in capability.required_inputs:
        param_dict = parse_required_input(input_str)
        
        if param_dict:
            for param_name, param_meta in param_dict.items():
                type_str = param_meta.get('type', 'string').lower()
                default_value = param_meta.get('default')
                min_value = param_meta.get('min')
                max_value = param_meta.get('max')
                definition = param_meta.get('definition', f"{param_name} parameter")
                
                param_type = type_mapping.get(type_str, str)
                has_default = 'default' in param_meta
                mandatory_status = "(optional)" if has_default else "(mandatory)"
                enhanced_description = f"{definition} {mandatory_status}"
                
                field_kwargs = {'description': enhanced_description}
                
                if min_value is not None and param_type in (int, float):
                    field_kwargs['ge'] = min_value
                if max_value is not None and param_type in (int, float):
                    field_kwargs['le'] = max_value
                
                if default_value is not None:
                    field_kwargs['default'] = default_value
                
                # type: ignore due to dynamic Field kwargs
                field_definitions[param_name] = (param_type, Field(**field_kwargs))  # type: ignore
        else:
            param_name = input_str.strip()
            if param_name:
                field_definitions[param_name] = (
                    str, 
                    Field(description=f"{param_name} parameter (mandatory)")
                )
    
    if not field_definitions:
        return None
    
    schema_name = f"{sanitize_tool_name(capability.title)}_schema"
    return create_model(
        schema_name,
        **field_definitions
    )


def create_adopt_tool(capability: AdoptAction, profile: Dict[str, Any]) -> Callable:
    """Dynamically create a LangChain tool for an Adopt capability."""
    tool_name = sanitize_tool_name(capability.title)
    tool_description = capability.description
    
    tool_schema = create_tool_schema(capability)
    
    def tool_func(**kwargs) -> str:
        """Execute this Adopt action with the provided input."""
        try:
            if 'kwargs' in kwargs and isinstance(kwargs['kwargs'], dict):
                actual_kwargs = kwargs['kwargs']
            else:
                actual_kwargs = kwargs
            
            user_input = actual_kwargs.get('user_input', '')
            
            workflow_params = {}
            if capability.required_inputs:
                param_info = {}
                for input_str in capability.required_inputs:
                    param_dict = parse_required_input(input_str)
                    for param_name, param_meta in param_dict.items():
                        param_info[param_name] = param_meta
                
                for param_name, param_meta in param_info.items():
                    if param_name in actual_kwargs:
                        workflow_params[param_name] = actual_kwargs[param_name]
                    elif 'default' in param_meta:
                        workflow_params[param_name] = param_meta['default']
                
                if not user_input:
                    user_input = f"Execute {capability.title}"
            else:
                if not user_input:
                    user_input = " ".join(str(v) for v in actual_kwargs.values() if v)
                if not user_input:
                    user_input = capability.description or capability.title
            
            result = run_action_by_id(
                action_id=capability.id,
                user_input=user_input,
                profile=profile,
                workflow_params=workflow_params if workflow_params else None
            )
            return result
        except Exception as e:
            raise ToolException(f"Failed to execute {capability.title}: {str(e)}")
    
    tool_func.__name__ = tool_name
    tool_func.__doc__ = tool_description
    
    if tool_schema:
        adopt_tool = tool(args_schema=tool_schema)(tool_func)
    else:
        adopt_tool = tool(tool_func)
    
    return adopt_tool


# =============================================================================
# Model with Tools (using Anthropic via LangChain)
# =============================================================================

from langchain_anthropic import ChatAnthropic


def create_model_with_tools(tools: List[Callable]):
    """Create Anthropic Claude model with tools bound.
    
    Args:
        tools: List of LangChain tools to bind to the model
        
    Returns:
        ChatAnthropic model with tools bound
    """
    # Validated as non-None in read_env()
    assert os.getenv('ANTHROPIC_API_KEY') is not None
    
    model = ChatAnthropic(
        model_name=os.getenv('ANTHROPIC_MODEL') or "",
        api_key=SecretStr(os.getenv('ANTHROPIC_API_KEY') or ""),
        timeout=10,
        stop=None,
    )
    return model.bind_tools(tools)


# =============================================================================
# Test Data Setup Functions
# =============================================================================

# Default adopt profile template
ADOPT_PROFILE_TEMPLATE = {
    "base_url": "",
    "application_base_url": "",
    "workflow_params": {},
    "security_params": {
        "cookie": ""
    }
}


def create_test_folder(action_id: str) -> Path:
    """
    Create a folder for the test data with the action_id as the name.
    
    Args:
        action_id: The action ID to use as folder name
        
    Returns:
        Path to the created folder
        
    Raises:
        FileExistsError: If the folder already exists
    """
    folder_path = Path(__file__).parent / action_id
    
    if folder_path.exists():
        raise FileExistsError(f"Folder already exists: {folder_path}")
    
    folder_path.mkdir(parents=True)
    print(f"✅ Created test folder: {folder_path}")
    
    return folder_path


def save_wdl(folder_path: Path, wdl_data: list) -> bool:
    """Save the WDL data to widdle.json in the test folder."""
    try:
        wdl_path = folder_path / "widdle.json"
        with open(wdl_path, 'w') as f:
            json.dump(wdl_data, f, indent=2)
        print(f"✅ Saved WDL to: {wdl_path}")
        return True
    except Exception as e:
        print(f"❌ Failed to save WDL: {e}")
        return False


def save_description(folder_path: Path, description: str) -> bool:
    """Save the description to description.txt in the test folder."""
    try:
        desc_path = folder_path / "description.txt"
        with open(desc_path, 'w') as f:
            f.write(description)
        print(f"✅ Saved description to: {desc_path}")
        return True
    except Exception as e:
        print(f"❌ Failed to save description: {e}")
        return False


def save_api_details(folder_path: Path, api_details: dict, api_id: str) -> bool:
    """Save the API details to api_details_{api_id}.json in the test folder."""
    try:
        api_path = folder_path / f"api_details_{api_id}.json"
        with open(api_path, 'w') as f:
            json.dump(api_details, f, indent=2)
        print(f"✅ Saved API details to: {api_path}")
        return True
    except Exception as e:
        print(f"❌ Failed to save API details: {e}")
        return False


def save_adopt_profile_template(folder_path: Path) -> bool:
    """Save the adopt_profile.json template to the test folder."""
    try:
        profile_path = folder_path / "adopt_profile.json"
        with open(profile_path, 'w') as f:
            json.dump(ADOPT_PROFILE_TEMPLATE, f, indent=2)
        print(f"✅ Saved adopt_profile.json template to: {profile_path}")
        return True
    except Exception as e:
        print(f"❌ Failed to save adopt_profile.json: {e}")
        return False


def save_prompt_template(folder_path: Path, description: str) -> bool:
    """Save a prompt.txt template to the test folder."""
    try:
        prompt_path = folder_path / "prompt.txt"
        # Create a basic prompt template based on the description
        prompt_content = f"# Test prompt for this action\n# Edit this file to customize the test prompt\n\n{description}"
        with open(prompt_path, 'w') as f:
            f.write(prompt_content)
        print(f"✅ Saved prompt.txt template to: {prompt_path}")
        return True
    except Exception as e:
        print(f"❌ Failed to save prompt.txt: {e}")
        return False


def save_tool_data(folder_path: Path, tool_data: dict) -> bool:
    """Save the full tool data to tool_data.json in the test folder."""
    try:
        tool_data_path = folder_path / "tool_data.json"
        with open(tool_data_path, 'w') as f:
            json.dump(tool_data, f, indent=2)
        print(f"✅ Saved tool data to: {tool_data_path}")
        return True
    except Exception as e:
        print(f"❌ Failed to save tool data: {e}")
        return False


# =============================================================================
# Test Execution Functions
# =============================================================================

def load_test_data(folder_path: Path) -> Dict[str, Any]:
    """
    Load test data from the action folder.
    
    Args:
        folder_path: Path to the test folder
        
    Returns:
        Dictionary with tool_data, profile, prompt, and api_details
    """
    result = {}
    
    # Load tool data
    tool_data_path = folder_path / "tool_data.json"
    if tool_data_path.exists():
        with open(tool_data_path, 'r') as f:
            result['tool_data'] = json.load(f)
    else:
        raise ValueError(f"tool_data.json not found in {folder_path}")
    
    # Load profile
    profile_path = folder_path / "adopt_profile.json"
    if profile_path.exists():
        with open(profile_path, 'r') as f:
            result['profile'] = json.load(f)
    else:
        raise ValueError(f"adopt_profile.json not found in {folder_path}")
    
    # Load prompt
    prompt_path = folder_path / "prompt.txt"
    if prompt_path.exists():
        with open(prompt_path, 'r') as f:
            # Skip comment lines
            lines = f.readlines()
            prompt_lines = [line for line in lines if not line.strip().startswith('#')]
            result['prompt'] = ''.join(prompt_lines).strip()
    else:
        raise ValueError(f"prompt.txt not found in {folder_path}")
    
    # Load API details (may be multiple files)
    result['api_details'] = {}
    for api_file in folder_path.glob("api_details_*.json"):
        api_id = api_file.stem.replace("api_details_", "")
        with open(api_file, 'r') as f:
            result['api_details'][api_id] = json.load(f)
    
    return result


def create_tool_from_data(tool_data: dict, profile: Dict[str, Any]) -> Callable:
    """
    Create a LangChain tool from the tool data.
    
    Args:
        tool_data: The tool data from tool_data.json
        profile: The adopt profile configuration
        
    Returns:
        A LangChain tool function
    """
    # Create AdoptAction from tool_data
    # The tool_data has the action id and other details
    action_id = tool_data.get('id', '')
    title = tool_data.get('title', tool_data.get('name', 'Unknown Tool'))
    description = tool_data.get('action_description', '')
    required_inputs = tool_data.get('required_inputs', [])
    
    capability = AdoptAction(
        id=action_id,
        title=title,
        description=description,
        required_inputs=required_inputs
    )
    
    return create_adopt_tool(capability, profile)


def compare_tool_call_with_api(tool_call: dict, api_details: Dict[str, dict]) -> Dict[str, Any]:
    """
    Compare the tool call output with the API details.
    
    This validates that the tool is calling the right API with the right format.
    
    Args:
        tool_call: The tool call arguments from the model
        api_details: Dictionary of API details keyed by api_id
        
    Returns:
        Dictionary with comparison results
    """
    results = {
        "matched": False,
        "tool_call": tool_call,
        "api_matches": [],
        "validation_errors": []
    }
    
    # Check if the tool call parameters match any API's expected format
    for api_id, api_detail in api_details.items():
        api_match = {
            "api_id": api_id,
            "matched_params": [],
            "missing_params": [],
            "extra_params": []
        }
        
        # Extract expected parameters from API details
        # API details typically have request_body, query_params, or path_params
        expected_params = set()
        
        # Check for request body parameters
        if 'request_body' in api_detail and api_detail['request_body']:
            if isinstance(api_detail['request_body'], dict):
                expected_params.update(api_detail['request_body'].keys())
        
        # Check for query parameters
        if 'query_params' in api_detail and api_detail['query_params']:
            if isinstance(api_detail['query_params'], list):
                for param in api_detail['query_params']:
                    if isinstance(param, dict) and 'name' in param:
                        expected_params.add(param['name'])
                    elif isinstance(param, str):
                        expected_params.add(param)
        
        # Check for path parameters
        if 'path_params' in api_detail and api_detail['path_params']:
            if isinstance(api_detail['path_params'], list):
                for param in api_detail['path_params']:
                    if isinstance(param, dict) and 'name' in param:
                        expected_params.add(param['name'])
                    elif isinstance(param, str):
                        expected_params.add(param)
        
        # Compare with tool call
        tool_params = set(tool_call.keys()) if tool_call else set()
        
        api_match['matched_params'] = list(tool_params & expected_params)
        api_match['missing_params'] = list(expected_params - tool_params)
        api_match['extra_params'] = list(tool_params - expected_params)
        
        # Consider it a match if we have some matched params and no critical missing ones
        if api_match['matched_params']:
            api_match['matched'] = True
            results['matched'] = True
        
        results['api_matches'].append(api_match)
    
    return results


# =============================================================================
# Suggestion Generation Functions
# =============================================================================

def generate_improvement_suggestions(
    test_results: Dict[str, Any],
    folder_path: Path
) -> str:
    """
    Generate suggestions for improving the tool based on test results.
    
    Analyzes the test failures and generates a Cursor-friendly prompt
    with specific suggestions for updating widdle.json and description.txt.
    
    Args:
        test_results: The results from run_test()
        folder_path: Path to the test folder
        
    Returns:
        A formatted string with suggestions that can be copied to Cursor
    """
    suggestions = []
    
    # Gather context about the failure
    tool_calls = test_results.get('tool_calls', [])
    tool_results = test_results.get('tool_results', [])
    api_comparison = test_results.get('api_comparison')
    error = test_results.get('error')
    final_response = test_results.get('final_response', '')
    prompt = test_results.get('prompt', '')
    
    # Load current widdle.json and description.txt for context
    widdle_content = ""
    description_content = ""
    tool_data_content = ""
    api_details_content = {}
    
    try:
        widdle_path = folder_path / "widdle.json"
        if widdle_path.exists():
            with open(widdle_path, 'r') as f:
                widdle_content = f.read()
    except Exception:
        pass
    
    try:
        desc_path = folder_path / "description.txt"
        if desc_path.exists():
            with open(desc_path, 'r') as f:
                description_content = f.read()
    except Exception:
        pass
    
    try:
        tool_data_path = folder_path / "tool_data.json"
        if tool_data_path.exists():
            with open(tool_data_path, 'r') as f:
                tool_data_content = f.read()
    except Exception:
        pass
    
    try:
        for api_file in folder_path.glob("api_details_*.json"):
            api_id = api_file.stem.replace("api_details_", "")
            with open(api_file, 'r') as f:
                api_details_content[api_id] = f.read()
    except Exception:
        pass
    
    # Build the suggestion prompt
    suggestion_lines = []
    suggestion_lines.append("=" * 80)
    suggestion_lines.append("📋 SUGGESTED CHANGES FOR CURSOR")
    suggestion_lines.append("=" * 80)
    suggestion_lines.append("")
    suggestion_lines.append("Copy the following prompt to Cursor to update the tool:")
    suggestion_lines.append("")
    suggestion_lines.append("-" * 80)
    suggestion_lines.append("--- BEGIN CURSOR PROMPT ---")
    suggestion_lines.append("-" * 80)
    suggestion_lines.append("")
    
    # Start the Cursor prompt
    cursor_prompt_lines = []
    cursor_prompt_lines.append(f"Please update the tool configuration files in `{folder_path}` to fix the following test failures:\n")
    
    # Section 1: Test Context
    cursor_prompt_lines.append("## Test Context\n")
    cursor_prompt_lines.append(f"**Test Prompt Used:** {prompt}\n")
    
    # Section 2: Failure Analysis
    cursor_prompt_lines.append("## Failure Analysis\n")
    
    # Check for no tool calls
    if not tool_calls:
        cursor_prompt_lines.append("### Issue: Model did not make any tool calls\n")
        cursor_prompt_lines.append("The LLM did not recognize this as a tool invocation scenario. This usually means:\n")
        cursor_prompt_lines.append("- The tool description is not clear enough about what the tool does")
        cursor_prompt_lines.append("- The tool name or description doesn't match the user's intent")
        cursor_prompt_lines.append("- The required parameters are not well documented\n")
        cursor_prompt_lines.append(f"**Model's direct response:** {final_response[:500] if final_response else 'N/A'}...\n")
    
    # Check for tool execution errors
    failed_tool_results = [r for r in tool_results if not r.get('success', False)]
    if failed_tool_results:
        cursor_prompt_lines.append("### Issue: Tool execution failed\n")
        for result in failed_tool_results:
            cursor_prompt_lines.append(f"- **Tool:** {result.get('tool_name', 'unknown')}")
            cursor_prompt_lines.append(f"- **Error:** {result.get('error', 'Unknown error')}\n")
    
    # Check for API format mismatches
    if api_comparison:
        if not api_comparison.get('matched', False):
            cursor_prompt_lines.append("### Issue: Tool call parameters don't match API format\n")
        
        for api_match in api_comparison.get('api_matches', []):
            api_id = api_match.get('api_id', 'unknown')
            missing = api_match.get('missing_params', [])
            extra = api_match.get('extra_params', [])
            matched = api_match.get('matched_params', [])
            
            if missing or extra:
                cursor_prompt_lines.append(f"**API ID:** {api_id}\n")
                if matched:
                    cursor_prompt_lines.append(f"- ✅ Matched parameters: {', '.join(matched)}")
                if missing:
                    cursor_prompt_lines.append(f"- ❌ Missing parameters (expected by API but not in tool schema): {', '.join(missing)}")
                if extra:
                    cursor_prompt_lines.append(f"- ⚠️  Extra parameters (in tool call but not expected by API): {', '.join(extra)}")
                cursor_prompt_lines.append("")
    
    # Check for general errors
    if error:
        cursor_prompt_lines.append("### Issue: General error\n")
        cursor_prompt_lines.append(f"**Error:** {error}\n")
    
    # Section 3: Current Tool Configuration
    cursor_prompt_lines.append("## Current Tool Configuration\n")
    
    cursor_prompt_lines.append("### Current `widdle.json` (Tool Implementation & Arg Schema):\n")
    cursor_prompt_lines.append("```json")
    cursor_prompt_lines.append(widdle_content[:2000] if len(widdle_content) > 2000 else widdle_content)
    if len(widdle_content) > 2000:
        cursor_prompt_lines.append("... (truncated)")
    cursor_prompt_lines.append("```\n")
    
    cursor_prompt_lines.append("### Current `description.txt` (Tool Docstring):\n")
    cursor_prompt_lines.append("```")
    cursor_prompt_lines.append(description_content)
    cursor_prompt_lines.append("```\n")
    
    # Section 4: Tool Call Details (what the model tried to do)
    if tool_calls:
        cursor_prompt_lines.append("## What the Model Tried to Do\n")
        for tc in tool_calls:
            cursor_prompt_lines.append(f"**Tool Called:** {tc.get('name', 'unknown')}")
            cursor_prompt_lines.append("**Arguments Passed:**")
            cursor_prompt_lines.append("```json")
            cursor_prompt_lines.append(json.dumps(tc.get('args', {}), indent=2))
            cursor_prompt_lines.append("```\n")
    
    # Section 5: API Details for Reference
    if api_details_content:
        cursor_prompt_lines.append("## API Details for Reference\n")
        for api_id, api_content in api_details_content.items():
            cursor_prompt_lines.append(f"### API: {api_id}\n")
            cursor_prompt_lines.append("```json")
            # Truncate if too long
            if len(api_content) > 1500:
                cursor_prompt_lines.append(api_content[:1500])
                cursor_prompt_lines.append("... (truncated)")
            else:
                cursor_prompt_lines.append(api_content)
            cursor_prompt_lines.append("```\n")
    
    # Section 6: Specific Recommendations
    cursor_prompt_lines.append("## Recommended Changes\n")
    cursor_prompt_lines.append("Based on the above analysis, please make the following changes:\n")
    
    cursor_prompt_lines.append("### For `widdle.json`:\n")
    if api_comparison:
        for api_match in api_comparison.get('api_matches', []):
            missing = api_match.get('missing_params', [])
            extra = api_match.get('extra_params', [])
            if missing:
                cursor_prompt_lines.append(f"1. Add the following missing parameters to the tool schema: **{', '.join(missing)}**")
                cursor_prompt_lines.append("   - Ensure each parameter has proper type, description, and default values if applicable")
            if extra:
                cursor_prompt_lines.append(f"2. Review if these extra parameters are needed: **{', '.join(extra)}**")
                cursor_prompt_lines.append("   - If not needed by the API, consider removing them or mapping them correctly")
    
    if not tool_calls:
        cursor_prompt_lines.append("1. Review the tool's `required_inputs` to ensure parameters are properly defined")
        cursor_prompt_lines.append("2. Ensure parameter names match what the API expects")
        cursor_prompt_lines.append("3. Add clear type annotations and descriptions for each parameter")
    
    cursor_prompt_lines.append("\n### For `description.txt`:\n")
    if not tool_calls:
        cursor_prompt_lines.append("1. Make the description more specific about what the tool does")
        cursor_prompt_lines.append("2. Include example use cases that match the test prompt")
        cursor_prompt_lines.append("3. Clearly state what parameters are required and what they should contain")
    else:
        cursor_prompt_lines.append("1. Ensure the description accurately reflects the tool's current behavior")
        cursor_prompt_lines.append("2. Add any constraints or validation requirements for parameters")
        cursor_prompt_lines.append("3. Include example inputs and expected outputs if helpful")
    
    cursor_prompt_lines.append("\n### Files to Update:\n")
    cursor_prompt_lines.append(f"- `{folder_path / 'widdle.json'}`")
    cursor_prompt_lines.append(f"- `{folder_path / 'description.txt'}`")
    
    cursor_prompt_lines.append("\nAfter making changes, run the test again with:")
    cursor_prompt_lines.append("```bash")
    cursor_prompt_lines.append(f"python cli/test_adopt_zaction.py --run {folder_path.name}")
    cursor_prompt_lines.append("```")
    
    # Combine everything
    suggestion_lines.extend(cursor_prompt_lines)
    suggestion_lines.append("")
    suggestion_lines.append("-" * 80)
    suggestion_lines.append("--- END CURSOR PROMPT ---")
    suggestion_lines.append("-" * 80)
    suggestion_lines.append("")
    
    return "\n".join(suggestion_lines)


def print_improvement_suggestions(test_results: Dict[str, Any], folder_path: Path) -> None:
    """
    Print improvement suggestions to the console.
    
    Args:
        test_results: The results from run_test()
        folder_path: Path to the test folder
    """
    suggestions = generate_improvement_suggestions(test_results, folder_path)
    print(suggestions)
    
    # Also save suggestions to a file for easy access
    suggestions_path = folder_path / "improvement_suggestions.md"
    try:
        with open(suggestions_path, 'w') as f:
            # Write as markdown without the wrapper
            f.write("# Tool Improvement Suggestions\n\n")
            f.write(f"Generated from test results for action: {folder_path.name}\n\n")
            f.write(suggestions)
        print(f"\n💾 Suggestions also saved to: {suggestions_path}")
    except Exception as e:
        print(f"\n⚠️  Could not save suggestions to file: {e}")


def run_test(folder_path: Path) -> Dict[str, Any]:
    """
    Run the test for a zaction.
    
    This function:
    1. Loads test data from the folder
    2. Creates a tool from the action
    3. Binds it to a model
    4. Runs the prompt through the model
    5. Compares the output with API details
    
    Args:
        folder_path: Path to the test folder
        
    Returns:
        Dictionary with test results
    """
    print("\n" + "=" * 80)
    print("🧪 RUNNING ZACTION TEST")
    print("=" * 80)
    print(f"Test folder: {folder_path}")
    print("=" * 80)
    
    results = {
        "success": False,
        "folder": str(folder_path),
        "prompt": "",
        "tool_calls": [],
        "tool_results": [],
        "api_comparison": None,
        "final_response": "",
        "error": None
    }
    
    try:
        # Step 1: Load test data
        print("\n📂 Step 1: Loading test data...")
        test_data = load_test_data(folder_path)
        results['prompt'] = test_data['prompt']
        print("✅ Loaded test data")
        print(f"   Prompt: {test_data['prompt'][:100]}..." if len(test_data['prompt']) > 100 else f"   Prompt: {test_data['prompt']}")
        
        # Step 2: Create tool from data
        print("\n🔧 Step 2: Creating tool from action data...")
        tool = create_tool_from_data(test_data['tool_data'], test_data['profile'])
        print(f"✅ Created tool: {tool.name}")  # type: ignore[attr-defined]
        
        # Step 3: Bind tool to model
        print("\n🤖 Step 3: Binding tool to Anthropic model...")
        model_with_tools = create_model_with_tools([tool])
        print("✅ Model ready with tool")
        
        # Step 4: Run prompt through model
        print("\n▶️  Step 4: Running prompt through model...")
        print(f"   Prompt: {test_data['prompt']}")
        print("-" * 60)
        
        # Build messages with system context about available workflow_params
        messages = []
        
        # Add system message if workflow_params are available
        workflow_params = test_data['profile'].get('workflow_params', {})
        if workflow_params:
            from langchain_core.messages import SystemMessage
            system_content = (
                "You are a helpful assistant with access to tools. "
                "When the user asks you to perform an action, use the available tool. "
                f"The following parameter values are already available and should be used when calling tools: {workflow_params}. "
                "Use these values directly - do not ask the user for them."
            )
            messages.append(SystemMessage(content=system_content))
            print(f"   Using workflow_params: {workflow_params}")
        
        messages.append(HumanMessage(content=test_data['prompt']))
        
        response = model_with_tools.invoke(messages)
        
        # Step 6: Check for tool calls
        if hasattr(response, 'tool_calls') and response.tool_calls: # type: ignore[attr-defined]
            print(f"🔧 Model selected {len(response.tool_calls)} tool(s):") # type: ignore[attr-defined]
            
            for tool_call in response.tool_calls: # type: ignore[attr-defined]
                tool_call_info = {
                    "name": tool_call.get('name', 'unknown'),
                    "args": tool_call.get('args', {}),
                    "id": tool_call.get('id')
                }
                results['tool_calls'].append(tool_call_info)
                print(f"   - {tool_call_info['name']}")
                print(f"     Args: {json.dumps(tool_call_info['args'], indent=2)}")
            
            # Step 7: Compare with API details
            print("\n📊 Step 5: Comparing tool calls with API details...")
            if test_data['api_details']:
                for tool_call_info in results['tool_calls']:
                    comparison = compare_tool_call_with_api(
                        tool_call_info['args'],
                        test_data['api_details']
                    )
                    results['api_comparison'] = comparison
                    
                    if comparison['matched']:
                        print("✅ Tool call matches API format!")
                        for api_match in comparison['api_matches']:
                            if api_match.get('matched'):
                                print(f"   API ID: {api_match['api_id']}")
                                print(f"   Matched params: {api_match['matched_params']}")
                                if api_match['missing_params']:
                                    print(f"   Missing params: {api_match['missing_params']}")
                                if api_match['extra_params']:
                                    print(f"   Extra params: {api_match['extra_params']}")
                    else:
                        print("⚠️  Tool call may not match expected API format")
                        results['api_comparison']['validation_errors'].append(
                            "No matching API parameters found"
                        )
            else:
                print("⚠️  No API details to compare against")
            
            # Step 8: Execute tool calls
            print("\n⚙️  Step 6: Executing tool calls...")
            from langchain_core.messages import ToolMessage
            tool_messages = []
            
            for tool_call in response.tool_calls: # type: ignore[attr-defined]
                tool_name = tool_call.get('name')
                tool_input = tool_call.get('args', {})
                tool_id = tool_call.get('id')
                
                if tool.name == tool_name:  # type: ignore[attr-defined]
                    try:
                        result = tool.invoke(tool_input)  # type: ignore[attr-defined]
                        results['tool_results'].append({
                            "tool_name": tool_name,
                            "success": True,
                            "result": str(result)[:500]  # Truncate for display
                        })
                        tool_messages.append(ToolMessage(
                            content=str(result),
                            tool_call_id=tool_id
                        ))
                        print(f"   ✅ {tool_name} executed successfully")
                    except Exception as e:
                        results['tool_results'].append({
                            "tool_name": tool_name,
                            "success": False,
                            "error": str(e)
                        })
                        tool_messages.append(ToolMessage(
                            content=f"Error: {str(e)}",
                            tool_call_id=tool_id
                        ))
                        print(f"   ❌ {tool_name} failed: {e}")
            
            # Step 8: Get final response
            if tool_messages:
                print("\n🤖 Step 7: Getting final response...")
                final_response = model_with_tools.invoke([
                    HumanMessage(content=test_data['prompt']),
                    response,
                    *tool_messages
                ])
                results['final_response'] = final_response.content
                print(f"   Response: {final_response.content[:200]}..." if len(str(final_response.content)) > 200 else f"   Response: {final_response.content}")
        else:
            print("⚠️  No tool calls made by the model")
            results['final_response'] = response.content
            print(f"   Direct response: {response.content}")
        
        # Determine success
        results['success'] = (
            len(results['tool_calls']) > 0 and
            any(r.get('success', False) for r in results['tool_results'])
        )
        
        # Final summary
        print("\n" + "=" * 80)
        if results['success']:
            print("🎉 TEST PASSED!")
        else:
            print("❌ TEST FAILED")
            # Generate and print improvement suggestions for failed tests
            print_improvement_suggestions(results, folder_path)
        print("=" * 80)
        
    except Exception as e:
        results['error'] = str(e)
        print(f"\n❌ Error during test: {e}")
        import traceback
        traceback.print_exc()
        # Generate suggestions even on exceptions
        print_improvement_suggestions(results, folder_path)
    
    return results


def fetch_all_apis(bearer_token: str, page_size: int = 50) -> List[Dict[str, Any]]:
    """
    Fetch all available APIs from the AdoptAI API with pagination support.
    
    Uses the same logic as APIManager.fetch_apis from create_tools.py.
    
    Args:
        bearer_token: Authentication bearer token
        page_size: Number of APIs to fetch per page (default: 50)
    
    Returns:
        List of API dictionaries
    """
    api_endpoint = os.getenv('ADOPT_API_ENDPOINT', 'https://connect.adopt.ai')
    url = f"{api_endpoint}/v1/tools/apis"
    headers = {
        "Authorization": f"Bearer {bearer_token}",
        "Content-Type": "application/json"
    }
    
    all_apis: List[Dict[str, Any]] = []
    page = 1
    
    print("⏳ Fetching all APIs for brute force search...")
    
    while True:
        params = {
            "page": page,
            "page_size": page_size
        }
        
        try:
            response = requests.get(url, headers=headers, params=params, timeout=30)
            
            if response.status_code != 200:
                print(f"❌ Failed to fetch APIs. Status code: {response.status_code}")
                break
            
            json_response = response.json()
            
            # Handle different response formats
            page_apis: List[Dict[str, Any]] = []
            has_more = False
            
            if isinstance(json_response, list):
                page_apis = json_response
                has_more = len(page_apis) >= page_size
            elif isinstance(json_response, dict):
                if "apis" in json_response:
                    page_apis = json_response["apis"]
                elif "data" in json_response:
                    page_apis = json_response["data"]
                elif "items" in json_response:
                    page_apis = json_response["items"]
                else:
                    page_apis = [json_response]
                
                has_more = (
                    json_response.get("has_more", False) or
                    json_response.get("hasMore", False) or
                    json_response.get("has_next", False) or
                    len(page_apis) >= page_size
                )
            
            if not page_apis:
                break
            
            all_apis.extend(page_apis)
            print(f"   Fetched page {page}: {len(page_apis)} API(s) (total: {len(all_apis)})")
            
            if not has_more or len(page_apis) < page_size:
                break
            
            page += 1
            
        except requests.exceptions.RequestException as e:
            print(f"❌ Network error while fetching APIs: {e}")
            break
    
    print(f"✅ Fetched {len(all_apis)} APIs total")
    return all_apis


def build_title_to_api_id_map(apis: List[Dict[str, Any]]) -> Dict[str, str]:
    """
    Build a lowercase title to api_id mapping from a list of APIs.
    
    Args:
        apis: List of API dictionaries
        
    Returns:
        Dictionary mapping lowercase titles to api_ids
    """
    title_map: Dict[str, str] = {}
    
    for api in apis:
        api_id = api.get('id', '')
        # Try 'title' first, then 'name' as fallback
        title = api.get('title') or api.get('name') or ''
        
        if api_id and title:
            title_lower = title.lower().strip()
            title_map[title_lower] = api_id
    
    return title_map


def bruteforce_api_id_search(title: str, bearer_token: Optional[str] = None) -> Optional[str]:
    """
    Bruteforce API ID search by title.
    
    Fetches all APIs and creates a title-to-api_id map, then matches
    the given title (case-insensitive) to find the corresponding API ID.
    
    Args:
        title: The title to search for
        bearer_token: Optional bearer token (will fetch if not provided)
        
    Returns:
        The matching API ID or None if not found
    """
    print(f"🔍 Bruteforce API ID search for: '{title}'")
    
    if not title:
        print("❌ Empty title provided")
        return None
    
    # Get bearer token if not provided
    if bearer_token is None:
        bearer_token = get_bearer_token()
    
    # Fetch all APIs
    apis = fetch_all_apis(bearer_token)
    
    if not apis:
        print("❌ No APIs fetched")
        return None
    
    # Build title -> api_id map
    title_map = build_title_to_api_id_map(apis)
    print(f"📋 Built title map with {len(title_map)} entries")
    
    # Search for the title (case-insensitive)
    title_lower = title.lower().strip()
    
    # Exact match first
    if title_lower in title_map:
        api_id = title_map[title_lower]
        print(f"✅ Found exact match: {api_id}")
        return api_id
    
    # Partial match (title contains search term or search term contains title)
    partial_matches: List[tuple] = []
    for map_title, api_id in title_map.items():
        if title_lower in map_title or map_title in title_lower:
            partial_matches.append((map_title, api_id))
    
    if partial_matches:
        if len(partial_matches) == 1:
            match_title, api_id = partial_matches[0]
            print(f"✅ Found partial match: '{match_title}' -> {api_id}")
            return api_id
        else:
            print(f"⚠️  Found {len(partial_matches)} partial matches:")
            for match_title, api_id in partial_matches[:5]:  # Show first 5
                print(f"   - '{match_title}' -> {api_id}")
            if len(partial_matches) > 5:
                print(f"   ... and {len(partial_matches) - 5} more")
            # Return the first match
            match_title, api_id = partial_matches[0]
            print(f"   Using first match: '{match_title}' -> {api_id}")
            return api_id
    
    print(f"❌ No API found matching title: '{title}'")
    return None

# =============================================================================
# Main Workflow Functions
# =============================================================================

def test_adopt_zaction(action_id: str) -> bool:
    """
    Main workflow to test an ADOPT zaction.
    
    This workflow:
    1. Creates a test folder with the action_id as the name
    2. Downloads the action details
    3. Extracts and saves the WDL
    4. Extracts and saves the description
    5. Downloads and saves the API details
    6. Creates adopt_profile.json template
    7. Creates prompt.txt template
    
    Args:
        action_id: The action ID to test
        
    Returns:
        bool: True if successful, False otherwise
    """
    print("\n" + "=" * 80)
    print("🧪 TEST ADOPT ZACTION - Setup")
    print("=" * 80)
    print(f"Action ID: {action_id}")
    print("=" * 80)
    
    try:
        # Step 1: Create test folder
        print("\n📁 Step 1: Creating test folder...")
        try:
            folder_path = create_test_folder(action_id)
        except FileExistsError as e:
            print(f"❌ {e}")
            return False
        
        # Step 2: Get authentication token
        print("\n🔐 Step 2: Authenticating with AdoptAI API...")
        bearer_token = get_bearer_token()
        print("✅ Authentication successful")
        
        # Step 3: Download action details
        print("\n📥 Step 3: Downloading action details...")
        success, tool_data, message = fetch_tool_details(bearer_token, action_id)
        
        if not success or not tool_data:
            print(f"❌ Failed to fetch tool details: {message}")
            folder_path.rmdir()
            return False
        
        print("✅ Action details downloaded successfully")
        
        # Step 4: Save the full tool data (for later use in testing)
        print("\n💾 Step 4: Saving tool data...")
        if not save_tool_data(folder_path, tool_data):
            return False
        
        # Step 5: Extract and save WDL
        print("\n📝 Step 5: Extracting and saving WDL...")
        wdl_data = tool_data.get('wdl', tool_data.get('widdle', []))
        if not save_wdl(folder_path, wdl_data):
            return False
        
        # Step 6: Extract and save description
        print("\n📄 Step 6: Extracting and saving description...")
        description = tool_data.get('description', tool_data.get('action_description', ''))
        if not save_description(folder_path, description):
            return False
        
        # Step 7: Download and save API details
        print("\n🔗 Step 7: Downloading API details...")
        api_ids = tool_data.get('api_ids', tool_data.get('documented_api_ids', []))
        
        if not api_ids:
            print("   ⚠️  No API IDs found in tool data, attempting brute force search by title...")
            found_api_id = bruteforce_api_id_search(tool_data.get('title', ''), bearer_token)
            if found_api_id:
                api_ids = [found_api_id]
            else:
                print("   ⚠️  Could not find API ID by title, skipping API details download")
        
        # Fetch and save API details for all found api_ids
        for api_id in api_ids:
            print(f"   📥 Fetching API details for: {api_id}")
            api_details = fetch_api_details(bearer_token, api_id)
            
            if api_details:
                if not save_api_details(folder_path, api_details, api_id):
                    print(f"   ⚠️  Failed to save API details for: {api_id}")
            else:
                print(f"   ⚠️  Could not fetch API details for: {api_id}")
        
        # Step 8: Create adopt_profile.json template
        print("\n📋 Step 8: Creating adopt_profile.json template...")
        if not save_adopt_profile_template(folder_path):
            return False
        
        # Step 9: Create prompt.txt template
        print("\n📝 Step 9: Creating prompt.txt template...")
        if not save_prompt_template(folder_path, description):
            return False
        
        # Final success message
        print("\n" + "=" * 80)
        print("🎉 TEST DATA PREPARED SUCCESSFULLY!")
        print("=" * 80)
        print(f"   Test folder: {folder_path}")
        print("   Files created:")
        print("      - tool_data.json")
        print("      - widdle.json")
        print("      - description.txt")
        if api_ids:
            for api_id in api_ids:
                print(f"      - api_details_{api_id}.json")
        print("      - adopt_profile.json (CONFIGURE THIS!)")
        print("      - prompt.txt (CUSTOMIZE THIS!)")
        print("=" * 80)
        print("\n📌 NEXT STEPS:")
        print("   1. Edit adopt_profile.json with your base_url, cookies, etc.")
        print("   2. Edit prompt.txt with a test prompt for the action")
        print(f"   3. Run: python test_adopt_zaction.py --run {action_id}")
        print("=" * 80 + "\n")
        
        return True
        
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    """Main entry point for the test_adopt_zaction script."""
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Test an ADOPT zaction",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Setup test data for an action
  python test_adopt_zaction.py <action_id>
  
  # Run the test for an action (after configuring adopt_profile.json and prompt.txt)
  python test_adopt_zaction.py --run <action_id>
"""
    )
    parser.add_argument("action_id", nargs="?", help="The Action ID to test")
    parser.add_argument("--run", metavar="ACTION_ID", help="Run the test for the specified action")
    
    args = parser.parse_args()
    
    if args.run:
        # Run mode - execute the test
        folder_path = Path(__file__).parent / args.run
        if not folder_path.exists():
            print(f"❌ Test folder does not exist: {folder_path}")
            print(f"   Run 'python test_adopt_zaction.py {args.run}' first to set up test data")
            sys.exit(1)
        
        results = run_test(folder_path)
        
        # Save results
        results_path = folder_path / "test_results.json"
        with open(results_path, 'w') as f:
            json.dump(results, f, indent=2, default=str)
        print(f"\n📊 Test results saved to: {results_path}")
        
        sys.exit(0 if results['success'] else 1)
    
    elif args.action_id:
        # Setup mode - prepare test data
        success = test_adopt_zaction(args.action_id)
        sys.exit(0 if success else 1)
    
    else:
        # Interactive mode
        print("\n" + "=" * 80)
        print("🧪 TEST ADOPT ZACTION - AdoptAI Tool Builder")
        print("=" * 80)
        print("This script prepares and runs tests for existing zactions.")
        print("=" * 80 + "\n")
        
        action_id = input("Enter the Action ID: ").strip()
        
        if not action_id:
            print("❌ No Action ID provided")
            sys.exit(1)
        
        success = test_adopt_zaction(action_id)
        sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
