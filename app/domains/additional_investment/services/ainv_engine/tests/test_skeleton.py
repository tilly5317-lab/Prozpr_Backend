"""Skeleton import contract for the additional_investment app domain.

Mirrors the rebalancing engine's lazy-chat invariant: importing the
``ainv_engine`` package must NOT eagerly import its ``chat`` submodule —
eager import risks a circular import via ``chat_core.turn_context``, exactly
as documented for ``rebal_engine``
(``app/domains/rebalancing/services/rebal_engine/CLAUDE.md``).

App imports are performed inside the test bodies (via importlib) so this test
module collects cleanly even before the skeleton exists; the red state is a
ModuleNotFoundError raised when each test runs.
"""

from __future__ import annotations

import importlib
import sys

_DOMAIN = "app.domains.additional_investment"
_SERVICES = _DOMAIN + ".services"
_AINV_ENGINE = _SERVICES + ".ainv_engine"
_AINV_TESTS = _AINV_ENGINE + ".tests"
_ROUTERS = _DOMAIN + ".routers"
_AINV_CHAT = _AINV_ENGINE + ".chat"


def test_package_and_subpackages_import():
    """Every skeleton package imports without error."""
    pkg = importlib.import_module(_DOMAIN)
    importlib.import_module(_SERVICES)
    importlib.import_module(_AINV_ENGINE)
    importlib.import_module(_AINV_TESTS)
    importlib.import_module(_ROUTERS)
    assert pkg is not None


def test_ainv_engine_init_does_not_import_chat_eagerly():
    """Importing the ainv_engine package must not pull in its chat submodule.

    Drop any cached copies first so we observe a genuinely fresh import of the
    package __init__, then assert the chat module name never landed in
    sys.modules as a side-effect.
    """
    for name in (_AINV_CHAT, _AINV_ENGINE):
        sys.modules.pop(name, None)

    importlib.import_module(_AINV_ENGINE)

    assert _AINV_CHAT not in sys.modules, (
        "ainv_engine/__init__.py must stay docstring-only — eagerly importing "
        "chat risks a circular import via chat_core.turn_context"
    )
