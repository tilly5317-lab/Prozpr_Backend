from pydantic import BaseModel


class AssetRange(BaseModel):
    min: float
    max: float


class IdealAllocation(BaseModel):
    large_cap: AssetRange
    mid_cap: AssetRange
    small_cap: AssetRange
    debt: AssetRange
    gold: AssetRange
    reasoning: str
