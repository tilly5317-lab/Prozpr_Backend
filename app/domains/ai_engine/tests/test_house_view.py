"""Structure-aware slicer: Prozpr-only must never leak a named house."""

import house_view as hv

_SAMPLE = """# Market Commentary — August 2026

**As of:** August 14, 2026

## Equities

### 1. Outlook?

Prozpr view: We are positive on equities over the medium term.

**ICICI Prudential (Aug 2026):** Constructive with staggered entry.

**HDFC (Jul 2026):** Optimistic medium-term.

### 2. Small caps?

**ICICI Prudential (Aug 2026):** No explicit small-cap call.

## Bonds

### 1. Fixed income?

Prozpr view: Prefer arbitrage funds for 1-2 year horizons.

**Kotak (Jul 2026):** No view found.

### 2. Direction of variables?

#### 2.1 Bond yields

Yields are expected to drift lower on liquidity.

**ICICI Prudential (Aug 2026):** Neutral duration.
"""

_HOUSES = ("ICICI", "HDFC", "Kotak", "Canara", "PPFAS", "CLSA")


def test_all_houses_returns_full_text(monkeypatch, tmp_path):
    monkeypatch.setattr(hv, "_VIEW_PATH", tmp_path / "v.md")
    (tmp_path / "v.md").write_text(_SAMPLE, encoding="utf-8")
    out = hv.load_house_view(prozpr_only=False)
    assert "ICICI Prudential" in out and "Prozpr view:" in out


def test_prozpr_only_excludes_every_house(monkeypatch, tmp_path):
    monkeypatch.setattr(hv, "_VIEW_PATH", tmp_path / "v.md")
    (tmp_path / "v.md").write_text(_SAMPLE, encoding="utf-8")
    out = hv.load_house_view(prozpr_only=True)
    assert "Prozpr view: We are positive" in out
    assert "Prozpr view: Prefer arbitrage" in out
    for h in _HOUSES:
        assert h not in out                       # no leak
    assert "## Equities" in out and "## Bonds" in out


def test_prozpr_only_drops_question_with_no_lead(monkeypatch, tmp_path):
    monkeypatch.setattr(hv, "_VIEW_PATH", tmp_path / "v.md")
    (tmp_path / "v.md").write_text(_SAMPLE, encoding="utf-8")
    out = hv.load_house_view(prozpr_only=True)
    assert "### 2. Small caps?" not in out        # no Prozpr lead → contributes nothing


def test_house_before_lead_is_dropped_not_leaked(monkeypatch, tmp_path):
    # A house block before the Prozpr lead is simply dropped — allow-list slicing
    # emits only the Prozpr paragraph, so the house never leaks even out of order.
    bad = "## Equities\n\n### 1. Q?\n\n**HDFC (Jul 2026):** x\n\nProzpr view: y\n"
    monkeypatch.setattr(hv, "_VIEW_PATH", tmp_path / "v.md")
    (tmp_path / "v.md").write_text(bad, encoding="utf-8")
    out = hv.load_house_view(prozpr_only=True)
    assert "Prozpr view: y" in out and "HDFC" not in out


def test_unknown_section_fails_closed(monkeypatch, tmp_path):
    monkeypatch.setattr(hv, "_VIEW_PATH", tmp_path / "v.md")
    (tmp_path / "v.md").write_text("## Crypto\n\n### 1. Q?\n\nProzpr view: x\n", encoding="utf-8")
    assert hv.load_house_view(prozpr_only=True) is None       # validator → fail-closed


def test_prozpr_only_ignores_nested_factual_content(monkeypatch, tmp_path):
    # Bonds "### 2" has no Prozpr view — its #### sub-question prose must NOT appear.
    monkeypatch.setattr(hv, "_VIEW_PATH", tmp_path / "v.md")
    (tmp_path / "v.md").write_text(_SAMPLE, encoding="utf-8")
    out = hv.load_house_view(prozpr_only=True)
    assert "Yields are expected" not in out and "### 2. Direction" not in out


def test_missing_file_returns_none(monkeypatch, tmp_path):
    monkeypatch.setattr(hv, "_VIEW_PATH", tmp_path / "absent.md")
    assert hv.load_house_view(prozpr_only=True) is None


def test_validator_flags_unknown_section():
    errs = hv.validate_house_view("## Crypto\n\n### 1. Q?\n\nProzpr view: x\n")
    assert errs and any("Crypto" in e for e in errs)
