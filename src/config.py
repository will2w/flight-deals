"""讀取 config/routes.yaml，並展開查詢所需的設定。"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "routes.yaml"
DATA_DIR = ROOT / "data"


@dataclass
class Route:
    from_airport: str
    to_airport: str


@dataclass
class Settings:
    currency: str = "EUR"
    language: str = "de"
    seat: str = "economy"
    carry_on_bags: int = 0
    checked_bags: int = 0
    hide_separate_and_self_transfer: bool = False
    exclude_basic_economy: bool = False
    top_n: int = 5
    days_ahead: list[int] = field(default_factory=lambda: [14, 30, 60])
    search_delay_seconds: float = 5.0


@dataclass
class DealRules:
    below_median_pct: float = 30.0
    below_historical_min: bool = True


@dataclass
class AppConfig:
    settings: Settings
    routes: list[Route]
    deal_rules: DealRules

    def departure_dates(self, today: date | None = None) -> list[str]:
        """把 days_ahead 偏移量展開成 YYYY-MM-DD 清單。"""
        today = today or date.today()
        return [(today + timedelta(days=offset)).isoformat() for offset in self.settings.days_ahead]


def load_config(path: Path = CONFIG_PATH) -> AppConfig:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    settings = raw.get("settings", {}) or {}
    rules = raw.get("deal_rules", {}) or {}
    return AppConfig(
        settings=Settings(
            currency=str(settings.get("currency", "EUR")),
            language=str(settings.get("language", "de")),
            seat=str(settings.get("seat", "economy")),
            carry_on_bags=int(settings.get("carry_on_bags", 0)),
            checked_bags=int(settings.get("checked_bags", 0)),
            hide_separate_and_self_transfer=bool(
                settings.get("hide_separate_and_self_transfer", False)
            ),
            exclude_basic_economy=bool(
                settings.get("exclude_basic_economy", False)
            ),
            top_n=int(settings.get("top_n", 5)),
            days_ahead=[int(x) for x in settings.get("days_ahead", [14, 30, 60])],
            search_delay_seconds=float(settings.get("search_delay_seconds", 5)),
        ),
        routes=[Route(str(r["from"]), str(r["to"])) for r in raw.get("routes", [])],
        deal_rules=DealRules(
            below_median_pct=float(rules.get("below_median_pct", 30)),
            below_historical_min=bool(rules.get("below_historical_min", True)),
        ),
    )
