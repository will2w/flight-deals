"""Playwright 深度擷取：強制點擊 Google Flights「Am günstigsten（最便宜）」分頁並解析所有低價。"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

DEFAULT_MAX_RESULTS = 80
DEFAULT_SCROLL_COUNT = 15
DEFAULT_WAIT_MS = 1200

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
            p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            ).close()
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
    """開啟 Google Flights 頁面、強制點擊「Am günstigsten」分頁、展開所有摺疊航班並解析。"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError(
            "未安裝 playwright，請先執行 pip install playwright 與 playwright install chromium。"
        ) from exc

    _ensure_chromium_checked()

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
            ],
        )
        context = browser.new_context(
            locale="de-DE",
            timezone_id="Europe/Berlin",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1366, "height": 960},
        )
        context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        page = context.new_page()

        try:
            page.goto(query_url, wait_until="domcontentloaded", timeout=60000)
            _dismiss_consent_dialog(page)

            # 等待初始航班列表載入
            page.wait_for_selector("li", timeout=25000)
            page.wait_for_timeout(2000)

            # 關鍵步驟：強制點擊頂部右側的「Am günstigsten / Cheapest（最便宜）」分頁
            _click_cheapest_tab_safely(page)

            # 展開所有摺疊的「顯示更多航班」
            _click_show_more(page)

            prev_count = 0
            stagnant_rounds = 0
            for _ in range(scroll_count):
                page.mouse.wheel(0, 4500)
                page.wait_for_timeout(DEFAULT_WAIT_MS)
                _click_show_more(page)

                current = _count_visible_cards(page)
                if current <= prev_count:
                    stagnant_rounds += 1
                    if stagnant_rounds >= 2 and current > 0:
                        break
                else:
                    stagnant_rounds = 0
                prev_count = current
                if current >= max_results * 3:
                    break

            cards = page.query_selector_all("li")
            for card in cards:
                try:
                    flight = _parse_card(card)
                    if flight and flight.price is not None and flight.price > 0:
                        flights.append(flight)
                except Exception as exc:
                    log.debug("略過無法解析的航班卡片：%s", exc)
        finally:
            browser.close()

    # 去重並依最低價格排序
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
    """自動關閉歐洲區 Google GDPR Cookie 同意視窗。"""
    try:
        reject_btn = page.get_by_role(
            "button",
            name=re.compile(r"(?i)(alle ablehnen|reject all|tout refuser|全部拒絕)"),
        )
        if reject_btn.count() > 0 and reject_btn.first.is_visible():
            reject_btn.first.click(timeout=3000)
            page.wait_for_timeout(1200)
    except Exception:
        pass


def _click_cheapest_tab_safely(page) -> None:
    """精準定位並點擊「Am günstigsten（最便宜）」按鈕或區塊。"""
    try:
        # 方法 1：尋找包含「Am günstigsten」或「ab [金額] €」的按鈕／區塊
        candidates = page.locator("text=/Am günstigsten/i")
        if candidates.count() > 0:
            for i in range(candidates.count()):
                el = candidates.nth(i)
                if el.is_visible():
                    el.click(timeout=3000)
                    page.wait_for_timeout(2000)
                    log.info("已成功點擊「Am günstigsten」分頁。")
                    return
        
        # 方法 2：透過座標或點擊第二個分頁籤
        tabs = page.locator("div[role='tab']")
        if tabs.count() >= 2:
            tabs.nth(1).click(timeout=3000)
            page.wait_for_timeout(2000)
    except Exception as exc:
        log.debug("點擊最便宜分頁時略過：%s", exc)


def _click_show_more(page) -> None:
    """展開所有摺疊的「其他航班 / 顯示更多航班」區塊。"""
    try:
        buttons = page.get_by_role(
            "button",
            name=re.compile(
                r"(?i)(weitere flüge|mehr flüge|more flights|show more|günstigere|cheaper|顯示更多|更多航班)"
            ),
        )
        for btn in buttons.all():
            if btn.is_visible():
                btn.click(timeout=2500)
                page.wait_for_timeout(1000)
    except Exception:
        pass


def _count_visible_cards(page) -> int:
    try:
        return page.locator("li").count()
    except Exception:
        return 0


def _parse_card(card) -> PlaywrightFlight | None:
    """從單一航班卡片 DOM 提取資訊（排除包含多張子卡片的外層容器 <li>）。"""
    if card.query_selector("li") is not None:
        return None

    text = card.inner_text() or ""
    if not text.strip():
        return None

    times = re.findall(r"(\d{1,2}:\d{2})\s*[–\-—]\s*(\d{1,2}:\d{2})", text)
    if not times:
        return None

    price = _extract_price(text)
    if price is None or price <= 0:
        return None

    flight = PlaywrightFlight(
        price=price,
        departure_time=times[0][0],
        arrival_time=times[0][1],
    )

    stops_match = re.search(r"(\d+)\s*(Stop|Stopp|停)", text, re.IGNORECASE)
    if stops_match:
        flight.stops = int(stops_match.group(1))
    elif re.search(r"(?i)(nonstop|direkt|直飛|直航)", text):
        flight.stops = 0

    flight.duration_minutes = _extract_duration(text)

    airport_codes = re.findall(r"\b([A-Z]{3})\b", text)
    airport_codes = [c for c in airport_codes if c not in {"EUR", "USD", "GBP", "TWD", "HKD"}]
    if len(airport_codes) >= 2:
        flight.from_airport = airport_codes[0]
        flight.to_airport = airport_codes[1]
        if flight.stops > 0 and len(airport_codes) >= 3:
            flight.transfer_airports = list(dict.fromkeys(airport_codes[1:-1]))

    flight.airlines = _extract_airlines(card, text)
    flight.is_best = bool(re.search(r"(?i)(beste flüge|top flights|最佳航班)", text))
    return flight


def _extract_price(text: str) -> int | None:
    """從卡片文字中提取最低有效票價。"""
    patterns = [
        r"€\s*([\d.]+)",
        r"([\d.]+)\s*€",
        r"EUR\s*([\d.]+)",
        r"([\d.]+)\s*EUR",
    ]
    values: list[int] = []
    for pattern in patterns:
        matches = re.findall(pattern, text)
        for m in matches:
            cleaned = m.replace(".", "").strip()
            if cleaned.isdigit():
                val = int(cleaned)
                if val >= 10:
                    values.append(val)
    return min(values) if values else None


def _extract_duration(text: str) -> int:
    """從卡片文字中提取總時長（分鐘）。"""
    en_match = re.search(r"(\d+)\s*h\s*(\d+)\s*m", text, re.IGNORECASE)
    if en_match:
        return int(en_match.group(1)) * 60 + int(en_match.group(2))

    de_match = re.search(
        r"(?:(\d+)\s*Std\.?)\s*(?:(\d+)\s*Min\.?)?",
        text,
    )
    if de_match and de_match.group(1):
        hours = int(de_match.group(1) or 0)
        minutes = int(de_match.group(2) or 0)
        return hours * 60 + minutes
    return 0


def _extract_airlines(card, text: str) -> list[str]:
    """從單張航班卡片中提取航空公司名稱。"""
    airlines: list[str] = []

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for line in lines:
        if re.search(r"(€|EUR|\d{1,2}:\d{2}|Std\.|Min\.|CO2|Stopp|Nonstop|Flughafenwechsel)", line):
            continue
        cleaned_line = re.sub(
            r"(?i)^(eigener transfer|self-transfer|separates ticket|separate tickets)\s*[·•\-]\s*",
            "",
            line,
        ).strip()
        if not cleaned_line or cleaned_line.lower() in {"eigener transfer", "self-transfer"}:
            continue
        if 2 <= len(cleaned_line) <= 75:
            for part in re.split(r"[,、·•]", cleaned_line):
                part_clean = part.strip().rstrip(".")
                if (
                    part_clean
                    and not part_clean.isupper()
                    and part_clean.lower() not in {"eigener transfer", "self-transfer"}
                ):
                    airlines.append(part_clean)
            if airlines:
                break

    if not airlines:
        try:
            imgs = card.query_selector_all("img")
            for img in imgs:
                label = img.get_attribute("aria-label") or img.get_attribute("alt") or ""
                if label and len(label) < 60:
                    airlines.append(label.strip())
        except Exception:
            pass

    return list(dict.fromkeys(airlines))
