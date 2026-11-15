# 特價機票監控

用 **GitHub Actions 定時抓票價 + Git 當資料庫 + Streamlit 當前端** 的全免費特價機票搜索程序。
資料來源為 Google Flights（透過 `fast-flights`），**不需要任何 API Key**。

架構與設計理由詳見 [DESIGN.md](./DESIGN.md)。

## 檔案結構

```
flight-deals/
├── config/routes.yaml          # 監控航線與特價規則
├── src/
│   ├── config.py               # 讀設定、展開出發日
│   ├── provider.py             # Google Flights 資料來源（無 Key）
│   ├── store.py                # JSON 快照 + CSV 歷史
│   └── pipeline.py             # 主流程（python -m src.pipeline）
├── app.py                      # Streamlit 前端
├── data/
│   ├── deals.json              # 最新結果（目前為示範資料）
│   └── history.csv             # 累積歷史票價
└── .github/workflows/scrape.yml # 排程抓取並自動 commit
```

## 本機執行

需要 Python 3.10+。

```bash
pip install -r requirements.txt
python -m src.pipeline     # 抓票價，寫入 data/
streamlit run app.py       # 開啟前端 http://localhost:8501
```

`data/deals.json` 目前附的是**示範資料**（`is_sample: true`），第一次跑完 pipeline 就會被真實資料覆蓋。

## 部署到 GitHub

1. **建立倉庫並推上去**

   ```bash
   git init
   git add .
   git commit -m "feat: 特價機票監控 MVP"
   git branch -M main
   git remote add origin https://github.com/<你的帳號>/flight-deals.git
   git push -u origin main
   ```

2. **開啟 Actions 寫入權限**
   Settings → Actions → General → Workflow permissions → 選 **Read and write permissions**。
   否則 workflow 無法把抓取結果 push 回倉庫。

3. **手動觸發一次驗證**
   Actions → `scrape-flight-deals` → Run workflow。成功後 `data/` 會出現新的 commit。

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
Google 反爬或頁面改版。先確認 `pip install -U fast-flights`，並在 `routes.yaml` 提高 `search_delay_seconds`、減少航線數量。

**為什麼特價都是空的？**
首次執行沒有歷史基準，只會記錄、不判定特價。累積數次資料後才會開始標記。

**票價準嗎？**
Google Flights 為聚合價，可能含第三方 OTA 或自助轉機行程，僅供參考，下單前請以航司即時報價為準。
