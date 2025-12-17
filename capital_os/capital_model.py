"""Chain-agnostic capital model for the core operating system."""

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Dict, Optional, Tuple


class LiquidityClass(Enum):
    LIQUID = "liquid"
    SEMI_LIQUID = "semi_liquid"
    ILLIQUID = "illiquid"


class VolatilityClass(Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    EXTREME = "extreme"


@dataclass(frozen=True)
class CostBasis:
    """Optional cost basis for a capital unit."""

    currency: str
    amount: Decimal
    method: str = "lot"


@dataclass(frozen=True)
class CapitalUnit:
    """Canonical, chain-agnostic representation of a holding."""

    asset_code: str
    quantity: Decimal


@dataclass(frozen=True)
class Exposure:
    """Capital exposure with liquidity and volatility classification."""

    unit: CapitalUnit
    liquidity: LiquidityClass
    volatility: VolatilityClass
    cost_basis: Optional[CostBasis] = None
    attributes: Optional[Dict[str, str]] = None


@dataclass(frozen=True)
class CapitalSnapshot:
    """Deterministic snapshot of capital exposure at a given point in time."""

    exposures: Tuple[Exposure, ...]
    as_of: str
    notes: Tuple[str, ...] = ()
