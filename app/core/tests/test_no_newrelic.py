"""New Relic is fully removed."""

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def test_no_newrelic_references_in_app_code():
    result = subprocess.run(
        [
            "grep",
            "-rin",
            "newrelic\\|new_relic",
            "app/",
            "--include=*.py",
            # This file names the thing it forbids; excluding it keeps the guard
            # from matching its own search pattern.
            f"--exclude={Path(__file__).name}",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.stdout.strip() == "", f"New Relic references remain:\n{result.stdout}"


def test_newrelic_ini_is_gone():
    assert not (ROOT / "newrelic.ini").exists()


def test_newrelic_not_in_requirements():
    reqs = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    assert "newrelic" not in reqs.lower()
