"""Claude API wrapper with conversation context and per-project serialization."""

import asyncio
import json
import logging
from collections import defaultdict
from typing import Any, AsyncIterator, Awaitable, Callable

import anthropic

from app.config import settings

logger = logging.getLogger(__name__)

# Per-project locks to serialize Claude API calls
_project_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

# Type alias for the tool executor callback
ToolExecutor = Callable[[str, dict[str, Any]], Awaitable[str]]

SYSTEM_PROMPT = """You are an API specification elicitation assistant. You help developers document and design web APIs by:

1. Asking clarifying questions about endpoints, request/response shapes, authentication, and error handling
2. Analyzing HAR recordings and screenshots of existing API behavior
3. Suggesting OpenAPI/Swagger specification structures
4. Identifying gaps, inconsistencies, and areas needing more detail

You have access to the conversation history, HAR data, screenshots, and project context.
Be concise, technical, and actionable. When you have enough information about an endpoint,
proactively suggest specification snippets in OpenAPI 3.x YAML format.

Keep responses focused and under 500 words unless the user asks for a detailed specification.

## Available Tools

You have tools to query timeline data captured from the user's browser sessions:

- **query_timeline**: Search and filter captured events (network requests, clicks, form inputs, narrations, URL changes). Use this to find specific API calls, user interactions, or to understand a workflow sequence. You can filter by event type and search text.

- **get_capture_summary**: Get a high-level overview of all captured data — event counts by type, unique URLs visited, discovered API endpoints, and time range. Use this first to understand what data is available before drilling into specifics.

When the user asks about their API, endpoints, or browsing session, use these tools to retrieve relevant data rather than asking the user to describe it. Reference the data naturally in your responses without explaining that you used a tool."""

TOOL_DEFINITIONS = [
    {
        "name": "query_timeline",
        "description": (
            "Query timeline events for the current project. Returns chronological "
            "events including network requests, user interactions (clicks, inputs), "
            "narrations, URL changes, and capture session markers. Use this to "
            "understand what the user did during their browsing session and what "
            "API calls were made."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "event_type": {
                    "type": "string",
                    "description": (
                        "Filter by event type. Options: network_request, click, "
                        "input, change, narration, url_change, capture_start, "
                        "capture_stop, message. Omit to return all types."
                    ),
                },
                "search_text": {
                    "type": "string",
                    "description": (
                        "Text to search for in event summaries and metadata. "
                        "Useful for finding events related to specific URLs, API "
                        "endpoints, or user actions. Case-insensitive substring match."
                    ),
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of events to return. Default 50, max 200.",
                },
            },
        },
    },
    {
        "name": "get_capture_summary",
        "description": (
            "Get aggregate statistics about captured timeline data for the project. "
            "Returns event counts by type, unique URLs visited, API endpoints "
            "discovered, and the time range of captured data. Use this for a "
            "high-level overview before drilling into specifics with query_timeline."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
]

MAX_TOOL_ITERATIONS = 5


def get_client() -> anthropic.AsyncAnthropic | None:
    """Return an async Anthropic client, or None if no API key is configured."""
    if not settings.anthropic_api_key:
        return None
    return anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)


def build_messages(conversation_history: list[dict]) -> list[dict]:
    """Convert our DB message format to Anthropic API message format."""
    messages = []
    for msg in conversation_history:
        role = "user" if msg["role"] == "human" else "assistant"
        content = msg["content"]
        # Add content_type prefix for voice transcripts
        if msg.get("content_type") == "voice_transcript":
            content = f"[Voice transcript] {content}"
        messages.append({"role": role, "content": content})
    return messages


def build_context_block(
    project: dict | None = None,
    processes: list[dict] | None = None,
    screenshots: list[dict] | None = None,
    har_summaries: list[str] | None = None,
) -> str:
    """Build a context summary block to prepend to the system prompt."""
    parts = []
    if project:
        parts.append(f"Project: {project['name']}")
        if project.get("description"):
            parts.append(f"Description: {project['description']}")
    if processes:
        proc_names = ", ".join(p["name"] for p in processes)
        parts.append(f"Processes: {proc_names}")
    if screenshots:
        parts.append(f"Screenshots captured: {len(screenshots)}")
    if har_summaries:
        parts.append("HAR recordings available:")
        for s in har_summaries[:5]:
            parts.append(f"  - {s}")
    if not parts:
        return ""
    return "\n".join(["[Current Context]", *parts, ""])


async def stream_response(
    project_id: str,
    conversation_history: list[dict],
    context_block: str = "",
) -> AsyncIterator[str]:
    """Stream a Claude response for the given conversation, yielding text deltas.

    Acquires per-project lock to serialize concurrent requests.
    """
    client = get_client()
    if client is None:
        yield "[Claude API key not configured. Set ANTHROPIC_API_KEY environment variable.]"
        return

    messages = build_messages(conversation_history)
    if not messages:
        yield "[No messages in conversation]"
        return

    system = SYSTEM_PROMPT
    if context_block:
        system = f"{context_block}\n{system}"

    async with _project_locks[project_id]:
        try:
            async with client.messages.stream(
                model=settings.claude_model,
                max_tokens=2048,
                system=system,
                messages=messages,
            ) as stream:
                async for text in stream.text_stream:
                    yield text
        except anthropic.APIError as e:
            logger.error("Claude API error: %s", e)
            yield f"\n[Claude API error: {e.message}]"
        except Exception as e:
            logger.error("Unexpected error streaming Claude response: %s", e)
            yield f"\n[Error: {str(e)}]"


async def stream_response_with_tools(
    project_id: str,
    conversation_history: list[dict],
    context_block: str = "",
    tools: list[dict] | None = None,
    tool_executor: ToolExecutor | None = None,
) -> AsyncIterator[str]:
    """Stream a Claude response with tool use support, yielding text deltas.

    When Claude requests a tool call, executes it via tool_executor and
    continues the conversation. Text deltas are yielded for each streaming
    segment. Tool calls are transparent — only final text reaches the user.
    """
    client = get_client()
    if client is None:
        yield "[Claude API key not configured. Set ANTHROPIC_API_KEY environment variable.]"
        return

    messages = build_messages(conversation_history)
    if not messages:
        yield "[No messages in conversation]"
        return

    system = SYSTEM_PROMPT
    if context_block:
        system = f"{context_block}\n{system}"

    api_kwargs: dict[str, Any] = {
        "model": settings.claude_model,
        "max_tokens": 2048,
        "system": system,
        "messages": messages,
    }
    if tools:
        api_kwargs["tools"] = tools

    async with _project_locks[project_id]:
        for _iteration in range(MAX_TOOL_ITERATIONS):
            try:
                async with client.messages.stream(**api_kwargs) as stream:
                    async for text in stream.text_stream:
                        yield text
                    response = await stream.get_final_message()

                if response.stop_reason != "tool_use":
                    return

                # Extract tool_use blocks
                tool_use_blocks = [
                    block for block in response.content
                    if block.type == "tool_use"
                ]
                if not tool_use_blocks or tool_executor is None:
                    return

                # Append assistant message to conversation for next API call
                # Only include API-permitted fields (SDK objects contain extra
                # internal fields like parsed_output that the API rejects).
                def _serialize_block(block):
                    if block.type == "text":
                        return {"type": "text", "text": block.text}
                    if block.type == "tool_use":
                        return {"type": "tool_use", "id": block.id,
                                "name": block.name, "input": block.input}
                    return {"type": block.type}

                messages.append({
                    "role": "assistant",
                    "content": [_serialize_block(b) for b in response.content],
                })

                # Execute each tool and collect results
                tool_results = []
                for tool_block in tool_use_blocks:
                    try:
                        result = await tool_executor(tool_block.name, tool_block.input)
                    except Exception as e:
                        logger.error("Tool execution error (%s): %s", tool_block.name, e)
                        result = json.dumps({"error": str(e)})
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": tool_block.id,
                        "content": result,
                    })

                # Append tool results as user message
                messages.append({"role": "user", "content": tool_results})
                api_kwargs["messages"] = messages

            except anthropic.APIError as e:
                logger.error("Claude API error: %s", e)
                yield f"\n[Claude API error: {e.message}]"
                return
            except Exception as e:
                logger.error("Unexpected error streaming Claude response: %s", e)
                yield f"\n[Error: {str(e)}]"
                return

        yield "\n[Reached maximum tool call iterations]"
