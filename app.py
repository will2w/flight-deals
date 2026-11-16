"""AERO-RADAR // 魔獸世界風格特價機票雷達（智能保底特價與極致精簡版 app.py）"""
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
    page_title="AZEROTH FLIGHT RADAR // 艾澤拉斯特價機票雷達",
    page_icon="🛡️",
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
    "economy": "普通席位 // 經濟艙",
    "premium-economy": "精良席位 // 豪華經濟",
    "business": "史詩席位 // 商務艙",
    "first": "傳說席位 // 頭等艙",
}

STOP_CHOICES = {"不限航段": None, "只要直飛 (Nonstop)": 0, "最多轉 1 次": 1}

AIRPORTS: dict[str, str] = {
    "TPE": "台北 桃園", "TSA": "台北 松山", "KHH": "高雄", "RMQ": "台中",
    "NRT": "東京 成田", "HND": "東京 羽田", "KIX": "大阪 關西", "ICN": "首爾 仁川",
    "HKG": "香港", "PVG": "上海 浦東", "PEK": "北京 首都", "CAN": "廣州", "SZX": "深圳",
    "BKK": "曼谷 蘇凡納布", "SIN": "新加坡", "KUL": "吉隆坡", "MNL": "馬尼拉",
    "DXB": "杜拜", "DOH": "杜哈", "IST": "伊斯坦堡",
    "DUS": "杜塞道夫", "FRA": "法蘭克福", "MUC": "慕尼黑", "BER": "柏林",
    "AMS": "阿姆斯特丹", "CDG": "巴黎 戴高樂", "LHR": "倫敦 希斯洛",
    "FCO": "羅馬", "MXP": "米蘭", "PRG": "布拉格", "VIE": "維也納",
    "LAX": "洛杉磯", "SFO": "舊金山", "JFK": "紐約 甘迺迪", "YVR": "溫哥華",
}


def inject_wow_theme() -> None:
    st.markdown(
        """
        <style>
        @import url('https://fonts.googleapis.com/css2?family=Cinzel+Decorative:wght@700&family=JetBrains+Mono:wght@400;600;700&family=Inter:wght@400;500;600;700&display=swap');

        .stApp {
            background: radial-gradient(circle at 50% 0%, #11141d 0%, #080a0f 60%, #030407 100%);
            color: #d1d5db;
            font-family: 'Inter', sans-serif;
        }

        .wow-banner {
            background: linear-gradient(135deg, rgba(30, 41, 59, 0.85) 0%, rgba(15, 23, 42, 0.95) 100%);
            border: 2px solid #78350f;
            border-image: linear-gradient(to right, #b45309, #f59e0b, #b45309) 1;
            border-radius: 6px;
            padding: 1.2rem 1.6rem;
            margin-bottom: 1.2rem;
            box-shadow: 0 0 30px rgba(217, 119, 6, 0.15);
        }
        .wow-badge {
            display: inline-block;
            font-family: 'Cinzel Decorative', serif;
            font-size: 0.72rem;
            font-weight: 700;
            letter-spacing: 0.12em;
            color: #fbbf24;
            background: rgba(180, 83, 9, 0.2);
            border: 1px solid #d97706;
            padding: 0.15rem 0.6rem;
            border-radius: 4px;
            margin-bottom: 0.3rem;
        }
        .wow-title {
            font-family: 'Cinzel Decorative', serif;
            font-size: 1.8rem;
            font-weight: 700;
            color: #fde68a;
            margin: 0;
        }
        .wow-subtitle {
            font-size: 0.8rem;
            color: #9ca3af;
            margin-top: 0.3rem;
            font-family: 'JetBrains Mono', monospace;
        }

        /* 卡片美化：更緊湊、資訊一目了然 */
        .wow-flight-card {
            background: linear-gradient(145deg, #131824 0%, #0b0f17 100%);
            border: 1px solid #374151;
            border-radius: 8px;
            padding: 0.9rem 1.1rem;
            margin-bottom: 0.75rem;
            box-shadow: 0 4px 12px rgba(0, 0, 0, 0.4);
            transition: all 0.2s ease;
        }
        .wow-flight-card:hover {
            border-color: #d97706;
            box-shadow: 0 0 16px rgba(217, 119, 6, 0.2);
        }
        .wow-card-epic {
            border: 1px solid #a335ee !important;
            border-left: 5px solid #a335ee !important;
            background: linear-gradient(145deg, #1d142c 0%, #0b0f17 100%) !important;
        }
        .wow-card-rare {
            border-left: 5px solid #0070dd !important;
        }

        .wow-price-tag {
            font-family: 'JetBrains Mono', monospace;
            font-size: 1.35rem;
            font-weight: 700;
            color: #fbbf24;
        }
        .wow-ota-tag {
            font-family: 'JetBrains Mono', monospace;
            font-size: 0.75rem;
            color: #38bdf8;
        }
        .wow-airline {
            font-weight: 600;
            color: #f3f4f6;
            font-size: 0.95rem;
        }
        .wow-meta {
            font-family: 'JetBrains Mono', monospace;
            font-size: 0.78rem;
            color: #9ca3af;
        }

        [data-testid="stForm"], [data-testid="stExpander"] {
            background: rgba(15, 23, 42, 0.85) !important;
            border: 1px solid #4b5563 !important;
            border-radius: 8px !important;
        }
        [data-testid="stMetric"] {
            background: linear-gradient(160deg, #111827 0%, #0b0f17 100%);
            border: 1px solid #b45309;
            border-radius: 6px;
            padding: 0.7rem 0.9rem;
        }
        [data-testid="stMetricLabel"] {
            color: #9ca3af !important;
            font-family: 'Cinzel Decorative', serif;
            font-size: 0.7rem !important;
        }
        [data-testid="stMetricValue"] {
            font-family: 'JetBrains Mono', monospace !important;
            color: #fbbf24 !important;
            font-weight: 700 !important;
        }
        button[kind="primaryFormSubmit"], button[kind="primary"] {
            background: linear-gradient(90deg, #9a3412 0%, #b45309 50%, #d97706 100%) !important;
            border: 1px solid #f59e0b !important;
            color: #ffffff !important;
            font-family: 'Cinzel Decorative', serif !important;
            font-weight: 700 !important;
            border-radius: 6px !important;
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

    df = pd.DataFrame(rows)
    if not df.empty:
        # 【智慧保底特價機制】：若歷史資料不足導致 0 筆特價，自動將價格排名前 15% 的超低價航班升格為特價！
        if df["is_deal"].sum() == 0:
            df = df.sort_values("price").reset_index(drop=True)
            cutoff_idx = max(1, int(len(df) * 0.15))
            df.loc[:cutoff_idx - 1, "is_deal"] = True
            
    return df


def format_duration(minutes) -> str:
    try:
        val = float(minutes)
        return f"{int(val // 60)}h {int(val % 60)}m" if val > 0 else "—"
    except (TypeError, ValueError):
        return "—"


def clean_airline_name(raw_airlines) -> str:
    """清理航司名稱，過濾冗長的德文代理說明如 'Durchgeführt von...'"""
    if isinstance(raw_airlines, list):
        name = "、".join(raw_airlines)
    else:
        name = str(raw_airlines or "未知航空")
    
    if "Durchgeführt von" in name:
        name = name.split("Durchgeführt von")[0].strip()
    if "operated by" in name.lower():
        name = name.split("operated by")[0].strip()
    return name.strip(" ,·-")


def render_results_grid(df: pd.DataFrame, currency: str, key_prefix: str = "search") -> None:
    if df.empty:
        st.info("未偵測到符合條件的航班，請嘗試放寬條件。")
        return

    df["price"] = pd.to_numeric(df["price"], errors="coerce")
    if "ota_price" in df.columns:
        df["ota_price"] = pd.to_numeric(df["ota_price"], errors="coerce")
    
    view = df.sort_values(["price", "duration_minutes"], ascending=[True, True]).reset_index(drop=True)
    cheapest = view.iloc[0]
    direct = view[view["stops"] == 0]
    deals_df = view[view["is_deal"] == True]

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("最低直銷金幣", f"€ {int(cheapest['price']):,}")
    col2.metric("直飛首選底價", f"€ {int(direct['price'].min()):,}" if not direct.empty else "無直飛航線")
    col3.metric("掉落有效組合", f"{len(view)} 筆")
    col4.metric("史詩特價命中", f"{len(deals_df)} 筆")

    st.markdown("---")

    tab_deals, tab_all = st.tabs([
        f"🔥 史詩特價專區 ({len(deals_df)} 筆)",
        f"📋 全部可用戰利品 ({len(view)} 筆)"
    ])

    with tab_deals:
        if deals_df.empty:
            st.info("⚔️ 目前無特價紀錄。")
        else:
            st.markdown("### 🌟 全場精選最高 CP 值與超低價特價機票")
            for i, row in deals_df.reset_index(drop=True).iterrows():
                _render_wow_card(row, currency, i + 1, force_epic=True)

    with tab_all:
        col_f1, col_f2 = st.columns(2)
        only_direct = col_f1.checkbox("僅顯示直飛航線 (Nonstop)", value=False, key=f"{key_prefix}_direct")
        min_p = int(view["price"].min())
        max_p = int(view["price"].max())
        max_budget = col_f2.slider("最高金幣預算 (EUR)", min_p, max_p, max_p, step=50, key=f"{key_prefix}_budget")

        filtered_view = view[(view["price"] <= max_budget)]
        if only_direct:
            filtered_view = filtered_view[filtered_view["stops"] == 0]

        st.markdown(f"### ⚔️ 排序清單（共 {len(filtered_view)} 筆符合條件）")
        
        for i in range(0, len(filtered_view), 2):
            c_left, c_right = st.columns(2)
            with c_left:
                if i < len(filtered_view):
                    _render_wow_card(filtered_view.iloc[i], currency, i + 1)
            with c_right:
                if i + 1 < len(filtered_view):
                    _render_wow_card(filtered_view.iloc[i + 1], currency, i + 2)

    csv_data = view.to_csv(index=False).encode("utf-8-sig")
    st.download_button(
        "⬇ 下載完整戰利品矩陣 CSV",
        data=csv_data,
        file_name=f"azeroth-radar_{cheapest['from_airport']}-{cheapest['to_airport']}.csv",
        mime="text/csv",
        key=f"{key_prefix}_download",
    )


def _render_wow_card(row, currency: str, rank: int, force_epic: bool = False) -> None:
    is_deal_item = force_epic or bool(row.get("is_deal", False))
    stops = int(row.get("stops", 0))
    
    card_class = "wow-flight-card"
    quality_badge = "🟢 精良"
    if is_deal_item:
        card_class += " wow-card-epic"
        quality_badge = "🟣 史詩特價 (EPIC DEAL)"
    elif stops == 0:
        card_class += " wow-card-rare"
        quality_badge = "🔵 稀有 (直飛)"

    price = int(row["price"])
    ota_price = row.get("ota_price")
    
    price_str = f"€ {price:,}"
    if pd.notna(ota_price) and ota_price > 0 and int(ota_price) < price:
        price_str += f" <span class='wow-ota-tag'>(OTA: € {int(ota_price):,})</span>"

    airlines = clean_airline_name(row.get("airlines"))
    
    segments = row.get("segments", [])
    has_self_transfer = any(
        "self-transfer" in str(seg).lower() or "eigener" in str(seg).lower()
        for seg in segments
    )
    transfer_text = "直飛 NONSTOP"
    if stops > 0:
        transfers_list = [
            airport for seg in segments for airport in seg.get("transfer_airports", [])
        ]
        transfer_text = f"{stops} 站 ({'、'.join(transfers_list)})" if transfers_list else f"{stops} 站"
        if has_self_transfer:
            transfer_text += " 🔄 自行轉機"

    dep_date = row.get("departure_date", "")
    dep_time = segments[0].get("departure_time", "") if segments else ""
    arr_time = segments[-1].get("arrival_time", "") if segments else ""
    duration = format_duration(row.get("duration_minutes", 0))

    st.markdown(
        f"""
        <div class="{card_class}">
            <div style="display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 0.3rem;">
                <div>
                    <span style="font-size: 0.7rem; font-weight: 700; color: #fbbf24; font-family: 'Cinzel Decorative', serif;">#{rank} | {quality_badge}</span>
                    <div class="wow-airline">{airlines}</div>
                </div>
                <div style="text-align: right;">
                    <div class="wow-price-tag">{price_str}</div>
                </div>
            </div>
            <div style="font-size: 0.85rem; color: #38bdf8; font-weight: 600; margin-bottom: 0.3rem;">
                {row['from_airport']} ➔ {row['to_airport']} &nbsp;|&nbsp; 出發日：{dep_date}
            </div>
            <div class="wow-meta" style="display: flex; justify-content: space-between; border-top: 1px solid rgba(255,255,255,0.08); padding-top: 0.3rem; margin-top: 0.3rem;">
                <span>⏳ 時段：{dep_time} – {arr_time} ({duration})</span>
                <span>🛡️ 轉機：{transfer_text}</span>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def search_tab() -> None:
    config = load_config()
    codes = list(AIRPORTS)
    default_origin = codes.index("FRA") if "FRA" in codes else 0
    default_dest = codes.index("HKG") if "HKG" in codes else 1

    with st.form("search_form"):
        trip_label = st.radio(
            "遠征模式 // ITINERARY MODE",
            ["單程", "往返", "開口行程"],
            horizontal=True,
        )
        col1, col2 = st.columns(2)
        origin = col1.selectbox("出發要塞 // ORIGIN", codes, index=default_origin, format_func=airport_label)
        destination = col2.selectbox("目標領地 // DESTINATION", codes, index=default_dest, format_func=airport_label)

        col3, col4 = st.columns(2)
        departure = col3.date_input("出發日期 // DEPARTURE", value=date.today() + timedelta(days=30), min_value=date.today())
        flexible = col4.number_input("彈性日期範圍 ±(天)", min_value=0, max_value=7, value=0, step=1)

        return_date = None
        if trip_label in ("往返", "開口行程"):
            return_date = col3.date_input("回程日期 // RETURN", value=departure + timedelta(days=7), min_value=departure)

        col5, col6 = st.columns(2)
        seat = col5.selectbox("席位階級 // CABIN CLASS", list(SEAT_LABELS), format_func=lambda s: SEAT_LABELS[s])
        stops_label = col6.radio("轉機限制 // STOPS", list(STOP_CHOICES), horizontal=True)

        with st.expander("⚙ 瀏覽器深度引擎參數（預設鎖定價格升冪排序與自行轉機）", expanded=False):
            col7, col8 = st.columns(2)
            carry_on_bags = col7.number_input("隨身行李數", min_value=0, max_value=2, value=0)
            checked_bags = col8.number_input("託運行李數", min_value=0, max_value=2, value=0)

        submitted = st.form_submit_button("⚔️ 啟動艾澤拉斯最低價雷達", type="primary", use_container_width=True)

    if submitted:
        if origin == destination:
            st.warning("出發地與目的地不能相同。")
        else:
            dates = [departure + timedelta(days=offset) for offset in range(-flexible, flexible + 1)]
            trip_type = {"單程": "one-way", "往返": "round-trip", "開口行程": "multi-city"}[trip_label]
            requests = []
            for outbound_date in dates:
                segments = [(origin, destination, outbound_date.isoformat())]
                if trip_label != "單程" and return_date:
                    segments.append((destination, origin, return_date.isoformat()))
                requests.append((tuple(segments), trip_type))

            rows: list[dict] = []
            progress = st.progress(0.0, text="🛡️ 正在召喚 Playwright 瀏覽器精靈，解鎖 Google Flights 最便宜分頁…")
            for index, (segments, t_type) in enumerate(requests):
                try:
                    rows.extend(
                        cached_search(
                            segments,
                            t_type,
                            seat,
                            config.settings.currency,
                            config.settings.language,
                            STOP_CHOICES[stops_label],
                            int(carry_on_bags),
                            int(checked_bags),
                            False,
                            False,
                            True,
                        )
                    )
                except Exception as exc:
                    st.warning(f"航段查詢異常：{exc}")
                progress.progress((index + 1) / len(requests))
            progress.empty()

            st.session_state["search_result"] = {
                "trip_label": trip_label,
                "segments": requests[0][0],
                "rows": rows,
                "dates": [d.isoformat() for d in dates],
            }

    result = st.session_state.get("search_result")
    if not result:
        st.caption("💡 提示：系統已鎖定絕對價格升冪排序，並自動為您挑選全場最高 CP 值特價組合。")
        return

    if not result["rows"]:
        st.error("本次遠征未取得任何有效戰利品，請稍後重試。")
        return

    df = mark_deals(result["rows"])
    render_results_grid(df, config.settings.currency, key_prefix="search")


def watchlist_tab() -> None:
    snapshot = load_snapshot()
    if snapshot is None:
        st.info("尚未同步遠征快照。")
        return
    offers_df = pd.DataFrame(snapshot.get("offers", []))
    if offers_df.empty:
        st.info("尚無監控戰利品。")
        return
    currency = snapshot.get("currency", "EUR")
    render_results_grid(offers_df, currency, key_prefix="watch")


def trend_tab() -> None:
    history = load_history()
    if history.empty:
        st.info("尚無歷史數據。")
        return
    history["price"] = pd.to_numeric(history["price"], errors="coerce")
    history = history.dropna(subset=["price"])
    st.line_chart(history.groupby("observed_at")["price"].min(), y_label="歷史最低金幣 (EUR)", color="#fbbf24")


def main() -> None:
    inject_wow_theme()
    st.markdown(
        """
        <div class="wow-banner">
            <div class="wow-badge">AZEROTH FLIGHT RAID // MARKET: DE (EUR) // EPIC DEAL FINDER</div>
            <h1 class="wow-title">艾澤拉斯特價機票監控中樞</h1>
            <div class="wow-subtitle">WORLD OF WARCRAFT THEMED GOOGLE FLIGHTS TELEMETRY &amp; EPIC DEAL FINDER</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    tab_search, tab_watch, tab_trend = st.tabs([
        "⚡ 實時戰利品搜尋",
        "🛡️ 遠征航線雷達",
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
