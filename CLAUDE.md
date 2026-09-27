# CLAUDE.md — 主人與狗・四檔追蹤

交接給 Claude Code 的專案說明。放在 repo 根目錄。

## 專案是什麼

Fred 幫岳父做的股票追蹤網頁。用科斯托蘭尼「主人與狗」的比喻：主人代表公司價值（EPS × 本益比，或均線），狗代表股價。網頁計算股價離主人多遠，再給出操作建議。

- 追蹤四檔：台積電 2330、聯發科 2454、欣興 3037、南亞科 2408
- 使用者：岳父，年長、用手機看。只有台積電有持股（4 張，其中核心 2 張）；其他三檔是觀察模式（0 張）
- Repo：`likeadragonRPG/master-dog`（GitHub，Public）
- 網址：https://likeadragonrpg.github.io/master-dog/ （GitHub Pages，main 分支、根目錄）
- Fred 不熟 Git／GitHub 操作。Claude Code 應直接幫他完成 commit 和 push，並用白話說明做了什麼

## 檔案結構

```
otc/                           # 上櫃股票資料（GitHub Actions 每天寫入，最近 3 個月）
index.html                     # 整個前端（單檔：HTML + CSS + JS，無 build 步驟）
data.json                      # 自動更新的資料（GitHub Actions 每天寫入）
scripts/update.py              # 抓資料腳本，只用 Python 內建模組
.github/workflows/update.yml   # 排程：平日台灣時間 15:30、19:00，另可手動執行
```

`update.py` 用 `dirname(dirname(__file__))` 找 repo 根目錄，所以**必須留在 `scripts/` 底下**。

## 資料流

1. GitHub Actions 執行 `update.py`：
   - 每日 K 線：證交所 `rwd/zh/afterTrading/STOCK_DAY`，失敗時改用舊版 `exchangeReport/STOCK_DAY`。一次抓一個月，每次請求間隔 sleep 4 秒。
     - 抓幾個月：資料少於 60 天時抓 3 個月；每月 1～7 號抓 2 個月；其他時候抓 1 個月。
     - 每檔最多保留 400 天。
   - EPS 與淨值：證交所 OpenAPI `v1/exchangeReport/BWIBBU_ALL`，用 `收盤 ÷ 本益比` 反推近四季 EPS，`收盤 ÷ 股價淨值比` 反推每股淨值。本益比為空（虧損）時不更新 EPS。
   - 每月資料 `rows`：只覆寫或新增「最新月份」那一筆。歷史月份固定不動，最多保留 72 個月。
2. commit `data.json` 並 push。這個 push 也會觸發 Pages 重新部署。
3. `index.html` 載入時 `fetch('data.json')`，合併進 localStorage 的狀態。
   - 如果 fetch 失敗（例如直接開檔案），就改用 HTML 內嵌的種子資料。

### data.json 格式

```json
{
  "updated": "2026-09-24",
  "stocks": {
    "2330": {
      "name": "台積電", "method": "pe",
      "rows": [["2021-10", 590, 22.11], ...],
      "bars": [["2026-06-30", o, h, l, c], ...],
      "fund": {"date": "...", "eps": 86.27, "bps": 248.0}
    },
    "2408": { "method": "pb", "rows": [["2021-10", 66.5, 7.4, 55.85], ...] }
  }
}
```

- `rows` 是每月一筆：`[年-月, 月收盤, 近四季EPS]`。南亞科（`method: "pb"`）多第 4 欄每股淨值。
- 2021-10 ～ 2026-09 的月資料來自 Goodinfo 河流圖資料。台積電的 EPS 是用法說會的季 EPS 自行加總近四季。

## 前端架構（index.html）

- localStorage key：`mdog-multi-v3`。結構是 `ALL = {cur, tab, st: {代號: 個股狀態}}`，全域變數 `S = ALL.st[ALL.cur]`。
- 舊版遷移：會讀取 `tsmc-master-dog-v2`，也就是 claude.ai 上舊版 artifact 的資料。
- 設定版本號 `ALL.ver`（目前 1）。舊的一次性旗標 `peDefault26`、`fwdEps1`、`dpe1`、`fwd2330`、`ttm1` 已整併，載入時會刪掉。之後要改預設值，就加 `ver<2` 的遷移。
- 個股狀態欄位：
  - `mode`：`pe` 或 `ma`
  - `eps`、`epsAuto`（手動改 EPS 後設為 false；清空欄位就恢復自動）
  - `pe`、`maN`
  - `near`、`far`（繩長 ±%）
  - `lots`、`core`、`held`（機動部位還持有幾張）
  - `cost`、`bars`、`trades`、`range`
  - `lt`：長期分頁設定，包含 `peMode`、`pe`、`fwd`、`scale`、`span`、`upd`
- 兩個分頁：
  - **每日操作**：K 線、偏離柱狀圖、繩子儀表、點位梯子、持股圓餅、情境滑桿。
  - **五年長期**：本益比（南亞科用本淨比）河流圖、五年偏離、分布直方圖、主人成長柱狀圖、自動產生的解讀文字。
- 自選股票（使用者自己輸入代號，支援上市、上櫃）：
  - `ALL.list` 是卡片順序（預設四檔也能移除、再一鍵加回），`ALL.custom[代號] = {name, pe, ym, close, eps, fetched}`，最多 8 檔。
  - 網頁**直接向證交所抓**：`rwd/zh/afterTrading/STOCK_DAY`、`BWIBBU` 都有 `Access-Control-Allow-Origin: *`。所有請求排隊，間隔 2 秒。
  - 加入時抓近 3 個月 K 線，再取近 2 年每季一個月的本益比，中位數當預設本益比；EPS = 同一天收盤 ÷ 本益比。沒有本益比（虧損）就改用 20 日均線。
  - 上櫃（`ALL.custom[代號].mkt === 'otc'`）：櫃買中心**沒有**開放跨網站讀取，所以由 `update.py` 的 `update_otc` 每天代抓全部上櫃股票，存成 `otc/年-月.json`（只留最近 3 個月）和 `otc/pe-hist.json`（過去兩年每季一天的本益比，每季重建）。網頁加入時先查 otc 檔，沒有才去證交所查上市。上櫃的本益比中位數 = pe-hist 的樣本 + 最近幾個月中位數。
  - 每次打開網頁補上新交易日（30 分鐘內抓過就跳過）。自選股票不在 `data.json`，只存在各自手機的 localStorage。
  - 自選股票沒有五年資料，`computeLong` 回 null，「五年長期」分頁停用並顯示原因。
- 頂部股票卡片：用 `withStock(code, fn)` 暫時切換 `S` 和 `ALL.cur` 來計算。注意 `aria-pressed` 要用切換前的 `cur` 判斷，不能在 `withStock` 裡面判斷。
- 圖表全部是手寫 SVG，寬度取容器的 `clientWidth`，視窗 resize 時重畫。**切到隱藏分頁時 `clientWidth` 是 0**，所以切換分頁後才能 render。

### 操作邏輯（`plan` / `advise`）

- 核心張數永遠不動；機動張數 = `lots - core`。
- 賣點：先在主人 +near% 賣第一張，再在 +far% 賣第二張。
- 買回點：已賣出 2 張時，回到主人位置買回一張；只剩 1 張沒買回時，跌到 -near% 再買回。
- 狗跌到 -far% 以下、而且沒有賣出過：回「不動作，先檢查主人」。
- `lots = 0` 是觀察模式：只會回「先不追」、「觀察中」或「可考慮分批買進」。

### 目前的預設值（Fred 確認過）

| 代號 | 主人 | EPS | 本益比 | near/far |
|---|---|---|---|---|
| 2330 | EPS × PE | 近四季（自動） | 26 | 6 / 12 |
| 2454 | EPS × PE | 近四季（自動） | 22 | 10 / 20 |
| 3037 | EPS × PE | 近四季（自動） | 25 | 10 / 20 |
| 2408 | EPS × PE | 近四季（自動） | 10 | 12 / 24 |

- **Fred 明確要求 EPS 用過去四季的歷史數字，不要用分析師預估。** 用預估 EPS 搭配歷史本益比，會把主人墊高，這是之前討論過並否決的做法。
- 本益比的估法：先取五年中位數（排除獲利低於高峰 40% 的失真月份），成長股往第 75 百分位靠。南亞科是景氣循環股，用上一次高峰期的本益比（2021-10 ～ 2022-09，約 6～10 倍），取上緣。
- 長期分頁的主人 = 近四季 EPS × 五年中位本益比，可以改成自訂。南亞科用淨值 × 本淨比。
- 河流圖的色帶 = 中位數 × [0.5, 0.75, 1, 1.3, 1.6, 2]。台積電預設一般刻度，其他三檔預設比例刻度。

## 設計規範

- 介面用**繁體中文**。台股慣例：**紅漲綠跌**。
- 手機優先，字要大，按鈕好點。圖表文字加底色描邊（paint-order halo），右側標籤要處理碰撞、自動錯開。
- 支援深淺色模式，顏色都用 CSS 變數（`--up`、`--down`、`--hold`、`--warn`、`--leash` 等）。
- 按不了的按鈕要 `disabled`，並在旁邊寫原因。岳父以為「按了沒反應」就是壞了。
- 報價與建議都不構成投資建議，頁尾已有聲明。

## 已知問題 / 待辦

- 只在模擬資料下測過，目前真實執行正常（第一次跑抓到 61 天）。
- Actions 已升級到 `checkout@v5`、`setup-python@v6`（Node 24），並固定 `ubuntu-24.04`。
- 使用者回饋（2026-09-27 LINE 群組）：最多人要「自己換股票」，已做成自選股票。另有人問「有沒有 app」（可做加到主畫面，Fred 說先不用）、台指期／選擇權（沒有 EPS，不適用）、自動下單（不做）。
- 資料只存在各自手機的 localStorage，不同裝置之間不同步，也沒有匯出／匯入功能。可以考慮加一個「備份碼」功能。
- `data.json` 的 K 棒會覆蓋使用者在同一天手動輸入的價格。這是刻意設計，以官方資料為準。
- `index.html` 內嵌的 `STOCKS` 和 `TSEED` 種子資料跟 `data.json` 重複，只當離線備援用，可以考慮精簡。
- 沒有自動化測試。之前是用 Playwright 腳本逐一點過所有按鈕，確認畫面有變化，建議補成正式測試。
- 自選股票的預設本益比用歷史中位數，景氣循環股（記憶體、群聯這類）獲利低谷時本益比很高，中位數會偏高、主人被墊高。原本四檔有排除失真月份的處理，自選股票還沒有。
- 其他可以做的方向：自選股票的五年長期分頁（需要補五年月資料）、財報公布日提醒、LINE 通知。

## 本機開發

```bash
python3 -m http.server 8000     # 然後打開 http://localhost:8000
python3 scripts/update.py       # 手動更新 data.json（需要能連到 twse.com.tw）
```

改完之後 push 到 main 就會自動部署，1～2 分鐘後生效。
