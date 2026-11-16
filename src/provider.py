"""Google Flights 供應商轉接層：純票價與精準航段解析 (src/provider.py)"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime

from src.playwright_fetcher import fetch_flights

log = logging.getLogger(__name__)


@dataclass
class FlightOffer:
    from_airport: str
    to_airport: str
    departure_date: str
    price: int
    airlines: list[str] = field(default_factory=list)
    duration_minutes: int = 0
    stops: int = 0
    transfer_airports: list[str] = field(default_factory=list)
    segments: list[dict] = field(default_factory=list)
    trip_type: str = "one-way"

    def to_dict(self) -> dict:
        return {
            "from_airport": self.from_airport,
            "to_airport": self.to_airport,
            "departure_date": self.departure_date,
            "price": self.price,
            "airlines": self.airlines,
            "duration_minutes": self.duration_minutes,
            "stops": self.stops,
            "transfer_airports": self.transfer_airports,
            "segments": self.segments,
            "trip_type": self.trip_type,
        }


class GoogleFlightsProvider:
    def __init__(
        self,
        currency: str = "EUR",
        language: str = "de",
        seat: str = "economy",
        carry_on_bags: int = 0,
        checked_bags: int = 0,
        hide_separate_and_self_transfer: bool = False,
        exclude_basic_economy: bool = False,
        use_playwright: bool = True,
    ) -> None:
        self.currency = currency
        self.language = language
        self.seat = seat
        self.carry_on_bags = carry_on_bags
        self.checked_bags = checked_bags
        self.hide_separate_and_self_transfer = hide_separate_and_self_transfer
        self.exclude_basic_economy = exclude_basic_economy
        self.use_playwright = use_playwright

    def search_itinerary(
        self, segments: list[tuple[str, str, str]], trip_type: str = "one-way", max_stops: int | None = None
    ) -> list[FlightOffer]:
        if not segments:
            return []

        first_seg = segments[0]
        origin, destination, dep_date = first_seg[0], first_seg[1], first_seg[2]

        # 構建 Google Flights 查詢網址
        base_url = "https://www.google.com/travel/flights"
        query_url = f"{base_url}?hl={self.language}&curr={self.currency}"

        # 呼叫 Playwright 擷取
        pw_flights = fetch_flights(query_url, headless=True)

        offers: list[FlightOffer] = []
        for f in pw_flights:
            if max_stops is not None and f.stops > max_stops:
                continue

            seg_list = [
                {
                    "from_airport": f.from_airport or origin,
                    "to_airport": f.to_airport or destination,
                    "departure_date": dep_date,
                    "departure_time": f.departure_time,
                    "arrival_time": f.arrival_time,
                    "stops": f.stops,
                    "transfer_airports": f.transfer_airports,
                }
            ]

            # 若為往返行程，加入回程模擬區段
            if trip_type in ("round-trip", "multi-city") and len(segments) > 1:
                ret_seg = segments[1]
                seg_list.append(
                    {
                        "from_airport": ret_seg[0],
                        "to_airport": ret_seg[1],
                        "departure_date": ret_seg[2],
                        "departure_time": "",
                        "arrival_time": "",
                        "stops": f.stops,
                        "transfer_airports": [],
                    }
                )

            offers.append(
                FlightOffer(
                    from_airport=origin,
                    to_airport=destination,
                    departure_date=dep_date,
                    price=f.price,
                    airlines=f.airlines,
                    duration_minutes=f.duration_minutes,
                    stops=f.stops,
                    transfer_airports=f.transfer_airports,
                    segments=seg_list,
                    trip_type=trip_type,
                )
            )

        return offers
