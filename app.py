"""AZEROTH FLIGHT RADAR // 燃燒遠征 (The Burning Crusade) 風格專業機票雷達 (app.py)"""
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
    page_title="TBC FLIGHT RADAR // 燃燒遠征機票雷達",
    page_icon="🟢",
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


def inject_tbc_theme() -> None:
    """注入魔獸世界：燃燒遠征 (TBC) 黑暗神廟深空紫、外域邪能綠與傳奇金邊框 CSS 樣式。"""
    st.markdown(
        """
        <style>
        @import url('https://fonts.googleapis.com/css2?family=Cinzel:wght@600;700;900&family=JetBrains+Mono:wght@400;600;700&family=Inter:wght@400;500;600;700&display=swap');

        .stApp {
            background: radial-gradient(circle at 50% 0%, #151026 0%, #0a0712 60%, #040308 100%);
            color: #e2e8f0;
            font-family: 'Inter', sans-serif;
        }

        /* TBC 燃燒遠征風格頂部橫幅 */
        .tbc-banner {
            background: linear-gradient(135deg, rgba(26, 15, 46, 0.9) 0%, rgba(11, 7, 20, 0.95) 100%);
            border: 2px solid #10b981;
            border-image: linear-gradient(to right, #059669, #10b981, #34d399) 1;
            border-radius: 8px;
            padding: 1.4rem 1.8rem;
            margin-bottom: 1.5rem;
            box-shadow: 0 0 35px rgba(16, 185, 129, 0.2), inset 0 0 20px rgba(0, 0, 0, 0.8);
        }
        .tbc-badge {
            display: inline-block;
            font-family: 'Cinzel', serif;
            font-size: 0.75rem;
            font-weight: 700;
            letter-spacing: 0.15em;
            color: #34d399;
            background: rgba(16, 185, 129, 0.15);
            border: 1px solid #10b981;
            padding: 0.2rem 0.75rem;
            border-radius: 4px;
            margin-bottom: 0.4rem;
        }
        .tbc-title {
            font-family: 'Cinzel', serif;
            font-size: 2rem;
            font-weight: 700;
            color: #a7f3d0;
            text-shadow: 0 2px 6px rgba(0,0,0,0.8);
            margin: 0;
        }
        .tbc-subtitle {
            font-size: 0.85rem;
            color: #94a3b8;
            margin-top: 0.3rem;
            font-family: 'JetBrains Mono', monospace;
        }

        /* 數據卡片與面板 */
        .tbc-panel {
            background: rgba(15, 11, 26, 0.85);
            border: 1px solid #34d399;
            border-radius: 8px;
            padding: 1.25rem;
            box-shadow: 0 4px 20px rgba(0,0,0,0.5);
        }

        /* 指標樣式：外域邪能綠光 */
        [data-testid="stMetric"] {
            background: linear-gradient(160deg, #111c18 0%, #0a110f 100%);
            border: 1px solid #059669;
            border-left: 4px solid #10b981;
            border-radius: 6px;
            padding: 0.8rem 1rem;
        }
        [data-testid="stMetricLabel"] {
            color: #94a3b8 !important;
            font-family: 'Cinzel', serif;
            font-size: 0.75rem !important;
        }
        [data-testid="stMetricValue"] {
            font-family: 'JetBrains Mono', monospace !important;
            color: #34d399 !important;
            font-weight: 700 !important;
        }

        /* 按鈕：邪能綠漸層 */
        button[kind="primaryFormSubmit"], button[kind="primary"] {
            background: linear-gradient(90deg, #047857 0%, #059669 50%, #10b981 100%) !important;
            border: 1px solid #34d399 !important;
            color: #ffffff !important;
            font-family: 'Cinzel', serif !important;
            font-weight: 700 !important;
            border-radius: 6px !important;
            box-shadow: 0 0 15px rgba(16, 185, 129, 0.4) !important;
        }
        button[kind="primaryFormSubmit"]:hover, button[kind="primary"]:hover {
            box-shadow: 0 0 25px rgba(52, 211, 153, 0.7) !important;
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


def render_professional_table(df: pd.DataFrame, currency: str, key_prefix: str = "search") -> None:
    if df.empty:
        st.info("未偵測到符合條件的航班。")
        return

    if "ota_price" not in df.columns:
        df["ota_price"] = None

    df["price"] = pd.to_numeric(df["price"], errors="coerce")
    df["ota_price"] = pd.to_numeric(df["ota_price"], errors="coerce")
    
    view = df.sort_values(["price", "duration_minutes"], ascending=[True, True]).reset_index(drop=True)
    cheapest = view.iloc[0]
    direct = view[view["stops"] == 0]
    deals_df = view[view["is_deal"] == True]

    valid_ota = view["ota_price"].dropna()
    min_ota_str = f"€ {int(valid_ota.min()):,}" if not valid_ota.empty and valid_ota.min() > 0 else "無資料"

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("最低直銷票價", f"€ {int(cheapest['price']):,}")
    col2.metric("最低代理商 (OTA)", min_ota_str)
    col3.metric("直飛最低價", f"€ {int(direct['price'].min()):,}" if not direct.empty else "無直飛")
    col4.metric("精選特價組合", f"{len(deals_df)} 筆")

    st.markdown("---")

    table_rows = []
    for index, row in view.iterrows():
        price = int(row["price"])
        ota_price = row.get("ota_price")
        airlines = clean_airline_name(row.get("airlines"))
        stops = int(row.get("stops", 0))
        dep_date = row.get("departure_date", "")
        segments = row.get("segments", [])
        
        dep_time = segments[0].get("departure_time", "") if segments and isinstance(segments, list) else ""
        arr_time = segments[-1].get("arrival_time", "") if segments and isinstance(segments, list) else ""
        duration = format_duration(row.get("duration_minutes", 0))
        
        has_self_transfer = any(
            "self-transfer" in str(seg).lower() or "eigener" in str(seg).lower()
            for seg in segments
        ) if isinstance(segments, list) else False
        
        transfers_list = [
            airport for seg in segments for airport in seg.get("transfer_airports", [])
        ] if isinstance(segments, list) else []
        
        transfer_desc = "直飛 (Nonstop)" if stops == 0 else f"{stops} 站 ({'、'.join(transfers_list)})"
        if has_self_transfer:
            transfer_desc += " [🔄 自行轉機]"

        price_display = f"€ {price:,} (直銷)"
        if pd.notna(ota_price) and ota_price > 0 and int(ota_price) < price:
            price_display = f"€ {price:,} (直) / € {int(ota_price):,} (OTA)"

        deal_tag = "🔥 DEAL" if row.get("is_deal") else "—"

        table_rows.append({
            "排名": index + 1,
            "特價": deal_tag,
            f"票價 ({currency})": price_display,
            "直銷價(純數值)": price,
            "航空公司": airlines,
            "航線": f"{row['from_airport']} ➔ {row['to_airport']}",
            "出發日": dep_date,
            "起降時段": f"{dep_time} – {arr_time}",
            "總時長": duration,
            "轉機詳情": transfer_desc,
        })

    table_df = pd.DataFrame(table_rows)
    display_df = table_df.drop(columns=["直銷價(純數值)"])
    st.dataframe(display_df, use_container_width=True, hide_index=True)

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
            progress = st.progress(0.0, text="🟢 正在通過外域邪能網路連線 Google Flights 最便宜分頁與雙價格…")
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
    render_professional_table(df, config.settings.currency, key_prefix="search")


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
    render_professional_table(offers_df, currency, key_prefix="watch")


def trend_tab() -> None:
    """【燃燒遠征優化】歷史價格走勢圖表：具備統計指標卡片、乾淨的時間軸格式化與精美折線圖。"""
    history = load_history()
    if history.empty:
        st.info("尚無歷史觀測數據，累積數次排程後將自動繪製價格走勢。")
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

    # 頂部選擇器
    col_a, col_b = st.columns(2)
    chart_route = col_a.selectbox("選擇觀測航線 // ROUTE", sorted(history["航線"].unique()))
    dates = sorted(history[history["航線"] == chart_route]["departure_date"].unique())
    chart_date = col_b.selectbox("選擇出發日期 // DEPARTURE DATE", dates)

    series = history[(history["航線"] == chart_route) & (history["departure_date"] == chart_date)]
    if series.empty:
        st.write("此日期組合尚無足夠數據。")
        return

    # 整理為乾淨的時間序列，將索引轉換為可讀的日期字串格式避免擠壓
    lowest = series.groupby("observed_at")["price"].min().sort_index()
    
    # 統計指標
    min_val = int(lowest.min())
    max_val = int(lowest.max())
    latest_val = int(lowest.iloc[-1])

    m1, m2, m3 = st.columns(3)
    m1.metric("歷史觀測最低價", f"€ {min_val:,}")
    m2.metric("歷史觀測最高價", f"€ {max_val:,}")
    m3.metric("最新回報價格", f"€ {latest_val:,}")

    st.markdown("---")
    st.subheader(f"📈 航線 {chart_route}（出發日：{chart_date}）價格走勢監控")
    
    # 轉換成乾淨的 DataFrame 繪製折線圖，確保橫軸完美對齊
    chart_df = pd.DataFrame({
        "觀測時間": lowest.index.tz_convert(ZoneInfo("Europe/Berlin")).strftime("%Y-%m-%d %H:%M"),
        "最低票價 (EUR)": lowest.values
    }).set_index("觀測時間")

    st.line_chart(chart_df, y_label="最低票價 (EUR)", color="#10b981")


def main() -> None:
    inject_tbc_theme()
    st.markdown(
        """
        <div class="tbc-banner">
            <div class="tbc-badge">OUTLAND TELEMETRY // MARKET: DE (EUR) // TBC EPIC RADAR</div>
            <h1 class="tbc-title">燃燒遠征機票雷達中樞</h1>
            <div class="tbc-subtitle">World of Warcraft: The Burning Crusade Themed Google Flights Telemetry &amp; Dual-Price Engine</div>
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
