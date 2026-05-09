import re
import yaml
from pathlib import Path


class SkillExecutor:
    """Renders a Markdown skill definition into (system, user) prompts.

    Skill ``.md`` files use YAML front matter for metadata and two sections:
      ## System Prompt  — the system prompt with ``{{variable}}`` placeholders
      ## User Prompt    — the user prompt with ``{{variable}}`` placeholders

    Variables use double-brace ``{{variable}}`` syntax to avoid conflicts with
    JSON examples (single braces) in prompt text.

    The actual LLM call lives outside this class — callers use ``render()`` to
    get the rendered prompts plus ``meta`` (model, max_tokens, etc.) and then
    invoke the LLM directly. This keeps response-shape concerns (JSON parsing,
    forced tool-use, validation) with the orchestrator that owns the schema.
    """

    def __init__(self, skill_path: Path):
        content = skill_path.read_text()
        self.meta: dict = {}
        self.system_template: str = ""
        self.user_template: str = ""
        self._parse(content)

    def _parse(self, content: str) -> None:
        # Extract YAML front matter between first pair of --- delimiters
        parts = content.split("---")
        if len(parts) >= 3:
            self.meta = yaml.safe_load(parts[1]) or {}
            body = "---".join(parts[2:])
        else:
            body = content

        # Extract ## System Prompt section
        self.system_template = self._extract_section(body, "System Prompt")
        # Extract ## User Prompt section
        self.user_template = self._extract_section(body, "User Prompt")

    def _extract_section(self, body: str, heading: str) -> str:
        # Match ## <heading> up to the next ## heading or end of string
        pattern = rf"##\s+{re.escape(heading)}\s*\n(.*?)(?=\n##\s|\Z)"
        match = re.search(pattern, body, re.DOTALL)
        return match.group(1).strip() if match else ""

    def _render(self, template: str, variables: dict) -> str:
        # Replace {{variable_name}} with the corresponding value
        # Leaves all single-brace content (JSON examples) untouched
        def replacer(m: re.Match) -> str:
            key = m.group(1)
            return str(variables.get(key, m.group(0)))

        return re.sub(r"\{\{(\w+)\}\}", replacer, template)

    def render(self, **variables) -> tuple[str, str]:
        """Return ``(system, user)`` with all ``{{variable}}`` placeholders filled."""
        return (
            self._render(self.system_template, variables),
            self._render(self.user_template, variables),
        )
