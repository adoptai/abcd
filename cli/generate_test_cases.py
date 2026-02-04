#!/usr/bin/env python3
"""
Generate Test Cases - Create test cases for tools based on their WDL.

This script analyzes a tool's WDL and generates appropriate test cases
that can be used for evaluation and verification.

IMPORTANT: This script integrates with the hierarchical workspace manager.
All operations use the active environment's credentials.

Usage:
    # Generate test cases for a tool
    python cli/generate_test_cases.py <tool-id>
    
    # Generate with custom output
    python cli/generate_test_cases.py <tool-id> --output tests.json
    
    # Generate using LLM for smarter prompts
    python cli/generate_test_cases.py <tool-id> --use-llm
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from wdl_common.api_client import AdoptAPIClient
from wdl_common.context import ensure_env, get_client


def analyze_wdl_for_test_cases(wdl: List[Dict]) -> Dict[str, Any]:
    """
    Analyze WDL to understand what inputs are needed and what to test.
    
    Returns:
        Analysis including required_inputs, operations, etc.
    """
    analysis = {
        'required_inputs': {},
        'operations': [],
        'api_endpoints': [],
        'has_pagination': False,
        'has_conditionals': False,
        'output_fields': [],
    }
    
    for block in wdl:
        operation = block.get('operation', '')
        
        if operation == 'METADATA' or 'required_inputs' in block:
            analysis['required_inputs'] = block.get('required_inputs', {})
        
        if operation == 'REST':
            analysis['operations'].append({
                'type': 'REST',
                'method': block.get('method', 'GET'),
                'url': block.get('url', ''),
                'api_endpoint': block.get('canonical_api_endpoint', ''),
            })
            if block.get('canonical_api_endpoint'):
                analysis['api_endpoints'].append(block.get('canonical_api_endpoint'))
        
        if operation == 'PAGINATE':
            analysis['has_pagination'] = True
            analysis['operations'].append({
                'type': 'PAGINATE',
                'strategy': block.get('strategy', ''),
            })
        
        if operation == 'FILTER':
            analysis['operations'].append({
                'type': 'FILTER',
                'conditions': block.get('conditions', []),
            })
        
        if operation == 'TRANSFORM':
            analysis['operations'].append({
                'type': 'TRANSFORM',
                'output_key': block.get('output_key', ''),
            })
            if block.get('output_key'):
                analysis['output_fields'].append(block.get('output_key'))
        
        if operation == 'CONDITIONAL' or 'conditions' in block:
            analysis['has_conditionals'] = True
    
    return analysis


def generate_basic_test_prompts(
    tool_title: str,
    tool_description: str,
    analysis: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """
    Generate basic test prompts based on WDL analysis.
    
    Returns:
        List of test case objects
    """
    test_cases = []
    required_inputs = analysis.get('required_inputs', {})
    
    # Basic functionality test
    test_cases.append({
        'name': 'Basic Functionality Test',
        'prompt': f"Use the {tool_title} tool",
        'expected_behavior': 'Tool executes without errors',
        'priority': 'high',
    })
    
    # Generate tests based on required inputs
    input_examples = {}
    for input_name, input_def in required_inputs.items():
        input_type = input_def.get('type', 'string') if isinstance(input_def, dict) else 'string'
        definition = input_def.get('definition', '') if isinstance(input_def, dict) else str(input_def)
        
        # Generate example value based on type and definition
        if input_type == 'string':
            if 'id' in input_name.lower():
                input_examples[input_name] = 'test-123'
            elif 'email' in input_name.lower():
                input_examples[input_name] = 'test@example.com'
            elif 'name' in input_name.lower():
                input_examples[input_name] = 'Test Name'
            elif 'url' in input_name.lower():
                input_examples[input_name] = 'https://example.com'
            else:
                input_examples[input_name] = f'test_{input_name}'
        elif input_type == 'number' or input_type == 'integer':
            input_examples[input_name] = 100
        elif input_type == 'boolean':
            input_examples[input_name] = True
        elif input_type == 'array':
            input_examples[input_name] = ['item1', 'item2']
        else:
            input_examples[input_name] = f'test_{input_name}'
    
    if required_inputs:
        # Test with specific inputs
        input_desc = ', '.join([f"{k}={v}" for k, v in list(input_examples.items())[:3]])
        test_cases.append({
            'name': 'Test with Sample Inputs',
            'prompt': f"Use {tool_title} with {input_desc}",
            'expected_behavior': 'Tool processes inputs correctly',
            'priority': 'high',
            'sample_inputs': input_examples,
        })
    
    # Test for list/pagination scenarios
    if analysis.get('has_pagination'):
        test_cases.append({
            'name': 'Pagination Test',
            'prompt': f"List all items using {tool_title}",
            'expected_behavior': 'Returns paginated results',
            'priority': 'medium',
        })
    
    # Test based on API operations
    for op in analysis.get('operations', []):
        if op.get('type') == 'REST':
            method = op.get('method', 'GET')
            if method == 'GET':
                test_cases.append({
                    'name': f'GET Operation Test',
                    'prompt': f"Retrieve data using {tool_title}",
                    'expected_behavior': 'Returns data successfully',
                    'priority': 'high',
                })
            elif method == 'POST':
                test_cases.append({
                    'name': f'POST Operation Test',
                    'prompt': f"Create a new item using {tool_title}",
                    'expected_behavior': 'Creates item successfully',
                    'priority': 'high',
                })
            elif method == 'PUT' or method == 'PATCH':
                test_cases.append({
                    'name': f'{method} Operation Test',
                    'prompt': f"Update an item using {tool_title}",
                    'expected_behavior': 'Updates item successfully',
                    'priority': 'high',
                })
    
    # Edge case tests
    test_cases.append({
        'name': 'Empty Input Test',
        'prompt': f"Use {tool_title} without specifying any parameters",
        'expected_behavior': 'Handles gracefully or prompts for required inputs',
        'priority': 'low',
    })
    
    return test_cases


def generate_llm_test_prompts(
    bearer_token: str,
    tool_id: str,
    tool_title: str,
    tool_description: str,
    wdl: List[Dict],
    analysis: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """
    Use LLM to generate smarter test prompts.
    
    Returns:
        List of test case objects
    """
    client = AdoptAPIClient(bearer_token)
    
    prompt = f"""Generate 5 realistic test prompts for the following tool:

**Tool Name**: {tool_title}
**Description**: {tool_description}

**Required Inputs**:
{json.dumps(analysis.get('required_inputs', {}), indent=2)}

**Operations**:
{json.dumps(analysis.get('operations', []), indent=2)}

Generate test prompts that:
1. Test the core functionality
2. Include realistic parameter values
3. Cover edge cases
4. Test error handling

Return as JSON array with format:
[
  {{"name": "Test Name", "prompt": "The actual test prompt", "expected_behavior": "What should happen", "priority": "high/medium/low"}}
]
"""
    
    try:
        # Use a general LLM call if available
        # For now, fall back to basic generation
        return generate_basic_test_prompts(tool_title, tool_description, analysis)
    except Exception:
        return generate_basic_test_prompts(tool_title, tool_description, analysis)


def save_test_cases(
    test_cases: List[Dict],
    tool_id: str,
    tool_title: str,
    output_path: Path,
) -> None:
    """Save test cases to JSON file."""
    output = {
        'tool_id': tool_id,
        'tool_title': tool_title,
        'generated_at': datetime.now().isoformat(),
        'test_cases': test_cases,
    }
    
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, 'w') as f:
        json.dump(output, f, indent=2)


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Generate test cases for tools based on their WDL",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Generate test cases
  python cli/generate_test_cases.py <tool-id>
  
  # Save to specific file
  python cli/generate_test_cases.py <tool-id> --output tests.json
  
  # Use LLM for smarter prompts
  python cli/generate_test_cases.py <tool-id> --use-llm
""",
    )
    
    parser.add_argument("tool_id", help="Tool ID to generate tests for")
    parser.add_argument("--output", "-o", help="Output file path")
    parser.add_argument("--use-llm", action="store_true", help="Use LLM for smarter prompts")
    parser.add_argument("--format", choices=['json', 'csv'], default='json',
                       help="Output format")
    
    args = parser.parse_args()
    
    print("\n" + "=" * 70)
    print("🧪 GENERATE TEST CASES")
    print("=" * 70)
    
    try:
        # Ensure environment is loaded and show which one we're using
        env_name = ensure_env()
        print(f"📁 Environment: {env_name}")
        
        # Get client (uses active environment credentials)
        client = get_client()
        
        # Get tool details
        print(f"\n⏳ Fetching tool {args.tool_id}...")
        success, tool, msg = client.get_action(args.tool_id)
        if not success:
            print(f"❌ Failed to get tool: {msg}")
            return 1
        
        tool_title = tool.get('title') or tool.get('name') or args.tool_id
        tool_description = tool.get('description', '')
        wdl = tool.get('wdl') or tool.get('widdle') or []
        
        print(f"   Tool: {tool_title}")
        print(f"   WDL Blocks: {len(wdl)}")
        
        # Analyze WDL
        print("\n📊 Analyzing WDL...")
        analysis = analyze_wdl_for_test_cases(wdl)
        print(f"   Required Inputs: {len(analysis['required_inputs'])}")
        print(f"   Operations: {len(analysis['operations'])}")
        
        # Generate test cases
        print("\n🧪 Generating test cases...")
        if args.use_llm:
            test_cases = generate_llm_test_prompts(
                bearer_token, args.tool_id, tool_title, tool_description, wdl, analysis
            )
        else:
            test_cases = generate_basic_test_prompts(tool_title, tool_description, analysis)
        
        print(f"   Generated {len(test_cases)} test cases")
        
        # Display test cases
        print("\n📋 Generated Test Cases:")
        for i, tc in enumerate(test_cases):
            priority_icon = "🔴" if tc['priority'] == 'high' else "🟡" if tc['priority'] == 'medium' else "🟢"
            print(f"\n   {i+1}. {priority_icon} {tc['name']}")
            print(f"      Prompt: \"{tc['prompt']}\"")
            print(f"      Expected: {tc['expected_behavior']}")
        
        # Save output
        output_file = args.output or f"diagnostics/test_cases_{args.tool_id}.json"
        output_path = Path(output_file)
        save_test_cases(test_cases, args.tool_id, tool_title, output_path)
        print(f"\n💾 Test cases saved to: {output_file}")
        
        return 0
        
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())









