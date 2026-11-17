"""Playwright 終極反偵測與真人行為模擬擷取 (src/playwright_fetcher.py)"""
from __future__ import annotations

import logging
import random
import re
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

DEFAULT_MAX_RESULTS = 80
DEFAULT_SCROLL_COUNT = 15
DEFAULT_WAIT_MS = 2500

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
                "--disable-features=IsolateOrigins,site-per-process",
                "--window-size=1920,1080",
            ],
        )
        
        # 建立高真實度 Context
        context = browser.new_context(
            locale="de-DE",
            timezone_id="Europe/Berlin",
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080},
            device_scale_factor=1,
            is_mobile=False,
            has_touch=False,
            color_scheme="dark",
        )
        
        # 【頂級反偵測注入】：徹底消除自動化特徵與指紋外泄
        context.add_init_script(
            """
            // 1. 移除 webdriver 標記
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            
            // 2. 偽裝 Chrome 運行環境與外掛
            window.navigator.chrome = {
                runtime: {},
                loadTimes: function() {},
                csi: function() {},
                app: {}
            };
            
            // 3. 偽裝語言與硬體核心數
            Object.defineProperty(navigator, 'languages', { get: () => ['de-DE', 'de', 'en-US', 'en'] });
            Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 8 });
            Object.defineProperty(navigator, 'deviceMemory', { get: () => 8 });
            
            // 4. 偽裝 WebGL 渲染器指紋，避開雲端虛擬機特徵
            const getParameterProxy = {
                apply: function(target, ctx, args) {
                    if (args[0] === 37445) return 'Intel Inc.';
                    if (args[0] === 37446) return 'Intel Iris OpenGL Engine';
                    return Reflect.apply(target, ctx, args);
                }
            };
            try {
                window.WebGLRenderingContext.prototype.getParameter = new Proxy(
                    window.WebGLRenderingContext.prototype.getParameter, getParameterProxy
                );
            } catch (e) {}

            // 5. 繞過 permissions 檢測
            const originalQuery = window.navigator.permissions.query;
            window.navigator.permissions.query = (parameters) => (
                parameters.name === 'notifications' ?
                    Promise.resolve({ state: 'denied' }) :
                    originalQuery(parameters)
            );
            """
        )
        
        page = context.new_page()

        try:
            log.info("[STEALTH] 正在以真人行為模擬連線 Google Flights: %s", query_url)
            
            # 隨機延遲，模擬真人輸入網址後的瀏覽行為
            page.goto("https://www.google.com/travel/flights", wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(random.randint(1500, 3000))
            
            # 導向實際目標查詢網址
            page.goto(query_url, wait_until="domcontentloaded", timeout=60000)
            
            # 隨機停留與人類思考時間
            page.wait_for_timeout(random.randint(3500, 5500))
            
            _dismiss_consent_dialog(page)
            _ensure_cheapest_tab_selected(page)

            # 【真人行為模擬】：在頁面隨機移動滑鼠與滾動
            page.mouse.move(random.randint(100, 500), random.randint(100, 500))
            page.wait_for_timeout(1000)

            # 動態滾動載入航班（模擬真人向下瀏覽）
            for _ in range(scroll_count):
                _expand_all_hidden_flights(page)
                scroll_step = random.randint(3000, 5000)
                page.evaluate(f"window.scrollBy(0, {scroll_step})")
                page.wait_for_timeout(random.randint(DEFAULT_WAIT_MS, DEFAULT_WAIT_MS + 1000))

            # 抓取候選卡片
            cards = page.query_selector_all("li, div[role='listitem']")
            if not cards:
                cards = page.query_selector_all("div.pIav2d, div.y11eBe, div.gws-flights-results__itinerary-card")

            log.info("[STEALTH] 檢索到候選卡片數量: %d", len(cards))

            for idx, card in enumerate(cards):
                try:
                    flight = _parse_card(card)
                    if flight and flight.price is not None and flight.price > 0:
                        flights.append(flight)
                except Exception as exc:
                    log.debug("略過卡片 %d: %s", idx, exc)
                    
            # 若標準卡片為空，自動儲存 debug 快照並嘗試全域文字解析
            if not flights:
                log.warning("[STEALTH] 標準卡片解析為 0 筆，儲存 debug_page.html 供診斷...")
                try:
                    html_content = page.content()
                    Path("debug_page.html").write_text(html_content, encoding="utf-8")
                except Exception:
                    pass
                
                body_text = page.evaluate("() => document.body.innerText") or ""
                if "€" in body_text and re.search(r"\d{1,2}:\d{2}", body_text):
                    flights = _parse_global_text(body_text)

        finally:
            browser.close()

    if not flights:
        raise RuntimeError("Playwright 擷取未取得任何航班結果（已自動儲存 debug_page.html，請檢查是否觸發 Google 反爬驗證）。")

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
                page.wait_for_timeout(2000)
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

    prices = sorted(list(set(prices)))
    primary_price = prices[-1] if len(prices) > 1 else prices[0]
    ota_price = prices[0] if len(prices) > 1 else None

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
        times = re.findall(r"(\d{1,2}:\d{2})\s*[–\-—]\s*(\d{1,2}:\d{2})(?:\+\d+)?", chunk)
        prices = _extract_all_prices(chunk)
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
            if any(kw in l_lower for kw in ["€", "eur", "std.", "min.", "co2", "stopp", "nonstop", "geschätzt", "kg", "durchschn", "eigener transfer", "flughafenwechsel"]):
                continue
            cleaned_line = re.sub(r"(?i)^(eigener transfer|self-transfer|separates ticket)\s*[·•\-]\s*", "", line).strip()
            if not cleaned_line or cleaned_line.lower() in {"eigener transfer", "self-transfer"}:
                continue
            if 2 <= len(cleaned_line) <= 75:
                for part in re.split(r"[,、·•]", cleaned_line):
                    part_clean = part.strip().rstrip(".")
                    if part_clean and not part_clean.isupper() and not re.match(r"^\d{1,2}:\d{2}", part_clean):
                        if part_clean.lower() not in {"eigener transfer", "self-transfer", "flughafenwechsel"}:
                            airlines.append(part_clean)
                if airlines:
                    break
    return list(dict.fromkeys(airlines))
