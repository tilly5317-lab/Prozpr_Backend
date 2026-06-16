"""Doc-gen must emit through the forced tool and discard the `outline` field."""

from unittest.mock import MagicMock, patch

import market_commentary.document_generator as dg


def test_generate_document_returns_document_and_discards_outline():
    resp = MagicMock()
    resp.tool_calls = [
        {
            "name": "return_commentary_document",
            "args": {
                "outline": "SECRET PLAN",
                "document": "# Prozpr\nMarket Commentary...",
            },
        }
    ]
    # Isolate the extract+discard behaviour from prompt/snapshot plumbing.
    with (
        patch.object(dg, "_build_prompt_vars", return_value={}),
        patch.object(dg, "DOCUMENT_GENERATION_PROMPT") as prompt,
        patch.object(dg, "_llm_bound") as bound,
    ):
        prompt.format_messages.return_value = ["m"]
        bound.invoke.return_value = resp
        out = dg.generate_document(snapshot=None)
    assert out.startswith("# Prozpr")
    assert "SECRET PLAN" not in out
