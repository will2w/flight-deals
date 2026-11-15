"""Streamlit 前端：讀取 data/ 顯示最新票價與特價清單。

本機執行：
    streamlit run app.py
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import streamlit as st

DATA_DIR = Path(__file__).parent / "data"
SNAPSHOT_PATH = DATA_DIR / "deals.json"
HISTORY_PATH = DATA_DIR / "history.csv"

st.set_page_config(page_title="特價機票監控", layout="wide")


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


def main() -> None:
    st.title("特價機票監控")
    st.caption("資料來源：Google Flights（經 fast-flights 抓取），僅供參考，下單前請以航司即時報價為準。")

    snapshot = load_snapshot()
    if snapshot is None:
        st.info("尚未產生資料。請先執行 `python -m src.pipeline`，或等 GitHub Actions 排程完成。")
        return

    if snapshot.get("is_sample"):
        st.warning("目前顯示的是示範資料（非真實票價），執行 `python -m src.pipeline` 後會被覆蓋。")

    summary = snapshot.get("summary", {})
    generated_at = snapshot.get("generated_at", "-")
    currency = snapshot.get("currency", "TWD")

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("監控航線", summary.get("routes_checked", 0))
    col2.metric("行程筆數", summary.get("offers", 0))
    col3.metric("特價筆數", summary.get("deals", 0))
    col4.metric("更新時間 (UTC)", str(generated_at)[:16].replace("T", " "))

    offers_df = pd.DataFrame(snapshot.get("offers", []))
    if offers_df.empty:
        st.info("這次執行沒有取得任何票價。")
        return

    failures = summary.get("failures", [])
    if failures:
        with st.expander(f"查詢失敗 {len(failures)} 筆"):
            st.dataframe(pd.DataFrame(failures), use_container_width=True)

    offers_df["航線"] = offers_df["from_airport"] + " → " + offers_df["to_airport"]
    offers_df["航空公司"] = offers_df["airlines"].apply(
        lambda v: "、".join(v) if isinstance(v, list) else (v or "")
    )

    st.sidebar.header("篩選")
    routes = sorted(offers_df["航線"].unique())
    picked_routes = st.sidebar.multiselect("航線", routes, default=routes)
    max_price = int(offers_df["price"].max())
    price_cap = st.sidebar.slider("最高票價", 0, max_price, max_price, step=100)
    max_stops = st.sidebar.slider("最多轉機次數", 0, int(offers_df["stops"].max()), int(offers_df["stops"].max()))
    only_deals = st.sidebar.checkbox("只看特價", value=False)

    view = offers_df[offers_df["航線"].isin(picked_routes) & (offers_df["price"] <= price_cap)]
    view = view[view["stops"] <= max_stops]
    if only_deals:
        view = view[view["is_deal"]]

    display_cols = {
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
        st.dataframe(deals[list(display_cols)].rename(columns=display_cols), use_container_width=True, hide_index=True)

    st.subheader("所有行程")
    st.dataframe(
        view.sort_values(["航線", "departure_date", "price"])[list(display_cols)].rename(columns=display_cols),
        use_container_width=True,
        hide_index=True,
    )

    history = load_history()
    if not history.empty:
        st.subheader("價格走勢")
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
        else:
            lowest = series.groupby("observed_at", as_index=True)["price"].min().sort_index()
            st.line_chart(lowest, y_label=f"最低票價 ({currency})")


main()
