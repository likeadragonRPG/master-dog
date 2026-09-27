"""每天收盤後由 GitHub Actions 執行：抓四檔股票的每日股價與最新本益比，更新 data.json。
只用 Python 內建模組，不需要安裝任何套件。"""
import json, os, time, urllib.request, datetime as dt

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data.json")
CODES = ["2330", "2454", "3037", "2408"]
UA = {"User-Agent": "Mozilla/5.0 (master-dog updater)", "Accept": "application/json"}
TW = dt.timezone(dt.timedelta(hours=8))


def get_json(url, tries=3):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:  # 網路不穩就重試
            print(f"  retry {i+1}: {url} ({e})")
            time.sleep(5 * (i + 1))
    return None


def num(x):
    try:
        return float(str(x).replace(",", "").replace("X", "").strip())
    except ValueError:
        return None


def roc_to_iso(s):
    y, m, d = s.strip().split("/")
    return f"{int(y) + 1911:04d}-{int(m):02d}-{int(d):02d}"


def month_bars(code, year, month):
    """證交所『個股日成交資訊』：一次一個月。"""
    ymd = f"{year}{month:02d}01"
    urls = [
        f"https://www.twse.com.tw/rwd/zh/afterTrading/STOCK_DAY?date={ymd}&stockNo={code}&response=json",
        f"https://www.twse.com.tw/exchangeReport/STOCK_DAY?response=json&date={ymd}&stockNo={code}",
    ]
    for u in urls:
        j = get_json(u)
        time.sleep(4)  # 證交所有流量限制，慢慢來
        if j and j.get("stat") == "OK" and j.get("data"):
            f = j.get("fields", [])
            idx = {n: f.index(n) for n in ["日期", "開盤價", "最高價", "最低價", "收盤價"] if n in f}
            if len(idx) < 5:
                idx = {"日期": 0, "開盤價": 3, "最高價": 4, "最低價": 5, "收盤價": 6}
            out = []
            for row in j["data"]:
                o, h, l, c = (num(row[idx[k]]) for k in ["開盤價", "最高價", "最低價", "收盤價"])
                if c:
                    out.append([roc_to_iso(row[idx["日期"]]), o or c, h or c, l or c, c])
            return out
    return []


def months_back(today, n):
    y, m = today.year, today.month
    res = []
    for _ in range(n):
        res.append((y, m))
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return res


def main():
    with open(DATA, encoding="utf-8") as f:
        data = json.load(f)
    today = dt.datetime.now(TW).date()

    # 1) 每日股價
    for code in CODES:
        s = data["stocks"][code]
        have = {b[0]: b for b in s["bars"]}
        n_months = 3 if len(have) < 60 else (2 if today.day <= 7 else 1)
        for (y, m) in months_back(today, n_months):
            for b in month_bars(code, y, m):
                have[b[0]] = b
        s["bars"] = sorted(have.values())[-400:]
        print(code, "bars:", len(s["bars"]), "last:", s["bars"][-1])

    # 2) 最新本益比 / 股價淨值比 → 反推近四季 EPS 與每股淨值
    #    本益比常比股價晚一天公布，所以要用「本益比那一天」的收盤價來反推，日期對不上就不更新
    pe = get_json("https://openapi.twse.com.tw/v1/exchangeReport/BWIBBU_ALL") or []
    pe = {r.get("Code"): r for r in pe if isinstance(r, dict)}
    for code in CODES:
        s = data["stocks"][code]
        r = pe.get(code)
        closes = {b[0]: b[4] for b in s["bars"]}
        day = roc_to_iso(f"{r['Date'][:-4]}/{r['Date'][-4:-2]}/{r['Date'][-2:]}") if r and r.get("Date") else None
        close = closes.get(day)
        if r and close:
            per, pbr = num(r.get("PEratio")), num(r.get("PBratio"))
            fund = s.setdefault("fund", {})
            if per and per > 0:
                fund["eps"] = round(close / per, 2)
            if pbr and pbr > 0:
                fund["bps"] = round(close / pbr, 2)
            fund["date"] = day
        print(code, "fund:", s.get("fund"), "(本益比日期", day, ")")

    # 3) 每月資料（五年長期）：本月用最新收盤與最新 EPS／淨值
    for code in CODES:
        s = data["stocks"][code]
        last = s["bars"][-1]
        ym = last[0][:7]
        rows = s["rows"]
        prev = rows[-1]
        eps = s.get("fund", {}).get("eps", prev[2])
        new = [ym, last[4], eps]
        if s["method"] == "pb":
            new.append(s.get("fund", {}).get("bps", prev[3] if len(prev) > 3 else None))
        if rows[-1][0] == ym:
            rows[-1] = new
        elif rows[-1][0] < ym:
            rows.append(new)
        s["rows"] = rows[-72:]

    data["updated"] = max(data["stocks"][c]["bars"][-1][0] for c in CODES)
    with open(DATA, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    print("updated:", data["updated"])


if __name__ == "__main__":
    main()
