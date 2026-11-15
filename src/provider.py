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
    trip_type: str = "one-way"
    return_date: str | None = None
    segments: list[dict] = field(default_factory=list)
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
        return self.search_itinerary(
            [(from_airport, to_airport, departure_date)],
            trip_type="one-way",
            max_stops=max_stops,
        )

    def search_itinerary(
        self,
        segments: list[tuple[str, str, str]],
        trip_type: str,
        max_stops: int | None = None,
    ) -> list[Offer]:
        """搜尋單程、往返或多航段行程。

        每段格式為 (出發地 IATA、目的地 IATA、出發日期)。
        """
        if not segments:
            raise ValueError("至少需要一段航程。")
        query = create_query(
            flights=[
                FlightQuery(
                    date=departure_date,
                    from_airport=origin,
                    to_airport=destination,
                )
                for origin, destination, departure_date in segments
            ],
            seat=self.seat,  # type: ignore[arg-type]
            trip=trip_type,  # type: ignore[arg-type]
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
            offers.append(
                self._to_offer(item, segments, trip_type, self.currency)
            )
        return offers

    @staticmethod
    def _to_offer(
        item,
        requested_segments: list[tuple[str, str, str]],
        trip_type: str,
        currency: str,
    ) -> Offer:
        grouped_legs = _group_flight_legs(item.flights, requested_segments)
        segment_offers = []
        for (origin, destination, departure_date), legs in zip(
            requested_segments, grouped_legs
        ):
            if not legs:
                continue
            first, last = legs[0], legs[-1]
            transfers = [
                f"{leg.to_airport.code} {leg.to_airport.name}".strip()
                for leg in legs[:-1]
            ]
            segment_offers.append(
                {
                    "from_airport": origin,
                    "to_airport": destination,
                    "departure_date": departure_date,
                    "departure_time": _fmt_hhmm(first.departure.time),
                    "arrival_time": _fmt_hhmm(last.arrival.time),
                    "duration_minutes": _total_minutes(first, last, legs),
                    "stops": max(len(legs) - 1, 0),
                    "transfer_airports": transfers,
                }
            )

        first_requested = requested_segments[0]
        first_segment = segment_offers[0] if segment_offers else {}
        last_segment = segment_offers[-1] if segment_offers else {}
        all_transfers = [
            airport
            for segment in segment_offers
            for airport in segment["transfer_airports"]
        ]
        return Offer(
            from_airport=first_requested[0],
            to_airport=first_requested[1],
            departure_date=first_requested[2],
            price=int(item.price),
            currency=currency,
            trip_type=trip_type,
            return_date=requested_segments[1][2] if len(requested_segments) > 1 else None,
            segments=segment_offers,
            airlines=list(item.airlines or []),
            stops=sum(segment["stops"] for segment in segment_offers),
            transfer_airports=all_transfers,
            duration_minutes=sum(segment["duration_minutes"] for segment in segment_offers),
            departure_time=first_segment.get("departure_time", ""),
            arrival_time=last_segment.get("arrival_time", ""),
            is_best="best" in str(item.type).lower(),
        )


def _group_flight_legs(
    flight_legs: list, requested_segments: list[tuple[str, str, str]]
) -> list[list]:
    """依查詢目的地，將連續航段切分為去程、回程等航段。"""
    grouped: list[list] = []
    cursor = 0
    for _, destination, _ in requested_segments:
        segment_legs = []
        while cursor < len(flight_legs):
            leg = flight_legs[cursor]
            cursor += 1
            segment_legs.append(leg)
            if leg.to_airport.code == destination:
                break
        grouped.append(segment_legs)

    if cursor < len(flight_legs) and grouped:
        grouped[-1].extend(flight_legs[cursor:])
    return grouped


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
