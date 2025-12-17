"""State engine for canonical capital snapshots."""

from dataclasses import dataclass
from decimal import Decimal
from typing import Dict, Iterable, Optional, Set, Tuple, Union

from .capital_model import CapitalSnapshot, CapitalUnit, Exposure, LiquidityClass, VolatilityClass


@dataclass(frozen=True)
class ObservedBalance:
    asset_code: str
    quantity: Decimal
    source: str


@dataclass(frozen=True)
class AssetClassification:
    liquidity: LiquidityClass
    volatility: VolatilityClass


@dataclass(frozen=True)
class ClassificationPolicy:
    default_liquidity: LiquidityClass = LiquidityClass.LIQUID
    default_volatility: VolatilityClass = VolatilityClass.HIGH
    overrides: Optional[Dict[str, AssetClassification]] = None

    def classify(self, asset_code: str) -> AssetClassification:
        if self.overrides and asset_code in self.overrides:
            return self.overrides[asset_code]
        return AssetClassification(
            liquidity=self.default_liquidity,
            volatility=self.default_volatility,
        )


@dataclass(frozen=True)
class ReconciliationIssue:
    asset_code: str
    sources: Tuple[str, ...]
    message: str


@dataclass(frozen=True)
class ReconciliationReport:
    issues: Tuple[ReconciliationIssue, ...]


@dataclass(frozen=True)
class StateEngineState:
    observed: Tuple[ObservedBalance, ...]
    snapshot: CapitalSnapshot
    report: ReconciliationReport


class StateEngine:
    """Ingests read-only balances and produces a deterministic snapshot."""

    def __init__(self, policy: Optional[ClassificationPolicy] = None) -> None:
        self._policy = policy or ClassificationPolicy()

    def ingest(self, observed: Iterable[ObservedBalance], as_of: str) -> StateEngineState:
        observed_list = tuple(self._normalize_observed(observed))
        exposures, report = self._normalize_balances(observed_list)
        snapshot = CapitalSnapshot(exposures=exposures, as_of=as_of)
        return StateEngineState(
            observed=observed_list,
            snapshot=snapshot,
            report=report,
        )

    def _normalize_observed(self, observed: Iterable[ObservedBalance]) -> Iterable[ObservedBalance]:
        for item in observed:
            yield ObservedBalance(
                asset_code=item.asset_code,
                quantity=_to_decimal(item.quantity),
                source=item.source,
            )

    def _normalize_balances(
        self, observed: Tuple[ObservedBalance, ...]
    ) -> Tuple[Tuple[Exposure, ...], ReconciliationReport]:
        totals: Dict[str, Decimal] = {}
        sources: Dict[str, Set[str]] = {}

        for item in observed:
            totals[item.asset_code] = totals.get(item.asset_code, Decimal("0")) + item.quantity
            sources.setdefault(item.asset_code, set()).add(item.source)

        issues = []
        for asset_code, source_set in sources.items():
            if len(source_set) > 1:
                issues.append(
                    ReconciliationIssue(
                        asset_code=asset_code,
                        sources=tuple(sorted(source_set)),
                        message="Multiple sources reported balances for this asset.",
                    )
                )

        exposures = []
        for asset_code in sorted(totals.keys()):
            classification = self._policy.classify(asset_code)
            unit = CapitalUnit(asset_code=asset_code, quantity=totals[asset_code])
            exposures.append(
                Exposure(
                    unit=unit,
                    liquidity=classification.liquidity,
                    volatility=classification.volatility,
                )
            )

        return tuple(exposures), ReconciliationReport(issues=tuple(issues))


def _to_decimal(value: Union[Decimal, int, float, str]) -> Decimal:
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))
