# Action Requirements: [ACTION NAME]

## Purpose
What should this action do? Describe in plain language.

## Input
What information does the user provide?
- Parameter 1: description
- Parameter 2: description

## Output
What should the action return?
- Expected format (text, table, JSON)
- Key information to include

## API Details (if applicable)
- API endpoint: 
- Method: GET/POST/etc
- Authentication: 
- Required headers:

## Test Cases

**CRITICAL**: Create **3 different test cases** in `test_cases/` directory BEFORE running any tests (e.g., `test_1.json`, `test_2.json`, `test_3.json`). Each test case should include:

```json
{
  "prompt": "User input or query",
  "workflow_params": {
    "param1": "value1"
  },
  "expected_output": {
    "description": "What the output should contain",
    "validation": "similarity",
    "key_fields": ["field1", "field2"],
    "sample_output": "Example output format"
  }
}
```

### Test Case 1 (Basic/Common Use Case)
**Prompt**: "..."
**Workflow Params**: `{"param": "value"}`
**Expected Output**: 
- Description: "..."
- Key Fields: ["field1", "field2"]
- Validation: similarity (output should be structurally similar with valid data)

### Test Case 2 (Different Input Scenario)
**Prompt**: "..."
**Workflow Params**: `{"param": "different_value"}`
**Expected Output**: 
- Description: "..."
- Key Fields: ["field1", "field2"]
- Validation: similarity (output should be structurally similar with valid data)

### Test Case 3 (Edge Case or Alternative Scenario)
**Prompt**: "..."
**Workflow Params**: `{"param": "edge_case_value"}`
**Expected Output**: 
- Description: "..."
- Key Fields: ["field1", "field2"]
- Validation: similarity (output should be structurally similar with valid data)

## Notes
Any additional context, constraints, or edge cases.
