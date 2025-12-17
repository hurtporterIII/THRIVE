"""Domain schemas for the core capital computation engine."""

from dataclasses import dataclass
from typing import Dict, Optional, Tuple


@dataclass(frozen=True)
class PositionInput:
    """Structured input describing one position to liquidate."""

    asset_type: str  # Expected values: "stock" or "crypto"
    ticker: str
    quantity: float
    cost_basis_per_unit: float
    current_price: float
    days_held: int
    filing_status: str = "single"
    state_tax_rate: Optional[float] = None


@dataclass(frozen=True)
class TruthResult:
    """Deterministic calculation output for a single position."""

    gross_value: float
    total_gain: float
    tax_classification: str
    federal_tax: float
    state_tax: float
    total_tax: float
    net_liquid_wealth: float
    efficiency_score: float
    confidence_level: str
    assumptions: Tuple[str, ...]
    loss_offset_value: Optional[float] = None
    tax_classification_countdown: Optional[int] = None

    def to_dict(self) -> Dict[str, object]:
        result: Dict[str, object] = {
            "gross_value": self.gross_value,
            "total_gain": self.total_gain,
            "tax_classification": self.tax_classification,
            "federal_tax": self.federal_tax,
            "state_tax": self.state_tax,
            "total_tax": self.total_tax,
            "net_liquid_wealth": self.net_liquid_wealth,
            "efficiency_score": self.efficiency_score,
            "confidence_level": self.confidence_level,
            "assumptions": list(self.assumptions),
        }

        if self.loss_offset_value is not None:
            result["loss_offset_value"] = self.loss_offset_value
        if self.tax_classification_countdown is not None:
            result["tax_classification_countdown"] = self.tax_classification_countdown

        return result
