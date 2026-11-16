"""Playwright 全量深度擷取：精準純票價與航空公司解析 (src/playwright_fetcher.py)"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

DEFAULT_MAX_RESULTS = 150
DEFAULT_SCROLL_COUNT = 30
DEFAULT_WAIT_MS = 1500

_chromium_checked = False


def _ensure_chromium_installed() -> None:
    global _chromium_checked
    if _chromium_checked:
        return
    import subprocess
    import sys
    from playwright.sync_api import sync_playwright

    try:
        with sync_playwright() as p:
            p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"]).close()
        _chromium_checked = True
        return
    except Exception:
        subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], capture_output=True, text=True, timeout=300)
        _chromium_checked = True


@dataclass
class PlaywrightFlight:
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
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError("未安裝 playwright") from exc

    _ensure_chromium_installed()

    flights: list[PlaywrightFlight] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=headless,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--disable-blink-features=AutomationControlled",
                "--window-size=1440,1200",
            ],
        )
        context = browser.new_context(
            locale="de-DE",
            timezone_id="Europe/Berlin",
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            viewport={"width": 1440, "height": 1200},
        )
        context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        page = context.new_page()

        try:
            page.goto(query_url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(2000)
            _dismiss_consent_dialog(page)
            _ensure_cheapest_tab_selected(page)

            try:
                page.wait_for_selector("li, div[role='listitem']", timeout=15000)
            except Exception:
                log.warning("未能在預設時間內找到清單元素，嘗試繼續滾動渲染...")

            prev_count = 0
            stagnant_rounds = 0
            for _ in range(scroll_count):
                _expand_all_hidden_flights(page)
                page.evaluate("window.scrollBy(0, 4000)")
                page.wait_for_timeout(DEFAULT_WAIT_MS)
                _expand_all_hidden_flights(page)

                current = _count_visible_cards(page)
                if current <= prev_count:
                    stagnant_rounds += 1
                    if stagnant_rounds >= 3 and current > 0:
                        break
                else:
                    stagnant_rounds = 0
                prev_count = current
                if current >= max_results * 3:
                    break

            cards = page.query_selector_all("li, div[role='listitem']")
            for card in cards:
                try:
                    flight = _parse_card(card)
                    if flight and flight.price is not None and flight.price > 0:
                        flights.append(flight)
                except Exception as exc:
                    log.debug("略過卡片：%s", exc)
        finally:
            browser.close()

    if not flights:
        raise RuntimeError("Playwright 擷取未取得任何航班結果。請確認網路連線或 Google Flights 網址是否正確。")

    seen: set[tuple] = set()
    unique: list[PlaywrightFlight] = []
    for f in flights:
        key = (f.price, f.departure_time, f.arrival_time, tuple(f.airlines))
        if key in seen:
            continue
        seen.add(key)
        unique.append(f)
    unique.sort(key=lambda f: (f.price or 10**9, f.duration_minutes))
    return unique[:max_results]


def _dismiss_consent_dialog(page) -> None:
    try:
        selectors = [
            "button:has-text('Alle ablehnen')",
            "button:has-text('Alle akzeptieren')",
            "button:has-text('Accept all')",
            "button:has-text('Reject all')",
            "form[action*='consent'] button",
            "div[role='dialog'] button:last-child",
        ]
        for sel in selectors:
            btn = page.locator(sel).first
            if btn.count() > 0 and btn.is_visible():
                btn.click(timeout=3000)
                page.wait_for_timeout(1500)
                break
    except Exception:
        pass


def _ensure_cheapest_tab_selected(page) -> None:
    try:
        tab = page.locator("[role='tab']:has-text('Am günstigsten'), [role='tab']:has-text('Cheapest')").first
        if tab.count() > 0 and tab.is_visible():
            if tab.get_attribute("aria-selected") != "true":
                tab.click(force=True, timeout=3000)
                page.wait_for_timeout(1500)
    except Exception:
        pass


def _expand_all_hidden_flights(page) -> None:
    try:
        page.evaluate(
            """() => {
                const keywords = ['weitere flüge', 'mehr flüge', 'more flights', 'show more', 'günstigere', 'cheaper', '顯示更多', '更多航班'];
                const buttons = Array.from(document.querySelectorAll('button, [role="button"]'));
                for (const btn of buttons) {
                    const txt = (btn.innerText || btn.getAttribute('aria-label') || '').toLowerCase();
                    if (keywords.some(kw => txt.includes(kw))) btn.click();
                }
            }"""
        )
    except Exception:
        pass


def _count_visible_cards(page) -> int:
    try:
        return page.evaluate(
            """() => {
                const items = Array.from(document.querySelectorAll("li, div[role='listitem']"));
                return items.filter(el => !el.querySelector("li, div[role='listitem']") && /\\d{1,2}:\\d{2}/.test(el.innerText || '')).length;
            }"""
        )
    except Exception:
        return 0


def _parse_card(card) -> PlaywrightFlight | None:
    if card.query_selector("li, div[role='listitem']") is not None:
        return None

    inner_text = card.inner_text() or ""
    aria_label = card.get_attribute("aria-label") or ""
    
    combined_text = f"{inner_text}\n{aria_label}".strip()
    if not combined_text:
        return None

    times = re.findall(r"(\d{1,2}:\d{2})\s*[–\-—]\s*(\d{1,2}:\d{2})(?:\+\d+)?", inner_text)
    if not times:
        return None

    prices = _extract_all_prices(combined_text)
    if not prices:
        return None

    # 直接取抓得到的最低有效價格
    lowest_price = min(prices)

    flight = PlaywrightFlight(
        price=lowest_price,
        departure_time=times[0][0],
        arrival_time=times[0][1],
    )

    stops_match = re.search(r"(\d+)\s*(Stop|Stopp|停)", combined_text, re.IGNORECASE)
    if stops_match:
        flight.stops = int(stops_match.group(1))
    elif re.search(r"(?i)(nonstop|direkt|直飛|直航)", combined_text):
        flight.stops = 0

    flight.duration_minutes = _extract_duration(combined_text)

    airport_codes = re.findall(r"\b([A-Z]{3})\b", inner_text)
    airport_codes = [c for c in airport_codes if c not in {"EUR", "USD", "GBP", "TWD", "HKD", "CO2"}]
    if len(airport_codes) >= 2:
        flight.from_airport = airport_codes[0]
        flight.to_airport = airport_codes[1]
        if flight.stops > 0 and len(airport_codes) >= 3:
            flight.transfer_airports = list(dict.fromkeys(airport_codes[1:-1]))

    flight.airlines = _extract_airlines(card, inner_text)
    flight.is_best = bool(re.search(r"(?i)(beste flüge|top flights|最佳航班)", combined_text))
    return flight


def _extract_all_prices(text: str) -> list[int]:
    patterns = [
        r"€\s*([\d.]+)",
        r"([\d.]+)\s*€",
        r"EUR\s*([\d.]+)",
        r"([\d.]+)\s*EUR",
        r"([\d.]+)\s*Euro",
    ]
    values: list[int] = []
    for pattern in patterns:
        matches = re.findall(pattern, text, re.IGNORECASE)
        for m in matches:
            cleaned = m.replace(".", "").strip()
            if cleaned.isdigit():
                val = int(cleaned)
                if 10 <= val <= 15000:
                    values.append(val)
    return values


def _extract_duration(text: str) -> int:
    en_match = re.search(r"(\d+)\s*h\s*(\d+)\s*m", text, re.IGNORECASE)
    if en_match:
        return int(en_match.group(1)) * 60 + int(en_match.group(2))
    de_match = re.search(r"(?:(\d+)\s*Std\.?)\s*(?:(\d+)\s*Min\.?)?", text)
    if de_match and de_match.group(1):
        return int(de_match.group(1) or 0) * 60 + int(de_match.group(2) or 0)
    return 0


def _extract_airlines(card, text: str) -> list[str]:
    airlines: list[str] = []
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for line in lines:
        l_lower = line.lower()
        # 嚴格排除時間格式（如 09:55）、價格、雜訊與專有名詞
        if re.match(r"^\d{1,2}:\d{2}", line):
            continue
        if any(kw in l_lower for kw in ["€", "eur", "std.", "min.", "co2", "stopp", "nonstop", "geschätzt", "kg", "durchschn", "eigener transfer", "flughafenwechsel"]):
            continue
        cleaned_line = re.sub(r"(?i)^(eigener transfer|self-transfer|separates ticket)\s*[·•\-]\s*", "", line).strip()
        if not cleaned_line or cleaned_line.lower() in {"eigener transfer", "self-transfer", "flughafenwechsel"}:
            continue
        if 2 <= len(cleaned_line) <= 75 and not cleaned_line.isdigit():
            for part in re.split(r"[,、·•]", cleaned_line):
                part_clean = part.strip().rstrip(".")
                if part_clean and not part_clean.isupper() and not re.match(r"^\d{1,2}:\d{2}", part_clean) and part_clean.lower() not in {"eigener transfer", "self-transfer", "flughafenwechsel"}:
                    airlines.append(part_clean)
            if airlines:
                break
    return list(dict.fromkeys(airlines))
