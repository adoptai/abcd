# Adopt Agent Configuration Documentation

## Overview

Adopt uses a master orchestrator pattern to manage specialized agents. The orchestrator selects the appropriate agent based on the user's message and delegates work to that agent. Each agent operates independently with its own tools and prompt until it completes the user's intent, at which point control returns to the orchestrator to continue the conversation.

## Agent Architecture

### Master Orchestrator
- Analyzes incoming user messages
- Selects the most appropriate agent to handle the request
- Receives control back after an agent completes its task
- Resumes conversation flow

### Agent Execution Flow
1. **Orchestrator Selection**: User message triggers agent selection
2. **Agent Activation**: Selected agent receives the message and begins execution
3. **Tool Loop**: Agent runs in a loop, using provided tools to fulfill the user's intent
4. **Completion**: Agent sends final message through output text
5. **Return to Orchestrator**: Control returns to orchestrator to resume conversation

## Agent Configuration Format

An agent configuration consists of an array of operations that define the agent's behavior. Each operation has a specific role in the agent's execution pipeline.

### Operation Types

#### 1. PROMPT_AND_TOOLS_AGENT

This is the primary operation that defines the agent's behavior.

**Required Fields:**
- `id` (string): Unique identifier for this agent
- `operation` (string): Must be `"PROMPT_AND_TOOLS_AGENT"`
- `action_ids` (array): List of tool IDs available to this agent
- `system_prompt` (string): The instructions that guide the agent's behavior
- `model_string` (string): The AI model to use (e.g., `"claude-4-5-sonnet"`)

**Example:**
```javascript
{
  action_ids: [
    '2d5840ca-102f-4cd9-b059-ebf0e565a92e',
    '1559f59b-ee23-4ada-b9dc-0da3195685a3',
    'd703a608-3e4d-4012-a18d-f47252d0e033',
  ],
  id: 'exampleUberAgent',
  model_string: 'claude-4-5-sonnet',
  operation: 'PROMPT_AND_TOOLS_AGENT',
  system_prompt: 'You are a helpful assistant...',
}
```

#### 2. EXTRACT

This operation extracts specific data from a previous operation's output.

**Required Fields:**
- `id` (string): Unique identifier for this extraction operation
- `operation` (string): Must be `"EXTRACT"`
- `input` (string): ID of the operation to extract from
- `field` (string): The field name to extract from the input operation

**Example:**
```javascript
{
  field: 'message',
  id: 'extractAgentMessage',
  input: 'exampleUberAgent',
  operation: 'EXTRACT',
}
```

#### 3. OUTPUT_TEXT

This operation outputs the final response back to the user and returns control to the orchestrator.

**Required Fields:**
- `id` (string): Unique identifier for this output operation
- `operation` (string): Must be `"OUTPUT_TEXT"`
- `format_string` (string): Format string for the output (use `{}` as placeholder)
- `values` (array): Array of operation IDs whose outputs should be inserted into the format string
- `raw` (boolean): Whether to output raw text without additional formatting

**Example:**
```javascript
{
  format_string: '{}',
  id: 'outputAgentResponse',
  operation: 'OUTPUT_TEXT',
  raw: true,
  values: [
    'extractAgentMessage',
  ],
}
```

## Complete Agent Example

Here's a complete agent configuration for a shipping logistics assistant:

```javascript
[
  {
    action_ids: [
      '2d5840ca-102f-4cd9-b059-ebf0e565a92e',
      '1559f59b-ee23-4ada-b9dc-0da3195685a3',
      'd703a608-3e4d-4012-a18d-f47252d0e033',
    ],
    id: 'exampleUberAgent',
    model_string: 'claude-4-5-sonnet',
    operation: 'PROMPT_AND_TOOLS_AGENT',
    system_prompt: 'You are a helpful assistant.\nYou help users...\n\nConversation history is given to you. Use that to understand which step you are in and proceed accordingly. Stick very closely to the data from the tools provided to you. Do not make up any data. If the tools return no data, say so explicitly.',
  },
  {
    field: 'message',
    id: 'extractAgentMessage',
    input: 'exampleUberAgent',
    operation: 'EXTRACT',
  },
  {
    format_string: '{}',
    id: 'outputAgentResponse',
    operation: 'OUTPUT_TEXT',
    raw: true,
    values: [
      'extractAgentMessage',
    ],
  },
]
```

## Key Concepts

### Action IDs (Tools)
- `action_ids` is an array of tool identifiers that the agent can use
- These tools are available to the agent during its execution loop
- The agent can call these tools as many times as needed to complete the task

### System Prompt
- Defines the agent's role, capabilities, and behavior
- Should include clear instructions on how to use the available tools
- Should specify the expected workflow or steps to complete tasks
- Can reference conversation history to maintain context

### Execution Loop
- The agent runs continuously until it determines the user's intent is fulfilled
- During execution, the agent can:
  - Ask clarifying questions
  - Invoke tools multiple times
  - Process tool responses
  - Make decisions based on conversation history
- The loop terminates when the agent provides its final response

### Output Flow
1. Agent completes its task (PROMPT_AND_TOOLS_AGENT)
2. Result is extracted (EXTRACT operation)
3. Output is formatted and sent (OUTPUT_TEXT operation)
4. Control returns to the orchestrator

## Best Practices

### System Prompt Design
- Clearly define the agent's purpose and role
- Provide step-by-step instructions for complex workflows
- Emphasize accuracy and adherence to tool-provided data
- Include guidance on handling edge cases (e.g., no data returned)
- Reference conversation history for context awareness

### Tool Selection
- Only include tools that are relevant to the agent's purpose
- Ensure tools cover all necessary functionality for the agent's workflow
- Keep the number of tools manageable for the agent to use effectively

### Agent Scope
- Design agents with clear, focused purposes
- Avoid creating overly broad agents that try to do too much
- Let the orchestrator handle routing between specialized agents

