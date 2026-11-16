"""Google Flights 資料來源，強制帶入 sc=e 與 tfu=EgoIABAAGAAgAigB 直接鎖定「最便宜」分頁。"""
from __future -> annotations

import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime
from itertools import product

from fast_flights import (
    FlightQuery,
    Passengers,
    create_query,
    get_flights,
)

log = logging.getLogger(__name__)

# 強制 Google Flights 直接開啟「Am günstigsten（最便宜）」分頁與價格排序的參數
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
    """擷取 Google Flights 票價，強制鎖定最便宜分頁與 gl=DE。"""

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

    def _build_query_with_cheapest_params(
        self,
        segments: list[tuple[str, str, str]],
        trip_type: str,
        max_stops: int | None,
    ):
        """建立 Query 並強制注入 sc=e（最便宜分頁）、tfu 與 gl=DE。"""
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
        original_url_fn = query.url

        def enhanced_url() -> str:
            url = original_url_fn()
            url = url.replace("/travel/flights?", "/travel/flights/search?")
            if "tfu=" not in url:
                sep = "&" if "?" in url else "?"
                url = f"{url}{sep}tfu={CHEAPEST_SORT_TFU}"
            if "sc=e" not in url:
                sep = "&" if "?" in url else "?"
                url = f"{url}{sep}sc=e"
            if "gl=" not in url and self.gl:
                sep = "&" if "?" in url else "?"
                url = f"{url}{sep}gl={self.gl}"
            return url

        query.url = enhanced_url  # type: ignore[method-assign]
        return query

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
        """搜尋行程，優先走 Playwright，若 Playwright 失敗則強制用帶有 sc=e 的 URL 透過 HTTP 抓取。"""
        if not segments:
            raise ValueError("至少需要一段航程。")

        if trip_type == "multi-city":
            return self._search_multicity(segments, max_stops)

        query = self._build_query_with_cheapest_params(segments, trip_type, max_stops)
        query_url = query.url()

        offers: list[Offer] = []
        pw_error = ""

        # 1. 優先嘗試 Playwright 深度擷取
        if self.use_playwright:
            try:
                from .playwright_fetcher import fetch_flights
                flights = fetch_flights(query_url, max_results=self.playwright_max_results)
                if flights:
                    for flight in flights:
                        if max_stops is not None and flight.stops > max_stops:
                            continue
                        if not flight.price or flight.price <= 0:
                            continue
                        origin, destination, departure_date = segments[0]
                        return_date = segments[1][2] if len(segments) > 1 else None
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
                        )
            except Exception as exc:
                pw_error = str(exc)
                log.warning("Playwright 擷取失敗 (%s)，切換至 HTTP 備援...", pw_error)

        # 2. 若 Playwright 未取得結果，改用帶有 sc=e 的 fast-flights HTTP 模式補抓
        if not offers:
            try:
                results = get_flights(query)
                for item in results:
                    if item.price is None:
                        continue
                    offer = self._to_offer(
                        item,
                        segments,
                        trip_type,
                        self.currency,
                        price_basis="fast_flights_quote_cheapest",
                    )
                    offers.append(offer)
            except Exception as http_exc:
                log.warning("HTTP 擷取亦失敗：%s", http_exc)

        if not offers:
            raise RuntimeError(
                f"無法取得報價。Playwright 錯誤：{pw_error or '無'}；請檢查網路連線或容器相依性。"
            )

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

    @staticmethod
    def _to_offer(
        item,
        requested_segments: list[tuple[str, str, str]],
        trip_type: str,
        currency: str,
        flight_legs: list | None = None,
        price: int | None = None,
        airlines: list[str] | None = None,
        price_basis: str | None = None,
    ) -> Offer:
        grouped_legs = _group_flight_legs(
            flight_legs if flight_legs is not None else item.flights,
            requested_segments,
        )
        if len(grouped_legs) != len(requested_segments):
            raise ValueError("來源回傳的航段數與查詢不一致。")

        segment_offers = []
        for (origin, destination, departure_date), legs in zip(
            requested_segments, grouped_legs
        ):
            if not legs:
                raise ValueError(f"來源未回傳 {origin}→{destination} 航段。")
            if (
                legs[0].from_airport.code != origin
                or legs[-1].to_airport.code != destination
            ):
                raise ValueError(
                    f"來源航段方向不符：預期 {origin}→{destination}。"
                )
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
            price=int(price if price is not None else item.price),
            currency=currency,
            trip_type=trip_type,
            price_basis=price_basis or "fast_flights_quote",
            return_date=requested_segments[1][2] if len(requested_segments) > 1 else None,
            segments=segment_offers,
            airlines=list(airlines if airlines is not None else item.airlines or []),
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
        if not segment_legs or segment_legs[-1].to_airport.code != destination:
            raise ValueError(f"來源航班沒有抵達預期目的地 {destination}。")
        grouped.append(segment_legs)

    if cursor != len(flight_legs):
        raise ValueError("來源回傳了未能配對到查詢航段的航班。")
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
