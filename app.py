"""QUEL'THALAS FLIGHT RADAR // 血精靈純票價與精準航司雙行往返戰情室 (app.py)"""
from __future__ import annotations

import json
import logging
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import airportsdata
import pandas as pd
import streamlit as st
import yaml

st.set_page_config(
    page_title="QUEL'THALAS FLIGHT RADAR // 血精靈機票雷達",
    page_icon="🩸",
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


def inject_blood_elf_theme() -> None:
    st.markdown(
        """
        <style>
        @import url('https://fonts.googleapis.com/css2?family=Cinzel:wght@600;700;900&family=JetBrains+Mono:wght@400;600;700&family=Inter:wght@400;500;600;700&display=swap');

        .stApp {
            background: radial-gradient(circle at 50% 0%, #1f0b14 0%, #0d0408 60%, #050203 100%);
            color: #f3e8ff;
            font-family: 'Inter', sans-serif;
        }

        .blood-elf-banner {
            background: linear-gradient(135deg, rgba(50, 10, 22, 0.9) 0%, rgba(20, 5, 10, 0.95) 100%);
            border: 2px solid #f59e0b;
            border-image: linear-gradient(to right, #b91c1c, #f59e0b, #ef4444) 1;
            border-radius: 8px;
            padding: 1.2rem 1.6rem;
            margin-bottom: 1.2rem;
            box-shadow: 0 0 35px rgba(220, 38, 38, 0.25), inset 0 0 20px rgba(0, 0, 0, 0.8);
        }
        .blood-elf-badge {
            display: inline-block;
            font-family: 'Cinzel', serif;
            font-size: 0.72rem;
            font-weight: 700;
            letter-spacing: 0.15em;
            color: #fde047;
            background: rgba(185, 28, 28, 0.25);
            border: 1px solid #f59e0b;
            padding: 0.15rem 0.6rem;
            border-radius: 4px;
            margin-bottom: 0.3rem;
        }
        .blood-elf-title {
            font-family: 'Cinzel', serif;
            font-size: 1.8rem;
            font-weight: 700;
            color: #fef08a;
            text-shadow: 0 2px 4px rgba(0,0,0,0.9);
            margin: 0;
        }
        .blood-elf-subtitle {
            font-size: 0.8rem;
            color: #cbd5e1;
            margin-top: 0.3rem;
            font-family: 'JetBrains Mono', monospace;
        }

        [data-testid="stMetric"] {
            background: linear-gradient(160deg, #230d14 0%, #100508 100%);
            border: 1px solid #b91c1c;
            border-left: 4px solid #f59e0b;
            border-radius: 6px;
            padding: 0.7rem 0.9rem;
        }
        [data-testid="stMetricLabel"] {
            color: #cbd5e1 !important;
            font-family: 'Cinzel', serif;
            font-size: 0.7rem !important;
        }
        [data-testid="stMetricValue"] {
            font-family: 'JetBrains Mono', monospace !important;
            color: #fde047 !important;
            font-weight: 700 !important;
        }
        button[kind="primaryFormSubmit"], button[kind="primary"] {
            background: linear-gradient(90deg, #991b1b 0%, #b91c1c 50%, #dc2626 100%) !important;
            border: 1px solid #f59e0b !important;
            color: #ffffff !important;
            font-family: 'Cinzel', serif !important;
            font-weight: 700 !important;
            border-radius: 6px !important;
            box-shadow: 0 0 15px rgba(245, 158, 11, 0.4) !important;
        }
        button[kind="primaryFormSubmit"]:hover, button[kind="primary"]:hover {
            box-shadow: 0 0 25px rgba(245, 158, 11, 0.7) !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


@st.cache_data
def load_all_airports() -> dict[str, str]:
    airports_dict = {}
    try:
        raw_data = airportsdata.load("IATA")
        for code, airport in raw_data.items():
            if len(code) != 3:
                continue
            name_parts = [
                str(airport.get(field, "")).strip()
                for field in ("city", "name", "country")
                if airport.get(field)
            ]
            airports_dict[code] = " · ".join(name_parts) or code
    except Exception:
        airports_dict = {"FRA": "Frankfurt · Germany", "HKG": "Hong Kong · Hong Kong"}
    return airports_dict


AIRPORTS = load_all_airports()


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


class StreamlitLogHandler(logging.Handler):
    """自訂日誌收集器，將後端爬蟲的 log 即時收集並顯示在前端界面中"""
    def __init__(self, log_list: list[str]):
        super().__init__()
        self.log_list = log_list

    def emit(self, record):
        log_entry = self.format(record)
        self.log_list.append(log_entry)


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
) -> tuple[list[dict], list[str]]:
    captured_logs: list[str] = []
    
    # 動態攔截 playwright_fetcher 與 provider 的 log
    fetcher_logger = logging.getLogger("src.playwright_fetcher")
    provider_logger = logging.getLogger("src.provider")
    
    handler = StreamlitLogHandler(captured_logs)
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    
    fetcher_logger.addHandler(handler)
    provider_logger.addHandler(handler)
    fetcher_logger.setLevel(logging.INFO)
    provider_logger.setLevel(logging.INFO)

    try:
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
        results = [
            {**offer.to_dict(), "quote_checked_at": quote_checked_at}
            for offer in offers
        ]
    finally:
        fetcher_logger.removeHandler(handler)
        provider_logger.removeHandler(handler)

    return results, captured_logs


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


def render_professional_table(df: pd.DataFrame, currency: str, trip_label: str, key_prefix: str = "search") -> None:
    if df.empty:
        st.info("未偵測到符合條件的航班。")
        return

    df["price"] = pd.to_numeric(df["price"], errors="coerce")
    view = df.sort_values(["price", "duration_minutes"], ascending=[True, True]).reset_index(drop=True)
    cheapest = view.iloc[0]
    direct = view[view["stops"] == 0]
    deals_df = view[view["is_deal"] == True]

    col1, col2, col3 = st.columns(3)
    col1.metric("最低票價", f"€ {int(cheapest['price']):,}")
    col2.metric("直飛最低價", f"€ {int(direct['price'].min()):,}" if not direct.empty else "無直飛")
    col3.metric("精選特價組合", f"{len(deals_df)} 筆")

    st.markdown("---")

    is_roundtrip = trip_label in ("往返", "開口行程")
    table_rows = []

    for index, row in view.iterrows():
        price = int(row["price"])
        airlines = clean_airline_name(row.get("airlines"))
        stops = int(row.get("stops", 0))
        dep_date = row.get("departure_date", "")
        segments = row.get("segments", [])
        
        price_str = f"€ {price:,}"

        out_dep_time = ""
        out_arr_time = ""
        out_route = f"{row.get('from_airport', '')} ➔ {row.get('to_airport', '')}"
        out_duration = format_duration(row.get("duration_minutes", 0))
        
        transfers_list = [
            airport for seg in segments for airport in seg.get("transfer_airports", [])
            if airport
        ] if isinstance(segments, list) else []
        
        out_transfer_desc = "直飛" if stops == 0 else f"{stops} 站"
        if transfers_list:
            out_transfer_desc += f" ({'、'.join(transfers_list)})"

        if segments and isinstance(segments, list):
            out_seg = segments[0]
            out_dep_time = out_seg.get("departure_time", "")
            out_arr_time = out_seg.get("arrival_time", "")

        deal_tag = "🔥 DEAL" if row.get("is_deal") else "—"

        if not is_roundtrip:
            table_rows.append({
                "排名": str(index + 1),
                "特價": deal_tag,
                f"票價 ({currency})": price_str,
                "航空公司": airlines,
                "航段方向": f"🛫 去程 ({dep_date})",
                "航線": out_route,
                "起降時段": f"{out_dep_time} – {out_arr_time}" if out_dep_time else "—",
                "總時長": out_duration,
                "轉機詳情": out_transfer_desc,
            })
        else:
            ret_dep_time = ""
            ret_arr_time = ""
            ret_route = "—"
            ret_duration = "—"
            ret_transfer_desc = "—"
            ret_date = dep_date
            ret_airline = airlines

            if len(segments) > 1:
                ret_seg = segments[1]
                ret_dep_time = ret_seg.get("departure_time", "")
                ret_arr_time = ret_seg.get("arrival_time", "")
                ret_date = ret_seg.get("departure_date", dep_date)
                ret_from = ret_seg.get("from_airport", row.get("to_airport", ""))
                ret_to = ret_seg.get("to_airport", row.get("from_airport", ""))
                ret_route = f"{ret_from} ➔ {ret_to}"
                ret_stops = int(ret_seg.get("stops", 0))
                ret_transfers = [a for a in ret_seg.get("transfer_airports", []) if a]
                ret_transfer_desc = "直飛" if ret_stops == 0 else f"{ret_stops} 站"
                if ret_transfers:
                    ret_transfer_desc += f" ({'、'.join(ret_transfers)})"
                ret_duration = format_duration(ret_seg.get("duration_minutes", 0))

            table_rows.append({
                "排名": str(index + 1),
                "特價": deal_tag,
                f"票價 ({currency})": price_str,
                "航空公司": airlines,
                "航段方向": f"🛫 去程 ({dep_date})",
                "航線": out_route,
                "起降時段": f"{out_dep_time} – {out_arr_time}" if out_dep_time else "—",
                "總時長": out_duration,
                "轉機詳情": out_transfer_desc,
            })
            table_rows.append({
                "排名": "",
                "特價": "",
                f"票價 ({currency})": "",
                "航空公司": ret_airline,
                "航段方向": f"🛬 回程 ({ret_date})",
                "航線": ret_route,
                "起降時段": f"{ret_dep_time} – {ret_arr_time}" if ret_dep_time else "—",
                "總時長": ret_duration,
                "轉機詳情": ret_transfer_desc,
            })

    table_df = pd.DataFrame(table_rows)
    table_df["排名"] = table_df["排名"].astype(str).replace("nan", "").replace("None", "")

    st.dataframe(table_df, width="stretch", hide_index=True)

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
    default_origin = codes.index("FRA") if "FRA" in codes else (0 if codes else "")
    default_dest = codes.index("HKG") if "HKG" in codes else (1 if len(codes) > 1 else 0)

    col_left, col_right = st.columns([0.38, 0.62], gap="large")

    with col_left:
        st.markdown("### 🎛️ 太陽之井雷達控制面板")
        
        trip_label = st.radio(
            "航程模式 // ITINERARY MODE",
            ["單程", "往返", "開口行程"],
            horizontal=True,
            key="trip_mode_radio"
        )

        with st.form("search_form"):
            st.markdown("##### 🛫 去程航段 (Outbound)")
            origin = st.selectbox("出發機場 // ORIGIN", codes, index=default_origin, format_func=airport_label, key="out_origin")
            destination = st.selectbox("目的機場 // DESTINATION", codes, index=default_dest, format_func=airport_label, key="out_dest")
            departure = st.date_input("出發日期 // DEPARTURE", value=date.today() + timedelta(days=30), min_value=date.today())

            return_date = None
            return_origin = None
            return_destination = None

            if trip_label == "往返":
                st.markdown("##### 🛬 回程航段 (Return)")
                return_date = st.date_input("回程日期 // RETURN", value=departure + timedelta(days=7), min_value=departure)
                return_origin = destination
                return_destination = origin
            elif trip_label == "開口行程":
                st.markdown("##### 🛬 回程航段 (Open-Jaw Return)")
                return_origin = st.selectbox("回程出發機場 // RETURN ORIGIN", codes, index=default_dest, format_func=airport_label, key="ret_origin")
                return_destination = st.selectbox("回程目的機場 // RETURN DESTINATION", codes, index=default_origin, format_func=airport_label, key="ret_dest")
                return_date = st.date_input("回程日期 // RETURN DATE", value=departure + timedelta(days=7), min_value=departure)

            flexible = st.number_input("彈性日期範圍 ±(天)", min_value=0, max_value=7, value=0, step=1)
            seat = st.selectbox("艙等 // CABIN CLASS", list(SEAT_LABELS), format_func=lambda s: SEAT_LABELS[s])
            stops_label = st.radio("轉機限制 // STOPS", list(STOP_CHOICES), horizontal=True)

            with st.expander("⚙ 進階參數（預設解鎖價格排序與自行轉機）", expanded=False):
                carry_on_bags = st.number_input("隨身行李數", min_value=0, max_value=2, value=0)
                checked_bags = st.number_input("託運行李數", min_value=0, max_value=2, value=0)

            submitted = st.form_submit_button("🩸 啟動奎爾薩拉斯即時雷達", type="primary", use_container_width=True)

    with col_right:
        st.markdown("### 📊 航班戰利品即時展示區")
        if submitted:
            if origin == destination and trip_label == "單程":
                st.warning("出發地與目的地不能相同。")
            else:
                dates = [departure + timedelta(days=offset) for offset in range(-flexible, flexible + 1)]
                t_type_map = {"單程": "one-way", "往返": "round-trip", "開口行程": "multi-city"}
                trip_type = t_type_map[trip_label]
                
                requests = []
                for outbound_date in dates:
                    if trip_label == "單程":
                        segments = [(origin, destination, outbound_date.isoformat())]
                    elif trip_label == "往返" and return_date:
                        ret_date_str = (return_date + (outbound_date - departure)).isoformat()
                        segments = [
                            (origin, destination, outbound_date.isoformat()),
                            (return_origin, return_destination, ret_date_str)
                        ]
                    elif trip_label == "開口行程" and return_date:
                        ret_date_str = (return_date + (outbound_date - departure)).isoformat()
                        segments = [
                            (origin, destination, outbound_date.isoformat()),
                            (return_origin, return_destination, ret_date_str)
                        ]
                    else:
                        segments = [(origin, destination, outbound_date.isoformat())]

                    requests.append((tuple(segments), trip_type))

                rows: list[dict] = []
                all_debug_logs: list[str] = []
                progress = st.progress(0.0, text="🩸 正在透過太陽之井魔網連線 Google Flights 查詢最低票價…")

                for index, (segments, t_type) in enumerate(requests):
                    try:
                        all_debug_logs.append(f"--- 查詢航段: {segments}, 類型: {t_type} ---")
                        result_batch, batch_logs = cached_search(
                            segments,
                            t_type,
                            seat,
                            config.settings.currency,
                            config.settings.language,
                            STOP_CHOICES[stops_label],
                            int(carry_on_bags),
                            int(checked_bags),
                            True,
                            False,
                            True,
                        )
                        rows.extend(result_batch)
                        all_debug_logs.extend(batch_logs)
                    except Exception as exc:
                        error_msg = str(exc)
                        all_debug_logs.append(f"❌ 查詢異常: {error_msg}")
                        st.warning(f"查詢異常：{error_msg}")
                    progress.progress((index + 1) / len(requests))
                progress.empty()

                # 【前端視覺化展示】：將後端收集到的所有爬蟲價格陣列與除錯日誌完整印在介面上
                with st.expander("🛠️ 點擊展開 Playwright 爬蟲 DEBUG 診斷記錄與價格陣列", expanded=True):
                    st.text("--- Playwright 爬蟲實時執行與價格解析日誌 ---")
                    for log_line in all_debug_logs:
                        if "DEBUG_CARD" in log_line:
                            st.markdown(f"<code style='color: #fde047;'>{log_line}</code>", unsafe_allow_html=True)
                        else:
                            st.text(log_line)
                    
                    debug_file = Path("debug_page.html")
                    if debug_file.exists():
                        st.error("偵測到爬蟲未抓取到元素，已自動儲存 `debug_page.html`。")
                        st.download_button(
                            label="📥 下載 debug_page.html 進行人工分析",
                            data=debug_file.read_bytes(),
                            file_name="debug_page.html",
                            mime="text/html"
                        )

                st.session_state["search_result"] = {
                    "trip_label": trip_label,
                    "segments": requests[0][0],
                    "rows": rows,
                    "dates": [d.isoformat() for d in dates],
                }

        result = st.session_state.get("search_result")
        if not result:
            st.info("👈 請在左側設定航線與模式（單程/往返/開口），點擊 **「啟動奎爾薩拉斯即時雷達」** 開始檢索。")
        else:
            if not result["rows"]:
                st.error("本次查詢未取得任何有效票價，請稍後重試。")
            else:
                df = mark_deals(result["rows"])
                render_professional_table(df, config.settings.currency, trip_label, key_prefix="search")


def watchlist_tab() -> None:
    config = load_config()
    codes = list(AIRPORTS)
    
    if "watch_routes" not in st.session_state:
        st.session_state["watch_routes"] = [
            {"from": route.from_airport, "to": route.to_airport}
            for route in config.routes
        ]
    routes = st.session_state["watch_routes"]

    col_left, col_right = st.columns([0.35, 0.65], gap="large")

    with col_left:
        st.markdown("### 🛡️ 銀月城防禦陣列控制面板")
        
        with st.form("add_watchlist_form"):
            new_origin = st.selectbox("出發要塞 ORIGIN", codes, format_func=airport_label, key="add_origin")
            new_destination = st.selectbox("目標領地 DESTINATION", codes, index=1 if len(codes) > 1 else 0, format_func=airport_label, key="add_dest")
            add_btn = st.form_submit_button("＋ 添加監控航線", use_container_width=True)

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

        st.markdown("---")
        st.markdown("#### 📋 既有監控陣列管理")
        if routes:
            route_labels = [f"{r['from']} ➔ {r['to']}" for r in routes]
            selected_to_remove = st.selectbox("選擇要移除的航線", route_labels, key="remove_select")
            if st.button("🗑️ 刪除所選航線", use_container_width=True):
                idx = route_labels.index(selected_to_remove)
                removed = routes.pop(idx)
                st.session_state["watch_routes"] = routes
                st.warning(f"已移除航線：{removed['from']} ➔ {removed['to']}")
                st.rerun()

            token = read_secret("GITHUB_TOKEN")
            repository = read_secret("GITHUB_REPOSITORY")
            branch = read_secret("GITHUB_BRANCH", "main")
            
            if st.button("☁️ 同步航線至 GitHub 排程倉庫", type="primary", use_container_width=True):
                if not token or not repository:
                    st.error("尚未設定 GitHub Secrets。")
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
            st.info("目前無監控航線。")

    with col_right:
        st.markdown("### 📊 戰情室聚合報價展示區")
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

        render_professional_table(filtered_watchlist, currency, "往返", key_prefix="watch")


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
        "观測時間": lowest.index.tz_convert(ZoneInfo("Europe/Berlin")).strftime("%Y-%m-%d %H:%M"),
        "最低票價 (EUR)": lowest.values
    }).set_index("观測時間")

    st.line_chart(chart_df, y_label="最低票價 (EUR)", color="#f59e0b")


def read_secret(name: str, default: str = "") -> str:
    try:
        value = st.secrets.get(name, default)
    except Exception:
        value = os.environ.get(name, default)
    return str(value or default).strip()


def main() -> None:
    inject_blood_elf_theme()
    st.markdown(
        """
        <div class="blood-elf-banner">
            <div class="blood-elf-badge">QUEL'THALAS TELEMETRY // MARKET: DE (EUR) // BLOOD ELF EPIC RADAR</div>
            <h1 class="blood-elf-title">血精靈機票雷達中樞</h1>
            <div class="blood-elf-subtitle">World of Warcraft: Blood Elf Themed Google Flights Telemetry &amp; Dual-Price Engine</div>
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
