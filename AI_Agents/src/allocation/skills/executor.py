import re
import json
import yaml
from pathlib import Path


class LLMParsingError(Exception):
    def __init__(self, raw_response: str):
        self.raw_response = raw_response
        super().__init__(f"Failed to parse LLM response: {raw_response[:200]}")


def _is_asset_range_fragment(d: dict) -> bool:
    """
    True if this dict is only a single min/max range, not the skill root object.

    raw_decode from the first `{` often grabs an inner {"min": x, "max": y} from
    nested JSON instead of the full allocation/recommendation object.
    """
    ks = set(d.keys())
    return ks <= {"min", "max"} and "min" in d and "max" in d


def _root_json_score(d: dict) -> tuple[int, int]:
    """
    Prefer objects that look like IdealAllocation or Recommendation roots.
    Higher tuple sorts greater.
    """
    anchors = (
        "large_cap",
        "mid_cap",
        "small_cap",
        "debt",
        "gold",
        "reasoning",
        "narrative",
        "action_items",
        "confidence",
        "disclaimers",
    )
    hits = sum(1 for k in anchors if k in d)
    return (hits, len(d))


def _pick_best_dict(candidates: list[dict]) -> dict | None:
    if not candidates:
        return None
    proper = [d for d in candidates if not _is_asset_range_fragment(d)]
    if not proper:
        return None
    return max(proper, key=_root_json_score)


class SkillExecutor:
    """
    Generic skill runner that reads a Markdown skill definition file.

    Skill .md files use YAML front matter for metadata and two sections:
      ## System Prompt  — the system prompt with {{variable}} placeholders
      ## User Prompt    — the user prompt with {{variable}} placeholders

    Variables use double-brace {{variable}} syntax to avoid conflicts
    with JSON examples (single braces) in prompt text.
    """

    def __init__(self, skill_path: Path, llm_client):
        content = skill_path.read_text()
        self.meta: dict = {}
        self.system_template: str = ""
        self.user_template: str = ""
        self._parse(content)
        self.llm = llm_client

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

    def _parse_json(self, raw: str) -> dict:
        """
        Parse a single JSON object from the model response.

        Models often prepend markdown or chain-of-thought despite instructions; we try:
        whole string, fenced ```json blocks, then json.JSONDecoder.raw_decode from each `{`.

        When multiple `{...}` objects exist, we skip inner AssetRange fragments (only min/max)
        and pick the dict that matches the expected skill root (most schema anchor keys).
        """
        if not raw or not raw.strip():
            raise LLMParsingError(raw or "")

        def try_load(s: str) -> dict | None:
            s = s.strip()
            if not s:
                return None
            try:
                val = json.loads(s)
                return val if isinstance(val, dict) else None
            except json.JSONDecodeError:
                return None

        def iter_raw_decode_dicts(text: str) -> list[dict]:
            out: list[dict] = []
            i = 0
            while i < len(text):
                j = text.find("{", i)
                if j == -1:
                    break
                try:
                    obj, _ = json.JSONDecoder().raw_decode(text[j:])
                    if isinstance(obj, dict) and obj:
                        out.append(obj)
                except json.JSONDecodeError:
                    pass
                i = j + 1
            return out

        cleaned = raw.strip()
        if cleaned.startswith("```json"):
            cleaned = cleaned[7:]
        elif cleaned.startswith("```"):
            cleaned = cleaned[3:]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        cleaned = cleaned.strip()

        candidates: list[dict] = []

        for blob in (cleaned, raw.strip()):
            whole = try_load(blob)
            if whole is not None:
                candidates.append(whole)
            candidates.extend(iter_raw_decode_dicts(blob))

        for m in re.finditer(r"```(?:json)?\s*([\s\S]*?)\s*```", raw):
            inner = m.group(1).strip()
            whole = try_load(inner)
            if whole is not None:
                candidates.append(whole)
            candidates.extend(iter_raw_decode_dicts(inner))

        best = _pick_best_dict(candidates)
        if best is not None:
            return best

        raise LLMParsingError(raw)

    async def run(self, **variables) -> tuple[dict, dict]:
        system = self._render(self.system_template, variables)
        user = self._render(self.user_template, variables)
        raw, usage = await self.llm.call(
            model=self.meta.get("model", "haiku"),
            system=system,
            user=user,
            max_tokens=self.meta.get("max_tokens", 1024),
        )
        return self._parse_json(raw), usage
