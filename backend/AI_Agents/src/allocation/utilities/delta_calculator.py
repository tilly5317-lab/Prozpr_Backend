from ..schemas.allocation import IdealAllocation
from ..schemas.portfolio import Portfolio
from ..schemas.delta import Delta, DeltaItem


class DeltaCalculator:
    def compute(self, ideal: IdealAllocation, portfolio: Portfolio | None) -> Delta | None:
        if portfolio is None:
            return None

        items = []
        for asset in ["large_cap", "mid_cap", "small_cap", "debt", "gold"]:
            r = getattr(ideal, asset)
            ideal_pct = round((r.min + r.max) / 2, 2)
            current_pct = getattr(portfolio, asset)
            delta_pct = round(ideal_pct - current_pct, 2)

            if delta_pct > 1:
                direction = "increase"
            elif delta_pct < -1:
                direction = "decrease"
            else:
                direction = "hold"

            items.append(
                DeltaItem(
                    asset_class=asset,
                    current_pct=current_pct,
                    ideal_pct=ideal_pct,
                    delta_pct=delta_pct,
                    direction=direction,
                )
            )

        return Delta(items=items)
