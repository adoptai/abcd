#!/usr/bin/env python3
"""
Update an existing ADOPT zaction.

This script:
1. Takes an action_id as input
2. Looks for a folder named action_id in the cli folder
3. Reads widdle.json, description.txt, and api_details.json from that folder
4. Publishes the widdle, populates instructions, saves draft, approves version
5. Patches the description and api_id
"""

import sys
import json
from pathlib import Path
from time import sleep

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from cli.auth import get_bearer_token
from cli.create_adopt_zaction import (
    fetch_tool_details,
    publish_widdle,
    populate_instructions,
    save_draft,
    approve_version,
    update_zaction_description,
    update_zaction_api_ids,
)


def update_adopt_zaction(action_id: str) -> bool:
    """
    Main workflow to update an existing ADOPT zaction.
    
    This workflow follows the same pattern as patch_url_params_workflow in patch_wdls.py:
    1. Reads widdle.json from cli/{action_id}/
    2. Publishes widdle with draft_id=None
    3. Immediately fetches to get post-publish updated_at
    4. Waits 3 seconds then polls for updated_at to change
    5. Populates instructions and polls for updated_at to change
    6. Saves draft and approves version
    7. Patches description from description.txt
    8. Patches api_id from api_details.json
    
    Args:
        action_id: The action ID to update
        
    Returns:
        bool: True if successful, False otherwise
    """
    print("\n" + "=" * 80)
    print("🔄 UPDATE ADOPT ZACTION")
    print("=" * 80)
    print(f"Action ID: {action_id}")
    print("=" * 80)
    
    # Step 1: Look for the folder
    cli_folder = Path(__file__).parent
    action_folder = cli_folder / action_id
    
    if not action_folder.exists():
        print(f"❌ Folder not found: {action_folder}")
        return False
    
    if not action_folder.is_dir():
        print(f"❌ Path is not a directory: {action_folder}")
        return False
    
    print(f"📁 Found folder: {action_folder}")
    
    # Step 2: Read widdle.json
    widdle_path = action_folder / "widdle.json"
    if not widdle_path.exists():
        print(f"❌ widdle.json not found in {action_folder}")
        return False
    
    try:
        with open(widdle_path, 'r') as f:
            widdle_data = json.load(f)
        print(f"✅ Loaded widdle.json ({len(widdle_data)} blocks)")
    except json.JSONDecodeError as e:
        print(f"❌ Failed to parse widdle.json: {e}")
        return False
    
    # Step 3: Read description.txt (optional)
    description_path = action_folder / "description.txt"
    description = None
    if description_path.exists():
        with open(description_path, 'r') as f:
            description = f.read().strip()
        print(f"✅ Loaded description.txt ({len(description)} chars)")
    else:
        print("⚠️  description.txt not found, will skip description update")
    
    # Step 4: Read api_details.json for api_id (optional)
    api_details_path = action_folder / "api_details.json"
    api_id = None
    if api_details_path.exists():
        try:
            with open(api_details_path, 'r') as f:
                api_details = json.load(f)
            api_id = api_details.get('api_id')
            if api_id:
                print(f"✅ Loaded api_id from api_details.json: {api_id}")
            else:
                print("⚠️  api_id key not found in api_details.json, will skip api_id update")
        except json.JSONDecodeError as e:
            print(f"⚠️  Failed to parse api_details.json: {e}, will skip api_id update")
    else:
        print("⚠️  api_details.json not found, will skip api_id update")
    
    try:
        # Step 5: Get authentication token
        print("\n🔐 Authenticating with AdoptAI API...")
        bearer_token = get_bearer_token()
        print("✅ Authentication successful")
        
        # Step 6: Fetch current tool details (just to verify the tool exists)
        print("\n📋 Step 1: Fetching current tool details...")
        success, tool_data, message = fetch_tool_details(bearer_token, action_id)
        
        if not success or not tool_data:
            print(f"❌ Failed to fetch tool details: {message}")
            return False
        
        tool_title = tool_data.get('title', 'Untitled')
        print(f"   Tool: {tool_title}")
        
        # Step 7: Publish widdle with draft_id=None (matching patch_url_params_workflow pattern)
        print("\n📝 Step 2: Publishing widdle...")
        success, message = publish_widdle(bearer_token, action_id, None, widdle_data)
        
        if not success:
            print(f"❌ Failed to publish widdle: {message}")
            return False
        
        # Step 8: Immediately fetch to get the post-publish updated_at
        print("\n⏳ Step 3: Fetching tool details immediately after publish...")
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
        
        # Step 9: Populate instructions (always regenerate after WDL update)
        # Capture current updated_at before populate
        current_updated_at_before_populate = tool_data.get('updated_at')
        
        print("\n📝 Step 4: Populating instructions from updated WDL...")
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
        
        # Step 10: Save draft
        print("\n💾 Step 5: Saving draft...")
        success, version_number, message = save_draft(bearer_token, action_id, draft_id)
        
        if not success:
            print(f"❌ Failed to save draft: {message}")
            return False
        
        # Step 11: Approve version
        if not version_number:
            print("❌ No version number returned from save draft")
            return False
        
        print("\n✅ Step 6: Approving version...")
        success, message = approve_version(bearer_token, action_id, version_number)
        
        if not success:
            print(f"❌ Failed to approve version: {message}")
            return False
        
        # Step 12: Patch description if available
        if description:
            print("\n📝 Step 7: Updating zaction description...")
            success, message = update_zaction_description(
                bearer_token=bearer_token,
                action_id=action_id,
                new_description=description
            )
            if not success:
                print(f"   ⚠️  Warning: {message}")
        else:
            print("\n⏭️  Step 7: Skipping description update (no description.txt)")
        
        # Step 13: Patch api_id if available
        if api_id:
            print("\n🔗 Step 8: Associating zaction with API ID...")
            success, message = update_zaction_api_ids(
                bearer_token=bearer_token,
                action_id=action_id,
                api_ids=[api_id]
            )
            if not success:
                print(f"   ⚠️  Warning: {message}")
        else:
            print("\n⏭️  Step 8: Skipping api_id update (no api_id found)")
        
        # Final success message
        print("\n" + "=" * 80)
        print("🎉 ZACTION UPDATED SUCCESSFULLY!")
        print("=" * 80)
        print(f"   Action ID: {action_id}")
        print(f"   Version: {version_number}")
        if description:
            print("   Description: Updated")
        if api_id:
            print(f"   API ID: {api_id}")
        print("=" * 80 + "\n")
        
        return True
        
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    """Main entry point for the update_adopt_zaction script."""
    print("\n" + "=" * 80)
    print("🔧 UPDATE ADOPT ZACTION - AdoptAI Tool Builder")
    print("=" * 80)
    print("This script updates an existing zaction from files in cli/{action_id}/")
    print("=" * 80 + "\n")
    
    # Get action_id from command line or prompt
    if len(sys.argv) > 1:
        action_id = sys.argv[1].strip()
    else:
        action_id = input("Enter the Action ID: ").strip()
    
    if not action_id:
        print("❌ No Action ID provided")
        sys.exit(1)
    
    # Update the zaction
    success = update_adopt_zaction(action_id)
    
    if success:
        print("✅ Zaction updated successfully!")
        sys.exit(0)
    else:
        print("❌ Failed to update zaction")
        sys.exit(1)


if __name__ == "__main__":
    main()
