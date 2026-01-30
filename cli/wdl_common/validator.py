#!/usr/bin/env python3
"""
WDL Validator - Comprehensive validation before upload.

Catches common issues before they reach the platform:
- required_inputs format (must be list, not dict)
- Tool title format for orchestrator compatibility
- Operation references
- JSON structure
- OUTPUT_TEXT value references
"""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set


@dataclass
class ValidationResult:
    """Result of WDL validation."""
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    auto_fixes_applied: List[str] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        """Check if validation passed (no errors)."""
        return len(self.errors) == 0

    @property
    def has_warnings(self) -> bool:
        """Check if validation has warnings."""
        return len(self.warnings) > 0

    def __str__(self) -> str:
        """Format validation result for display."""
        lines = []

        if self.errors:
            lines.append("❌ VALIDATION ERRORS:")
            for err in self.errors:
                lines.append(f"   • {err}")

        if self.warnings:
            if lines:
                lines.append("")
            lines.append("⚠️  WARNINGS:")
            for warn in self.warnings:
                lines.append(f"   • {warn}")

        if self.auto_fixes_applied:
            if lines:
                lines.append("")
            lines.append("🔧 AUTO-FIXES APPLIED:")
            for fix in self.auto_fixes_applied:
                lines.append(f"   • {fix}")

        if not lines:
            lines.append("✅ Validation passed")

        return "\n".join(lines)


class WDLValidator:
    """
    Validate WDL before upload to catch issues early.

    Validates:
    1. JSON structure
    2. Required operation fields
    3. Operation references
    4. required_inputs format (Issue 2.2)
    5. Tool titles for orchestrator (Issue 2.3)
    6. OUTPUT_TEXT references (Issue 4.3)
    """

    # Anthropic tool name pattern
    TOOL_NAME_PATTERN = re.compile(r'^[a-zA-Z0-9_-]{1,128}$')

    # Valid operation types
    VALID_OPERATIONS = {
        "REST", "JQ_FILTER", "EXTRACT", "PROJECT", "OUTPUT_TEXT",
        "PROMPT", "PROMPT_AND_TOOLS_AGENT", "CONDITION", "FOR_EACH",
        "PARALLEL", "SET_VARIABLE", "GET_VARIABLE", "PAGINATE",
        "TRANSFORM", "AGGREGATE", "MERGE", "SPLIT", "WAIT",
    }

    def __init__(self, workspace: Optional[Path] = None):
        """
        Initialize validator.

        Args:
            workspace: Optional workspace path for context
        """
        self.workspace = workspace

    def validate(
        self,
        wdl: List[Dict[str, Any]],
        context: str = "action",
        auto_fix: bool = False,
    ) -> ValidationResult:
        """
        Run all validations and return results.

        Args:
            wdl: The WDL to validate
            context: "action" or "orchestrator" - affects title validation
            auto_fix: Whether to apply auto-fixes

        Returns:
            ValidationResult with errors, warnings, and fixes applied
        """
        result = ValidationResult()

        # 1. Basic structure validation
        result.errors.extend(self._validate_structure(wdl))

        if result.errors:
            # Stop early if basic structure is invalid
            return result

        # 2. Required fields validation
        result.errors.extend(self._validate_required_fields(wdl))

        # 3. Operation reference validation
        result.warnings.extend(self._validate_references(wdl))

        # 4. required_inputs format validation (Issue 2.2)
        req_errors = self._validate_required_inputs_format(wdl)
        if req_errors and auto_fix:
            self._auto_fix_required_inputs(wdl)
            result.auto_fixes_applied.append(
                "Converted required_inputs from dict to list format"
            )
        else:
            result.errors.extend(req_errors)

        # 5. Title/name validation for orchestrator context (Issue 2.3)
        if context == "orchestrator":
            title_errors = self._validate_tool_names(wdl)
            if title_errors and auto_fix:
                fixes = self._auto_fix_tool_titles(wdl)
                result.auto_fixes_applied.extend(fixes)
            else:
                result.errors.extend(title_errors)
        else:
            # Always warn about invalid titles even in action context
            title_warnings = self._validate_tool_names(wdl)
            if title_warnings:
                result.warnings.append(
                    "Tool titles contain special characters. "
                    "This will cause issues if used in PROMPT_AND_TOOLS_AGENT."
                )

        # 6. OUTPUT_TEXT reference validation (Issue 4.3)
        result.warnings.extend(self._validate_output_text_references(wdl))

        # 7. Validate duplicate IDs
        result.errors.extend(self._validate_duplicate_ids(wdl))

        return result

    def validate_file(
        self,
        wdl_path: Path,
        context: str = "action",
        auto_fix: bool = False,
    ) -> ValidationResult:
        """
        Validate WDL from a file.

        Args:
            wdl_path: Path to widdle.json
            context: "action" or "orchestrator"
            auto_fix: Whether to apply auto-fixes

        Returns:
            ValidationResult
        """
        result = ValidationResult()

        if not wdl_path.exists():
            result.errors.append(f"WDL file not found: {wdl_path}")
            return result

        try:
            wdl = json.loads(wdl_path.read_text())
        except json.JSONDecodeError as e:
            result.errors.append(
                f"Invalid JSON syntax at line {e.lineno}, column {e.colno}: {e.msg}"
            )
            return result

        file_result = self.validate(wdl, context, auto_fix)

        # Save auto-fixed WDL if fixes were applied
        if auto_fix and file_result.auto_fixes_applied:
            wdl_path.write_text(json.dumps(wdl, indent=2))

        return file_result

    # =========================================================================
    # Validation Methods
    # =========================================================================

    def _validate_structure(self, wdl: Any) -> List[str]:
        """Validate basic WDL structure."""
        errors = []

        if not isinstance(wdl, list):
            errors.append("WDL must be a JSON array/list")
            return errors

        if len(wdl) == 0:
            errors.append("WDL is empty - must have at least one operation")
            return errors

        for i, op in enumerate(wdl):
            if not isinstance(op, dict):
                errors.append(f"Operation {i} must be a JSON object")

        return errors

    def _validate_required_fields(self, wdl: List[Dict[str, Any]]) -> List[str]:
        """Validate required fields for each operation."""
        errors = []

        for i, op in enumerate(wdl):
            if not isinstance(op, dict):
                continue

            # Skip metadata blocks
            if self._is_metadata_block(op):
                continue

            # Check for id
            op_id = op.get("id")
            if not op_id:
                errors.append(f"Operation {i} missing required 'id' field")
            elif not isinstance(op_id, str):
                errors.append(f"Operation {i} has invalid 'id' (must be string)")

            # Check for operation type
            operation = op.get("operation")
            if not operation:
                errors.append(f"Operation {i} missing required 'operation' field")
            elif not isinstance(operation, str):
                errors.append(f"Operation {i} has invalid 'operation' (must be string)")
            elif operation not in self.VALID_OPERATIONS:
                # Just warn for unknown operations
                pass

            # Operation-specific validation
            if operation == "REST":
                if "url" not in op:
                    errors.append(f"Operation {i} (REST) missing required 'url' field")
                if "method" not in op:
                    errors.append(f"Operation {i} (REST) missing required 'method' field")

            elif operation == "JQ_FILTER":
                if "input" not in op and "inputs" not in op:
                    errors.append(
                        f"Operation {i} (JQ_FILTER) missing 'input' or 'inputs' field"
                    )
                if "filter" not in op:
                    errors.append(f"Operation {i} (JQ_FILTER) missing 'filter' field")

            elif operation == "EXTRACT":
                if "input" not in op:
                    errors.append(f"Operation {i} (EXTRACT) missing 'input' field")

            elif operation == "PROJECT":
                if "input" not in op:
                    errors.append(f"Operation {i} (PROJECT) missing 'input' field")
                if "fields" not in op:
                    errors.append(f"Operation {i} (PROJECT) missing 'fields' field")

        return errors

    def _validate_references(self, wdl: List[Dict[str, Any]]) -> List[str]:
        """Validate that input references point to valid IDs."""
        warnings = []

        # Collect all defined IDs and output_keys
        defined_ids: Set[str] = set()
        for op in wdl:
            if isinstance(op, dict):
                op_id = op.get("id")
                if op_id:
                    defined_ids.add(op_id)
                output_key = op.get("output_key")
                if output_key:
                    defined_ids.add(output_key)

        # Built-in references
        builtin_refs = {"workflow_arguments", "security_params", "status_codes"}

        # Check references
        for i, op in enumerate(wdl):
            if not isinstance(op, dict):
                continue

            # Check input field
            input_ref = op.get("input", "")
            if isinstance(input_ref, str) and input_ref.startswith("{"):
                ref_parts = self._extract_reference(input_ref)
                if ref_parts:
                    first_part = ref_parts[0]
                    if first_part not in defined_ids and first_part not in builtin_refs:
                        warnings.append(
                            f"Operation {i} references '{first_part}' which may not be defined"
                        )

            # Check inputs array
            inputs = op.get("inputs", [])
            if isinstance(inputs, list):
                for inp in inputs:
                    if isinstance(inp, str) and inp.startswith("{"):
                        ref_parts = self._extract_reference(inp)
                        if ref_parts:
                            first_part = ref_parts[0]
                            if first_part not in defined_ids and first_part not in builtin_refs:
                                warnings.append(
                                    f"Operation {i} references '{first_part}' which may not be defined"
                                )

        return warnings

    def _validate_required_inputs_format(self, wdl: List[Dict[str, Any]]) -> List[str]:
        """
        Validate required_inputs is a list of JSON strings, not a dict.

        The platform expects:
        "required_inputs": [
            "{\"field\": {\"type\": \"string\"}}"
        ]

        NOT:
        "required_inputs": {"field": {"type": "string"}}
        """
        errors = []

        for op in wdl:
            if not isinstance(op, dict):
                continue

            if "required_inputs" in op:
                req_inputs = op["required_inputs"]
                if isinstance(req_inputs, dict):
                    errors.append(
                        "required_inputs must be a list of JSON strings, not a dict. "
                        "Example: [\"{{\\\"field\\\": {{\\\"type\\\": \\\"string\\\"}}}}\"]"
                    )
                    break  # Only report once

        return errors

    def _validate_tool_names(self, wdl: List[Dict[str, Any]]) -> List[str]:
        """
        Validate tool titles match Anthropic pattern.

        Pattern: ^[a-zA-Z0-9_-]{1,128}$
        """
        errors = []

        for op in wdl:
            if not isinstance(op, dict):
                continue

            # Check metadata block for title
            if "metadata" in op:
                title = op["metadata"].get("title", "")
                if title and not self.TOOL_NAME_PATTERN.match(title):
                    sanitized = self._sanitize_title(title)
                    errors.append(
                        f"Title '{title}' contains invalid characters for orchestrator use. "
                        f"Use only: letters, numbers, hyphens, underscores. "
                        f"Suggested: '{sanitized}'"
                    )

        return errors

    def _validate_output_text_references(self, wdl: List[Dict[str, Any]]) -> List[str]:
        """
        Validate OUTPUT_TEXT value references.

        When using EXTRACT with output_key, OUTPUT_TEXT.values should reference
        the output_key, not the EXTRACT operation ID.
        """
        warnings = []

        # Map of EXTRACT IDs to their output_keys
        extract_output_keys: Dict[str, str] = {}
        for op in wdl:
            if isinstance(op, dict) and op.get("operation") == "EXTRACT":
                op_id = op.get("id")
                output_key = op.get("output_key")
                if op_id and output_key:
                    extract_output_keys[op_id] = output_key

        # Check OUTPUT_TEXT operations
        for op in wdl:
            if isinstance(op, dict) and op.get("operation") == "OUTPUT_TEXT":
                values = op.get("values", [])
                format_string = op.get("format_string", "")

                if isinstance(values, list):
                    for val in values:
                        if isinstance(val, str) and val in extract_output_keys:
                            output_key = extract_output_keys[val]
                            warnings.append(
                                f"OUTPUT_TEXT references EXTRACT '{val}' directly. "
                                f"Consider using output_key '{output_key}' instead for cleaner output."
                            )

        return warnings

    def _validate_duplicate_ids(self, wdl: List[Dict[str, Any]]) -> List[str]:
        """Check for duplicate operation IDs."""
        errors = []
        seen_ids: Set[str] = set()

        for i, op in enumerate(wdl):
            if not isinstance(op, dict):
                continue

            op_id = op.get("id")
            if op_id:
                if op_id in seen_ids:
                    errors.append(f"Duplicate operation ID: '{op_id}'")
                seen_ids.add(op_id)

        return errors

    # =========================================================================
    # Auto-Fix Methods
    # =========================================================================

    def _auto_fix_required_inputs(self, wdl: List[Dict[str, Any]]) -> None:
        """Convert dict required_inputs to list format."""
        for op in wdl:
            if isinstance(op, dict) and "required_inputs" in op:
                req_inputs = op["required_inputs"]
                if isinstance(req_inputs, dict):
                    # Convert dict to list of JSON strings
                    req_inputs_list = [
                        json.dumps({k: v})
                        for k, v in req_inputs.items()
                    ]
                    op["required_inputs"] = req_inputs_list

    def _auto_fix_tool_titles(self, wdl: List[Dict[str, Any]]) -> List[str]:
        """Sanitize tool titles for orchestrator compatibility."""
        fixes = []

        for op in wdl:
            if isinstance(op, dict) and "metadata" in op:
                title = op["metadata"].get("title", "")
                if title and not self.TOOL_NAME_PATTERN.match(title):
                    sanitized = self._sanitize_title(title)
                    op["metadata"]["title"] = sanitized
                    fixes.append(f"Title fixed: '{title}' → '{sanitized}'")

        return fixes

    # =========================================================================
    # Helper Methods
    # =========================================================================

    def _is_metadata_block(self, op: Dict[str, Any]) -> bool:
        """Check if operation is a metadata block (not a real operation)."""
        metadata_keys = {"metadata", "required_inputs", "suggestions", "statement"}
        return any(key in op for key in metadata_keys) and "operation" not in op

    def _extract_reference(self, ref_string: str) -> List[str]:
        """Extract reference parts from {ref.path.parts} format."""
        if not ref_string.startswith("{") or not ref_string.endswith("}"):
            return []

        content = ref_string.strip("{}")
        parts = content.split(".")
        return parts if parts else []

    def _sanitize_title(self, title: str) -> str:
        """Convert title to valid tool name format."""
        # Replace spaces with hyphens
        sanitized = title.replace(" ", "-")
        # Remove invalid characters
        sanitized = re.sub(r'[^a-zA-Z0-9_-]', '', sanitized)
        # Truncate to 128 chars
        return sanitized[:128]


# =========================================================================
# Convenience Functions
# =========================================================================

def validate_wdl(
    wdl: List[Dict[str, Any]],
    context: str = "action",
    auto_fix: bool = False,
) -> ValidationResult:
    """
    Validate WDL (convenience function).

    Args:
        wdl: The WDL to validate
        context: "action" or "orchestrator"
        auto_fix: Whether to apply auto-fixes

    Returns:
        ValidationResult
    """
    validator = WDLValidator()
    return validator.validate(wdl, context, auto_fix)


def validate_wdl_file(
    wdl_path: Path,
    context: str = "action",
    auto_fix: bool = False,
) -> ValidationResult:
    """
    Validate WDL file (convenience function).

    Args:
        wdl_path: Path to widdle.json
        context: "action" or "orchestrator"
        auto_fix: Whether to apply auto-fixes

    Returns:
        ValidationResult
    """
    validator = WDLValidator()
    return validator.validate_file(wdl_path, context, auto_fix)


