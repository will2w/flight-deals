"""Streamlit 航班監控與最低價雷達儀表板 (app.py)"""
from __future__ import annotations

import logging
import pandas as pd
import streamlit as st

from src.pipeline import build_baselines, is_deal
from src.provider import GoogleFlightsProvider

# 設定頁面配置
st.set_page_config(
    page_title="航班最低價雷達 | Flight Deals Monitor",
    page_icon="✈️",
    layout="wide",
)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

# --- 側邊欄控制面板 ---
st.sidebar.header("⚙️ 雷達掃描控制台")

from_airport = st.sidebar.text_input("出發機場 (IATA)", value="FRA").upper()
to_airport = st.sidebar.text_input("目的地機場 (IATA)", value="HKG").upper()

days_ahead_input = st.sidebar.text_input("掃描天數 (逗號分隔)", value="14, 30, 33, 38, 60")
try:
    days_ahead = [int(d.strip()) for d in days_ahead_input.split(",") if d.strip().isdigit()]
except Exception:
    days_ahead = [14, 30, 60]

top_n = st.sidebar.slider("每航段保留報價數 (Top N)", min_value=5, max_value=150, value=50, step=5)
hide_self_transfer = st.sidebar.checkbox("排除自行轉機 (Eigener Transfer)", value=False)

st.sidebar.markdown("---")
run_scan = st.sidebar.button("🚀 啟動最低價雷達掃描", type="primary")

if st.sidebar.button("🗑️ 清除系統快取"):
    st.cache_data.clear()
    st.sidebar.success("快取已清除！")


# --- 主畫面標題 ---
st.title("✈️ 國際航線最低價雷達與組合矩陣")
st.markdown(
    "結合 **Playwright 全量深度擷取**、**Am günstigsten 最便宜分頁鎖定**、**自行轉機 (Eigener Transfer)** 與 **直銷/OTA 雙價格對比**。"
)

# --- 核心搜尋與呈現邏輯 ---
@st.cache_data(ttl=600, show_spinner=False)
def cached_search(from_apt, to_apt, days_list, max_n, hide_transfer):
    """執行 Google Flights 多天期深度掃描。"""
    from datetime import datetime, timedelta
    
    provider = GoogleFlightsProvider(
        currency="EUR",
        language="de",
        hide_separate_and_self_transfer=hide_transfer,
        playwright_max_results=max_n,
    )
    
    all_offers = []
    base_date = datetime.now()
    
    for days in days_list:
        target_date = (base_date + timedelta(days=days)).strftime("%Y-%m-%d")
        log.info("正在掃描航線 %s -> %s，日期：%s", from_apt, to_apt, target_date)
        try:
            results = provider.search(
                from_airport=from_apt,
                to_airport=to_apt,
                departure_date=target_date,
            )
            if results:
                all_offers.extend(results[:max_n])
        except Exception as exc:
            log.warning("掃描 %s -> %s (%s) 失敗：%s", from_apt, to_apt, target_date, exc)
            
    return all_offers


if run_scan:
    if not from_airport or not to_airport:
        st.error("請輸入有效得出發與目的地機場代碼。")
    else:
        with st.spinner(f"正在透過 Playwright 深度擷取 {from_airport} → {to_airport} 的全量最低價與自行轉機組合（請稍候約 15-30 秒）..."):
            offers = cached_search(from_airport, to_airport, days_ahead, top_n, hide_self_transfer)
            
        if not offers:
            st.warning("未取得任何有效航班資料，請檢查網路連線或稍後再試。")
        else:
            st.success(f"成功擷取共 {len(offers)} 筆有效航班報價！")
            
            # 統計指標區
            min_price = min(o.price for o in offers)
            min_ota = min((o.ota_price for o in offers if o.ota_price), default=min_price)
            best_duration = min(o.duration_minutes for o in offers if o.duration_minutes > 0)
            
            col1, col2, col3, col4 = st.columns(4)
            with col1:
                st.metric("最低直銷票價", f"€ {min_price}")
            with col2:
                st.metric("最低 OTA 代理商價", f"€ {min_ota}")
            with col3:
                st.metric("有效組合總數", f"{len(offers)} 筆")
            with col4:
                st.metric("最短總時長", f"{best_duration // 60}h {best_duration % 60}m")
                
            st.markdown("### 📊 單程報價矩陣與雙價格清單")
            
            table_data = []
            for rank, offer in enumerate(offers, 1):
                # 雙價格顯示格式
                price_display = f"€ {offer.price}"
                if getattr(offer, "ota_price", None) and offer.ota_price < offer.price:
                    price_display = f"€ {offer.price} (OTA: € {offer.ota_price})"
                    
                # 檢查自行轉機
                has_self_transfer = any(
                    "self-transfer" in str(seg).lower() or "eigener" in str(seg).lower()
                    for seg in offer.segments
                )
                transfer_tag = " 🔄 自行轉機 (Eigener Transfer)" if has_self_transfer or offer.stops >= 2 else ""
                
                table_data.append({
                    "排名": rank,
                    "票價 (直銷/OTA)": price_display,
                    "主要航空公司": ", ".join(offer.airlines),
                    "轉機次數": f"{offer.stops} 站{transfer_tag}",
                    "轉機機場": ", ".join(offer.transfer_airports) if offer.transfer_airports else "直飛",
                    "航段": f"{offer.from_airport} → {offer.to_airport}",
                    "出發日": offer.departure_date,
                    "去程時段": f"{offer.departure_time} - {offer.arrival_time}",
                    "總時長": f"{offer.duration_minutes // 60}h {offer.duration_minutes % 60}m",
                    "提供來源": offer.provider,
                })
                
            df = pd.DataFrame(table_data)
            st.dataframe(df, use_container_width=True, hide_index=True)
            
            # 下載 CSV 按鈕
            csv_data = df.to_csv(index=False).encode("utf-8")
            st.download_button(
                label="📥 下載完整報價矩陣 CSV",
                data=csv_data,
                file_name=f"flight_deals_{from_airport}_{to_airport}.csv",
                mime="text/csv",
            )
else:
    st.info("👈 請在左側設定航線與掃描天數，並點擊 **「啟動最低價雷達掃描」** 開始運作。")
