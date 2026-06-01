from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class QuantAnalysisResult:
    symbol: str
    score: float
    signal: str
    factors: Dict[str, float]
    risk: Dict[str, float]
    latest: Dict[str, Any]
    warnings: List[str] = field(default_factory=list)
    integrations: Dict[str, Any] = field(default_factory=dict)


@dataclass
class QuantPick:
    symbol: str
    score: float
    signal: str
    factors: Dict[str, float]
    risk: Dict[str, float]


@dataclass
class BacktestResult:
    symbol: str
    strategy: str
    start_date: Optional[str]
    end_date: Optional[str]
    initial_cash: float
    final_value: float
    total_return: float
    annualized_return: float
    max_drawdown: float
    win_rate: float
    trades: int
    equity_curve: List[Dict[str, Any]]
    engine: str = "vector"
    calmar_ratio: float = 0.0
    profit_loss_ratio: float = 0.0


@dataclass
class DataLakeSyncResult:
    source: str
    collection: str
    total: int
    inserted: int
    updated: int
    errors: List[str] = field(default_factory=list)


@dataclass
class FactorResearchResult:
    universe_size: int
    candidates: List[Dict[str, Any]]
    errors: Dict[str, str] = field(default_factory=dict)


@dataclass
class ForecastResult:
    symbol: str
    engine: str
    horizon: int
    trend_score: float
    upside_probability: float
    expected_return: float
    expected_drawdown: float
    path: List[Dict[str, Any]] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


@dataclass
class PatternRecognitionResult:
    symbol: str
    patterns: List[Dict[str, Any]]
    source: str = "sequoia-x-inspired"


@dataclass
class IntegrationCapability:
    name: str
    project: str
    status: str
    capabilities: List[str]
    integration_mode: str
    notes: str = ""
