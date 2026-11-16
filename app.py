"""AERO-RADAR // 高效專業機票價格雷達與雙價格矩陣 (app.py)"""
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
    page_title="FLIGHT DEALS RADAR // 專業機票價格雷達",
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
    "economy": "經濟艙 (Economy)",
    "premium-economy": "豪華經濟艙 (Premium)",
    "business": "商務艙 (Business)",
    "first": "頭等艙 (First)",
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


def inject_clean_theme() -> None:
    """注入乾淨、專業、高對比的現代科技風 CSS。"""
    st.markdown(
        """
        <style>
        .stApp {
            background-color: #0b0f19;
            color: #f3f4f6;
            font-family: -apple-system, BlinkMacSystemFont, sans-serif;
        }
        .main-banner {
            background: linear-gradient(135deg, #1e293b 0%, #0f172a 100%);
            border: 1px solid #334155;
            border-radius: 8px;
            padding: 1.2rem 1.5rem;
            margin-bottom: 1.2rem;
        }
        .flight-row-card {
            background: #111827;
            border: 1px solid #374151;
            border-radius: 8px;
            padding: 1rem 1.25rem;
            margin-bottom: 0.75rem;
            box-shadow: 0 2px 8px rgba(0,0,0,0.3);
        }
        .flight-row-card:hover {
            border-color: #0ea5e9;
        }
        .price-direct {
            font-size: 1.4rem;
            font-weight: 700;
            color: #38bdf8;
        }
        .price-ota {
            font-size: 1.1rem;
            font-weight: 600;
            color: #34d399;
        }
        .badge-deal {
            background: rgba(239, 68, 68, 0.2);
            border: 1px solid #ef4444;
            color: #fca5a5;
            padding: 0.1rem 0.5rem;
            border-radius: 4px;
            font-size: 0.75rem;
            font-weight: 700;
        }
        .badge-self-transfer {
            background: rgba(245, 158, 11, 0.2);
            border: 1px solid #f59e0b;
            color: #fcd34d;
            padding: 0.1rem 0.5rem;
            border-radius: 4px;
            font-size: 0.75rem;
            font-weight: 600;
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
    if not df.empty and df["is_deal"].sum() == 0:
        df = df.sort_values("price").reset_index(drop=True)
        cutoff = max(1, int(len(df) * 0.15))
        df.loc[:cutoff - 1, "is_deal"] = True
    return df


def format_duration(minutes) -> str:
    try:
        val = float(minutes)
        return f"{int(val // 60)}h {int(val % 60)}m" if val > 0 else "—"
    except (TypeError, ValueError):
        return "—"


def clean_airline_name(raw_airlines) -> str:
    if isinstance(raw_airlines, list):
        name = "、".join(raw_airlines)
    else:
        name = str(raw_airlines or "未知航空")
    if "Durchgeführt von" in name:
        name = name.split("Durchgeführt von")[0].strip()
    if "operated by" in name.lower():
        name = name.split("operated by")[0].strip()
    return name.strip(" ,·-")


def render_professional_dashboard(df: pd.DataFrame, currency: str, key_prefix: str = "search") -> None:
    """專業資訊豐富的航班雷達儀表板（雙價格 + 自行轉機 + 完整時間資訊）。"""
    if df.empty:
        st.info("未偵測到符合條件的航班。")
        return

    df["price"] = pd.to_numeric(df["price"], errors="coerce")
    if "ota_price" in df.columns:
        df["ota_price"] = pd.to_numeric(df["ota_price"], errors="coerce")
    
    view = df.sort_values(["price", "duration_minutes"], ascending=[True, True]).reset_index(drop=True)
    cheapest = view.iloc[0]
    direct = view[view["stops"] == 0]
    deals_df = view[view["is_deal"] == True]

    # 頂部指標
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("最低直銷票價", f"€ {int(cheapest['price']):,}")
    col2.metric("最低代理商 (OTA)", f"€ {int(view['ota_price'].dropna().min()):,}" if not view['ota_price'].dropna().empty else "無資料")
    col3.metric("直飛最低價", f"€ {int(direct['price'].min()):,}" if not direct.empty else "無直飛")
    col4.metric("精選特價組合", f"{len(deals_df)} 筆")

    st.markdown("---")

    # 篩選工具列
    col_f1, col_f2, col_f3 = st.columns(3)
    only_deals = col_f1.checkbox("🔥 僅顯示特價推薦", value=False, key=f"{key_prefix}_deals")
    only_direct = col_f2.checkbox("✈️ 僅顯示直飛航線", value=False, key=f"{key_prefix}_direct")
    max_budget = col_f3.slider("最高預算 (EUR)", int(view["price"].min()), int(view["price"].max()), int(view["price"].max()), step=50, key=f"{key_prefix}_budget")

    filtered = view[view["price"] <= max_budget]
    if only_deals:
        filtered = filtered[filtered["is_deal"] == True]
    if only_direct:
        filtered = filtered[filtered["stops"] == 0]

    st.markdown(f"### 📋 完整報價清單（共 {len(filtered)} 筆結果，已按價格從低到高排序）")

    for index, row in filtered.iterrows():
        price = int(row["price"])
        ota_price = row.get("ota_price")
        airlines = clean_airline_name(row.get("airlines"))
        stops = int(row.get("stops", 0))
        dep_date = row.get("departure_date", "")
        segments = row.get("segments", [])
        
        dep_time = segments[0].get("departure_time", "") if segments else ""
        arr_time = segments[-1].get("arrival_time", "") if segments else ""
        duration = format_duration(row.get("duration_minutes", 0))
        
        # 檢查自行轉機
        has_self_transfer = any(
            "self-transfer" in str(seg).lower() or "eigener" in str(seg).lower()
            for seg in segments
        )
        
        transfers_list = [
            airport for seg in segments for airport in seg.get("transfer_airports", [])
        ]
        transfer_desc = "直飛 (Nonstop)" if stops == 0 else f"{stops} 站 ({'、'.join(transfers_list)})"
        
        deal_badge = '<span class="badge-deal">🔥 特價 DEAL</span>' if row.get("is_deal") else ''
        transfer_badge = '<span class="badge-self-transfer">🔄 自行轉機 (Eigener Transfer)</span>' if has_self_transfer else ''

        # 雙價格顯示邏輯
        price_html = f'<span class="price-direct">€ {price:,}</span> <span style="font-size:0.8rem; color:#94a3b8;">(官網直銷)</span>'
        if pd.notna(ota_price) and ota_price > 0 and int(ota_price) < price:
            price_html += f'&nbsp;&nbsp;|&nbsp;&nbsp;<span class="price-ota">€ {int(ota_price):,}</span> <span style="font-size:0.8rem; color:#34d399;">(OTA 代理商)</span>'

        st.markdown(
            f"""
            <div class="flight-row-card">
                <div style="display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 0.5rem;">
                    <div>
                        <span style="font-size: 0.8rem; color: #94a3b8; font-weight: 600;">#{index+1}</span>
                        &nbsp;{deal_badge}&nbsp;{transfer_badge}
                        <div style="font-size: 1.05rem; font-weight: 700; color: #f8fafc; margin-top: 0.2rem;">{airlines}</div>
                    </div>
                    <div style="text-align: right;">
                        <div>{price_html}</div>
                    </div>
                </div>
                <div style="display: flex; justify-content: space-between; font-size: 0.88rem; color: #cbd5e1; border-top: 1px solid #1e293b; padding-top: 0.5rem; margin-top: 0.5rem;">
                    <span><b>航線：</b> {row['from_airport']} ➔ {row['to_airport']} &nbsp;|&nbsp; <b>出發日：</b> {dep_date}</span>
                    <span><b>時段：</b> {dep_time} – {arr_time} ({duration}) &nbsp;|&nbsp; <b>轉機：</b> {transfer_desc}</span>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    # 下載按鈕
    csv_data = view.to_csv(index=False).encode("utf-8-sig")
    st.download_button(
        "⬇ 下載完整報價矩陣 CSV",
        data=csv_data,
        file_name=f"flight_deals_{cheapest['from_airport']}-{cheapest['to_airport']}.csv",
        mime="text/csv",
        key=f"{key_prefix}_download",
    )


def search_tab() -> None:
    config = load_config()
    codes = list(AIRPORTS)
    default_origin = codes.index("FRA") if "FRA" in codes else 0
    default_dest = codes.index("HKG") if "HKG" in codes else 1

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
        departure = col3.date_input("出發日期 // DEPARTURE", value=date.today() + timedelta(days=30), min_value=date.today())
        flexible = col4.number_input("彈性日期範圍 ±(天)", min_value=0, max_value=7, value=0, step=1)

        return_date = None
        if trip_label in ("往返", "開口行程"):
            return_date = col3.date_input("回程日期 // RETURN", value=departure + timedelta(days=7), min_value=departure)

        col5, col6 = st.columns(2)
        seat = col5.selectbox("艙等 // CABIN CLASS", list(SEAT_LABELS), format_func=lambda s: SEAT_LABELS[s])
        stops_label = col6.radio("轉機限制 // STOPS", list(STOP_CHOICES), horizontal=True)

        with st.expander("⚙ 進階參數（預設解鎖價格排序與自行轉機）", expanded=False):
            col7, col8 = st.columns(2)
            carry_on_bags = col7.number_input("隨身行李數", min_value=0, max_value=2, value=0)
            checked_bags = col8.number_input("託運行李數", min_value=0, max_value=2, value=0)

        submitted = st.form_submit_button("🚀 啟動即時航班價格雷達", type="primary", use_container_width=True)

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
            progress = st.progress(0.0, text="🛰 正在透過 Playwright 抓取 Google Flights 最便宜分頁與雙價格…")
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
                    st.warning(f"查詢異常：{exc}")
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
        st.info("👈 請在上方設定航線與日期，點擊「啟動即時航班價格雷達」開始查詢。")
        return

    if not result["rows"]:
        st.error("本次查詢未取得任何有效票價，請稍後重試。")
        return

    df = mark_deals(result["rows"])
    render_professional_dashboard(df, config.settings.currency, key_prefix="search")


def watchlist_tab() -> None:
    snapshot = load_snapshot()
    if snapshot is None:
        st.info("尚未同步排程快照。")
        return
    offers_df = pd.DataFrame(snapshot.get("offers", []))
    if offers_df.empty:
        st.info("尚無監控資料。")
        return
    currency = snapshot.get("currency", "EUR")
    render_professional_dashboard(offers_df, currency, key_prefix="watch")


def trend_tab() -> None:
    history = load_history()
    if history.empty:
        st.info("尚無歷史數據。")
        return
    history["price"] = pd.to_numeric(history["price"], errors="coerce")
    history = history.dropna(subset=["price"])
    st.line_chart(history.groupby("observed_at")["price"].min(), y_label="歷史最低票價 (EUR)", color="#38bdf8")


def main() -> None:
    inject_clean_theme()
    st.markdown(
        """
        <div class="main-banner">
            <div style="font-family: monospace; font-size: 0.75rem; color: #38bdf8; font-weight: 700; margin-bottom: 0.2rem;">SYSTEM ONLINE // MARKET: DE (EUR) // DUAL-PRICE &amp; SELF-TRANSFER RADAR</div>
            <h1 style="font-size: 1.75rem; font-weight: 700; color: #f8fafc; margin: 0;">國際航班價格雷達與雙價格分析儀</h1>
            <div style="font-size: 0.82rem; color: #94a3b8; margin-top: 0.2rem;">Real-Time Google Flights Price Telemetry with Direct vs. OTA Pricing &amp; Self-Transfer Support</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    tab_search, tab_watch, tab_trend = st.tabs([
        "⚡ 即時航班搜尋",
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
