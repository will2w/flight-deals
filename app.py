"""Streamlit 前端：自選航線即時搜尋、監控清單、價格走勢。

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

import pandas as pd
import streamlit as st
import yaml
import airportsdata

st.set_page_config(page_title="特價機票監控", layout="wide")

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
    "economy": "經濟艙",
    "premium-economy": "豪華經濟艙",
    "business": "商務艙",
    "first": "頭等艙",
}

STOP_CHOICES = {"不限": None, "只要直飛": 0, "最多轉 1 次": 1}
GOOGLE_429_COOLDOWN_SECONDS = 15 * 60

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

# 使用完整 IATA 機場清單補足常用清單；既有繁體中文名稱優先保留。
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
) -> list[dict]:
    """查詢多航段行程；保留來源報價時間，快取 5 分鐘。"""
    provider = GoogleFlightsProvider(
        currency=currency,
        language=language,
        seat=seat,
        carry_on_bags=carry_on_bags,
        checked_bags=checked_bags,
        hide_separate_and_self_transfer=hide_separate_and_self_transfer,
        exclude_basic_economy=exclude_basic_economy,
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
        return f"{float(minutes) / 60:.1f}"
    except (TypeError, ValueError):
        return "-"


def format_transfers(value, stops: int | float | None = None) -> str:
    if isinstance(value, list):
        if value:
            return "、".join(str(item) for item in value)
        return "未提供" if stops and stops > 0 else "直飛"
    if not isinstance(value, str) or not value.strip():
        return "未提供" if stops and stops > 0 else "直飛"
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
    st.subheader("監控航線設定")
    st.caption("新增或移除航線後，可保存回 GitHub；Actions 下一次排程就會監控新航線。若尚未設定 Token，可下載設定檔再到 GitHub 網頁更新。")

    with st.form("add_monitored_route"):
        left, right = st.columns(2)
        origin = left.selectbox("出發地", list(AIRPORTS), format_func=airport_label, key="watch_origin")
        destination = right.selectbox(
            "目的地",
            list(AIRPORTS),
            index=list(AIRPORTS).index("NRT"),
            format_func=airport_label,
            key="watch_destination",
        )
        add_route = st.form_submit_button("＋ 新增航線", use_container_width=True)

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
                        "出發地": airport_label(route["from"]),
                        "目的地": airport_label(route["to"]),
                    }
                    for route in routes
                ]
            ),
            use_container_width=True,
            hide_index=True,
        )
        remove_label = st.selectbox("移除一條航線", ["不移除"] + labels, key="remove_route")
        if st.button("移除所選航線", disabled=remove_label == "不移除"):
            index = labels.index(remove_label)
            routes.pop(index)
            st.session_state["watch_routes"] = routes
            st.rerun()
    else:
        st.info("清單目前沒有航線，請先新增至少一條。")

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
    if col_save.button("保存並更新排程", type="primary", use_container_width=True):
        if not token or not repository:
            st.error("尚未設定 GitHub 權限。請在 Streamlit Cloud 的 App settings → Secrets 設定 GITHUB_TOKEN、GITHUB_REPOSITORY。")
        elif not routes:
            st.error("至少保留一條監控航線。")
        else:
            try:
                commit_url = update_routes(repository, branch, token, routes)
                st.success("航線已保存到 GitHub。GitHub Actions 將依排程監控這些航線。")
                if commit_url:
                    st.link_button("查看 GitHub 設定提交", commit_url)
            except Exception as exc:
                st.error(f"保存失敗：{exc}")

    col_download.download_button(
        "下載 routes.yaml（手動更新用）",
        data=yaml_content.encode("utf-8"),
        file_name="routes.yaml",
        mime="text/yaml",
        use_container_width=True,
    )
    with st.expander("如何啟用網頁內直接保存？"):
        st.markdown(
            "在 GitHub 建立一個 **Fine-grained personal access token**，只授權此倉庫的 "
            "**Contents: Read and write**。接著到 Streamlit Cloud → App settings → Secrets "
            "加入以下設定（將範例值換成你的資料）："
        )
        st.code(
            'GITHUB_TOKEN = "github_pat_..."\n'
            'GITHUB_REPOSITORY = "你的帳號/flight-deals"\n'
            'GITHUB_BRANCH = "main"',
            language="toml",
        )
        st.caption("Token 只放在 Streamlit Secrets，不要貼到程式碼或公開倉庫。也可以下載 routes.yaml，再到 GitHub 網頁手動更新。")


def render_results(df: pd.DataFrame, currency: str) -> None:
    if df.empty:
        st.info("沒有查到符合條件的行程，試著放寬轉機限制或換個日期。")
        return

    if "quote_checked_at" in df:
        quote_times = pd.to_datetime(
            df["quote_checked_at"], utc=True, errors="coerce"
        )
    else:
        quote_times = pd.Series(dtype="datetime64[ns, UTC]")
        st.warning("這是沒有來源時間戳的舊結果，請重新搜尋取得新版報價。")
    oldest_quote_time = quote_times.min()
    if pd.notna(oldest_quote_time):
        quote_age = (pd.Timestamp.now(tz="UTC") - oldest_quote_time).total_seconds()
        if quote_age > 300:
            st.warning("目前結果的來源報價已超過 5 分鐘，可能已變動；請重新搜尋確認。")

    cheapest = df.loc[df["price"].idxmin()]
    direct = df[df["stops"] == 0]
    col1, col2, col3 = st.columns(3)
    col1.metric(f"最低票價 ({currency})", f"{int(cheapest['price']):,}")
    col2.metric(f"直飛最低價 ({currency})", f"{int(direct['price'].min()):,}" if not direct.empty else "無直飛")
    col3.metric("符合特價", int(df["is_deal"].sum()))

    view = df.sort_values(["price"]).copy()
    view["航空公司"] = view["airlines"].apply(lambda v: "、".join(v) if isinstance(v, list) else "")
    view["行程"] = view["segments"].apply(
        lambda segments: " / ".join(
            f"{airport_label(segment['from_airport'])} → {airport_label(segment['to_airport'])}"
            for segment in segments
        )
    )
    view["去程時段"] = view["segments"].apply(
        lambda segments: _segment_times(segments, 0)
    )
    view["回程時段"] = view["segments"].apply(
        lambda segments: _segment_times(segments, 1)
    )
    view["去程時長(小時)"] = view["segments"].apply(
        lambda segments: _segment_duration(segments, 0)
    )
    view["回程時長(小時)"] = view["segments"].apply(
        lambda segments: _segment_duration(segments, 1)
    )
    view["轉機地點"] = view["segments"].apply(_format_itinerary_transfers)
    quote_source = (
        view["quote_checked_at"]
        if "quote_checked_at" in view
        else pd.Series(pd.NaT, index=view.index, dtype="datetime64[ns, UTC]")
    )
    view["報價時間（柏林）"] = pd.to_datetime(
        quote_source, utc=True, errors="coerce"
    ).dt.tz_convert(ZoneInfo("Europe/Berlin")).dt.strftime("%Y-%m-%d %H:%M")
    price_basis = view.get(
        "price_basis", pd.Series("fast_flights_quote", index=view.index)
    )
    view["價格依據"] = price_basis.map(
        {
            "google_quote": "fast-flights 擷取報價",
            "fast_flights_quote": "fast-flights 擷取報價",
            "fast_flights_round_trip_quote": "fast-flights 往返報價",
            "sum_of_one_way_quotes": "單程票價加總估算",
        }
    ).fillna("fast-flights 擷取報價")
    view["特價"] = view["is_deal"].map({True: "是", False: ""})
    columns = {
        "行程": "行程",
        "departure_date": "去程日",
        "return_date": "回程日",
        "price": f"票價 ({currency})",
        "報價時間（柏林）": "報價時間（柏林）",
        "價格依據": "價格依據",
        "baseline_median": f"歷史中位數 ({currency})",
        "航空公司": "航空公司",
        "stops": "轉機",
        "轉機地點": "轉機地點",
        "去程時段": "去程時段",
        "去程時長(小時)": "去程時長(小時)",
        "回程時段": "回程時段",
        "回程時長(小時)": "回程時長(小時)",
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


def _segment_times(segments: list[dict], index: int) -> str:
    if len(segments) <= index:
        return "—"
    segment = segments[index]
    return f"{segment['departure_time']}–{segment['arrival_time']}"


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
        if text != "直飛":
            label = labels[index] if index < len(labels) else f"第 {index + 1} 段"
            parts.append(f"{label}：{text}")
    return "；".join(parts) if parts else "直飛"


def run_search(
    config,
    requests: list[tuple[tuple[tuple[str, str, str], ...], str]],
    seat: str,
    max_stops: int | None,
    carry_on_bags: int,
    checked_bags: int,
    hide_separate_and_self_transfer: bool,
    exclude_basic_economy: bool,
) -> tuple[list[dict], list[str]]:
    """逐個日期組合查詢，單一組合失敗不中斷整批。"""
    rows: list[dict] = []
    errors: list[str] = []
    progress = st.progress(0.0, text="查詢中…")
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
                )
            )
        except Exception as exc:
            route = " / ".join(f"{origin}→{destination}" for origin, destination, _ in segments)
            if "429" in str(exc):
                st.session_state["google_rate_limit_until"] = (
                    time.monotonic() + GOOGLE_429_COOLDOWN_SECONDS
                )
                errors.append(
                    f"{route}：Google Flights 暫時限流 (HTTP 429)。"
                    "已暫停本工作階段的後續查詢，請至少等候 15 分鐘再試；"
                    "若仍被限流，請延長等待時間。"
                )
                break
            errors.append(f"{route}：{exc}")
        progress.progress((index + 1) / len(requests), text=f"查詢中… {index + 1}/{len(requests)}")
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
        int(
            st.session_state.get("google_rate_limit_until", 0)
            - time.monotonic()
        ),
    )
    if cooldown_seconds:
        st.warning(
            "Google Flights 回傳 HTTP 429，暫時停止查詢。"
            f"本工作階段約 {max(1, (cooldown_seconds + 59) // 60)} 分鐘後可再試；"
            "Google 的限流時間可能更長。"
        )

    with st.form("search_form"):
        trip_label = st.radio(
            "行程類型",
            ["單程", "往返", "開口行程"],
            horizontal=True,
        )
        col1, col2 = st.columns(2)
        origin = col1.selectbox("出發地", codes, index=default_origin, format_func=airport_label)
        destination = col2.selectbox("目的地", codes, index=default_dest, format_func=airport_label)

        col3, col4 = st.columns(2)
        departure = col3.date_input(
            "去程日期", value=date.today() + timedelta(days=30), min_value=date.today()
        )
        flexible = col4.number_input(
            "去程彈性日期 ±(天)", min_value=0, max_value=7, value=0, step=1
        )

        return_date = None
        return_origin = destination
        return_destination = origin
        open_jaw_type = ""
        if trip_label == "往返":
            return_date = st.date_input(
                "回程日期", value=departure + timedelta(days=7), min_value=departure
            )
            st.caption(f"往返：{airport_label(destination)} → {airport_label(origin)}")
        elif trip_label == "開口行程":
            return_date = st.date_input(
                "回程日期", value=departure + timedelta(days=7), min_value=departure
            )
            open_jaw_type = st.radio(
                "開口方向",
                ["目的地開口（A→B，C→A）", "出發地開口（A→B，B→C）"],
                horizontal=True,
            )
            other_codes = [code for code in codes if code not in {origin, destination}]
            default_other_code = "TPE" if "TPE" in other_codes else other_codes[0]
            if open_jaw_type.startswith("目的地"):
                return_origin = st.selectbox(
                    "回程出發城市",
                    codes,
                    index=codes.index(default_other_code),
                    format_func=airport_label,
                )
                return_destination = origin
            else:
                return_origin = destination
                return_destination = st.selectbox(
                    "回程抵達城市",
                    codes,
                    index=codes.index(default_other_code),
                    format_func=airport_label,
                )

        col5, col6 = st.columns(2)
        seat = col5.selectbox("艙等", list(SEAT_LABELS), format_func=lambda s: SEAT_LABELS[s])
        stops_label = col6.radio("轉機", list(STOP_CHOICES), horizontal=True)

        col7, col8, col9, col10 = st.columns(4)
        carry_on_bags = col7.number_input(
            "隨身行李數",
            min_value=0,
            max_value=2,
            value=config.settings.carry_on_bags,
            help="依 fast-flights 3.1.0 將估算行李費納入票價；0 表示不加行李費。",
        )
        checked_bags = col8.number_input(
            "託運行李數",
            min_value=0,
            max_value=2,
            value=config.settings.checked_bags,
            help="依 fast-flights 3.1.0 將估算行李費納入票價；0 表示不加行李費。",
        )
        hide_separate = col9.checkbox(
            "排除分開出票／自行轉機",
            value=config.settings.hide_separate_and_self_transfer,
        )
        exclude_basic = col10.checkbox(
            "排除基本經濟艙",
            value=config.settings.exclude_basic_economy,
        )

        submitted = st.form_submit_button(
            "搜尋",
            type="primary",
            use_container_width=True,
        )

    st.caption(
        "目前使用德文（de）及 EUR 對齊德國顯示。fast-flights 3.1.0 沒有 gl 國家市場參數，"
        "Streamlit Cloud 出口位置仍可能影響結果；票價為免費擷取報價，可能與 Google Flights 網頁不同。"
    )
    st.caption(
        "往返使用同一次雙航段查詢的總價；開口價格是各段單程報價加總估算。"
        "歷史特價判定目前只套用於單程。"
    )

    if submitted:
        if cooldown_seconds:
            st.warning("Google Flights 仍在冷卻期間，這次沒有送出查詢。")
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
            st.warning("目的地開口的回程出發城市要與去程目的地不同。")
        elif (
            trip_label == "開口行程"
            and open_jaw_type.startswith("出發地")
            and return_destination == origin
        ):
            st.warning("出發地開口的回程抵達城市要與去程出發地不同。")
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
            )
            # 存進 session_state，避免側欄或下載按鈕觸發 rerun 後結果消失
            st.session_state["search_result"] = {
                "trip_label": trip_label,
                "segments": requests[0][0],
                "rows": rows,
                "errors": errors,
                "dates": [d.isoformat() for d in dates],
            }

    result = st.session_state.get("search_result")
    if not result:
        st.caption("選擇航線與日期後按「搜尋」。相同條件快取 5 分鐘，結果會標示來源報價時間。")
        return

    for message in result["errors"]:
        st.warning(f"查詢失敗 {message}")
    if not result["rows"]:
        st.error("沒有取得任何票價。請稍後重試，或檢查查價服務設定與用量。")
        return

    df = mark_deals(result["rows"])
    if len(result["dates"]) > 1:
        st.subheader("各日期最低價")
        daily = df.groupby("departure_date")["price"].min().sort_index()
        st.bar_chart(daily, y_label=f"最低票價 ({config.settings.currency})")

    route_title = " / ".join(
        f"{airport_label(origin)} → {airport_label(destination)}"
        for origin, destination, _ in result["segments"]
    )
    st.subheader(f"{result['trip_label']}：{route_title}")
    render_results(df, config.settings.currency)


def watchlist_tab() -> None:
    route_manager()
    st.divider()

    snapshot = load_snapshot()
    if snapshot is None:
        st.info("尚未產生票價資料。航線已可先設定；GitHub Actions 跑過後就會出現在清單。")
        return

    if snapshot.get("is_sample"):
        st.warning("目前顯示的是示範資料（非真實票價）。")
    if snapshot.get("currency", "TWD") != "EUR":
        st.warning("這是切換歐元前的舊快照，仍以原幣別顯示。到 GitHub → Actions 手動執行一次，便會更新成 EUR。")

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

    currency = snapshot.get("currency", "EUR")
    offers_df["航線"] = offers_df["from_airport"] + " → " + offers_df["to_airport"]
    offers_df["航空公司"] = offers_df["airlines"].apply(
        lambda v: "、".join(v) if isinstance(v, list) else (v or "")
    )
    offers_df["轉機地點"] = [
        format_transfers(value, stops)
        for value, stops in zip(
            offers_df.get("transfer_airports", pd.Series(index=offers_df.index, dtype=object)),
            offers_df["stops"],
        )
    ]
    offers_df["總時長(小時)"] = offers_df["duration_minutes"].apply(format_duration)

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
        "baseline_median": f"歷史中位數 ({currency})",
        "航空公司": "航空公司",
        "stops": "轉機",
        "轉機地點": "轉機地點",
        "總時長(小時)": "總時長(小時)",
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
    if "currency" not in history.columns:
        st.info("歷史資料仍是舊格式；下一次 GitHub Actions 更新後會開始記錄 EUR 價格走勢。")
        return
    history = history[history["currency"].astype(str).str.upper() == "EUR"].copy()
    if history.empty:
        st.info("尚無 EUR 歷史資料。下一次 GitHub Actions 更新後會開始記錄。")
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

    lowest = series.groupby("observed_at")["price"].min().sort_index()
    st.line_chart(lowest, y_label="最低票價 (EUR)")


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
