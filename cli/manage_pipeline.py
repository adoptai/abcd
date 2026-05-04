#!/usr/bin/env python3
"""
Create and manage pipeline workspaces.

Usage:
    python cli/manage_pipeline.py --create -t "My Pipeline" \\
        -d "What this pipeline does" \\
        --prompt "Fetch all contacts from Salesforce and store them"

    python cli/manage_pipeline.py --create -t "S3 Sync" \\
        --source-type connector \\
        --source-connector-id <connector_id> \\
        --source-connector-type amazon_s3 \\
        --source-connector-name "My S3 Bucket"

This creates a local workspace:
    workspaces/{env}/pipelines/{pipeline-id}/
        pipeline.json   – metadata
        widdle.json     – WDL (edit this directly)
        versions/       – version snapshots (populated by save_pipeline_draft.py)

After creating, edit widdle.json then:
    python cli/save_pipeline_draft.py <pipeline-id>     # push to platform
    python cli/test_pipeline.py <pipeline-id>           # run a test
    python cli/publish_pipeline.py <pipeline-id>        # publish
"""

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from cli.wdl_common.context import ensure_env
from cli.wdl_common.workspace_manager import get_workspace_manager


def _slugify(text: str) -> str:
    """Convert a title to a filesystem-safe slug."""
    slug = text.lower().strip()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s_]+", "-", slug)
    slug = re.sub(r"-+", "-", slug)
    return slug.strip("-")[:64]


def cmd_create(args: argparse.Namespace) -> int:
    """Create a new pipeline workspace."""
    env_name = ensure_env()
    manager = get_workspace_manager()

    pipeline_id = args.id or _slugify(args.title)

    # Build source config
    if args.source_type == "connector":
        if not args.source_connector_id:
            print("❌ --source-connector-id is required when --source-type=connector")
            return 1
        source = {
            "integration_id": args.source_connector_id,
            "integration_type": args.source_connector_type or "connector",
            "integration_name": args.source_connector_name or args.source_connector_id,
        }
    elif args.source_type == "salesforce":
        source = {
            "integration_id": args.source_connector_id or "salesforce",
            "integration_type": "salesforce",
            "integration_name": args.source_connector_name or "Salesforce",
        }
    else:
        source = {
            "integration_id": "internal_data_store",
            "integration_type": "internal",
            "integration_name": "Internal Data Store",
        }

    destinations = [{"type": "internal_data_store", "label": "results"}]
    if args.destination_connector_id:
        destinations.append(
            {
                "type": args.destination_connector_type or "connector",
                "integration_id": args.destination_connector_id,
                "label": args.destination_label or "output",
            }
        )

    success, path, message = manager.create_pipeline_workspace(
        pipeline_id=pipeline_id,
        name=args.title,
        description=args.description or "",
        prompt=args.prompt or "",
        source=source,
        destinations=destinations,
        schedule_type=args.schedule_type or "manual",
        env_name=env_name,
    )

    if not success:
        print(f"❌ {message}")
        return 1

    print(f"\n✅ {message}")
    print(f"   Environment : {env_name}")
    print(f"   Path        : {path}")
    print("\n📝 Next steps:")
    print(f"   1. Edit widdle.json:  {path / 'widdle.json'}")
    print(f"   2. Push draft:        python cli/save_pipeline_draft.py {pipeline_id}")
    print(f"   3. Run test:          python cli/test_pipeline.py {pipeline_id}")
    print(f"   4. Publish:           python cli/publish_pipeline.py {pipeline_id} --yes")
    print()
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    """Show details of a local pipeline workspace."""
    env_name = ensure_env()
    manager = get_workspace_manager()

    meta = manager.get_pipeline_workspace(args.pipeline_id, env_name)
    if not meta:
        print(f"❌ Pipeline workspace not found: {args.pipeline_id}")
        return 1

    if args.json:
        path = meta.pop("path", None)
        print(json.dumps(meta, indent=2, default=str))
        if path:
            meta["path"] = path
    else:
        print(f"\n📋 Pipeline: {meta['pipeline_id']}")
        print(f"   Name          : {meta['name']}")
        print(f"   Description   : {meta.get('description', '')}")
        print(f"   State         : {meta.get('state', 'local')}")
        print(f"   Remote ID     : {meta.get('remote_pipeline_id') or '(not yet pushed)'}")
        print(f"   Version ID    : {meta.get('version_id') or '(none)'}")
        print(f"   Source        : {meta.get('source', {}).get('integration_name', '?')}")
        print(f"   Schedule      : {meta.get('schedule_type', 'manual')}")
        path = meta.get("path")
        if path:
            wdl_path = Path(path) / "widdle.json"
            wdl = json.loads(wdl_path.read_text()) if wdl_path.exists() else []
            print(f"   WDL steps     : {len(wdl)}")
            print(f"   Path          : {path}")
        print()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create and manage pipeline workspaces",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Create with internal data store (default source)
  python cli/manage_pipeline.py --create -t "Sync Bank Clients" \\
      -d "Fetch all bank clients and store them" \\
      --prompt "Fetch all bank clients from portal and store"

  # Create with a Salesforce source
  python cli/manage_pipeline.py --create -t "Salesforce Contacts" \\
      --source-type salesforce \\
      --prompt "Sync all Salesforce contacts to internal store"

  # Create with a custom connector source
  python cli/manage_pipeline.py --create -t "S3 Data Sync" \\
      --source-type connector \\
      --source-connector-id my-s3-connector \\
      --source-connector-type amazon_s3 \\
      --source-connector-name "My S3 Bucket"

  # Show pipeline details
  python cli/manage_pipeline.py --show sync-bank-clients
        """,
    )

    # Mode
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--create", action="store_true", help="Create a new pipeline workspace")
    mode.add_argument("--show", metavar="PIPELINE_ID", help="Show pipeline workspace details")

    # Create options
    parser.add_argument("--id", help="Pipeline ID (slug). Derived from title if omitted.")
    parser.add_argument("--title", "-t", help="Pipeline title (required for --create)")
    parser.add_argument("--description", "-d", help="Pipeline description")
    parser.add_argument(
        "--prompt",
        help="One-line prompt describing what the pipeline does (used as dummy prompt for API)",
    )
    parser.add_argument(
        "--source-type",
        choices=["internal", "salesforce", "connector"],
        default="internal",
        help="Source type (default: internal)",
    )
    parser.add_argument(
        "--source-connector-id", help="Connector instance ID (for --source-type connector)"
    )
    parser.add_argument("--source-connector-type", help="Connector provider type (e.g. amazon_s3)")
    parser.add_argument("--source-connector-name", help="Human-readable source name")
    parser.add_argument("--destination-connector-id", help="Optional destination connector ID")
    parser.add_argument("--destination-connector-type", help="Destination connector type")
    parser.add_argument("--destination-label", help="Destination label (default: output)")
    parser.add_argument(
        "--schedule-type",
        choices=["manual", "cron"],
        default="manual",
        help="Schedule type (default: manual)",
    )
    parser.add_argument("--json", action="store_true", help="Output as JSON (for --show)")

    args = parser.parse_args()

    if args.create:
        if not args.title:
            parser.error("--title / -t is required with --create")
        if not args.prompt:
            args.prompt = args.title
        # Wrap show pipeline_id into Namespace for cmd_show
        return cmd_create(args)
    else:
        # --show
        args.pipeline_id = args.show
        return cmd_show(args)


if __name__ == "__main__":
    sys.exit(main())
