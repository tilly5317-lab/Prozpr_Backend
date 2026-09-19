"""build_rebal_facts_pack surfaces the Prozpr view only when passed (gated at the caller)."""
from types import SimpleNamespace

from app.domains.rebalancing.services.rebal_engine.service import build_rebal_facts_pack


def test_view_absent_by_default():
    assert "fund_house_view" not in build_rebal_facts_pack(SimpleNamespace())


def test_view_present_when_passed():
    pack = build_rebal_facts_pack(SimpleNamespace(), fund_house_view="OUR STANCE")
    assert pack["fund_house_view"] == "OUR STANCE"


def test_empty_view_not_added():
    # A None/empty slice (loader returned nothing, fail-closed) must not add the key.
    assert "fund_house_view" not in build_rebal_facts_pack(SimpleNamespace(), fund_house_view=None)
    assert "fund_house_view" not in build_rebal_facts_pack(SimpleNamespace(), fund_house_view="")
