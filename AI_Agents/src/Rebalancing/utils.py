"""Pure helpers used across pipeline steps. No state, no I/O."""

from __future__ import annotations

from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP


def round_to_step(amount: Decimal, step: int) -> Decimal:
    """Round to the nearest multiple of `step`. step <= 1 means no rounding
    beyond integer. Sign is preserved."""
    if step <= 1:
        return amount.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    if amount == 0:
        return Decimal(0)
    sign = Decimal(-1) if amount < 0 else Decimal(1)
    abs_amt = abs(amount)
    quantized = (abs_amt / Decimal(step)).quantize(Decimal("1"), rounding=ROUND_HALF_UP) * Decimal(step)
    return sign * quantized


def floor_to_step(amount: Decimal, step: int) -> Decimal:
    """Round toward zero to the nearest multiple of `step`. Used when the
    sum of multiple rounded amounts must not exceed a hard cap (e.g. per-fund
    buy amounts must not exceed total available sell cash)."""
    if step <= 1:
        return amount.quantize(Decimal("1"), rounding=ROUND_DOWN)
    if amount == 0:
        return Decimal(0)
    sign = Decimal(-1) if amount < 0 else Decimal(1)
    abs_amt = abs(amount)
    floored = (abs_amt / Decimal(step)).quantize(Decimal("1"), rounding=ROUND_DOWN) * Decimal(step)
    return sign * floored


def compute_stcg(st_value: Decimal, st_cost: Decimal) -> Decimal:
    """Short-term gain (signed; negative = realised loss)."""
    return st_value - st_cost


def compute_ltcg(lt_value: Decimal, lt_cost: Decimal) -> Decimal:
    """Long-term gain (signed). Annual exemption is applied at portfolio
    level by the caller — this returns the gross figure."""
    return lt_value - lt_cost


def compute_exit_load(units_value_inr: Decimal, exit_load_pct: float) -> Decimal:
    """Exit load on the rupee value of units still inside the exit-load
    period. Returns 0 for non-positive inputs."""
    if exit_load_pct <= 0 or units_value_inr <= 0:
        return Decimal(0)
    return units_value_inr * Decimal(str(exit_load_pct)) / Decimal(100)


def estimate_tax(
    stcg: Decimal,
    ltcg: Decimal,
    regime: str,
    stcg_rate_pct: float,
    ltcg_rate_pct: float,
    ltcg_exemption: Decimal,
) -> Decimal:
    """Approximate tax estimate. STCG taxed at flat rate; LTCG above
    exemption at LT rate. Losses don't generate refunds — clamped at 0."""
    _ = regime  # reserved for future regime-specific routing
    stcg_tax = max(stcg, Decimal(0)) * Decimal(str(stcg_rate_pct)) / Decimal(100)
    taxable_ltcg = max(ltcg - ltcg_exemption, Decimal(0))
    ltcg_tax = taxable_ltcg * Decimal(str(ltcg_rate_pct)) / Decimal(100)
    return stcg_tax + ltcg_tax
