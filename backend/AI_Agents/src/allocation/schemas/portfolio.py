from pydantic import BaseModel


class Portfolio(BaseModel):
    large_cap: float
    mid_cap: float
    small_cap: float
    debt: float
    gold: float
