"""Streamlit 前端：自選航線即時搜尋、監控清單、價格走勢（航空雷達科技感 UI）。

本機執行：
    streamlit run app.py
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import airportsdata
import pandas as pd
import streamlit as st
import yaml

st.set_page_config(
    page_title="AERO-RADAR // 特價機票監控系統",
    page_icon="✈️",
    layout="wide",
    initial_sidebar_state="collapsed",
)

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.config import load_config  # noqa: E402
from src.github_config import update_routes  # noqa: E402
from src.pipeline import build_baselines, is_deal  # noqa: E402
from src.provider import GoogleFlightsProvider  # noqa: E402
from src.store import read_history  # noqa: E402

SNAPSHOT_PATH = ROOT / "data" / "deals.json"
HISTORY_PATH = ROOT / "data" / "history.csv"

SEAT_LABELS = {
    "economy": "經濟艙 // ECONOMY",
    "premium-economy": "豪華經濟艙 // PREMIUM",
    "business": "商務艙 // BUSINESS",
    "first": "頭等艙 // FIRST",
}

STOP_CHOICES = {"不限": None, "只要直飛": 0, "最多轉 1 次": 1}
GOOGLE_429_COOLDOWN_SECONDS = 15 * 60

# 常用機場（三碼代碼 -> 顯示名稱）
AIRPORTS: dict[str, str] = {
    # 台灣
    "TPE": "台北 桃園", "TSA": "台北 松山", "KHH": "高雄", "RMQ": "台中", "TNN": "台南",
    "MZG": "澎湖", "HUN": "花蓮",
    # 東北亞
    "NRT": "東京 成田", "HND": "東京 羽田", "KIX": "大阪 關西", "ITM": "大阪 伊丹",
    "NGO": "名古屋", "FUK": "福岡", "CTS": "札幌 新千歲", "OKA": "沖繩", "SDJ": "仙台",
    "HIJ": "廣島", "KOJ": "鹿兒島", "ICN": "首爾 仁川", "GMP": "首爾 金浦",
    "PUS": "釜山", "CJU": "濟州", "TAE": "大邱",
    # 港澳與中國
    "HKG": "香港", "MFM": "澳門", "PVG": "上海 浦東", "SHA": "上海 虹橋",
    "PEK": "北京 首都", "PKX": "北京 大興", "CAN": "廣州", "SZX": "深圳",
    "CTU": "成都 雙流", "TFU": "成都 天府", "XMN": "廈門", "HGH": "杭州",
    "NKG": "南京", "CKG": "重慶", "WUH": "武漢", "TAO": "青島", "XIY": "西安", "KMG": "昆明",
    # 東南亞
    "BKK": "曼谷 蘇凡納布", "DMK": "曼谷 廊曼", "CNX": "清邁", "HKT": "普吉",
    "SGN": "胡志明市", "HAN": "河內", "DAD": "峴港", "SIN": "新加坡",
    "KUL": "吉隆坡", "PEN": "檳城", "BKI": "亞庇", "MNL": "馬尼拉", "CEB": "宿霧",
    "CGK": "雅加達", "DPS": "峇里島", "RGN": "仰光", "PNH": "金邊",
    # 南亞與中東
    "DEL": "德里", "BOM": "孟買", "BLR": "班加羅爾", "MLE": "馬爾地夫",
    "CMB": "可倫坡", "DXB": "杜拜", "DOH": "杜哈", "IST": "伊斯坦堡",
    # 大洋洲
    "SYD": "雪梨", "MEL": "墨爾本", "BNE": "布里斯本", "AKL": "奧克蘭",
    "GUM": "關島", "NAN": "楠迪",
    # 歐洲
    "DUS": "杜塞道夫", "FRA": "法蘭克福", "MUC": "慕尼黑", "BER": "柏林",
    "AMS": "阿姆斯特丹", "CDG": "巴黎 戴高樂", "LHR": "倫敦 希斯洛",
    "FCO": "羅馬", "MXP": "米蘭", "MAD": "馬德里", "BCN": "巴塞隆納",
    "ZRH": "蘇黎世", "VIE": "維也納", "PRG": "布拉格", "CPH": "哥本哈根",
    "ARN": "斯德哥爾摩", "HEL": "赫爾辛基", "ATH": "雅典", "LIS": "里斯本",
    # 北美
    "LAX": "洛杉磯", "SFO": "舊金山", "SEA": "西雅圖", "JFK": "紐約 甘迺迪",
    "ORD": "芝加哥", "BOS": "波士頓", "DFW": "達拉斯", "YVR": "溫哥華", "YYZ": "多倫多",
}


def inject_cyber_theme() -> None:
    """注入深色航空雷達科技感 CSS 樣式（高對比、毛玻璃卡片、等寬數據字體）。"""
    st.markdown(
        """
        <style>
        @import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600;700&family=Inter:wght@400;500;600;700&display=swap');

        /* 全局深色科技背景 */
        .stApp {
            background: radial-gradient(circle at 15% 10%, #0f172a 0%, #090d16 55%, #05070c 100%);
            color: #e2e8f0;
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
        }

        /* 頂部漸層標題橫幅 */
        .aero-banner {
            background: linear-gradient(135deg, rgba(14, 165, 233, 0.14) 0%, rgba(99, 102, 241, 0.10) 50%, rgba(16, 185, 129, 0.08) 100%);
            border: 1px solid rgba(56, 189, 248, 0.28);
            border-radius: 14px;
            padding: 1.25rem 1.6rem;
            margin-bottom: 1.2rem;
            box-shadow: 0 0 28px rgba(14, 165, 233, 0.08);
            backdrop-filter: blur(12px);
        }
        .aero-badge {
            display: inline-block;
            font-family: 'JetBrains Mono', monospace;
            font-size: 0.72rem;
            font-weight: 700;
            letter-spacing: 0.12em;
            color: #38bdf8;
            background: rgba(56, 189, 248, 0.12);
            border: 1px solid rgba(56, 189, 248, 0.35);
            padding: 0.2rem 0.65rem;
            border-radius: 999px;
            margin-bottom: 0.45rem;
        }
        .aero-title {
            font-size: 1.85rem;
            font-weight: 700;
            letter-spacing: -0.02em;
            background: linear-gradient(90deg, #f8fafc 0%, #38bdf8 55%, #34d399 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            margin: 0;
            line-height: 1.25;
        }
        .aero-subtitle {
            font-size: 0.85rem;
            color: #94a3b8;
            margin-top: 0.35rem;
            font-family: 'JetBrains Mono', monospace;
        }

        /* 表單與容器毛玻璃質感 */
        [data-testid="stForm"], [data-testid="stExpander"] {
            background: rgba(15, 23, 42, 0.72) !important;
            border: 1px solid rgba(56, 189, 248, 0.22) !important;
            border-radius: 12px !important;
            box-shadow: 0 8px 32px rgba(0, 0, 0, 0.35);
            backdrop-filter: blur(10px);
        }

        /* 指標卡片 (st.metric) 發光邊框與等寬數字 */
        [data-testid="stMetric"] {
            background: linear-gradient(160deg, rgba(15, 23, 42, 0.9) 0%, rgba(12, 20, 36, 0.95) 100%);
            border: 1px solid rgba(56, 189, 248, 0.25);
            border-left: 3px solid #38bdf8;
            border-radius: 10px;
            padding: 0.85rem 1.1rem;
            box-shadow: 0 4px 20px rgba(0, 0, 0, 0.25);
        }
        [data-testid="stMetricLabel"] {
            color: #94a3b8 !important;
            font-size: 0.8rem !important;
            text-transform: uppercase;
            letter-spacing: 0.05em;
        }
        [data-testid="stMetricValue"] {
            font-family: 'JetBrains Mono', monospace !important;
            font-weight: 700 !important;
            color: #38bdf8 !important;
        }

        /* 頁籤 Tabs 樣式 */
        .stTabs [data-baseweb="tab-list"] {
            gap: 0.5rem;
            background: rgba(15, 23, 42, 0.6);
            padding: 0.35rem;
            border-radius: 10px;
            border: 1px solid rgba(148, 163, 184, 0.15);
        }
        .stTabs [data-baseweb="tab"] {
            border-radius: 7px;
            color: #94a3b8;
            font-weight: 600;
            padding: 0.45rem 1.1rem;
        }
        .stTabs [aria-selected="true"] {
            background: linear-gradient(90deg, rgba(14, 165, 233, 0.25), rgba(52, 211, 153, 0.2)) !important;
            color: #f8fafc !important;
            border: 1px solid rgba(56, 189, 248, 0.45) !important;
        }

        /* 主搜尋按鈕霓虹漸層 */
        button[kind="primaryFormSubmit"], button[kind="primary"] {
            background: linear-gradient(90deg, #0284c7 0%, #0ea5e9 50%, #10b981 100%) !important;
            border: none !important;
            color: #ffffff !important;
            font-weight: 700 !important;
            letter-spacing: 0.04em;
            border-radius: 8px !important;
            box-shadow: 0 0 18px rgba(14, 165, 233, 0.35) !important;
            transition: all 0.2s ease !important;
        }
        button[kind="primaryFormSubmit"]:hover, button[kind="primary"]:hover {
            box-shadow: 0 0 26px rgba(16, 185, 129, 0.55) !important;
            transform: translateY(-1px);
        }

        /* 數據表格圓角與邊框 */
        [data-testid="stDataFrame"] {
            border: 1px solid rgba(56, 189, 248, 0.22);
            border-radius: 10px;
            overflow: hidden;
        }

        /* 分隔線發光感 */
        hr {
            border-color: rgba(56, 189, 248, 0.18) !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


@st.cache_data
def load_iata_airports() -> dict:
    return airportsdata.load("IATA")


for airport_code, airport in load_iata_airports().items():
    if len(airport_code) != 3:
        continue
    airport_name = " · ".join(
        str(airport.get(field, "")).strip()
        for field in ("city", "name", "country")
        if airport.get(field)
    )
    AIRPORTS.setdefault(airport_code, airport_name or airport_code)


def airport_label(code: str) -> str:
    return f"{code} · {AIRPORTS.get(code, '')}".strip(" ·")


@st.cache_data(ttl=600)
def load_snapshot() -> dict | None:
    if not SNAPSHOT_PATH.exists():
        return None
    return json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))


@st.cache_data(ttl=600)
def load_history() -> pd.DataFrame:
    if not HISTORY_PATH.exists():
        return pd.DataFrame()
    return pd.read_csv(HISTORY_PATH)


@st.cache_data(ttl=300, show_spinner=False)
def cached_search(
    segments: tuple[tuple[str, str, str], ...],
    trip_type: str,
    seat: str,
    currency: str,
    language: str,
    max_stops: int | None,
    carry_on_bags: int,
    checked_bags: int,
    hide_separate_and_self_transfer: bool,
    exclude_basic_economy: bool,
    use_playwright: bool = True,
) -> list[dict]:
    """查詢多航段行程；預設啟用價格排序深度擷取，快取 5 分鐘。"""
    provider = GoogleFlightsProvider(
        currency=currency,
        language=language,
        seat=seat,
        carry_on_bags=carry_on_bags,
        checked_bags=checked_bags,
        hide_separate_and_self_transfer=hide_separate_and_self_transfer,
        exclude_basic_economy=exclude_basic_economy,
        use_playwright=use_playwright,
    )
    offers = provider.search_itinerary(
        list(segments), trip_type=trip_type, max_stops=max_stops
    )
    quote_checked_at = datetime.now(timezone.utc).isoformat()
    return [
        {**offer.to_dict(), "quote_checked_at": quote_checked_at}
        for offer in offers
    ]


def mark_deals(rows: list[dict]) -> pd.DataFrame:
    """用歷史基準標記每筆是否為特價。"""
    baselines = build_baselines(read_history(), currency="EUR")
    rules = load_config().deal_rules
    for row in rows:
        key = (row["from_airport"], row["to_airport"], row["departure_date"])
        prices = (
            baselines.get(key, [])
            if row.get("trip_type", "one-way") == "one-way"
            else []
        )
        row["is_deal"] = is_deal(row["price"], prices, rules)
        row["baseline_median"] = int(pd.Series(prices).median()) if prices else None
    return pd.DataFrame(rows)


def format_duration(minutes) -> str:
    """將分鐘格式化為小時，保留一位小數。"""
    try:
        val = float(minutes)
        return f"{val / 60:.1f} h" if val > 0 else "—"
    except (TypeError, ValueError):
        return "—"


def format_transfers(value, stops: int | float | None = None) -> str:
    if isinstance(value, list):
        if value:
            return "、".join(str(item) for item in value)
        return "未提供" if stops and stops > 0 else "直飛 NONSTOP"
    if not isinstance(value, str) or not value.strip():
        return "未提供" if stops and stops > 0 else "直飛 NONSTOP"
    return "、".join(part for part in value.split("|") if part)


def read_secret(name: str, default: str = "") -> str:
    try:
        value = st.secrets.get(name, default)
    except Exception:
        value = os.environ.get(name, default)
    return str(value or default).strip()


def route_manager() -> None:
    """編輯監控航線，並透過 GitHub API 將設定保存至倉庫。"""
    config = load_config()
    if "watch_routes" not in st.session_state:
        st.session_state["watch_routes"] = [
            {"from": route.from_airport, "to": route.to_airport}
            for route in config.routes
        ]

    routes = st.session_state["watch_routes"]
    st.subheader("📡 監控航線雷達配置")
    st.caption("新增或移除航線後同步回 GitHub；Actions 排程將自動執行價格排序掃描。")

    with st.form("add_monitored_route"):
        left, right = st.columns(2)
        origin = left.selectbox("出發機場 ORIGIN", list(AIRPORTS), format_func=airport_label, key="watch_origin")
        destination = right.selectbox(
            "抵達機場 DESTINATION",
            list(AIRPORTS),
            index=list(AIRPORTS).index("NRT") if "NRT" in AIRPORTS else 1,
            format_func=airport_label,
            key="watch_destination",
        )
        add_route = st.form_submit_button("＋ 註冊監控航線", use_container_width=True)

    if add_route:
        if origin == destination:
            st.warning("出發地與目的地不能相同。")
        elif any(r["from"] == origin and r["to"] == destination for r in routes):
            st.info("這條航線已在監控清單中。")
        else:
            routes.append({"from": origin, "to": destination})
            st.session_state["watch_routes"] = routes
            st.rerun()

    labels = [f"{route['from']} → {route['to']}" for route in routes]
    if labels:
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "航線代碼": f"{route['from']} ➔ {route['to']}",
                        "出發地": airport_label(route["from"]),
                        "目的地": airport_label(route["to"]),
                    }
                    for route in routes
                ]
            ),
            use_container_width=True,
            hide_index=True,
        )
        remove_label = st.selectbox("移除監控航線", ["不移除"] + labels, key="remove_route")
        if st.button("刪除所選航線", disabled=remove_label == "不移除"):
            index = labels.index(remove_label)
            routes.pop(index)
            st.session_state["watch_routes"] = routes
            st.rerun()
    else:
        st.info("監控清單為空，請先新增至少一條航線。")

    config_data = yaml.safe_load((ROOT / "config" / "routes.yaml").read_text(encoding="utf-8")) or {}
    config_data["settings"] = config_data.get("settings", {})
    config_data["settings"]["currency"] = "EUR"
    config_data["routes"] = routes
    yaml_content = yaml.safe_dump(
        config_data, allow_unicode=True, sort_keys=False, default_flow_style=False
    )

    token = read_secret("GITHUB_TOKEN")
    repository = read_secret("GITHUB_REPOSITORY")
    branch = read_secret("GITHUB_BRANCH", "main")
    col_save, col_download = st.columns(2)
    if col_save.button("同步寫入 GitHub 排程", type="primary", use_container_width=True):
        if not token or not repository:
            st.error("尚未設定 GitHub 權限。請在 Streamlit Secrets 加入 GITHUB_TOKEN 與 GITHUB_REPOSITORY。")
        elif not routes:
            st.error("至少保留一條監控航線。")
        else:
            try:
                commit_url = update_routes(repository, branch, token, routes)
                st.success("航線設定已寫入 GitHub Repository。")
                if commit_url:
                    st.link_button("檢視 GitHub Commit 紀錄", commit_url)
            except Exception as exc:
                st.error(f"同步失敗：{exc}")

    col_download.download_button(
        "匯出 routes.yaml",
        data=yaml_content.encode("utf-8"),
        file_name="routes.yaml",
        mime="text/yaml",
        use_container_width=True,
    )


def render_results(df: pd.DataFrame, currency: str) -> None:
    if df.empty:
        st.info("未偵測到符合條件的航班，請嘗試放寬轉機限制或調整日期。")
        return

    if "quote_checked_at" in df:
        quote_times = pd.to_datetime(df["quote_checked_at"], utc=True, errors="coerce")
    else:
        quote_times = pd.Series(dtype="datetime64[ns, UTC]")
    oldest_quote_time = quote_times.min()
    if pd.notna(oldest_quote_time):
        quote_age = (pd.Timestamp.now(tz="UTC") - oldest_quote_time).total_seconds()
        if quote_age > 300:
            st.warning("目前快取報價已超過 5 分鐘，建議重新掃描取得最新即時票價。")

    # 嚴格依價格從低到高排序
    view = df.sort_values(["price", "duration_minutes"], ascending=[True, True]).copy()
    cheapest = view.iloc[0]
    direct = view[view["stops"] == 0]

    col1, col2, col3, col4 = st.columns(4)
    col1.metric(f"最低掃描票價 ({currency})", f"€ {int(cheapest['price']):,}")
    col2.metric(
        f"直飛最低價 ({currency})",
        f"€ {int(direct['price'].min()):,}" if not direct.empty else "無直飛航線",
    )
    col3.metric("掃描有效組合", f"{len(view)} 筆")
    col4.metric("命中歷史特價", f"{int(view['is_deal'].sum())} 筆")

    view["航空公司"] = view["airlines"].apply(
        lambda v: "、".join(v) if isinstance(v, list) and v else "未標示"
    )
    view["航段路徑"] = view["segments"].apply(
        lambda segments: " / ".join(
            f"{segment['from_airport']} ➔ {segment['to_airport']}"
            for segment in segments
        )
    )
    view["去程時段"] = view["segments"].apply(lambda segments: _segment_times(segments, 0))
    view["回程時段"] = view["segments"].apply(lambda segments: _segment_times(segments, 1))
    view["去程時長"] = view["segments"].apply(lambda segments: _segment_duration(segments, 0))
    view["回程時長"] = view["segments"].apply(lambda segments: _segment_duration(segments, 1))
    view["轉機節點"] = view["segments"].apply(_format_itinerary_transfers)
    quote_source = (
        view["quote_checked_at"]
        if "quote_checked_at" in view
        else pd.Series(pd.NaT, index=view.index, dtype="datetime64[ns, UTC]")
    )
    view["報價時間 (BER)"] = (
        pd.to_datetime(quote_source, utc=True, errors="coerce")
        .dt.tz_convert(ZoneInfo("Europe/Berlin"))
        .dt.strftime("%Y-%m-%d %H:%M")
    )
    price_basis = view.get(
        "price_basis", pd.Series("playwright_scroll_quote", index=view.index)
    )
    view["擷取引擎"] = price_basis.map(
        {
            "google_quote": "HTTP 快速擷取",
            "fast_flights_quote": "HTTP 快速擷取",
            "fast_flights_round_trip_quote": "HTTP 往返擷取",
            "sum_of_one_way_quotes": "多段單程加總",
            "playwright_scroll_quote": "瀏覽器價格排序深度擷取",
        }
    ).fillna("瀏覽器價格排序深度擷取")
    view["特價標記"] = view["is_deal"].map({True: "🔥 DEAL", False: "—"})

    columns = {
        "price": f"票價 ({currency})",
        "航空公司": "航空公司",
        "stops": "轉機次數",
        "轉機節點": "轉機節點",
        "航段路徑": "航段路徑",
        "departure_date": "去程日",
        "return_date": "回程日",
        "去程時段": "去程時段",
        "去程時長": "去程時長",
        "回程時段": "回程時段",
        "回程時長": "回程時長",
        "baseline_median": f"歷史中位數 ({currency})",
        "特價標記": "特價標記",
        "擷取引擎": "擷取引擎",
        "報價時間 (BER)": "報價時間 (BER)",
    }
    st.dataframe(
        view[list(columns)].rename(columns=columns),
        use_container_width=True,
        hide_index=True,
        column_config={
            f"票價 ({currency})": st.column_config.NumberColumn(
                f"票價 ({currency})",
                format="€ %d",
            ),
            f"歷史中位數 ({currency})": st.column_config.NumberColumn(
                f"歷史中位數 ({currency})",
                format="€ %d",
            ),
        },
    )

    st.download_button(
        "⬇ 下載完整報價矩陣 CSV",
        view.to_csv(index=False).encode("utf-8-sig"),
        file_name=f"aero-radar_{cheapest['from_airport']}-{cheapest['to_airport']}_{cheapest['departure_date']}.csv",
        mime="text/csv",
        on_click="ignore",
    )


def _segment_times(segments: list[dict], index: int) -> str:
    if len(segments) <= index:
        return "—"
    segment = segments[index]
    dep = segment.get("departure_time", "")
    arr = segment.get("arrival_time", "")
    return f"{dep} – {arr}" if dep or arr else "—"


def _segment_duration(segments: list[dict], index: int) -> str:
    if len(segments) <= index:
        return "—"
    return format_duration(segments[index].get("duration_minutes"))


def _format_itinerary_transfers(segments: list[dict]) -> str:
    labels = ["去程", "回程", "第 3 段", "第 4 段"]
    parts = []
    for index, segment in enumerate(segments):
        airports = segment.get("transfer_airports", [])
        text = format_transfers(airports, segment.get("stops", 0))
        if text != "直飛 NONSTOP":
            label = labels[index] if index < len(labels) else f"第 {index + 1} 段"
            parts.append(f"{label}：{text}")
    return "；".join(parts) if parts else "直飛 NONSTOP"


def run_search(
    config,
    requests: list[tuple[tuple[tuple[str, str, str], ...], str]],
    seat: str,
    max_stops: int | None,
    carry_on_bags: int,
    checked_bags: int,
    hide_separate_and_self_transfer: bool,
    exclude_basic_economy: bool,
    use_playwright: bool = True,
) -> tuple[list[dict], list[str]]:
    """逐個日期組合查詢，單一組合失敗不中斷整批。"""
    rows: list[dict] = []
    errors: list[str] = []
    progress = st.progress(0.0, text="🛰 正在連線 Google Flights 並切換價格從低到高排序…")
    for index, (segments, trip_type) in enumerate(requests):
        try:
            rows.extend(
                cached_search(
                    segments,
                    trip_type,
                    seat,
                    config.settings.currency,
                    config.settings.language,
                    max_stops,
                    carry_on_bags,
                    checked_bags,
                    hide_separate_and_self_transfer,
                    exclude_basic_economy,
                    use_playwright,
                )
            )
        except Exception as exc:
            route = " / ".join(f"{origin}→{destination}" for origin, destination, _ in segments)
            if "429" in str(exc):
                st.session_state["google_rate_limit_until"] = (
                    time.monotonic() + GOOGLE_429_COOLDOWN_SECONDS
                )
                errors.append(
                    f"{route}：觸發 Google Flights 頻率限制 (HTTP 429)，已啟動 15 分鐘冷卻保護。"
                )
                break
            errors.append(f"{route}：{exc}")
        progress.progress(
            (index + 1) / len(requests),
            text=f"🛰 掃描進度：{index + 1} / {len(requests)} 組航段",
        )
        if index < len(requests) - 1:
            time.sleep(config.settings.search_delay_seconds)
    progress.empty()
    return rows, errors


def search_tab() -> None:
    config = load_config()
    codes = list(AIRPORTS)
    default_origin = codes.index("FRA") if "FRA" in codes else 0
    default_dest = codes.index("HKG") if "HKG" in codes else 1
    cooldown_seconds = max(
        0,
        int(st.session_state.get("google_rate_limit_until", 0) - time.monotonic()),
    )
    if cooldown_seconds:
        st.warning(
            f"⚠ 系統處於冷卻保護期，約 {max(1, (cooldown_seconds + 59) // 60)} 分鐘後可重新掃描。"
        )

    with st.form("search_form"):
        trip_label = st.radio(
            "航程模式 // ITINERARY MODE",
            ["單程", "往返", "開口行程"],
            horizontal=True,
        )
        col1, col2 = st.columns(2)
        origin = col1.selectbox("出發機場 // ORIGIN", codes, index=default_origin, format_func=airport_label)
        destination = col2.selectbox("目的機場 // DESTINATION", codes, index=default_dest, format_func=airport_label)

        col3, col4 = st.columns(2)
        departure = col3.date_input(
            "去程日期 // DEPARTURE", value=date.today() + timedelta(days=30), min_value=date.today()
        )
        flexible = col4.number_input(
            "彈性日期範圍 ±(天)", min_value=0, max_value=7, value=0, step=1
        )

        return_date = None
        return_origin = destination
        return_destination = origin
        open_jaw_type = ""
        if trip_label == "往返":
            return_date = st.date_input(
                "回程日期 // RETURN", value=departure + timedelta(days=7), min_value=departure
            )
            st.caption(f"往返路徑：{airport_label(destination)} ➔ {airport_label(origin)}")
        elif trip_label == "開口行程":
            return_date = st.date_input(
                "回程日期 // RETURN", value=departure + timedelta(days=7), min_value=departure
            )
            open_jaw_type = st.radio(
                "開口方向 // OPEN-JAW VECTOR",
                ["目的地開口（A→B，C→A）", "出發地開口（A→B，B→C）"],
                horizontal=True,
            )
            other_codes = [code for code in codes if code not in {origin, destination}]
            default_other_code = "TPE" if "TPE" in other_codes else other_codes[0]
            if open_jaw_type.startswith("目的地"):
                return_origin = st.selectbox(
                    "回程出發機場",
                    codes,
                    index=codes.index(default_other_code),
                    format_func=airport_label,
                )
                return_destination = origin
            else:
                return_origin = destination
                return_destination = st.selectbox(
                    "回程抵達機場",
                    codes,
                    index=codes.index(default_other_code),
                    format_func=airport_label,
                )

        col5, col6 = st.columns(2)
        seat = col5.selectbox("艙等 // CABIN CLASS", list(SEAT_LABELS), format_func=lambda s: SEAT_LABELS[s])
        stops_label = col6.radio("轉機限制 // STOPS", list(STOP_CHOICES), horizontal=True)

        with st.expander("⚙ 進階篩選與擷取引擎參數（預設已開啟價格從低到高排序）", expanded=False):
            col7, col8, col9, col10 = st.columns(4)
            carry_on_bags = col7.number_input(
                "隨身行李數",
                min_value=0,
                max_value=2,
                value=config.settings.carry_on_bags,
                help="0 表示不額外疊加行李費，顯示裸票最低價。",
            )
            checked_bags = col8.number_input(
                "託運行李數",
                min_value=0,
                max_value=2,
                value=config.settings.checked_bags,
                help="0 表示不額外疊加行李費，顯示裸票最低價。",
            )
            hide_separate = col9.checkbox(
                "排除分開出票／自行轉機",
                value=config.settings.hide_separate_and_self_transfer,
            )
            exclude_basic = col10.checkbox(
                "排除基本經濟艙",
                value=config.settings.exclude_basic_economy,
            )
            use_playwright = st.checkbox(
                "強制按「價格從低到高」排序並展開所有折疊低價航班（Playwright 引擎）",
                value=True,
                help="預設啟用：模擬德國市場 (gl=DE / EUR) 瀏覽器切換價格排序並展開所有廉航與轉機組合。",
            )

        submitted = st.form_submit_button(
            "🚀 啟動最低價雷達掃描",
            type="primary",
            use_container_width=True,
        )

    if submitted:
        if cooldown_seconds:
            st.warning("冷卻期間暫停送出查詢。")
        elif origin == destination:
            st.warning("出發地與目的地不能相同。")
        elif trip_label != "單程" and return_date <= departure + timedelta(days=flexible):
            st.warning("回程日期必須晚於所有可能的去程日期，請調整日期或縮小彈性範圍。")
        elif trip_label == "開口行程" and return_origin == return_destination:
            st.warning("開口行程的回程出發地與目的地不能相同。")
        elif (
            trip_label == "開口行程"
            and open_jaw_type.startswith("目的地")
            and return_origin == destination
        ):
            st.warning("目的地開口的回程出發城市需與去程目的地不同。")
        elif (
            trip_label == "開口行程"
            and open_jaw_type.startswith("出發地")
            and return_destination == origin
        ):
            st.warning("出發地開口的回程抵達城市需與去程出發地不同。")
        else:
            dates = [departure + timedelta(days=offset) for offset in range(-flexible, flexible + 1)]
            trip_type = {"單程": "one-way", "往返": "round-trip", "開口行程": "multi-city"}[trip_label]
            requests = []
            for outbound_date in dates:
                segments = [(origin, destination, outbound_date.isoformat())]
                if trip_label != "單程":
                    segments.append(
                        (return_origin, return_destination, return_date.isoformat())
                    )
                requests.append((tuple(segments), trip_type))

            rows, errors = run_search(
                config,
                requests,
                seat,
                STOP_CHOICES[stops_label],
                int(carry_on_bags),
                int(checked_bags),
                hide_separate,
                exclude_basic,
                use_playwright,
            )
            st.session_state["search_result"] = {
                "trip_label": trip_label,
                "segments": requests[0][0],
                "rows": rows,
                "errors": errors,
                "dates": [d.isoformat() for d in dates],
            }

    result = st.session_state.get("search_result")
    if not result:
        st.caption("💡 提示：系統預設使用德國市場 (gl=DE) 與 EUR 計價，並自動切換 Google Flights 價格從低到高排序。")
        return

    for message in result["errors"]:
        st.warning(f"查詢異常：{message}")
    if not result["rows"]:
        st.error("本次掃描未取得任何有效票價，請稍後重試。")
        return

    df = mark_deals(result["rows"])
    if len(result["dates"]) > 1:
        st.subheader("📊 彈性日期最低票價分布")
        daily = df.groupby("departure_date")["price"].min().sort_index()
        st.bar_chart(daily, y_label=f"最低票價 ({config.settings.currency})", color="#38bdf8")

    route_title = " / ".join(
        f"{airport_label(origin)} ➔ {airport_label(destination)}"
        for origin, destination, _ in result["segments"]
    )
    st.subheader(f"🛫 {result['trip_label']}報價矩陣：{route_title}")
    render_results(df, config.settings.currency)


def watchlist_tab() -> None:
    route_manager()
    st.divider()

    snapshot = load_snapshot()
    if snapshot is None:
        st.info("尚未產生排程快照。設定好航線後，等待 GitHub Actions 執行即可顯示。")
        return

    if snapshot.get("is_sample"):
        st.warning("目前顯示的是示範資料。")

    summary = snapshot.get("summary", {})
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("監控航線數", summary.get("routes_checked", 0))
    col2.metric("已收集報價", summary.get("offers", 0))
    col3.metric("偵測到特價", summary.get("deals", 0))
    col4.metric("最後同步 (UTC)", str(snapshot.get("generated_at", "-"))[:16].replace("T", " "))

    offers_df = pd.DataFrame(snapshot.get("offers", []))
    if offers_df.empty:
        st.info("本次排程尚未取得有效票價。")
        return

    failures = summary.get("failures", [])
    if failures:
        with st.expander(f"⚠ 查詢失敗紀錄 ({len(failures)} 筆)"):
            st.dataframe(pd.DataFrame(failures), use_container_width=True)

    currency = snapshot.get("currency", "EUR")
    offers_df["航線"] = offers_df["from_airport"] + " ➔ " + offers_df["to_airport"]
    offers_df["航空公司"] = offers_df["airlines"].apply(
        lambda v: "、".join(v) if isinstance(v, list) and v else (v or "未標示")
    )
    offers_df["轉機節點"] = [
        format_transfers(value, stops)
        for value, stops in zip(
            offers_df.get("transfer_airports", pd.Series(index=offers_df.index, dtype=object)),
            offers_df["stops"],
        )
    ]
    offers_df["總時長"] = offers_df["duration_minutes"].apply(format_duration)

    routes = sorted(offers_df["航線"].unique())
    stops_upper = max(int(offers_df["stops"].max()), 1)
    price_upper = max(int(offers_df["price"].max()), 100)
    with st.expander("🔍 監控清單篩選器", expanded=False):
        col_a, col_b = st.columns(2)
        picked = col_a.multiselect("篩選航線", routes, default=routes)
        max_stops = col_b.slider("最多轉機次數", 0, stops_upper, int(offers_df["stops"].max()))
        col_c, col_d = st.columns(2)
        price_cap = col_c.slider("票價上限 (EUR)", 0, price_upper, price_upper, step=50)
        only_deals = col_d.checkbox("僅顯示命中特價", value=False)

    view = offers_df[offers_df["航線"].isin(picked) & (offers_df["price"] <= price_cap)]
    view = view[view["stops"] <= max_stops]
    if only_deals:
        view = view[view["is_deal"]]

    columns = {
        "航線": "航線",
        "departure_date": "出發日",
        "price": f"票價 ({currency})",
        "baseline_median": f"歷史中位數 ({currency})",
        "航空公司": "航空公司",
        "stops": "轉機",
        "轉機節點": "轉機節點",
        "總時長": "總時長",
        "departure_time": "起飛",
        "arrival_time": "抵達",
        "is_deal": "特價",
    }

    st.subheader("🔥 命中特價機票")
    deals = view[view["is_deal"]].sort_values("price")
    if deals.empty:
        st.write("目前沒有符合條件的特價紀錄。")
    else:
        st.dataframe(deals[list(columns)].rename(columns=columns), use_container_width=True, hide_index=True)

    st.subheader("📋 完整監控報價表")
    st.dataframe(
        view.sort_values(["航線", "departure_date", "price"])[list(columns)].rename(columns=columns),
        use_container_width=True,
        hide_index=True,
    )


def trend_tab() -> None:
    history = load_history()
    if history.empty:
        st.info("尚無歷史觀測數據，累積數次排程後將自動繪製價格走勢圖。")
        return
    if "currency" not in history.columns:
        st.info("歷史資料格式較舊，下次排程更新後將開始記錄 EUR 走勢。")
        return
    history = history[history["currency"].astype(str).str.upper() == "EUR"].copy()
    if history.empty:
        st.info("尚無 EUR 歷史數據。")
        return

    history["航線"] = history["from_airport"] + " ➔ " + history["to_airport"]
    history["price"] = pd.to_numeric(history["price"], errors="coerce")
    history = history.dropna(subset=["price"])
    history["observed_at"] = pd.to_datetime(history["observed_at"], errors="coerce", utc=True)

    col_a, col_b = st.columns(2)
    chart_route = col_a.selectbox("選擇觀測航線", sorted(history["航線"].unique()))
    dates = sorted(history[history["航線"] == chart_route]["departure_date"].unique())
    chart_date = col_b.selectbox("選擇出發日期", dates)

    series = history[(history["航線"] == chart_route) & (history["departure_date"] == chart_date)]
    if series.empty:
        st.write("此日期組合尚無足夠數據。")
        return

    lowest = series.groupby("observed_at")["price"].min().sort_index()
    st.line_chart(lowest, y_label="歷史最低票價 (EUR)", color="#34d399")


def main() -> None:
    inject_cyber_theme()

    st.markdown(
        """
        <div class="aero-banner">
            <div class="aero-badge">SYSTEM ONLINE // MARKET: DE (EUR) // SORT: LOWEST PRICE FIRST</div>
            <h1 class="aero-title">AERO-RADAR 特價機票監控中樞</h1>
            <div class="aero-subtitle">REAL-TIME GOOGLE FLIGHTS PRICE SCANNER &amp; AUTOMATED DEAL TELEMETRY</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    tab_search, tab_watch, tab_trend = st.tabs([
        "⚡ 即時最低價搜尋",
        "🛰 航線監控清單",
        "📈 歷史價格走勢",
    ])
    with tab_search:
        search_tab()
    with tab_watch:
        watchlist_tab()
    with tab_trend:
        trend_tab()


if __name__ == "__main__":
    main()
