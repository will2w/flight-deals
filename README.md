# 特價機票監控

用 **GitHub Actions 定時抓票價 + Git 當資料庫 + Streamlit 當前端** 的特價機票搜索程序。
票價資料來源為 Google Flights（透過 `fast-flights`），**不需要航班 API Key**；航班票價與歷史比較統一使用 **EUR**。

架構與設計理由詳見 [DESIGN.md](./DESIGN.md)。

## 檔案結構

```
flight-deals/
├── config/routes.yaml          # 監控航線與特價規則
├── src/
│   ├── config.py               # 讀設定、展開出發日
│   ├── github_config.py        # 從 Streamlit 將航線設定保存回 GitHub
│   ├── provider.py             # Google Flights 資料來源（無 Key）
│   ├── store.py                # JSON 快照 + CSV 歷史
│   └── pipeline.py             # 主流程（python -m src.pipeline）
├── app.py                      # Streamlit 前端（即時搜尋 / 監控清單 / 價格走勢）
├── data/
│   ├── deals.json              # 最新結果（目前為示範資料）
│   └── history.csv             # 累積歷史票價
└── .github/workflows/scrape.yml # 排程抓取並自動 commit
```

## 前端功能

`app.py` 分成三個頁籤：

- **即時搜尋**：使用完整 IATA 機場清單搜尋出發地與目的地，可選**單程、往返、目的地開口（A→B、C→A）或出發地開口（A→B、B→C）**。選日期、艙等與轉機限制後查 Google Flights；可比較去程彈性日期，查看每段時長及轉機地點，並下載 CSV。同條件 30 分鐘內走快取。
- **監控清單**：在頁面直接新增／移除航線；有設定 GitHub Token 時，可按「保存並更新排程」直接更新 GitHub 的 `config/routes.yaml`，Actions 下次排程即採用新航線。也可下載設定檔後在 GitHub 網頁手動更新。
- **價格走勢**：以 EUR 歷史價格繪製「某航線某出發日」的最低票價走勢。

常用機場顯示繁體中文名稱，其餘機場由 `airportsdata` IATA 資料集補齊。行程表以小時分別顯示去程／回程時長，並列出各段轉機機場；直飛行程顯示「直飛」。往返及開口方案的價格是整趟行程總價。

### 啟用網頁直接保存航線

在 GitHub 建立 Fine-grained personal access token，只授權此倉庫的 **Contents: Read and write**。到 Streamlit Cloud → App settings → Secrets 加入：

```toml
GITHUB_TOKEN = "github_pat_..."
GITHUB_REPOSITORY = "你的帳號/flight-deals"
GITHUB_BRANCH = "main"
```

Token 只放在 Streamlit Secrets，不要寫進程式碼或提交到倉庫。若不設定 Token，仍可在頁面編輯清單並下載 `routes.yaml`，再用 GitHub 網頁上傳。

## 部署到 GitHub

1. **建立倉庫並用 GitHub 網頁上傳檔案**

   建立公開倉庫 `flight-deals`，不要初始化 README。進入倉庫 → Add file → Upload files，把專案內容上傳到倉庫根目錄，再按 Commit changes。

2. **開啟 Actions 寫入權限**
   Settings → Actions → General → Workflow permissions → 選 **Read and write permissions**。
   否則 workflow 無法把抓取結果 push 回倉庫。

3. **手動觸發一次驗證**
   Actions → `scrape-flight-deals` → Run workflow。成功後 `data/` 會出現新的 commit。

   第一次執行或切換幣別後，建議手動跑一次，讓最新快照改為 EUR。

## 部署前端（Streamlit Community Cloud）

1. 前往 <https://share.streamlit.io>，用 GitHub 登入。
2. New app → 選這個倉庫與 `main` 分支，Main file path 填 `app.py`。
3. 部署完即可看到最新資料；Actions 每次更新 `data/` 後重新載入頁面即為最新（app 內建 10 分鐘快取）。

> 注意：**Streamlit 無法部署在 GitHub Pages**（Pages 只托管靜態檔案，Streamlit 需要 Python 伺服器）。
> 若你要的是純靜態頁面，可改將 `data/deals.json` 發布為 Pages 上的公開 JSON 端點。

## 調整設定

編輯 [config/routes.yaml](./config/routes.yaml)：

- `routes`：要監控的航線（三碼機場代碼）。
- `days_ahead`：從今天起，往後第 N 天出發（預設 14 / 30 / 60 天）。
- `top_n`：每條航線每個出發日保留幾個最便宜行程。
- `deal_rules.below_median_pct`：低於歷史中位數多少 % 視為特價。

## 常見問題

**抓不到資料 / 全部失敗？**
Google 反爬或頁面改版。可在 GitHub 網頁提高 `config/routes.yaml` 的 `search_delay_seconds`、減少航線數量；若套件需要更新，在 `requirements.txt` 提高 `fast-flights` 版本後 Commit。

**為什麼特價都是空的？**
首次執行沒有歷史基準，只會記錄、不判定特價。累積數次資料後才會開始標記。

**票價準嗎？**
Google Flights 為聚合價，可能含第三方 OTA 或自助轉機行程，僅供參考，下單前請以航司即時報價為準。
