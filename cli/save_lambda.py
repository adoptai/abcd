#!/usr/bin/env python3
"""
Save lambda files to remote backend.

Features:
- Upload local files: save_lambda.py my-lambda
- Dry run to preview: save_lambda.py my-lambda --dry-run
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
        description="Save lambda files to remote backend",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Upload files to backend
  python save_lambda.py my-lambda

  # Dry run to see what would be uploaded
  python save_lambda.py my-lambda --dry-run
        """,
    )

    parser.add_argument("name", help="Lambda name")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be uploaded without making changes",
    )
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

    lambda_id = ctx.metadata.get("lambda_id")

    client = get_api_client_for_env()

    # Create remote lambda if not linked
    if not lambda_id:
        print("\nNo lambda_id found — creating remote lambda...")
        language = ctx.metadata.get("language", "python")
        entry_point = ctx.metadata.get("entry_point", "script.py")
        description = ctx.metadata.get("description", "")

        if args.dry_run:
            print(f"  [DRY-RUN] Would create lambda '{ctx.name}' ({language})")
        else:
            success, data, msg = client.create_lambda(
                name=ctx.name,
                description=description,
                language=language,
                entry_point=entry_point,
            )
            if not success or not data:
                print(f"ERROR: Failed to create remote lambda: {msg}", file=sys.stderr)
                sys.exit(1)

            lambda_id = data.get("id") or data.get("lambda_id")
            if not lambda_id:
                print("ERROR: Remote lambda created but no ID returned", file=sys.stderr)
                sys.exit(1)

            # Persist lambda_id to metadata.json
            metadata_path = ctx.path / "metadata.json"
            metadata = ctx.metadata.copy()
            metadata["lambda_id"] = lambda_id
            metadata_path.write_text(json.dumps(metadata, indent=2))
            print(f"  Created remote lambda: {lambda_id}")

    # Collect source files to upload
    source_files = []
    # Exclude both config/metadata files; lambda.json is the legacy format
    skip_names = {"metadata.json", "lambda.json"}
    for item in ctx.path.rglob("*"):
        if item.is_file() and item.name not in skip_names and "test_cases" not in item.parts:
            rel = str(item.relative_to(ctx.path))
            content = item.read_text(errors="replace")
            source_files.append({"path": rel, "content": content})

    if not source_files:
        print("WARNING: No source files found to upload", file=sys.stderr)

    print(f"\nFiles to upload ({len(source_files)}):")
    for f in source_files:
        print(f"  {f['path']}")

    if args.dry_run:
        print("\n[DRY-RUN] Would upload files.")
        sys.exit(0)

    # Upload files
    print("\nUploading files...")
    success, data, msg = client.save_lambda_files(lambda_id=str(lambda_id), files=source_files)
    if not success:
        print(f"ERROR: Upload failed: {msg}", file=sys.stderr)
        sys.exit(1)
    print(f"  Uploaded {len(source_files)} file(s)")

    print("\nDone.")
    sys.exit(0)


if __name__ == "__main__":
    main()
