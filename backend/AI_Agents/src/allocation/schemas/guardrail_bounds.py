from pydantic import BaseModel


class AssetBound(BaseModel):
    min_pct: float
    max_pct: float


class GuardrailBounds(BaseModel):
    large_cap: AssetBound
    mid_cap: AssetBound
    small_cap: AssetBound
    debt: AssetBound
    gold: AssetBound
