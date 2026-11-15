"""Streamlit 前端：自選航線即時搜尋、監控清單、價格走勢。

本機執行：
    streamlit run app.py
"""
from __future__ import annotations

import json
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.config import load_config  # noqa: E402
from src.pipeline import build_baselines, is_deal  # noqa: E402
from src.provider import GoogleFlightsProvider  # noqa: E402
from src.store import read_history  # noqa: E402

SNAPSHOT_PATH = ROOT / "data" / "deals.json"
HISTORY_PATH = ROOT / "data" / "history.csv"

SEAT_LABELS = {
    "economy": "經濟艙",
    "premium-economy": "豪華經濟艙",
    "business": "商務艙",
    "first": "頭等艙",
}

STOP_CHOICES = {"不限": None, "只要直飛": 0, "最多轉 1 次": 1}

# 常用機場（三碼代碼 -> 顯示名稱），可自行增刪
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
    "LHR": "倫敦 希斯洛", "CDG": "巴黎 戴高樂", "AMS": "阿姆斯特丹", "FRA": "法蘭克福",
    "MUC": "慕尼黑", "FCO": "羅馬", "MXP": "米蘭", "MAD": "馬德里", "BCN": "巴塞隆納",
    "ZRH": "蘇黎世", "VIE": "維也納", "PRG": "布拉格", "CPH": "哥本哈根",
    "ARN": "斯德哥爾摩", "HEL": "赫爾辛基", "ATH": "雅典", "LIS": "里斯本",
    # 北美
    "LAX": "洛杉磯", "SFO": "舊金山", "SEA": "西雅圖", "JFK": "紐約 甘迺迪",
    "ORD": "芝加哥", "BOS": "波士頓", "DFW": "達拉斯", "YVR": "溫哥華", "YYZ": "多倫多",
}

st.set_page_config(page_title="特價機票監控", layout="wide")


def airport_label(code: str) -> str:
    return f"{code} {AIRPORTS.get(code, '')}".strip()


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


@st.cache_data(ttl=1800, show_spinner=False)
def cached_search(
    origin: str,
    destination: str,
    departure_date: str,
    seat: str,
    currency: str,
    language: str,
    max_stops: int | None,
) -> list[dict]:
    """即時查 Google Flights。參數必須可雜湊，同條件 30 分鐘內走快取。"""
    provider = GoogleFlightsProvider(currency=currency, language=language, seat=seat)
    offers = provider.search(origin, destination, departure_date, max_stops=max_stops)
    return [offer.to_dict() for offer in offers]


def mark_deals(rows: list[dict]) -> pd.DataFrame:
    """用歷史基準標記每筆是否為特價。"""
    baselines = build_baselines(read_history())
    rules = load_config().deal_rules
    for row in rows:
        key = (row["from_airport"], row["to_airport"], row["departure_date"])
        prices = baselines.get(key, [])
        row["is_deal"] = is_deal(row["price"], prices, rules)
        row["baseline_median"] = int(pd.Series(prices).median()) if prices else None
    return pd.DataFrame(rows)


def render_results(df: pd.DataFrame, currency: str) -> None:
    if df.empty:
        st.info("沒有查到符合條件的行程，試著放寬轉機限制或換個日期。")
        return

    cheapest = df.loc[df["price"].idxmin()]
    direct = df[df["stops"] == 0]
    col1, col2, col3 = st.columns(3)
    col1.metric(f"最低票價 ({currency})", f"{int(cheapest['price']):,}")
    col2.metric("直飛最低價", f"{int(direct['price'].min()):,}" if not direct.empty else "無直飛")
    col3.metric("符合特價", int(df["is_deal"].sum()))

    view = df.sort_values(["price"]).copy()
    view["航空公司"] = view["airlines"].apply(lambda v: "、".join(v) if isinstance(v, list) else "")
    view["特價"] = view["is_deal"].map({True: "是", False: ""})
    columns = {
        "departure_date": "出發日",
        "price": f"票價 ({currency})",
        "baseline_median": "歷史中位數",
        "航空公司": "航空公司",
        "stops": "轉機",
        "duration_minutes": "總時長(分)",
        "departure_time": "起飛",
        "arrival_time": "抵達",
        "特價": "特價",
    }
    st.dataframe(
        view[list(columns)].rename(columns=columns),
        use_container_width=True,
        hide_index=True,
    )

    st.download_button(
        "下載搜尋結果 CSV",
        view.to_csv(index=False).encode("utf-8-sig"),
        file_name=f"{cheapest['from_airport']}-{cheapest['to_airport']}-{cheapest['departure_date']}.csv",
        mime="text/csv",
        on_click="ignore",
    )


def run_search(config, origin: str, destination: str, dates: list[date], seat: str, max_stops) -> tuple[list[dict], list[str]]:
    """逐日查詢，單日失敗不中斷整批。"""
    rows: list[dict] = []
    errors: list[str] = []
    progress = st.progress(0.0, text="查詢中…")
    for index, day in enumerate(dates):
        try:
            rows.extend(
                cached_search(
                    origin,
                    destination,
                    day.isoformat(),
                    seat,
                    config.settings.currency,
                    config.settings.language,
                    max_stops,
                )
            )
        except Exception as exc:
            errors.append(f"{day.isoformat()}：{exc}")
        progress.progress((index + 1) / len(dates), text=f"查詢中… {day.isoformat()}")
        if index < len(dates) - 1:
            time.sleep(config.settings.search_delay_seconds)
    progress.empty()
    return rows, errors


def search_tab() -> None:
    config = load_config()
    codes = list(AIRPORTS)
    default_origin = codes.index("TPE") if "TPE" in codes else 0
    default_dest = codes.index("NRT") if "NRT" in codes else 1

    with st.form("search_form"):
        col1, col2 = st.columns(2)
        origin = col1.selectbox("出發地", codes, index=default_origin, format_func=airport_label)
        destination = col2.selectbox("目的地", codes, index=default_dest, format_func=airport_label)

        col3, col4, col5, col6 = st.columns(4)
        departure = col3.date_input("出發日", value=date.today() + timedelta(days=30), min_value=date.today())
        flexible = col4.number_input("彈性日期 ±(天)", min_value=0, max_value=7, value=0, step=1)
        seat = col5.selectbox("艙等", list(SEAT_LABELS), format_func=lambda s: SEAT_LABELS[s])
        stops_label = col6.radio("轉機", list(STOP_CHOICES), horizontal=False)

        submitted = st.form_submit_button("搜尋", type="primary", use_container_width=True)

    if submitted:
        if origin == destination:
            st.warning("出發地與目的地不能相同。")
        else:
            dates = [departure + timedelta(days=offset) for offset in range(-flexible, flexible + 1)]
            rows, errors = run_search(config, origin, destination, dates, seat, STOP_CHOICES[stops_label])
            # 存進 session_state，避免側欄或下載按鈕觸發 rerun 後結果消失
            st.session_state["search_result"] = {
                "origin": origin,
                "destination": destination,
                "rows": rows,
                "errors": errors,
                "dates": [d.isoformat() for d in dates],
            }

    result = st.session_state.get("search_result")
    if not result:
        st.caption("選擇航線與日期後按「搜尋」。每次查詢 Google Flights 約需數秒，結果會快取 30 分鐘。")
        return

    for message in result["errors"]:
        st.warning(f"查詢失敗 {message}")
    if not result["rows"]:
        st.error("沒有取得任何票價。可能是 Google 反爬或暫時無法解析，請稍後重試。")
        return

    df = mark_deals(result["rows"])
    if len(result["dates"]) > 1:
        st.subheader("各日期最低價")
        daily = df.groupby("departure_date")["price"].min().sort_index()
        st.bar_chart(daily, y_label=f"最低票價 ({config.settings.currency})")

    st.subheader(f"{airport_label(result['origin'])} → {airport_label(result['destination'])}")
    render_results(df, config.settings.currency)


def watchlist_tab() -> None:
    snapshot = load_snapshot()
    if snapshot is None:
        st.info("尚未產生資料。等 GitHub Actions 排程跑過一次，或在本機執行 `python -m src.pipeline`。")
        return

    if snapshot.get("is_sample"):
        st.warning("目前顯示的是示範資料（非真實票價）。")

    summary = snapshot.get("summary", {})
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("監控航線", summary.get("routes_checked", 0))
    col2.metric("行程筆數", summary.get("offers", 0))
    col3.metric("特價筆數", summary.get("deals", 0))
    col4.metric("更新時間 (UTC)", str(snapshot.get("generated_at", "-"))[:16].replace("T", " "))

    offers_df = pd.DataFrame(snapshot.get("offers", []))
    if offers_df.empty:
        st.info("這次執行沒有取得任何票價。")
        return

    failures = summary.get("failures", [])
    if failures:
        with st.expander(f"查詢失敗 {len(failures)} 筆"):
            st.dataframe(pd.DataFrame(failures), use_container_width=True)

    currency = snapshot.get("currency", "TWD")
    offers_df["航線"] = offers_df["from_airport"] + " → " + offers_df["to_airport"]
    offers_df["航空公司"] = offers_df["airlines"].apply(
        lambda v: "、".join(v) if isinstance(v, list) else (v or "")
    )

    routes = sorted(offers_df["航線"].unique())
    stops_upper = max(int(offers_df["stops"].max()), 1)
    price_upper = max(int(offers_df["price"].max()), 100)
    with st.expander("篩選", expanded=False):
        col_a, col_b = st.columns(2)
        picked = col_a.multiselect("航線", routes, default=routes)
        max_stops = col_b.slider("最多轉機次數", 0, stops_upper, int(offers_df["stops"].max()))
        col_c, col_d = st.columns(2)
        price_cap = col_c.slider("最高票價", 0, price_upper, price_upper, step=100)
        only_deals = col_d.checkbox("只看特價", value=False)

    view = offers_df[offers_df["航線"].isin(picked) & (offers_df["price"] <= price_cap)]
    view = view[view["stops"] <= max_stops]
    if only_deals:
        view = view[view["is_deal"]]

    columns = {
        "航線": "航線",
        "departure_date": "出發日",
        "price": f"票價 ({currency})",
        "baseline_median": "歷史中位數",
        "航空公司": "航空公司",
        "stops": "轉機",
        "duration_minutes": "總時長(分)",
        "departure_time": "起飛",
        "arrival_time": "抵達",
        "is_deal": "特價",
    }

    st.subheader("特價機票")
    deals = view[view["is_deal"]].sort_values("price")
    if deals.empty:
        st.write("目前沒有符合條件的特價。")
    else:
        st.dataframe(deals[list(columns)].rename(columns=columns), use_container_width=True, hide_index=True)

    st.subheader("所有行程")
    st.dataframe(
        view.sort_values(["航線", "departure_date", "price"])[list(columns)].rename(columns=columns),
        use_container_width=True,
        hide_index=True,
    )


def trend_tab() -> None:
    history = load_history()
    if history.empty:
        st.info("尚無歷史資料。累積數次抓取後即可看到走勢。")
        return

    history["航線"] = history["from_airport"] + " → " + history["to_airport"]
    history["price"] = pd.to_numeric(history["price"], errors="coerce")
    history = history.dropna(subset=["price"])
    history["observed_at"] = pd.to_datetime(history["observed_at"], errors="coerce", utc=True)

    col_a, col_b = st.columns(2)
    chart_route = col_a.selectbox("航線", sorted(history["航線"].unique()))
    dates = sorted(history[history["航線"] == chart_route]["departure_date"].unique())
    chart_date = col_b.selectbox("出發日", dates)

    series = history[(history["航線"] == chart_route) & (history["departure_date"] == chart_date)]
    if series.empty:
        st.write("此組合尚無歷史資料。")
        return

    snapshot = load_snapshot()
    currency = snapshot.get("currency", "TWD") if snapshot else "TWD"
    lowest = series.groupby("observed_at")["price"].min().sort_index()
    st.line_chart(lowest, y_label=f"最低票價 ({currency})")


def main() -> None:
    st.title("特價機票監控")
    st.caption("資料來源：Google Flights（經 fast-flights 抓取），僅供參考，下單前請以航司即時報價為準。")

    tab_search, tab_watch, tab_trend = st.tabs(["即時搜尋", "監控清單", "價格走勢"])
    with tab_search:
        search_tab()
    with tab_watch:
        watchlist_tab()
    with tab_trend:
        trend_tab()


main()
