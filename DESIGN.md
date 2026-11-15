# 特價機票搜索程序 — 設計方案

## 1. 目標與範圍

**目標**：以 GitHub Actions 與 Streamlit 使用免費的 `fast-flights` 擷取並搜尋指定航線票價、自動標記「特價」，不依賴任何付費航班 API 或航班 API Key。

**範圍（MVP）**
- 定時抓取指定航線 × 指定出發日的**單程經濟艙**最低票價。
- 保存歷史票價，據此判定「是否為特價」。
- 以 Streamlit 網頁呈現：最新票價、特價清單、價格走勢。
- 在 Streamlit 監控清單新增／移除航線，並回寫 GitHub 設定。
- 全站票價使用 EUR；行程時長以小時顯示，列出轉機機場。
- 即時搜尋支援單程、往返與兩種開口行程，機場選單載入完整 IATA 清單。
- 全部跑在 GitHub 上：Actions 負責排程抓取，Git 倉庫當資料庫，Streamlit Cloud 負責前端。

**非目標（第一版不做）**
- 三段以上的自訂多城市行程、轉機組合最佳化。
- 自動下單、自動付款。
- 使用者帳號系統。

---

## 2. 「基於 GitHub」的角色分工

GitHub 在這個專案裡扮演三個角色，兩個免費服務直接綁同一個倉庫：

| 角色 | 用什麼 | 說明 |
|---|---|---|
| 排程執行 | **GitHub Actions** | cron 定時跑爬蟲，零伺服器成本 |
| 資料儲存 | **Git 倉庫本身** | 每次抓取結果 commit 回 `data/`，天然有版本與歷史 |
| 網頁前端 | **Streamlit Community Cloud** | 連接同一個倉庫，讀 `data/` 呈現 |

> **重要釐清**：Streamlit 是「需要 Python 伺服器」的應用，**無法**部署在 GitHub Pages 上（Pages 只能托管純靜態檔案）。因此：
> - 前端改用 **Streamlit Community Cloud**（免費，直接連 GitHub 倉庫，push 後自動重部署）。
> - 如果你想要一個純靜態的 GitHub Pages 頁面，可另外把 `data/deals.json` 直接發布到 Pages 當作公開資料端點（本方案已把資料寫成純 JSON，天生可用）。
>
> 一句話：**Actions + Git 當後端，Streamlit Cloud 當前端**，這就是最務實的「基於 GitHub」架構。

---

## 3. 整體架構

```
                    ┌─────────────────────────────────────────┐
   GitHub Actions   │  1. checkout 倉庫                        │
   (cron 每 6 小時)  │  2. pip install -r requirements.txt      │
                    │  3. python -m src.pipeline               │
                    │  4. git commit + push data/              │
                    └───────────────┬─────────────────────────┘
                                    │
                                    ▼
        ┌───────────────────────────────────────────────────────┐
        │  src/pipeline.py （主流程）                            │
        │                                                        │
        │  config/routes.yaml ──► 產生「航線 × 出發日」查詢清單    │
        │         │                                              │
        │         ▼                                              │
        │  provider.py  GoogleFlightsProvider                    │
        │         │  （fast-flights，無需航班 API Key）             │
        │         ▼                                              │
        │  正規化為 Offer {價格, 航司, 轉機數, 時長, 時間}          │
        │         │                                              │
        │         ▼                                              │
        │  store.py  讀歷史 → 算基準（中位數／歷史最低）           │
        │         │  → 標記 is_deal                              │
        │         ▼                                              │
        │  寫出 data/deals.json（快照）                          │
        │        data/history.csv（累積歷史）                     │
        └───────────────┬───────────────────────────────────────┘
                        │  git push
                        ▼
        ┌───────────────────────────────────────────────────────┐
        │  倉庫 data/  ──►  Streamlit Community Cloud            │
        │                   app.py  前端呈現                     │
        └───────────────────────────────────────────────────────┘
```

---

## 4. 資料來源選型（無 Key）

| 方案 | 需要 Key | 覆蓋度 | 穩定性 | 結論 |
|---|---|---|---|---|
| **fast-flights 3.1.0（Google Flights 擷取）** | 否 | 全球、聚合各家 OTA | 中（Google 改版時需更新套件） | 本方案採用 |
| Kiwi.com 公開 GraphQL | 否 | 歐洲強 | 低（有 Cloudflare） | 可作為備援 provider |
| Amadeus / Kiwi Tequila 官方 API | **是** | 高 | 高 | 免費額度小，違背「無 Key」 |
| RSS / 特價郵件聚合 | 否 | 只看已發布的促銷 | 高 | 適合當「促銷情報」補充來源 |

**採用理由**：專案依 `fast-flights` PyPI 3.1.0 的免費 API 實作，使用 `create_query()` 與 `get_flights()`，不匯入或呼叫付費整合。擷取價格可能與 Google Flights 網頁不同，且會隨市場條件及時間變動。

**回傳資料模型（fast-flights 3.1.0）**
```
get_flights(query) -> ResultList[Flights]

Flights:
  type: str                 # Google 分組（best / 其他）
  price: int | None         # 票價，未提供報價時為 None
  airlines: list[str]       # 航司代碼，如 ["BR", "CI"]
  flights: list[SingleFlight]
  carbon: CarbonEmission

SingleFlight:
  from_airport / to_airport: Airport(name, code)
  departure / arrival: SimpleDatetime(date=(Y,M,D), time=(H,M))
  duration: int             # 分鐘
  plane_type: str
```

往返搜尋依 3.1.0 文件將去程及回程放在同一次 `round-trip` 查詢中，檢查每個回傳結果是否同時包含兩段，再採用來源提供的往返總價。開口行程目前把各段單程結果配對，票價為單程報價加總估算，並非保證的多城市聯程總價。

**風險**：Google 反爬與改版。對策見 §9。

---

## 5. 模組職責

| 檔案 | 職責 | 對外介面 |
|---|---|---|
| `config/routes.yaml` | 監控航線、幣別、出發日偏移、特價規則 | 純設定 |
| `src/config.py` | 讀設定、展開出發日清單 | `load_config() -> AppConfig` |
| `src/github_config.py` | 使用 Streamlit Secret 的 Fine-grained Token 回寫 `routes.yaml` | `update_routes(...)` |
| `src/provider.py` | 以 fast-flights 免費擷取；將單程、往返及分段開口結果正規化成 `Offer` | `search_itinerary(segments, trip_type) -> list[Offer]` |
| `src/store.py` | 快照 JSON 與歷史 CSV 讀寫 | `write_snapshot / append_history / load_history` |
| `src/pipeline.py` | 主流程、特價判定、輸出 | `python -m src.pipeline` |
| `app.py` | Streamlit 前端 | Streamlit Cloud 執行 |
| `.github/workflows/scrape.yml` | 排程與自動 commit | GitHub Actions |

**關鍵設計**：`provider.py` 定義統一的 `Offer` 介面，之後要換成 Kiwi 或官方 API，只要新增一個 `Provider` 子類，`pipeline` 與前端完全不用改。

---

## 6. 資料模型與儲存

**為什麼用 Git + 檔案，而不是資料庫？**
- 免伺服器、免維護、天然有版本歷史（可 diff 出票價變化）。
- 前端是唯讀的，檔案讀取已足夠。
- 資料量小（每天數百筆），CSV 完全夠用。

**`data/deals.json`（最新快照，前端主資料）**
```json
{
  "generated_at": "2026-11-16T02:00:00+00:00",
  "is_sample": false,
  "currency": "EUR",
  "summary": { "routes_checked": 3, "offers": 42, "deals": 5, "failures": [] },
  "offers": [
    {
      "from_airport": "TPE", "to_airport": "NRT", "departure_date": "2026-12-01",
      "price": 299, "currency": "EUR", "airlines": ["BR"], "stops": 0,
      "trip_type": "one-way", "price_basis": "fast_flights_quote", "return_date": null,
      "segments": [{"from_airport": "TPE", "to_airport": "NRT"}],
      "transfer_airports": [], "duration_minutes": 185,
      "departure_time": "08:40", "arrival_time": "12:45",
      "is_best": true, "is_deal": true,
      "baseline_median": 9800, "provider": "google-flights"
    }
  ],
  "deals": []
}
```

**`data/history.csv`（累積歷史，算基準與趨勢用）**
```
observed_at,from_airport,to_airport,departure_date,price,currency,airlines,stops,transfer_airports,duration_minutes,...
```
每一列＝「某次抓取、某航線、某出發日、某個行程的價格」。
歷史基準只比較相同幣別；舊版缺少幣別欄位的歷史不會混入 EUR 基準。

---

## 7. 「特價」判定演算法

對同一組 **(出發地, 目的地, 出發日)** 建立票價基準，用 `data/history.csv` 累積：

```
baseline = 該鍵過去所有觀測的價格清單

is_deal(price) =
       price < min(baseline)                                   # 創歷史新低
    or (len(baseline) >= 3 and price <= median(baseline) * (1 - 30%))  # 低於中位數 30%
```

- 用「同一出發日」的歷史做基準，而非跨日期，因為**票價會隨出發日接近而變化**，混在一起比會失真。
- 首次執行沒有歷史 → 只記錄、不判定（避免一裝好就全部誤報）。
- 門檻（`below_median_pct`、是否採歷史新低）都在 `config/routes.yaml` 調整。

---

## 8. 排程與部署

**Actions**（`.github/workflows/scrape.yml`）
- `cron: "0 1,7,13,19 * * *"`（UTC）＝ 台北時間 09:00 / 15:00 / 21:00 / 03:00。
- 支援 `workflow_dispatch` 手動觸發。
- 抓取後自動 commit `data/` 並 push（需在倉庫 Settings → Actions → Workflow permissions 選 **Read and write**）。

**Streamlit Community Cloud**
1. 登入 share.streamlit.io → New app → 選此倉庫與分支。
2. Main file path 填 `app.py`，它會自動 `pip install -r requirements.txt`。
3. 之後 Actions 每次 push `data/`，前端重新載入即可看到最新結果（app 內有 `ttl=600` 快取）。

---

## 9. 反爬與穩定性

- **節流**：每次查詢間隔 `search_delay_seconds`（預設 2 秒），控制每日請求量。
- **失敗隔離**：單一航線失敗不中斷整批，錯誤記入 `summary.failures`。
- **失敗重試**：可在外層加上指數退避重試（本 MVP 先記錄，不重試）。
- **代理**：`fast-flights` 的查詢函式支援 `proxy=` 參數；CI 大量請求被擋時可接 Bright Data 之類的住宅代理。
- **降級**：若 Google Flights 長期無法解析，切換到 Kiwi／官方 API provider（介面已預留）。

---

## 10. 合規與注意事項

- 爬取的是**公開票價頁面**，請控制頻率、勿商業轉售資料、遵守目標網站條款與當地法規。
- 票價變動極快，畫面呈現的價格**僅供參考**，下單前務必以航司／OTA 即時報價為準。
- Google Flights 為聚合價，可能含第三方 OTA 或自助轉機行程，實際購票前需確認行李與轉機條件。

---

## 11. 後續演進路線

1. **通知**：`is_deal` 為真時，推到 GitHub Issue／Email／飛書 Webhook（新增 `src/notify.py`）。
2. **更多 provider**：Kiwi GraphQL、Skyscanner RapidAPI（有 Key 時）。
3. **多段開口行程**：目前支援兩段式開口；之後可擴充成三段以上自訂城市順序。
4. **更聰明的基準**：改用「同航線 ±N 天」的價格分布，或以「出發前天數」建模。
5. **前端強化**：價格日曆熱力圖、訂閱條件後寄信通知。
