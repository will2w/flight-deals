"""AZEROTH FLIGHT RADAR // 燃燒遠征全球機場完整版與左右分欄戰情室 (app.py)"""
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

# 基礎熱門機場對照，後續會透過 airportsdata 自動擴充全球完整機場
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
    st.markdown(
        """
        <style>
        @import url('https://fonts.googleapis.com/css2?family=Cinzel:wght@600;700;900&family=JetBrains+Mono:wght@400;600;700&family=Inter:wght@400;500;600;700&display=swap');

        .stApp {
            background: radial-gradient(circle at 50% 0%, #151026 0%, #0a0712 60%, #040308 100%);
            color: #e2e8f0;
            font-family: 'Inter', sans-serif;
        }

        .tbc-banner {
            background: linear-gradient(135deg, rgba(26, 15, 46, 0.9) 0%, rgba(11, 7, 20, 0.95) 100%);
            border: 2px solid #10b981;
            border-image: linear-gradient(to right, #059669, #10b981, #34d399) 1;
            border-radius: 8px;
            padding: 1.2rem 1.6rem;
            margin-bottom: 1.2rem;
            box-shadow: 0 0 30px rgba(16, 185, 129, 0.2);
        }
        .tbc-badge {
            display: inline-block;
            font-family: 'Cinzel', serif;
            font-size: 0.72rem;
            font-weight: 700;
            letter-spacing: 0.15em;
            color: #34d399;
            background: rgba(16, 185, 129, 0.15);
            border: 1px solid #10b981;
            padding: 0.15rem 0.6rem;
            border-radius: 4px;
            margin-bottom: 0.3rem;
        }
        .tbc-title {
            font-family: 'Cinzel', serif;
            font-size: 1.8rem;
            font-weight: 700;
            color: #a7f3d0;
            margin: 0;
        }
        .tbc-subtitle {
            font-size: 0.8rem;
            color: #94a3b8;
            margin-top: 0.3rem;
            font-family: 'JetBrains Mono', monospace;
        }

        [data-testid="stMetric"] {
            background: linear-gradient(160deg, #111c18 0%, #0a110f 100%);
            border: 1px solid #059669;
            border-left: 4px solid #10b981;
            border-radius: 6px;
            padding: 0.7rem 0.9rem;
        }
        [data-testid="stMetricLabel"] {
            color: #94a3b8 !important;
            font-family: 'Cinzel', serif;
            font-size: 0.7rem !important;
        }
        [data-testid="stMetricValue"] {
            font-family: 'JetBrains Mono', monospace !important;
            color: #34d399 !important;
            font-weight: 700 !important;
        }
        button[kind="primaryFormSubmit"], button[kind="primary"] {
            background: linear-gradient(90deg, #047857 0%, #059669 50%, #10b981 100%) !important;
            border: 1px solid #34d399 !important;
            color: #ffffff !important;
            font-family: 'Cinzel', serif !important;
            font-weight: 700 !important;
            border-radius: 6px !important;
            box-shadow: 0 0 15px rgba(16, 185, 129, 0.4) !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


@st.cache_data
def load_iata_airports() -> dict:
    """自動從 airportsdata 套件載入全球完整 IATA 機場資料庫。"""
    try:
        return airportsdata.load("IATA")
    except Exception:
        return {}


# 動態將全球完整機場資料庫補全至 AIRPORTS 字典中
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
    """【左右分欄設計】左側戰術控制面板，右側即時戰利品展示。"""
    config = load_config()
    codes = list(AIRPORTS)
    default_origin = codes.index("FRA") if "FRA" in codes else 0
    default_dest = codes.index("HKG") if "HKG" in codes else 1

    col_left, col_right = st.columns([0.35, 0.65], gap="large")

    with col_left:
        st.markdown("### 🎛️ 戰術雷達控制面板")
        with st.form("search_form"):
            trip_label = st.radio(
                "航程模式 // ITINERARY MODE",
                ["單程", "往返", "開口行程"],
                horizontal=True,
            )
            origin = st.selectbox("出發機場 // ORIGIN", codes, index=default_origin, format_func=airport_label)
            destination = st.selectbox("目的機場 // DESTINATION", codes, index=default_dest, format_func=airport_label)

            departure = st.date_input("出發日期 // DEPARTURE", value=date.today() + timedelta(days=30), min_value=date.today())
            flexible = st.number_input("彈性日期範圍 ±(天)", min_value=0, max_value=7, value=0, step=1)

            return_date = None
            if trip_label in ("往返", "開口行程"):
                return_date = st.date_input("回程日期 // RETURN", value=departure + timedelta(days=7), min_value=departure)

            seat = st.selectbox("艙等 // CABIN CLASS", list(SEAT_LABELS), format_func=lambda s: SEAT_LABELS[s])
            stops_label = st.radio("轉機限制 // STOPS", list(STOP_CHOICES), horizontal=True)

            with st.expander("⚙ 進階參數（預設解鎖價格排序與自行轉機）", expanded=False):
                carry_on_bags = st.number_input("隨身行李數", min_value=0, max_value=2, value=0)
                checked_bags = st.number_input("託運行李數", min_value=0, max_value=2, value=0)

            submitted = st.form_submit_button("🚀 啟動即時航班價格雷達", type="primary", use_container_width=True)

    with col_right:
        st.markdown("### 📊 航班戰利品即時展示區")
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
            st.info("👈 請在左側設定航線與條件，點擊 **「啟動即時航班價格雷達」** 開始檢索。")
        else:
            if not result["rows"]:
                st.error("本次查詢未取得任何有效票價，請稍後重試。")
            else:
                df = mark_deals(result["rows"])
                render_professional_table(df, config.settings.currency, key_prefix="search")


def watchlist_tab() -> None:
    """多航線戰術戰情室：動態新增與刪除全球機場監控航線。"""
    config = load_config()
    codes = list(AIRPORTS)
    
    if "watch_routes" not in st.session_state:
        st.session_state["watch_routes"] = [
            {"from": route.from_airport, "to": route.to_airport}
            for route in config.routes
        ]
    routes = st.session_state["watch_routes"]

    st.subheader("🛡️ 多航線戰術戰情室（航線監控管理）")
    st.caption("在此自由添加或刪除監控航線，系統將自動同步並透過 GitHub Actions 守衛排程。")

    with st.form("add_watchlist_form"):
        c1, c2, c3 = st.columns([2, 2, 1])
        new_origin = c1.selectbox("出發要塞 ORIGIN", codes, format_func=airport_label, key="add_origin")
        new_destination = c2.selectbox("目標領地 DESTINATION", codes, index=1 if len(codes) > 1 else 0, format_func=airport_label, key="add_dest")
        add_btn = c3.form_submit_button("＋ 添加航線", use_container_width=True)

    if add_btn:
        if new_origin == new_destination:
            st.warning("出發地與目的地不能相同。")
        elif any(r["from"] == new_origin and r["to"] == new_destination for r in routes):
            st.info("此航線已在防禦陣列中。")
        else:
            routes.append({"from": new_origin, "to": new_destination})
            st.session_state["watch_routes"] = routes
            st.success(f"成功添加航線：{new_origin} ➔ {new_destination}")
            st.rerun()

    if routes:
        route_labels = [f"{r['from']} ➔ {r['to']}" for r in routes]
        st.markdown("#### 📋 目前監控陣列清單")
        
        col_del_1, col_del_2 = st.columns([3, 1])
        selected_to_remove = col_del_1.selectbox("選擇要移除的航線", route_labels, key="remove_select")
        if col_del_2.button("🗑️ 刪除所選航線", use_container_width=True):
            idx = route_labels.index(selected_to_remove)
            removed = routes.pop(idx)
            st.session_state["watch_routes"] = routes
            st.warning(f"已移除航線：{removed['from']} ➔ {removed['to']}")
            st.rerun()

        token = read_secret("GITHUB_TOKEN")
        repository = read_secret("GITHUB_REPOSITORY")
        branch = read_secret("GITHUB_BRANCH", "main")
        
        if st.button("☁️ 同步航線至 GitHub 排程倉庫", type="primary"):
            if not token or not repository:
                st.error("尚未設定 GitHub Secrets (GITHUB_TOKEN 與 GITHUB_REPOSITORY)。")
            else:
                try:
                    config_data = yaml.safe_load((ROOT / "config" / "routes.yaml").read_text(encoding="utf-8")) or {}
                    config_data["routes"] = routes
                    commit_url = update_routes(repository, branch, token, routes)
                    st.success("航線設定已成功同步至 GitHub！")
                    if commit_url:
                        st.link_button("檢視 GitHub Commit 紀錄", commit_url)
                except Exception as exc:
                    st.error(f"同步失敗：{exc}")
    else:
        st.info("目前無監控航線，請在上方新增。")

    st.markdown("---")
    
    snapshot = load_snapshot()
    if snapshot is None:
        st.info("尚未產生排程快照數據。")
        return
        
    offers_df = pd.DataFrame(snapshot.get("offers", []))
    if offers_df.empty:
        st.info("尚無監控報價戰利品。")
        return

    currency = snapshot.get("currency", "EUR")
    offers_df["航線"] = offers_df["from_airport"] + " ➔ " + offers_df["to_airport"]
    available_routes = sorted(offers_df["航線"].unique())
    
    selected_route_filter = st.selectbox("🎯 篩選檢視指定監控航線報價", ["全部顯示 (All Routes)"] + available_routes)
    filtered_watchlist = offers_df if selected_route_filter == "全部顯示 (All Routes)" else offers_df[offers_df["航線"] == selected_route_filter]

    st.markdown(f"### ⚔️ 戰情室聚合報價清單（共 {len(filtered_watchlist)} 筆）")
    render_professional_table(filtered_watchlist, currency, key_prefix="watch")


def trend_tab() -> None:
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

    col_a, col_b = st.columns(2)
    chart_route = col_a.selectbox("選擇觀測航線 // ROUTE", sorted(history["航線"].unique()))
    dates = sorted(history[history["航線"] == chart_route]["departure_date"].unique())
    chart_date = col_b.selectbox("選擇出發日期 // DEPARTURE DATE", dates)

    series = history[(history["航線"] == chart_route) & (history["departure_date"] == chart_date)]
    if series.empty:
        st.write("此日期組合尚無足夠數據。")
        return

    lowest = series.groupby("observed_at")["price"].min().sort_index()
    
    min_val = int(lowest.min())
    max_val = int(lowest.max())
    latest_val = int(lowest.iloc[-1])

    m1, m2, m3 = st.columns(3)
    m1.metric("歷史觀測最低價", f"€ {min_val:,}")
    m2.metric("歷史觀測最高價", f"€ {max_val:,}")
    m3.metric("最新回報價格", f"€ {latest_val:,}")

    st.markdown("---")
    st.subheader(f"📈 航線 {chart_route}（出發日：{chart_date}）價格走勢監控")
    
    chart_df = pd.DataFrame({
        "觀測時間": lowest.index.tz_convert(ZoneInfo("Europe/Berlin")).strftime("%Y-%m-%d %H:%M"),
        "最低票價 (EUR)": lowest.values
    }).set_index("觀測時間")

    st.line_chart(chart_df, y_label="最低票價 (EUR)", color="#10b981")


def read_secret(name: str, default: str = "") -> str:
    try:
        value = st.secrets.get(name, default)
    except Exception:
        value = os.environ.get(name, default)
    return str(value or default).strip()


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
        "🛡️ 航線監控清單",
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
