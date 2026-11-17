"""Google Flights Provider 修正單程參數 (src/provider.py)"""
from __future__ import annotations

import logging
from typing import Any
from urllib.parse import quote

from src.playwright_fetcher import fetch_flights

log = logging.getLogger(__name__)


class GoogleFlightsProvider:
    def __init__(
        self,
        currency: str = "EUR",
        language: str = "de",
        seat: str = "economy",
        carry_on_bags: int = 0,
        checked_bags: int = 0,
        hide_separate_and_self_transfer: bool = True,
        exclude_basic_economy: bool = False,
        use_playwright: bool = True,
    ):
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
    ) -> list[Any]:
        offers = []
        for segment in segments:
            origin, dest, dep_date = segment
            
            # 【關鍵修復】：根據 trip_type 強制建構單程或來回的查詢字串
            if trip_type == "one-way":
                query_str = f"One-way flight from {origin} to {dest} on {dep_date}"
            else:
                query_str = f"Flights from {origin} to {dest} on {dep_date}"

            query_url = (
                f"https://www.google.com/travel/flights?q={quote(query_str)}"
                f"&hl={self.language}&curr={self.currency}"
            )
            
            log.info("[PROVIDER] 發起 Google Flights 查詢網址: %s", query_url)

            if self.use_playwright:
                pw_flights = fetch_flights(query_url)
                for f in pw_flights:
                    offer = _convert_playwright_flight(f, origin, dest, dep_date, trip_type)
                    if max_stops is not None and offer.stops > max_stops:
                        continue
                    offers.append(offer)
        return offers


def _convert_playwright_flight(f: Any, origin: str, dest: str, dep_date: str, trip_type: str) -> Any:
    from src.models import FlightOffer, FlightSegment

    seg = FlightSegment(
        from_airport=f.from_airport or origin,
        to_airport=f.to_airport or dest,
        departure_date=dep_date,
        departure_time=f.departure_time,
        arrival_time=f.arrival_time,
        duration_minutes=f.duration_minutes,
        stops=f.stops,
        transfer_airports=f.transfer_airports,
        airline=f.airlines[0] if f.airlines else "未知航空",
        flight_number="",
    )
    return FlightOffer(
        price=f.price,
        currency="EUR",
        airlines=f.airlines,
        segments=[seg],
        trip_type=trip_type,
        is_best=f.is_best,
    )
