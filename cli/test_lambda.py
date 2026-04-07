#!/usr/bin/env python3
"""
Test lambda execution.

Features:
- Test with inline JSON: test_lambda.py my-lambda --input '{"key": "value"}'
- Test with file input: test_lambda.py my-lambda --input-file input.json
- Compile/validate only: test_lambda.py my-lambda --compile
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from cli.wdl_common.api_client import get_api_client_for_env
from cli.wdl_common.workspace_manager import get_workspace_manager


def main() -> None:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Test lambda execution",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Run with no input
  python test_lambda.py my-lambda

  # Run with inline JSON input
  python test_lambda.py my-lambda --input '{"key": "value"}'

  # Run with input from file
  python test_lambda.py my-lambda --input-file tests/input.json

  # Validate/compile only (no execution)
  python test_lambda.py my-lambda --compile
        """,
    )

    parser.add_argument("name", help="Lambda name")
    parser.add_argument("--input", "-i", help="Input data as JSON string")
    parser.add_argument("--input-file", "-f", help="Input data from JSON file")
    parser.add_argument(
        "--compile",
        action="store_true",
        help="Validate/compile only — do not execute",
    )
    parser.add_argument("--version", "-n", type=int, help="Specific version to test")
    parser.add_argument("--env", "-e", help="Environment to use")
    parser.add_argument("--verbose", "-v", action="store_true", help="Show verbose output")

    args = parser.parse_args()

    manager = get_workspace_manager()
    if args.env:
        if manager.env_exists(args.env):
            manager.active_env = args.env
        else:
            print(f"ERROR: Environment '{args.env}' not found", file=sys.stderr)
            sys.exit(1)

    ctx = manager.find_lambda(args.name)
    if not ctx:
        print(f"ERROR: Lambda '{args.name}' not found", file=sys.stderr)
        sys.exit(1)

    print(f"Lambda : {ctx.name}")
    print(f"Path   : {ctx.path}")
    print(f"Lang   : {ctx.lambda_json.get('language', 'python')}")

    # Validate — check required files exist
    script_file = ctx.lambda_json.get("entry_point", "script.py")
    script_path = ctx.path / script_file
    if not script_path.exists():
        print(f"ERROR: Entry point '{script_file}' not found in workspace", file=sys.stderr)
        sys.exit(1)

    print(f"Entry  : {script_file} (found)")

    if args.compile:
        print("\nCompile check passed.")
        sys.exit(0)

    lambda_id = ctx.metadata.get("lambda_id")
    if not lambda_id:
        print(
            "ERROR: Lambda is not linked to remote (no lambda_id). Run save_lambda.py first.",
            file=sys.stderr,
        )
        sys.exit(1)

    # Build input data
    input_data: dict = {}
    if args.input and args.input_file:
        print("ERROR: Cannot use both --input and --input-file", file=sys.stderr)
        sys.exit(1)

    if args.input:
        try:
            input_data = json.loads(args.input)
        except json.JSONDecodeError as e:
            print(f"ERROR: Invalid JSON in --input: {e}", file=sys.stderr)
            sys.exit(1)
    elif args.input_file:
        input_file = Path(args.input_file)
        if not input_file.exists():
            print(f"ERROR: Input file not found: {args.input_file}", file=sys.stderr)
            sys.exit(1)
        try:
            input_data = json.loads(input_file.read_text())
        except json.JSONDecodeError as e:
            print(f"ERROR: Invalid JSON in input file: {e}", file=sys.stderr)
            sys.exit(1)

    client = get_api_client_for_env()

    print(f"\nRunning test (lambda_id={lambda_id})...")
    if args.verbose and input_data:
        print(f"Input: {json.dumps(input_data, indent=2)}")

    success, result, msg = client.test_lambda(
        lambda_id=lambda_id,
        input_data=input_data if input_data else None,
        version_number=args.version,
    )

    if not success:
        print(f"\nFAILED: {msg}", file=sys.stderr)
        if result:
            print(json.dumps(result, indent=2), file=sys.stderr)
        sys.exit(1)

    print(f"\nPASSED: {msg}")
    if result:
        print(json.dumps(result, indent=2))

    sys.exit(0)


if __name__ == "__main__":
    main()
