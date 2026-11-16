"""Google Flights 資料來源，直接組合帶有 sc=e 與 tfu 的完整 URL 交由 Playwright 擷取。"""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime
from itertools import product

from fast_flights import (
    FlightQuery,
    Passengers,
    create_query,
)

log = logging.getLogger(__name__)

# 強制 Google Flights 直接開啟「Am günstigsten（最便宜）」分頁的內部參數
CHEAPEST_SORT_TFU = "EgoIABAAGAAgAigB"


@dataclass
class Offer:
    """正規化後的單一行程報價。"""

    from_airport: str
    to_airport: str
    departure_date: str
    price: int
    currency: str = "EUR"
    trip_type: str = "one-way"
    price_basis: str = "fast_flights_quote"
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
    """擷取 Google Flights 票價，直接鎖定最便宜分頁與 gl=DE。"""

    name = "google-flights"

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
        playwright_max_results: int = 80,
        gl: str = "DE",
    ):
        self.currency = currency
        self.language = language
        self.seat = seat
        self.carry_on_bags = carry_on_bags
        self.checked_bags = checked_bags
        self.hide_separate_and_self_transfer = hide_separate_and_self_transfer
        self.exclude_basic_economy = exclude_basic_economy
        self.use_playwright = use_playwright
        self.playwright_max_results = playwright_max_results
        self.gl = gl

    def _build_exact_cheapest_url(
        self,
        segments: list[tuple[str, str, str]],
        trip_type: str,
        max_stops: int | None,
    ) -> str:
        """建立 Query 後，手動強制拼接 sc=e 與 tfu 參數，確保不被框架過濾。"""
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
            carry_on_bags=self.carry_on_bags,
            checked_bags=self.checked_bags,
            hide_separate_and_self_transfer=self.hide_separate_and_self_transfer,
            exclude_basic_economy=self.exclude_basic_economy,
        )
        url = query.url()
        # 強制替換為 search 路由以支援完整的 sc=e 分頁參數
        url = url.replace("/travel/flights?", "/travel/flights/search?")
        
        # 強制拼接參數
        if "tfu=" not in url:
            url += f"&tfu={CHEAPEST_SORT_TFU}"
        if "sc=e" not in url:
            url += "&sc=e"
        if f"gl={self.gl}" not in url:
            url += f"&gl={self.gl}"
            
        return url

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
        """直接使用帶有 sc=e（最便宜分頁）的網址進行 Playwright 深度擷取。"""
        if not segments:
            raise ValueError("至少需要一段航程。")

        if trip_type == "multi-city":
            return self._search_multicity(segments, max_stops)

        query_url = self._build_exact_cheapest_url(segments, trip_type, max_stops)
        log.info("最終產生的 Google Flights 查詢網址：%s", query_url)

        offers: list[Offer] = []
        origin, destination, departure_date = segments[0]
        return_date = segments[1][2] if len(segments) > 1 else None

        from .playwright_fetcher import fetch_flights
        flights = fetch_flights(query_url, max_results=self.playwright_max_results)
        
        if not flights:
            raise RuntimeError("Playwright 擷取未取得任何航班結果，請確認網路與瀏覽器相依性。")

        for flight in flights:
            if max_stops is not None and flight.stops > max_stops:
                continue
            if not flight.price or flight.price <= 0:
                continue
            segment = {
                "from_airport": origin,
                "to_airport": destination,
                "departure_date": departure_date,
                "departure_time": flight.departure_time,
                "arrival_time": flight.arrival_time,
                "duration_minutes": flight.duration_minutes,
                "stops": flight.stops,
                "transfer_airports": flight.transfer_airports,
            }
            segments_data = [segment]
            if trip_type == "round-trip" and len(segments) > 1:
                ret_origin, ret_dest, ret_date = segments[1]
                segments_data.append(
                    {
                        "from_airport": ret_origin,
                        "to_airport": ret_dest,
                        "departure_date": ret_date,
                        "departure_time": "",
                        "arrival_time": "",
                        "duration_minutes": 0,
                        "stops": 0,
                        "transfer_airports": [],
                    }
                )
            offers.append(
                Offer(
                    from_airport=origin,
                    to_airport=destination,
                    departure_date=departure_date,
                    price=flight.price,
                    currency=self.currency,
                    trip_type=trip_type,
                    price_basis="playwright_scroll_quote",
                    return_date=return_date,
                    segments=segments_data,
                    airlines=flight.airlines,
                    stops=flight.stops,
                    transfer_airports=flight.transfer_airports,
                    duration_minutes=flight.duration_minutes,
                    departure_time=flight.departure_time,
                    arrival_time=flight.arrival_time,
                    is_best=flight.is_best,
                    provider="google-flights-playwright",
                )
            ]

        if not offers:
            raise ValueError("擷取結果中沒有符合轉機條件的航班。")
        offers.sort(key=lambda o: o.price)
        return offers

    def _search_multicity(
        self,
        segments: list[tuple[str, str, str]],
        max_stops: int | None,
    ) -> list[Offer]:
        choices = [
            self._search_single_segment(segment, max_stops)[:8]
            for segment in segments
        ]
        if len(choices) != len(segments) or any(not leg for leg in choices):
            raise ValueError("開口行程至少有一段沒有查到可用航班。")

        offers: list[Offer] = []
        for selected in product(*choices):
            chosen_segments = [
                segment
                for offer in selected
                for segment in offer.segments
            ]
            airlines = list(
                dict.fromkeys(airline for offer in selected for airline in offer.airlines)
            )
            transfers = [
                airport
                for offer in selected
                for airport in offer.transfer_airports
            ]
            offers.append(
                Offer(
                    from_airport=segments[0][0],
                    to_airport=segments[0][1],
                    departure_date=segments[0][2],
                    price=sum(offer.price for offer in selected),
                    currency=self.currency,
                    trip_type="multi-city",
                    price_basis="sum_of_one_way_quotes",
                    return_date=segments[1][2] if len(segments) > 1 else None,
                    segments=chosen_segments,
                    airlines=airlines,
                    stops=sum(offer.stops for offer in selected),
                    transfer_airports=transfers,
                    duration_minutes=sum(
                        offer.duration_minutes for offer in selected
                    ),
                    departure_time=selected[0].departure_time,
                    arrival_time=selected[-1].arrival_time,
                    is_best=all(offer.is_best for offer in selected),
                    provider=selected[0].provider,
                )
            )
        if not offers:
            raise ValueError("沒有取得航段方向完整的開口行程組合。")
        offers.sort(key=lambda o: o.price)
        return offers[:40]

    def _search_single_segment(
        self,
        segment: tuple[str, str, str],
        max_stops: int | None,
    ) -> list[Offer]:
        return self.search_itinerary([segment], trip_type="one-way", max_stops=max_stops)
