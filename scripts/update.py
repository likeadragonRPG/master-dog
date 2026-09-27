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

    # 4) 上櫃股票（給網頁的「自己加入股票」用）。失敗不影響上面四檔的更新
    try:
        update_otc(today)
    except Exception as e:
        print("otc failed:", e)


# ===== 上櫃股票 =====
# 櫃買中心不允許網頁直接讀取，所以由這裡每天代抓全部上櫃股票，存成 otc/年-月.json：
#   {"n": {代號: 名稱}, "s": {代號: [[日, 開, 高, 低, 收, 本益比或null], ...]}, "done": [已處理的日]}
# 另存 otc/pe-hist.json：過去兩年每季取一天的本益比，網頁用來算本益比中位數。
OTC = os.path.join(ROOT, "otc")
TPEX = "https://www.tpex.org.tw/www/zh-tw/afterTrading/"


def tpex_day(day):
    """某一天全部上櫃股票的 {代號: (名稱, 開, 高, 低, 收)} 和 {代號: 本益比}。抓不到回 None；休市回空的。"""
    ds = day.strftime("%Y/%m/%d")
    j = get_json(f"{TPEX}otc?date={ds}&type=EW&response=json")
    time.sleep(2)
    if not j or j.get("stat") != "ok":
        return None
    quotes = {}
    for tb in j.get("tables", []):
        f = [x.strip() for x in tb.get("fields", [])]
        if "代號" not in f or "收盤" not in f:
            continue
        i = {k: f.index(k) for k in ["代號", "名稱", "開盤", "最高", "最低", "收盤"]}
        for r in tb.get("data", []):
            code = r[i["代號"]].strip()
            c = num(r[i["收盤"]])
            if len(code) == 4 and code.isdigit() and c:  # 只收一般股票
                o, h, l = (num(r[i[k]]) for k in ["開盤", "最高", "最低"])
                quotes[code] = (r[i["名稱"]].strip(), o or c, h or c, l or c, c)
    return quotes, (tpex_pe(day) if quotes else {})


def tpex_pe(day):
    j = get_json(f"{TPEX}peQryDate?date={day.strftime('%Y/%m/%d')}&response=json")
    time.sleep(2)
    out = {}
    for tb in (j or {}).get("tables", []):
        f = [x.strip() for x in tb.get("fields", [])]
        if "股票代號" in f and "本益比" in f:
            for r in tb.get("data", []):
                pe = num(r[f.index("本益比")])
                if pe and pe > 0:
                    out[r[f.index("股票代號")].strip()] = pe
    return out


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def save_json(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))


def update_otc(today):
    os.makedirs(OTC, exist_ok=True)
    keep = set()
    for (y, m) in months_back(today, 3):
        name = f"{y}-{m:02d}.json"
        keep.add(name)
        path = os.path.join(OTC, name)
        mo = load_json(path, {"n": {}, "s": {}, "done": []})
        changed = False
        d = dt.date(y, m, 1)
        while d.month == m and d <= today:
            if d.weekday() < 5 and d.day not in mo["done"]:
                got = tpex_day(d)
                if got is not None:
                    quotes, pes = got
                    for code, (nm, o, h, l, c) in quotes.items():
                        mo["n"][code] = nm
                        mo["s"].setdefault(code, []).append([d.day, o, h, l, c, pes.get(code)])
                    # 今天還沒資料可能是還沒公布，下次再試；以前的日子沒資料就是休市
                    if quotes or d < today:
                        mo["done"].append(d.day)
                        changed = True
                    print("otc", d, len(quotes), "檔")
            d += dt.timedelta(days=1)
        if changed:
            for rows in mo["s"].values():
                rows.sort()
            save_json(path, mo)
    for f in os.listdir(OTC):  # 只保留最近 3 個月
        if f[:4].isdigit() and f not in keep:
            os.remove(os.path.join(OTC, f))

    # 本益比歷史：每季一天，兩年 8 個點；每季重建一次
    hp = os.path.join(OTC, "pe-hist.json")
    hist = load_json(hp, {})
    tag = f"{today.year}Q{(today.month - 1) // 3 + 1}"
    if hist.get("built") != tag:
        s = {}
        for (y, m) in months_back(today, 24)[3::3]:
            d = dt.date(y, m, 15)
            for _ in range(7):  # 遇到假日往後找
                pes = tpex_pe(d) if d.weekday() < 5 else {}
                if pes:
                    break
                d += dt.timedelta(days=1)
            for code, pe in pes.items():
                s.setdefault(code, []).append(pe)
            print("otc pe-hist", d, len(pes), "檔")
        save_json(hp, {"built": tag, "s": s})


if __name__ == "__main__":
    main()
