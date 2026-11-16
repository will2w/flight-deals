"""Playwright 深度擷取：模擬瀏覽器捲動以取得更多 Google Flights 結果。

`fast-flights` 預設只用 HTTP 抓初始 HTML，大約只有 6 筆結果。
本模組使用 Playwright 開啟真實瀏覽器，捲動頁面觸發 Google Flights
的延遲載入（lazy-load），再從 DOM 解析所有航班卡片。

注意事項：
- 需安裝 playwright 及瀏覽器：``pip install playwright && playwright install chromium``
- Streamlit Cloud 需在 ``packages.txt`` 加入瀏覽器系統依賴
- Google Flights DOM 可能改版，解析器做了多層 fallback 以提高穩定性
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)

# 結果上限，避免捲動過久
DEFAULT_MAX_RESULTS = 50
DEFAULT_SCROLL_COUNT = 12
DEFAULT_WAIT_MS = 1500

_chromium_checked = False


def _ensure_chromium_installed() -> None:
    """確保 Playwright Chromium 已安裝；首次使用時自動安裝。"""
    global _chromium_checked
    if _chromium_checked:
        return
    import subprocess
    import sys

    from playwright.sync_api import sync_playwright

    try:
        with sync_playwright() as p:
            p.chromium.launch(headless=True).close()
        _chromium_checked = True
        return
    except Exception:
        log.info("Chromium 未安裝，正在執行 playwright install chromium ...")
        result = subprocess.run(
            [sys.executable, "-m", "playwright", "install", "chromium"],
            capture_output=True,
            text=True,
            timeout=300,
        )
        if result.returncode != 0:
            raise RuntimeError(
                "Playwright Chromium 安裝失敗。請手動執行：playwright install chromium"
            ) from None
        _chromium_checked = True


@dataclass
class PlaywrightFlight:
    """從 DOM 解析出的單筆航班結果。"""

    price: int | None = None
    airlines: list[str] = field(default_factory=list)
    departure_time: str = ""
    arrival_time: str = ""
    duration_minutes: int = 0
    stops: int = 0
    transfer_airports: list[str] = field(default_factory=list)
    from_airport: str = ""
    to_airport: str = ""
    is_best: bool = False


def fetch_flights(
    query_url: str,
    max_results: int = DEFAULT_MAX_RESULTS,
    scroll_count: int = DEFAULT_SCROLL_COUNT,
    headless: bool = True,
) -> list[PlaywrightFlight]:
    """開啟 Google Flights 頁面、捲動載入、解析所有航班卡片。

    Args:
        query_url: fast-flights ``query.url()`` 產生的 Google Flights 搜尋網址。
        max_results: 最多回傳幾筆結果。
        scroll_count: 最多捲動幾次。
        headless: 是否以無頭模式執行。

    Returns:
        解析後的航班清單，依價格排序。
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError(
            "未安裝 playwright，請先執行 pip install playwright 與 playwright install chromium。"
        ) from exc

    _ensure_chromium_installed()

    flights: list[PlaywrightFlight] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(
            locale="de-DE",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/129.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 900},
        )
        page = context.new_page()

        try:
            page.goto(query_url, wait_until="domcontentloaded", timeout=60000)
            # 等初始結果出現
            page.wait_for_selector("li", timeout=30000)
            _click_show_more(page)

            prev_count = 0
            for _ in range(scroll_count):
                page.mouse.wheel(0, 5000)
                page.wait_for_timeout(DEFAULT_WAIT_MS)
                _click_show_more(page)

                current = _count_visible_cards(page)
                if current <= prev_count and current > 0:
                    # 沒有更多結果可載入
                    break
                prev_count = current
                if current >= max_results:
                    break

            cards = page.query_selector_all("li")
            for card in cards:
                if len(flights) >= max_results:
                    break
                try:
                    flight = _parse_card(card)
                    if flight and flight.price is not None:
                        flights.append(flight)
                except Exception as exc:
                    log.debug("略過無法解析的航班卡片：%s", exc)
        finally:
            browser.close()

    # 去重並依價格排序
    seen: set[tuple] = set()
    unique: list[PlaywrightFlight] = []
    for f in flights:
        key = (f.price, f.departure_time, f.arrival_time, tuple(f.airlines))
        if key in seen:
            continue
        seen.add(key)
        unique.append(f)
    unique.sort(key=lambda f: (f.price or 10**9, f.duration_minutes))
    return unique


def _click_show_more(page) -> None:
    """若頁面有「顯示更多」按鈕則點擊。"""
    try:
        buttons = page.get_by_role("button", name=re.compile(r"(?i)(mehr|more|更多|顯示更多)"))
        for btn in buttons.all():
            if btn.is_visible():
                btn.click(timeout=3000)
                page.wait_for_timeout(800)
    except Exception:
        pass


def _count_visible_cards(page) -> int:
    try:
        return page.locator("li").count()
    except Exception:
        return 0


def _parse_card(card) -> PlaywrightFlight | None:
    """從單一航班卡片 DOM 提取資訊。"""
    text = card.inner_text() or ""
    if not text.strip():
        return None

    flight = PlaywrightFlight()

    # 價格：抓最後一個 € 或 EUR 後面的數字
    flight.price = _extract_price(text)
    if flight.price is None:
        return None

    # 起降時間：HH:MM – HH:MM 格式
    times = re.findall(r"(\d{1,2}:\d{2})\s*[–\-—]\s*(\d{1,2}:\d{2})", text)
    if times:
        flight.departure_time = times[0][0]
        flight.arrival_time = times[0][1]

    # 轉機次數
    stops_match = re.search(r"(\d+)\s*(Stop|Stopp|停)", text, re.IGNORECASE)
    if stops_match:
        flight.stops = int(stops_match.group(1))
    elif re.search(r"(?i)(nonstop|direkt|直飛|直航)", text):
        flight.stops = 0

    # 時長：例如 "16 Std. 45 Min" 或 "16h 45m"
    flight.duration_minutes = _extract_duration(text)

    # 機場代碼（三碼）
    airport_codes = re.findall(r"\b([A-Z]{3})\b", text)
    if len(airport_codes) >= 2:
        flight.from_airport = airport_codes[0]
        flight.to_airport = airport_codes[1]
        # 轉機機場：若有轉機，取中間的機場代碼
        if flight.stops > 0 and len(airport_codes) >= 3:
            flight.transfer_airports = list(dict.fromkeys(airport_codes[1:-1]))

    # 航空公司：從 aria-label 或文字中提取
    flight.airlines = _extract_airlines(card, text)

    # 是否為「最佳」分類
    flight.is_best = bool(re.search(r"(?i)(best|beste|最佳)", text))

    return flight


def _extract_price(text: str) -> int | None:
    """從卡片文字中提取價格（EUR）。"""
    # 嘗試多種格式：€328, 328 €, EUR 328, 328 EUR
    patterns = [
        r"€\s*([\d.]+)",
        r"([\d.]+)\s*€",
        r"EUR\s*([\d.]+)",
        r"([\d.]+)\s*EUR",
    ]
    for pattern in patterns:
        matches = re.findall(pattern, text)
        if matches:
            # 取最大的數字（通常是票價，避免抓到 CO2e 之類的）
            values = [int(m.replace(".", "")) for m in matches if m.replace(".", "").isdigit()]
            if values:
                return max(values)
    return None


def _extract_duration(text: str) -> int:
    """從卡片文字中提取總時長（分鐘）。"""
    # 德文格式："16 Std. 45 Min." 或 "16 Std."
    de_match = re.search(
        r"(?:(\d+)\s*Std\.?|Std\.)?\s*(?:(\d+)\s*Min\.?)?",
        text,
    )
    # 英文格式："16h 45m"
    en_match = re.search(r"(\d+)\s*h\s*(\d+)\s*m", text, re.IGNORECASE)

    if en_match:
        return int(en_match.group(1)) * 60 + int(en_match.group(2))
    if de_match and (de_match.group(1) or de_match.group(2)):
        hours = int(de_match.group(1) or 0)
        minutes = int(de_match.group(2) or 0)
        return hours * 60 + minutes
    return 0


def _extract_airlines(card, text: str) -> list[str]:
    """從卡片中提取航空公司名稱。"""
    airlines: list[str] = []
    # 從 img 的 aria-label 或 alt 屬性
    try:
        imgs = card.query_selector_all("img")
        for img in imgs:
            label = img.get_attribute("aria-label") or img.get_attribute("alt") or ""
            if label and len(label) < 60:
                airlines.append(label.strip())
    except Exception:
        pass

    if not airlines:
        # 從文字中猜航空公司（常見名稱）
        known_airlines = [
            "Lufthansa", "Cathay Pacific", "Cathay", "Turkish Airlines", "Turkish",
            "Emirates", "Qatar Airways", "Qatar", "Etihad", "Singapore Airlines",
            "Singapore", "Air China", "Air France", "KLM", "British Airways",
            "Swiss", "Austrian", "ANA", "All Nippon", "Japan Airlines", "JAL",
            "Korean Air", "Asiana", "Thai Airways", "Thai", "Vietnam Airlines",
            "Vietnam", "Malaysia Airlines", "Malaysia", "EVA Air", "EVA",
            "China Airlines", "Hainan", "XiamenAir", "Xiamen", "United",
            "American Airlines", "American", "Delta", "Air Canada", "Finnair",
            "SAS", "Norwegian", "Ryanair", "EasyJet", "Wizz Air", "Wizz",
            "Aegean", "Alitalia", "ITA Airways", "ITA", "Iberia", "TAP",
            "Aeroflot", "Saudia", "Gulf Air", "Royal Jordanian", "EgyptAir",
            "Ethiopian", "Kenya Airways", "South African",
        ]
        for name in known_airlines:
            if re.search(re.escape(name), text, re.IGNORECASE):
                airlines.append(name)
    return list(dict.fromkeys(airlines))


def query_to_url(
    from_airport: str,
    to_airport: str,
    departure_date: str,
    currency: str = "EUR",
    language: str = "de",
    return_date: str | None = None,
    seat: str = "economy",
) -> str:
    """組建 Google Flights 搜尋網址（不用 fast-flights，直接組 URL）。"""
    trip = 2 if return_date else 1
    base = "https://www.google.com/travel/flights"
    params = {
        "q": f"flights from {from_airport} to {to_airport} on {departure_date}",
        "hl": language,
        "curr": currency,
        "trip": str(trip),
    }
    if return_date:
        params["return"] = return_date
    query_string = "&".join(f"{k}={v}" for k, v in params.items())
    return f"{base}?{query_string}"
