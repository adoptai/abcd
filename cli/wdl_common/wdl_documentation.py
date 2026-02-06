#!/usr/bin/env python3
"""
WDL Documentation Provider - Remote-First Roaming RAG.

Provides URLs and paths to WDL documentation. Cursor/LLMs roam the documentation
themselves using the index as the starting point - no keyword mapping needed.

The key insight: Cursor is intelligent enough to:
1. Fetch the index to see all available operations
2. Decide which operations are relevant to the task
3. Fetch those specific .md files as needed
"""

import os
from pathlib import Path
from typing import List, Optional


# Remote documentation URL (GitHub Pages)
WDL_DOCS_BASE_URL = "https://adoptai.github.io/widdle_docs/operations/"


class WDLDocumentationProvider:
    """Provides URLs/paths to WDL operation documentation for Cursor to roam."""

    def __init__(self, docs_path: Optional[Path] = None, use_remote: bool = True) -> None:
        """
        Initialize the documentation provider.

        Args:
            docs_path: Path to local documentation folder (optional fallback).
            use_remote: If True, prefer remote URLs over local paths.
        """
        self.use_remote = use_remote
        self.base_url = WDL_DOCS_BASE_URL
        self._docs_path = docs_path

    @property
    def docs_path(self) -> Optional[Path]:
        """Get local docs path (for fallback only)."""
        if self._docs_path:
            return self._docs_path
        return self._find_local_docs_path()

    def _find_local_docs_path(self) -> Optional[Path]:
        """Find the local WDL docs path (fallback)."""
        search_paths = [
            # Symlink in abcd repo
            Path(__file__).parent.parent.parent / "wdl_docs",
            # Direct path to ProjectA3
            Path(__file__).parent.parent.parent.parent
            / "ProjectA3/actionbot/explanation_prompt_md_collection",
            # Environment variable
            Path(os.getenv("WDL_DOCS_PATH", "")),
        ]

        for path in search_paths:
            if path.exists() and (path / "SYSTEM.md").exists():
                return path

        return None

    @property
    def index_url(self) -> str:
        """URL to the index - the operation index for Cursor to fetch first."""
        return f"{self.base_url}index.md"

    @property
    def system_index_path(self) -> Optional[Path]:
        """Path to local SYSTEM.md (fallback only)."""
        if self.docs_path:
            return self.docs_path / "SYSTEM.md"
        return None

    def get_operation_doc_url(self, operation_name: str) -> str:
        """
        Get URL to documentation for a specific operation.

        Args:
            operation_name: Name like "REST", "FILTER", "OUTPUT_TEXT"

        Returns:
            URL to the documentation file
        """
        # Try common naming patterns
        patterns = [
            f"{operation_name}_OPERATION_DESCRIPTION.md",
            f"{operation_name}_DESCRIPTION.md",
            f"{operation_name}.md",
        ]
        # Default to the most common pattern
        return f"{self.base_url}{patterns[0]}"

    def get_operation_doc_path(self, operation_name: str) -> Optional[Path]:
        """
        Get local path to documentation for a specific operation (fallback).

        Args:
            operation_name: Name like "REST", "FILTER", "OUTPUT_TEXT"

        Returns:
            Path to doc file or None if not found
        """
        if not self.docs_path:
            return None

        patterns = [
            f"{operation_name}_OPERATION_DESCRIPTION.md",
            f"{operation_name}_DESCRIPTION.md",
            f"{operation_name}.md",
        ]

        for pattern in patterns:
            doc_path = self.docs_path / pattern
            if doc_path.exists():
                return doc_path

        return None

    def list_all_doc_files(self) -> List[Path]:
        """List all local documentation files (fallback)."""
        if not self.docs_path:
            return []
        return sorted(self.docs_path.glob("*_DESCRIPTION.md"))

    def get_roaming_instructions(self) -> str:
        """
        Get instructions for Cursor on how to roam the documentation.

        Returns:
            Markdown instructions for Cursor
        """
        return f"""# WDL Documentation - Roaming RAG

## How to Use This Documentation

WDL operation documentation is available at:
`{self.base_url}`

### Step 1: Fetch the Index
Start by fetching the index to see all available operations:
- URL: `{self.index_url}`
- Contains: Brief description of each operation with filenames

### Step 2: Fetch Specific Operations
Based on the task, fetch only the operations you need:
- Append the filename from the index to the base URL
- Example: `{self.base_url}REST_OPERATION_DESCRIPTION.md`
- Files contain: full syntax, parameters, examples

### Step 3: Generate WDL
Use the fetched documentation to generate valid WDL.

**Do NOT fetch all files at once** - fetch the index first, then selectively 
fetch only what's needed for the current task.
"""
