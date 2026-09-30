"""Request bodies."""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator

from autobot.data import TIMEFRAMES


class DataReq(BaseModel):
    source: Literal["demo", "alpaca"] = "demo"
    symbols: List[str] = Field(default_factory=lambda: ["SPY"], min_length=1)
    timeframe: str = "15Min"
    days: int = Field(90, ge=2, le=3650)
    rth_only: bool = True

    @field_validator("symbols")
    @classmethod
    def _clean(cls, v):
        out = [s.strip().upper() for s in v if s.strip()]
        if not out:
            raise ValueError("at least one symbol")
        return out

    @field_validator("timeframe")
    @classmethod
    def _tf(cls, v):
        if v not in TIMEFRAMES:
            raise ValueError(f"timeframe must be one of {list(TIMEFRAMES)}")
        return v


class RunReq(BaseModel):
    data: DataReq = Field(default_factory=DataReq)
    fee_bps: float = Field(0.0, ge=0, le=100)
    slippage_bps: float = Field(2.0, ge=0, le=100)
    allow_short: bool = False


class CompareReq(RunReq):
    strategies: Dict[str, Dict[str, Any]]  # name -> params (partial is fine, defaults fill in)


class DetailReq(RunReq):
    symbol: str
    strategy: str
    params: Dict[str, Any] = Field(default_factory=dict)


class SearchReq(RunReq):
    names: Optional[List[str]] = None
    train_frac: float = Field(0.7, gt=0.1, lt=0.95)
    grids: Optional[Dict[str, Dict[str, List[Any]]]] = None


class WalkForwardReq(RunReq):
    strategy: str
    train_bars: int = Field(1000, ge=50)
    test_bars: int = Field(250, ge=10)
    step_bars: Optional[int] = None  # default = test_bars
    rank_by: Literal["sharpe", "total_return_%", "sortino", "calmar"] = "sharpe"
    grid: Optional[Dict[str, List[Any]]] = None
    fixed_params: Optional[Dict[str, Any]] = None  # the comparison curve; defaults if None
