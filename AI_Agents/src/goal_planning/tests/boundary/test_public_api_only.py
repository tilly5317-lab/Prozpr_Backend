# AI_Agents/src/goal_planning/tests/boundary/test_public_api_only.py
import ast
from pathlib import Path

# This test runs only when the bridge layer exists.
# For now it's a placeholder that asserts the path is empty (or matches the rule when populated).
BRIDGE_DIR = Path(__file__).resolve().parents[5] / "app" / "services" / "ai_bridge" / "goal_planning"
FORBIDDEN_PREFIXES = (
    "goal_planning.engine",
    "goal_planning.agent",
    "AI_Agents.src.goal_planning.engine",
    "AI_Agents.src.goal_planning.agent",
)


def test_bridge_imports_only_public_api():
    """Bridge code (when it exists) must import only from top-level goal_planning."""
    if not BRIDGE_DIR.exists():
        return  # bridge not yet created; nothing to check
    violations: list[str] = []
    for py_file in BRIDGE_DIR.rglob("*.py"):
        tree = ast.parse(py_file.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if any(alias.name.startswith(p) for p in FORBIDDEN_PREFIXES):
                        violations.append(f"{py_file.name}:{node.lineno} import {alias.name}")
            elif isinstance(node, ast.ImportFrom) and node.module:
                if any(node.module.startswith(p) for p in FORBIDDEN_PREFIXES):
                    violations.append(f"{py_file.name}:{node.lineno} from {node.module}")
    assert not violations, "Bridge has internal imports:\n" + "\n".join(violations)
