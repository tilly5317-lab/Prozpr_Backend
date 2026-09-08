"""PII contract: the 422 handler logs field names, never submitted values."""

import logging

import pytest
from fastapi import FastAPI, Request
from pydantic import BaseModel, ValidationError

from app.core.exceptions import register_exception_handlers


class _Money(BaseModel):
    amount: int


def _make_validation_error() -> ValidationError:
    with pytest.raises(ValidationError) as exc_info:
        _Money(amount="SECRET_HOLDING_VALUE")
    return exc_info.value


async def test_validation_handler_does_not_log_input_values(caplog):
    app = FastAPI()
    register_exception_handlers(app)
    handler = app.exception_handlers[ValidationError]

    scope = {"type": "http", "method": "POST", "path": "/x", "headers": []}
    with caplog.at_level(logging.ERROR):
        await handler(Request(scope), _make_validation_error())

    logged = caplog.text
    assert "SECRET_HOLDING_VALUE" not in logged
    assert "amount" in logged  # field name is still useful and safe
