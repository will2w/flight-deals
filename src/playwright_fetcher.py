"""Playwright 終極除錯版：完整診斷卡片文字與價格提取過程 (src/playwright_fetcher.py)"""
from __future__ import annotations

import logging
import random
import re
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

DEFAULT_MAX_RESULTS = 100
DEFAULT_SCROLL_COUNT = 30
DEFAULT_WAIT_MS = 3000

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
    ota_price: int | None = None
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
                "--disable-infobars",
                "--disable-extensions",
                "--disable-blink-features=AutomationControlled",
                "--window-size=1920,1080",
            ],
        )
        context = browser.new_context(
            locale="de-DE",
            timezone_id="Europe/Berlin",
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080},
        )
        
        context.add_init_script(
            """
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            window.navigator.chrome = { runtime: {} };
            Object.defineProperty(navigator, 'languages', { get: () => ['de-DE', 'de', 'en-US', 'en'] });
            """
        )
        
        page = context.new_page()

        try:
            log.info("[STEALTH] 正在連線 Google Flights: %s", query_url)
            page.goto("https://www.google.com/travel/flights", wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(random.randint(1500, 2500))
            
            page.goto(query_url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(4000)
            
            _dismiss_consent_dialog(page)
            
            # 1. 強制點擊「Am günstigsten」（最便宜）分頁
            _force_click_cheapest_tab(page)

            # 2. 循環滾動與點擊展開更多航班
            last_height = 0
            for i in range(scroll_count):
                _expand_all_hidden_flights(page)
                page.evaluate("window.scrollTo(0, document.body.scrollHeight);")
                page.wait_for_timeout(DEFAULT_WAIT_MS)
                
                current_height = page.evaluate("document.body.scrollHeight")
                if current_height == last_height and i > 5:
                    log.info(f"[SCROLL] 頁面高度已達極限，提前結束滾動（第 {i+1} 輪）。")
                    break
                last_height = current_height

            _expand_all_hidden_flights(page)
            page.wait_for_timeout(2000)

            cards = page.query_selector_all("li, div[role='listitem']")
            if not cards:
                cards = page.query_selector_all("div.pIav2d, div.y11eBe, div.gws-flights-results__itinerary-card")

            log.info("檢索到候選卡片總數: %d", len(cards))

            for idx, card in enumerate(cards):
                try:
                    flight = _parse_card(card, idx)
                    if flight and flight.price is not None and flight.price > 0:
                        flights.append(flight)
                except Exception as exc:
                    log.debug("略過卡片 %d：%s", idx, exc)

            if not flights:
                body_text = page.evaluate("() => document.body.innerText") or ""
                if "€" in body_text and re.search(r"\d{1,2}:\d{2}", body_text):
                    flights = _parse_global_text(body_text)

        finally:
            browser.close()

    if not flights:
        raise RuntimeError("Playwright 擷取未取得任何航班結果。")

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


def _force_click_cheapest_tab(page) -> None:
    try:
        tab = page.locator("[role='tab']:has-text('Am günstigsten'), [role='tab']:has-text('Cheapest')").first
        if tab.count() > 0 and tab.is_visible():
            tab.click(force=True, timeout=5000)
            log.info("[STEALTH] 透過 Locator 成功點擊 'Am günstigsten' 分頁。")
            page.wait_for_timeout(4000)
            return

        clicked = page.evaluate(
            """() => {
                const elements = Array.from(document.querySelectorAll('[role="tab"], button, div, span'));
                for (const el of elements) {
                    const txt = (el.innerText || el.getAttribute('aria-label') || '').toLowerCase();
                    if (txt.includes('am günstigsten') || txt.includes('cheapest')) {
                        el.click();
                        return true;
                    }
                }
                return false;
            }"""
        )
        if clicked:
            log.info("[STEALTH] 透過 JS 成功點擊 'Am günstigsten' 分頁。")
            page.wait_for_timeout(4000)
    except Exception as exc:
        log.warning("[STEALTH] 點擊分頁異常：%s", exc)


def _expand_all_hidden_flights(page) -> None:
    try:
        page.evaluate(
            """() => {
                const keywords = [
                    'mehr flüge', 'weitere flüge', 'more flights', 'show more', 
                    'günstigere', 'cheaper', '顯示更多', '更多航班', 'alle ansehen', 'show all'
                ];
                const buttons = Array.from(document.querySelectorAll('button, [role="button"], div[role="button"], span'));
                for (const btn of buttons) {
                    const txt = (btn.innerText || btn.getAttribute('aria-label') || '').toLowerCase();
                    if (keywords.some(kw => txt.includes(kw))) {
                        try { btn.click(); } catch (e) {}
                    }
                }
            }"""
        )
    except Exception:
        pass


def _parse_card(card, card_idx: int) -> PlaywrightFlight | None:
    if card.query_selector("li, div[role='listitem']") is not None:
        return None

    inner_text = card.inner_text() or ""
    aria_label = card.get_attribute("aria-label") or ""
    
    combined_text = f"{inner_text}\n{aria_label}".strip()
    if not combined_text:
        return None

    # 過濾自行轉機
    lower_text = combined_text.lower()
    excluded_keywords = ["eigener transfer", "self-transfer", "separates ticket", "flughafenwechsel"]
    if any(kw in lower_text for kw in excluded_keywords):
        log.debug("[PARSE] 卡片 %d 包含自行轉機，已過濾。", card_idx)
        return None

    times = re.findall(r"(\d{1,2}:\d{2})\s*[–\-—]\s*(\d{1,2}:\d{2})(?:\+\d+)?", inner_text)
    if not times:
        return None

    prices = _extract_exact_prices(combined_text)
    if not prices:
        log.debug("[PARSE] 卡片 %d 未能提取到任何有效價格。原始文字:\n%s", card_idx, inner_text[:200])
        return None

    prices = sorted(list(set(prices)))
    primary_price = prices[0]
    ota_price = prices[1] if len(prices) > 1 else None

    # 【DEBUG 核心】：把抓到的價格與對應文字印到日誌中，方便您在前端展開查看
    log.info("[DEBUG_CARD %d] 提取價格陣列: %s -> 採用主票價: €%s | 航空公司/內容片段: %s", 
             card_idx, prices, primary_price, inner_text.replace('\n', ' ')[:100])

    flight = PlaywrightFlight(
        price=primary_price,
        ota_price=ota_price,
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


def _parse_global_text(body_text: str) -> list[PlaywrightFlight]:
    flights: list[PlaywrightFlight] = []
    blocks = body_text.split("\n")
    for i in range(len(blocks) - 3):
        chunk = "\n".join(blocks[max(0, i-2):min(len(blocks), i+5)])
        
        chunk_lower = chunk.lower()
        if any(kw in chunk_lower for kw in ["eigener transfer", "self-transfer", "separates ticket"]):
            continue

        times = re.findall(r"(\d{1,2}:\d{2})\s*[–\-—]\s*(\d{1,2}:\d{2})(?:\+\d+)?", chunk)
        prices = _extract_exact_prices(chunk)
        if times and prices:
            flights.append(
                PlaywrightFlight(
                    price=prices[0],
                    departure_time=times[0][0],
                    arrival_time=times[0][1],
                    stops=0 if "nonstop" in chunk.lower() or "direkt" in chunk.lower() else 1
                )
            )
    return flights


def _extract_exact_prices(text: str) -> list[int]:
    # 嚴格匹配帶有 € 或 EUR 的金額格式，避免抓到時長或 CO2 數值
    patterns = [
        r"€\s*([\d.]+)",
        r"([\d.]+)\s*€",
        r"EUR\s*([\d.]+)",
        r"([\d.]+)\s*EUR",
    ]
    values: list[int] = []
    for pattern in patterns:
        matches = re.findall(pattern, text, re.IGNORECASE)
        for m in matches:
            cleaned = m.replace(".", "").strip()
            if cleaned.isdigit():
                val = int(cleaned)
                if 50 <= val <= 15000:
                    values.append(val)
    return sorted(list(set(values)))


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
    try:
        imgs = card.query_selector_all("img")
        for img in imgs:
            alt = img.get_attribute("alt") or img.get_attribute("title") or ""
            alt = alt.strip()
            if alt and len(alt) >= 2 and alt.lower() not in {"logo", "flight", "flug", "icon", "airline"}:
                if alt not in airlines:
                    airlines.append(alt)
    except Exception:
        pass

    if not airlines:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        for line in lines:
            l_lower = line.lower()
            if any(kw in l_lower for kw in ["€", "eur", "std.", "min.", "co2", "stopp", "nonstop", "geschätzt", "kg", "durchschn"]):
                continue
            cleaned_line = re.sub(r"(?i)^(eigener transfer|self-transfer|separates ticket)\s*[·•\-]\s*", "", line).strip()
            if not cleaned_line:
                continue
            if 2 <= len(cleaned_line) <= 75:
                for part in re.split(r"[,、·•]", cleaned_line):
                    part_clean = part.strip().rstrip(".")
                    if part_clean and not part_clean.isupper() and not re.match(r"^\d{1,2}:\d{2}", part_clean):
                        if part_clean.lower() not in {"eigener transfer", "self-transfer", "flughafenwechsel", "separates ticket"}:
                            airlines.append(part_clean)
                if airlines:
                    break
    return list(dict.fromkeys(airlines))
