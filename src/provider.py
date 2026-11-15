"""資料來源：Google Flights（透過 fast-flights，無需 API Key）。

對外只暴露一個統一介面 `Provider.search()`，回傳正規化後的 Offer 清單，
之後要換成 Kiwi 或官方 API，只需新增另一個 Provider 實作。
"""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime

from fast_flights import FlightQuery, Passengers, create_query, get_flights

log = logging.getLogger(__name__)


@dataclass
class Offer:
    """正規化後的單一行程報價。"""

    from_airport: str
    to_airport: str
    departure_date: str
    price: int
    currency: str = "EUR"
    airlines: list[str] = field(default_factory=list)
    stops: int = 0
    transfer_airports: list[str] = field(default_factory=list)
    duration_minutes: int = 0
    departure_time: str = ""
    arrival_time: str = ""
    is_best: bool = False
    provider: str = "google-flights"

    def to_dict(self) -> dict:
        return asdict(self)


class GoogleFlightsProvider:
    """用 fast-flights 查 Google Flights 票價。"""

    name = "google-flights"

    def __init__(self, currency: str = "EUR", language: str = "zh-TW", seat: str = "economy"):
        self.currency = currency
        self.language = language
        self.seat = seat

    def search(
        self,
        from_airport: str,
        to_airport: str,
        departure_date: str,
        max_stops: int | None = None,
    ) -> list[Offer]:
        query = create_query(
            flights=[
                FlightQuery(
                    date=departure_date,
                    from_airport=from_airport,
                    to_airport=to_airport,
                )
            ],
            seat=self.seat,  # type: ignore[arg-type]
            trip="one-way",
            passengers=Passengers(adults=1),
            language=self.language,
            currency=self.currency,
            max_stops=max_stops,
        )
        results = get_flights(query)
        offers: list[Offer] = []
        for item in results:
            if item.price is None:  # Google 有時不給報價
                continue
            offers.append(self._to_offer(item, from_airport, to_airport, departure_date, self.currency))
        return offers

    @staticmethod
    def _to_offer(item, from_airport: str, to_airport: str, departure_date: str, currency: str) -> Offer:
        legs = item.flights
        first, last = legs[0], legs[-1]
        transfer_airports = [
            f"{leg.to_airport.code} {leg.to_airport.name}".strip()
            for leg in legs[:-1]
        ]
        return Offer(
            from_airport=from_airport,
            to_airport=to_airport,
            departure_date=departure_date,
            price=int(item.price),
            currency=currency,
            airlines=list(item.airlines or []),
            stops=max(len(legs) - 1, 0),
            transfer_airports=transfer_airports,
            duration_minutes=_total_minutes(first, last, legs),
            departure_time=_fmt_hhmm(first.departure.time),
            arrival_time=_fmt_hhmm(last.arrival.time),
            is_best="best" in str(item.type).lower(),
        )


def _fmt_hhmm(value) -> str:
    hour, minute = value
    return f"{hour:02d}:{minute:02d}"


def _total_minutes(first, last, legs) -> int:
    """優先用起降時間計算總時長（含轉機等待），失敗則退回各段飛行時間相加。"""
    try:
        departure = datetime(*first.departure.date, *first.departure.time)
        arrival = datetime(*last.arrival.date, *last.arrival.time)
        return max(int((arrival - departure).total_seconds() // 60), 0)
    except (TypeError, ValueError):
        return sum(leg.duration or 0 for leg in legs)
