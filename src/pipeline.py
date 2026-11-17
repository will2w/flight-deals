"""主流程：抓票價 → 對照歷史判定特價 → 寫入 data/ (src/pipeline.py)。"""
from __future__ import annotations

import logging
import statistics
import time
from collections import defaultdict
from typing import Any

from .config import AppConfig, load_config
from .provider import GoogleFlightsProvider
from .store import append_history, now_iso, read_history, write_snapshot

log = logging.getLogger("flight-deals")

MIN_SAMPLES_FOR_MEDIAN = 3

BaselineKey = tuple[str, str, str]  # (出發地, 目的地, 出發日)


def build_baselines(
    history: list[dict[str, str]], currency: str = "EUR"
) -> dict[BaselineKey, list[int]]:
    """依航線、出發日與幣別分組。"""
    buckets: dict[BaselineKey, list[int]] = defaultdict(list)
    for row in history:
        if row.get("currency", "").upper() != currency.upper():
            continue
        try:
            price = int(float(row["price"]))
        except (KeyError, TypeError, ValueError):
            continue
        key = (
            row.get("from_airport", ""),
            row.get("to_airport", ""),
            row.get("departure_date", ""),
        )
        buckets[key].append(price)
    return buckets


def is_deal(price: int, prices: list[int], rules) -> bool:
    """票價是否為特價判定。"""
    if not prices:
        return False
    if rules.below_historical_min and price < min(prices):
        return True
    if len(prices) >= MIN_SAMPLES_FOR_MEDIAN:
        median = statistics.median(prices)
        if price <= median * (1 - rules.below_median_pct / 100):
            return True
    return False


def collect_offers(config: AppConfig, baselines: dict[BaselineKey, list[int]]) -> tuple[list[dict], list[dict]]:
    """逐條航線 × 逐個出發日抓取，回傳 (所有保留的行程, 失敗紀錄)。"""
    provider = GoogleFlightsProvider(
        currency=config.settings.currency,
        language=config.settings.language,
        seat=config.settings.seat,
        carry_on_bags=config.settings.carry_on_bags,
        checked_bags=config.settings.checked_bags,
        hide_separate_and_self_transfer=config.settings.hide_separate_and_self_transfer,
        exclude_basic_economy=config.settings.exclude_basic_economy,
    )
    dates = config.departure_dates()
    offers: list[dict] = []
    failures: list[dict] = []

    for route in config.routes:
        for departure_date in dates:
            try:
                # 【核心修正】：改用 search_itinerary 並符合單程結構定義
                segments = [(route.from_airport, route.to_airport, departure_date)]
                found = provider.search_itinerary(segments, trip_type="one-way")
            except Exception as exc:
                log.warning("查詢失敗 %s→%s %s: %s", route.from_airport, route.to_airport, departure_date, exc)
                failures.append(
                    {
                        "from": route.from_airport,
                        "to": route.to_airport,
                        "departure_date": departure_date,
                        "error": str(exc),
                    }
                )
                continue

            found.sort(key=lambda offer: offer.price)
            key: BaselineKey = (route.from_airport, route.to_airport, departure_date)
            prices = baselines.get(key, [])

            for offer in found[: config.settings.top_n]:
                record = offer.to_dict()
                record["is_deal"] = is_deal(offer.price, prices, config.deal_rules)
                record["baseline_median"] = int(statistics.median(prices)) if prices else None
                offers.append(record)

            time.sleep(config.settings.search_delay_seconds)

    return offers, failures


def run(config: AppConfig | None = None) -> dict[str, Any]:
    config = config or load_config()
    baselines = build_baselines(read_history(), currency=config.settings.currency)
    offers, failures = collect_offers(config, baselines)
    deals = [offer for offer in offers if offer["is_deal"]]
    observed_at = now_iso()

    payload: dict[str, Any] = {
        "generated_at": observed_at,
        "is_sample": False,
        "currency": config.settings.currency,
        "routes": [{"from": r.from_airport, "to": r.to_airport} for r in config.routes],
        "rules": {
            "below_median_pct": config.deal_rules.below_median_pct,
            "below_historical_min": config.deal_rules.below_historical_min,
        },
        "summary": {
            "routes_checked": len(config.routes),
            "queries": len(config.routes) * len(config.departure_dates()),
            "offers": len(offers),
            "deals": len(deals),
            "failures": failures,
        },
        "offers": offers,
        "deals": deals,
    }

    path = write_snapshot(payload)
    append_history(offers, observed_at, currency=config.settings.currency)

    log.info("完成：%d 筆行程、%d 筆特價，寫入 %s", len(offers), len(deals), path)
    return payload


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    payload = run()
    return 0 if payload["summary"]["offers"] > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
