"""Playwright 全量深度擷取：確保切換至 Am günstigsten 分頁、展開全部並支援 li / listitem 雙容器解析。"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

DEFAULT_MAX_RESULTS = 150
DEFAULT_SCROLL_COUNT = 25
DEFAULT_WAIT_MS = 1000

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
    """開啟 Google Flights 頁面、確認切換至「Am günstigsten」分頁、展開全部並抓出所有航班。"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError(
            "未安裝 playwright，請先執行 pip install playwright 與 playwright install chromium。"
        ) from exc

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
            viewport={"width": 1440, "height": 1200},
        )
        context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        page = context.new_page()

        try:
            page.goto(query_url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(1500)

            _dismiss_consent_dialog(page)

            # 等待網頁正文出現起降時間格式（HH:MM – HH:MM）
            try:
                page.wait_for_function(
                    """() => {
                        const txt = document.body ? document.body.innerText : '';
                        return /\\d{1,2}:\\d{2}\\s*[–\\-—]\\s*\\d{1,2}:\\d{2}/.test(txt);
                    }""",
                    timeout=25000,
                )
            except Exception:
                page_title = page.title()
                snippet = (
                    page.evaluate("() => document.body ? document.body.innerText : ''") or ""
                )[:180].replace("\n", " ")
                raise RuntimeError(
                    f"頁面未渲染出航班列表。標題：[{page_title}]，摘要：[{snippet}]"
                ) from None

            page.wait_for_timeout(1000)

            # 1. 強制點擊並確認「Am günstigsten（最便宜）」分頁處於選中狀態
            _ensure_cheapest_tab_selected(page)

            # 2. 循環捲動 + 展開所有摺疊按鈕
            prev_count = 0
            stagnant_rounds = 0
            for _ in range(scroll_count):
                _expand_all_hidden_flights(page)
                page.evaluate("window.scrollBy(0, 3500)")
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

            # 同時支援 <li> 與 <div role="listitem"> 兩種 Google Flights 卡片容器
            cards = page.query_selector_all("li, div[role='listitem']")
            for card in cards:
                try:
                    flight = _parse_card(card)
                    if flight and flight.price is not None and flight.price > 0:
                        flights.append(flight)
                except Exception as exc:
                    log.debug("略過無法解析的航班卡片：%s", exc)
        finally:
            browser.close()

    # 去重並嚴格依最低價格排序
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
    """自動處理 Google 歐洲區 GDPR Cookie 同意視窗或跳轉頁。"""
    try:
        btn = page.locator(
            "button:has-text('Alle ablehnen'), button:has-text('Reject all'), "
            "button:has-text('Alle akzeptieren'), button:has-text('Accept all'), "
            "form[action*='consent'] button"
        ).first
        if btn.count() > 0 and btn.is_visible():
            btn.click(timeout=4000)
            page.wait_for_timeout(1800)
    except Exception:
        pass


def _ensure_cheapest_tab_selected(page) -> None:
    """點擊「Am günstigsten / Cheapest」分頁並驗證是否已切換成功。"""
    for _ in range(3):
        try:
            # 優先使用 Playwright 原生座標點擊（比 JS .click() 更能觸發 Google Wiz 框架事件）
            tab = page.locator("[role='tab']:has-text('Am günstigsten'), [role='tab']:has-text('Cheapest')").first
            if tab.count() > 0 and tab.is_visible():
                if tab.get_attribute("aria-selected") == "true":
                    return
                tab.click(force=True, timeout=3000)
                page.wait_for_timeout(1800)
                if tab.get_attribute("aria-selected") == "true":
                    return

            # 備援：透過文字定位點擊
            text_target = page.locator("text=/Am günstigsten|Cheapest/i").first
            if text_target.count() > 0 and text_target.is_visible():
                text_target.click(force=True, timeout=3000)
                page.wait_for_timeout(1800)
        except Exception as exc:
            log.debug("嘗試點擊 Am günstigsten 分頁：%s", exc)


def _expand_all_hidden_flights(page) -> None:
    """點擊畫面上所有「顯示更多航班 / 其他航班」摺疊按鈕。"""
    try:
        page.evaluate(
            """() => {
                const keywords = [
                    'weitere flüge', 'mehr flüge', 'andere flüge',
                    'more flights', 'show more', 'view more',
                    'günstigere', 'cheaper', '顯示更多', '更多航班', '其他航班'
                ];
                const buttons = Array.from(document.querySelectorAll('button, [role="button"]'));
                for (const btn of buttons) {
                    const txt = (btn.innerText || btn.getAttribute('aria-label') || '').toLowerCase();
                    if (keywords.some(kw => txt.includes(kw))) {
                        btn.click();
                    }
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
    """從單一航班卡片 DOM 提取資訊（同時參考 inner_text 與 aria-label）。"""
    if card.query_selector("li, div[role='listitem']") is not None:
        return None

    inner_text = card.inner_text() or ""
    aria_label = card.get_attribute("aria-label") or ""
    combined_text = f"{inner_text}\n{aria_label}".strip()
    if not combined_text:
        return None

    times = re.findall(r"(\d{1,2}:\d{2})\s*[–\-—]\s*(\d{1,2}:\d{2})", inner_text)
    if not times:
        return None

    price = _extract_price(combined_text)
    if price is None or price <= 0:
        return None

    flight = PlaywrightFlight(
        price=price,
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
    airport_codes = [c for c in airport_codes if c not in {"EUR", "USD", "GBP", "TWD", "HKD"}]
    if len(airport_codes) >= 2:
        flight.from_airport = airport_codes[0]
        flight.to_airport = airport_codes[1]
        if flight.stops > 0 and len(airport_codes) >= 3:
            flight.transfer_airports = list(dict.fromkeys(airport_codes[1:-1]))

    flight.airlines = _extract_airlines(card, inner_text)
    flight.is_best = bool(re.search(r"(?i)(beste flüge|top flights|最佳航班)", combined_text))
    return flight


def _extract_price(text: str) -> int | None:
    """從卡片文字與 aria-label 中提取最低有效票價（支援 €、EUR 與 Euro）。"""
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
    """從單張航班卡片中提取航空公司名稱（過濾 Eigener Transfer 前綴）。"""
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
