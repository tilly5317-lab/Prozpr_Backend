"""Shared 'reasoning-first, discarded' tool contract for free-text surfaces.

A surface that should think privately before answering binds the tool from
``reasoned_reply_tool`` with forced ``tool_choice`` and reads the answer back with
``extract_reasoned_reply``. The thinking field is declared FIRST so its tokens are
generated before — and therefore condition — the answer tokens, and the backend never
returns it to the customer.

Self-contained (stdlib only); must not import any peer agent module. Duck-types the
response's ``.tool_calls`` so it does not depend on langchain.
"""

from __future__ import annotations

DEFAULT_REASONING_DESC = (
    "Your private scratchpad. Think through the answer here — which figures apply, which "
    "named band a score maps to, whether to include market context, what to leave out. The "
    "customer NEVER sees this field; it is discarded. Do all working-out here so the answer "
    "field stays clean (no preamble, no raw scores, no internal field names). Keep this "
    "brief — a few lines, not paragraphs."
)


def reasoned_reply_tool(
    *,
    name: str,
    answer_description: str,
    answer_field: str = "answer",
    thinking_field: str = "reasoning",
    thinking_description: str = DEFAULT_REASONING_DESC,
) -> dict:
    """Build an Anthropic tool whose input_schema declares ``thinking_field`` FIRST then
    ``answer_field``, both required. Field order is load-bearing — do not reorder."""
    return {
        "name": name,
        "description": (
            "Return the final customer-facing reply. Call this exactly once. Put your "
            "private working-out in the thinking field (discarded) and the clean, "
            "customer-ready text in the answer field. Emit no free-text outside this call."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                thinking_field: {"type": "string", "description": thinking_description},
                answer_field: {"type": "string", "description": answer_description},
            },
            "required": [thinking_field, answer_field],
        },
    }


def extract_reasoned_reply(response, *, answer_field: str = "answer") -> str | None:
    """Return the answer field from a forced-tool response, or None if the tool call is
    missing/empty/malformed (caller falls back). The thinking field is never returned."""
    tool_calls = getattr(response, "tool_calls", None) or []
    for call in tool_calls:
        args = (call.get("args") if isinstance(call, dict) else None) or {}
        value = args.get(answer_field)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None
