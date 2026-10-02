"""One fund-count rule for rebalancing and additional investment: two funds per
subgroup at or above the line, one below it — on the whole corpus."""

from decimal import Decimal

import pytest

from Rebalancing.config import funds_per_subgroup


@pytest.mark.parametrize("corpus, n", [(Decimal("4999999"), 1), (Decimal("5000000"), 2), (5_000_000.0, 2)])
def test_two_funds_at_or_above_fifty_lakh(corpus, n):
    assert funds_per_subgroup(corpus) == n
