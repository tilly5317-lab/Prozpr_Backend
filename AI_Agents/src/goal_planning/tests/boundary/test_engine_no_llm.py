# AI_Agents/src/goal_planning/tests/boundary/test_engine_no_llm.py
import ast
from pathlib import Path

ENGINE_DIR = Path(__file__).resolve().parents[2] / "engine"
FORBIDDEN = ("langchain_anthropic", "anthropic", "langchain_core", "langgraph")


def test_engine_has_no_llm_imports():
    """Engine must have zero LLM imports — including anthropic exceptions (stricter than project rule)."""
    violations: list[str] = []
    for py_file in ENGINE_DIR.rglob("*.py"):
        tree = ast.parse(py_file.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if any(alias.name == p or alias.name.startswith(p + ".") for p in FORBIDDEN):
                        violations.append(f"{py_file.name}:{node.lineno} import {alias.name}")
            elif isinstance(node, ast.ImportFrom) and node.module:
                if any(node.module == p or node.module.startswith(p + ".") for p in FORBIDDEN):
                    violations.append(f"{py_file.name}:{node.lineno} from {node.module}")
    assert not violations, "Engine has LLM imports:\n" + "\n".join(violations)
