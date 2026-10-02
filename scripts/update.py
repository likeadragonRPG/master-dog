"""每天收盤後由 GitHub Actions 執行：抓上市股票每日股價與最新本益比，更新 data.json。
只用 Python 內建模組，不需要安裝任何套件。"""
import json, os, time, urllib.request, datetime as dt

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data.json")
CODES = ["2330", "2454", "3037", "2408", "3189", "8046", "2327"]
STOCK_NAMES = {"3189": "景碩", "8046": "南電", "2327": "國巨"}
UA = {"User-Agent": "Mozilla/5.0 (master-dog updater)", "Accept": "application/json"}
TW = dt.timezone(dt.timedelta(hours=8))


def get_json(url, tries=2):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=15) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:  # 網路不穩就重試
            print(f"  retry {i+1}: {url} ({e})")
            if i + 1 < tries:
                time.sleep(2 * (i + 1))
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
    valid_response = False
    for u in urls:
        j = get_json(u)
        time.sleep(4)  # 證交所有流量限制，慢慢來
        if j and j.get("stat") == "OK" and isinstance(j.get("data"), list):
            valid_response = True
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
    # None 表示兩個端點都失敗；空 list 表示端點有回應但當月沒有資料。
    return [] if valid_response else None


def months_back(today, n):
    y, m = today.year, today.month
    res = []
    for _ in range(n):
        res.append((y, m))
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return res


def save_json(path, obj):
    """先完整寫入暫存檔，再原子替換，避免中斷留下截斷 JSON。"""
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def validate_bars(code, bars):
    if not bars:
        raise RuntimeError(f"{code} 沒有任何 K 線，取消發布。")
    dates = [b[0] for b in bars]
    if dates != sorted(set(dates)):
        raise RuntimeError(f"{code} K 線日期重複或未排序，取消發布。")
    for b in bars:
        if len(b) != 5 or not all(isinstance(v, (int, float)) for v in b[1:]) or b[4] <= 0:
            raise RuntimeError(f"{code} 出現格式錯誤的 K 線：{b!r}")


def twse_is_trading_day(day):
    """用證交所大盤日報分辨休市與漏抓；None 表示日曆端點本身失敗。"""
    if day.weekday() >= 5:
        return False
    ymd = day.strftime("%Y%m%d")
    j = get_json(f"https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?date={ymd}&type=ALLBUT0999&response=json")
    if j and j.get("stat") == "OK":
        return True
    if j and j.get("stat") == "很抱歉，沒有符合條件的資料!":
        return False
    return None


def update_twse(data, today):
    """建立並驗證完整候選資料，全部通過後才原子發布 data.json。"""
    for code in CODES:
        s = data["stocks"][code]
        have = {b[0]: b for b in s["bars"]}
        n_months = 3 if len(have) < 60 else (2 if today.day <= 7 else 1)
        for y, m in months_back(today, n_months):
            got = month_bars(code, y, m)
            if got is None:
                raise RuntimeError(f"{code} {y}-{m:02d} 兩個證交所端點都抓取失敗，取消發布。")
            for b in got:
                have[b[0]] = b
        s["bars"] = sorted(have.values())[-400:]
        validate_bars(code, s["bars"])
        print(code, "bars:", len(s["bars"]), "last:", s["bars"][-1])

    # 上市股票日期不應分歧。偵測單一 API 回舊資料或漏抓。
    latest = {c: data["stocks"][c]["bars"][-1][0] for c in CODES}
    if len(set(latest.values())) != 1:
        raise RuntimeError(f"上市股票最後交易日不一致，取消發布：{latest}")
    market_day = twse_is_trading_day(today)
    if market_day is True and any(day != today.isoformat() for day in latest.values()):
        raise RuntimeError(f"證交所確認今天有交易，但個股資料仍停在舊日期：{latest}；取消發布並等待下次重試。")

    # 本益比 / 股價淨值比 → 反推 EPS / BPS。缺資料時保留舊值，但標示為警告。
    pe_data = get_json("https://openapi.twse.com.tw/v1/exchangeReport/BWIBBU_ALL")
    warnings = []
    if market_day is None:
        warnings.append("無法確認證交所今天是否開市；股價日期已做跨股票一致性檢查。")
    if not isinstance(pe_data, list):
        warnings.append("證交所 BWIBBU_ALL 暫時無法取得；本次保留既有 EPS/BPS。")
        pe = {}
    else:
        pe = {r.get("Code"): r for r in pe_data if isinstance(r, dict)}
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
        else:
            warnings.append(f"{code} 本益比資料日期無法與收盤價配對；保留既有 EPS/BPS。")
        print(code, "fund:", s.get("fund"), "(本益比日期", day, ")")

    for code in CODES:
        s = data["stocks"][code]
        last = s["bars"][-1]
        ym = last[0][:7]
        rows = s.setdefault("rows", [])
        prev = rows[-1] if rows else None
        eps = s.get("fund", {}).get("eps", (prev[2] if prev else 0))
        new = [ym, last[4], eps]
        if s["method"] == "pb":
            new.append(s.get("fund", {}).get("bps", prev[3] if prev and len(prev) > 3 else None))
        if not rows:
            # 新增追蹤股第一次更新時，以本次回補到的各月收盤建立起始資料。
            month_last = {}
            for bar in s["bars"]:
                month_last[bar[0][:7]] = bar[4]
            rows.extend([[month, close, eps] for month, close in sorted(month_last.items())[-72:]])
        elif rows[-1][0] == ym:
            rows[-1] = new
        elif rows[-1][0] < ym:
            rows.append(new)
        s["rows"] = rows[-72:]

    data["updated"] = max(latest.values())
    data["health"] = {
        "twse": {"ok": True, "dates": latest},
        "market": {"date": today.isoformat(), "tradingDay": market_day},
        "warnings": warnings,
    }
    save_json(DATA, data)
    print("updated:", data["updated"])
    for warning in warnings:
        print(f"::warning::{warning}")


def main():
    with open(DATA, encoding="utf-8") as f:
        data = json.load(f)
    # 新加入的上市股第一次執行前先建立空殼，更新成功後才會公開到 data.json。
    for code, name in STOCK_NAMES.items():
        data["stocks"].setdefault(code, {
            "name": name, "method": "pe", "rows": [], "bars": [], "fund": {}
        })
    today = dt.datetime.now(TW).date()
    errors = []

    try:
        update_twse(data, today)
    except Exception as e:
        errors.append(f"TWSE 更新失敗：{e}")
        print(f"::error::{errors[-1]}")

    # OTC 與 TWSE 分開執行；其中一邊失敗，不會丟掉另一邊已成功的資料。
    try:
        pending = update_otc(today)
        sync_featured_otc(data)
        save_json(DATA, data)
        if pending:
            errors.append("TPEx 尚有未完成日期或本益比歷史，會保留重試資格：" + ", ".join(pending[:12]))
            print(f"::error::{errors[-1]}")
    except Exception as e:
        errors.append(f"TPEx 更新失敗：{e}")
        print(f"::error::{errors[-1]}")

    if errors:
        raise RuntimeError("資料來源未全部完成；已保存成功來源的資料，下一次排程會重試。")


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
    found_quote_table = False
    for tb in j.get("tables", []):
        f = [x.strip() for x in tb.get("fields", [])]
        if "代號" not in f or "收盤" not in f:
            continue
        found_quote_table = True
        i = {k: f.index(k) for k in ["代號", "名稱", "開盤", "最高", "最低", "收盤"]}
        for r in tb.get("data", []):
            code = r[i["代號"]].strip()
            c = num(r[i["收盤"]])
            if len(code) == 4 and code.isdigit() and c:  # 只收一般股票
                o, h, l = (num(r[i[k]]) for k in ["開盤", "最高", "最低"])
                quotes[code] = (r[i["名稱"]].strip(), o or c, h or c, l or c, c)
    if not found_quote_table:
        return None
    return quotes, (tpex_pe(day) if quotes else {})


def tpex_pe(day):
    j = get_json(f"{TPEX}peQryDate?date={day.strftime('%Y/%m/%d')}&response=json")
    time.sleep(2)
    if not j or j.get("stat") != "ok":
        return None
    out = {}
    found_table = False
    for tb in (j or {}).get("tables", []):
        f = [x.strip() for x in tb.get("fields", [])]
        if "股票代號" in f and "本益比" in f:
            found_table = True
            rows = tb.get("data", [])
            # 正常全市場資料有數百筆；過短的回應視為截斷或格式異常，留待重試。
            if not isinstance(rows, list) or len(rows) < 200:
                return None
            for r in rows:
                pe = num(r[f.index("本益比")])
                if pe and pe > 0:
                    out[r[f.index("股票代號")].strip()] = pe
    return out if found_table else None


def load_json(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, encoding="utf-8") as f:
            value = json.load(f)
    except (OSError, ValueError) as e:
        raise RuntimeError(f"{path} 存在但無法讀取有效 JSON，保留原檔並停止覆寫：{e}") from e
    if not isinstance(value, dict) or not isinstance(value.get("s"), dict):
        raise RuntimeError(f"{path} 結構不完整，保留原檔並停止覆寫。")
    return value


def sync_featured_otc(data):
    """把群聯最近三個月的櫃買日線複製到共用 data.json，讓新訪客直接看得到。"""
    code = "8299"
    months = []
    for y, m in months_back(dt.datetime.now(TW).date(), 3):
        path = os.path.join(OTC, f"{y}-{m:02d}.json")
        if not os.path.exists(path):
            continue
        month = load_json(path, {})
        name = month.get("n", {}).get(code)
        if name:
            months.append((f"{y}-{m:02d}", name, month.get("s", {}).get(code, [])))
    if not months:
        raise RuntimeError("櫃買資料中找不到群聯（8299），保留既有 data.json 並等待重試。")

    months.sort(key=lambda item: item[0])
    bars = []
    rows = []
    eps = None
    for ym, name, records in months:
        valid = [r for r in records if len(r) >= 5 and r[4] and r[4] > 0]
        valid.sort(key=lambda r: r[0])
        for day, o, h, l, close, *rest in valid:
            bars.append([f"{ym}-{day:02d}", o, h, l, close])
            pe = rest[0] if rest else None
            if pe and pe > 0:
                eps = round(close / pe, 2)
        if valid:
            last = valid[-1]
            pe = last[5] if len(last) > 5 else None
            month_eps = round(last[4] / pe, 2) if pe and pe > 0 else (eps or 0)
            rows.append([ym, last[4], month_eps])
    if not bars:
        raise RuntimeError("群聯（8299）櫃買資料沒有有效日線，保留既有 data.json 並等待重試。")
    data["stocks"][code] = {
        "name": months[-1][1], "method": "pe", "rows": rows[-72:],
        "bars": bars[-400:], "fund": {"date": bars[-1][0], "eps": eps or 0},
        "market": "otc",
    }


def update_otc(today):
    os.makedirs(OTC, exist_ok=True)
    keep = set()
    pending = []
    for (y, m) in months_back(today, 3):
        name = f"{y}-{m:02d}.json"
        keep.add(name)
        path = os.path.join(OTC, name)
        mo = load_json(path, {"n": {}, "s": {}, "done": [], "pe_done": []})
        # 舊版沒有獨立記錄本益比是否抓成功；以有實際 PE 值的日期作為已完成，其餘日期會重抓。
        if "pe_done" not in mo:
            pe_days = {r[0] for rows in mo.get("s", {}).values() for r in rows if len(r) > 5 and r[5] is not None}
            mo["pe_done"] = sorted(pe_days)
        changed = False
        d = dt.date(y, m, 1)
        while d.month == m and d <= today:
            if d.weekday() < 5:
                has_quotes = any(any(len(row) > 0 and row[0] == d.day for row in rows)
                                 for rows in mo.get("s", {}).values())
                needs_quotes = d.day not in mo["done"]
                needs_pe = has_quotes and d.day not in mo["pe_done"]
                if needs_quotes:
                    got = tpex_day(d)
                    if got is None:
                        pending.append(f"{d} 行情端點失敗")
                    else:
                        quotes, pes = got
                        for code, (nm, o, h, l, c) in quotes.items():
                            mo["n"][code] = nm
                            rows = mo["s"].setdefault(code, [])
                            rows[:] = [r for r in rows if r[0] != d.day]
                            rows.append([d.day, o, h, l, c, pes.get(code) if pes is not None else None])
                        if quotes or d < today:
                            mo["done"] = sorted(set(mo["done"]) | {d.day})
                            changed = True
                        else:
                            pending.append(f"{d} 行情尚未公布")
                        if quotes and pes is not None:
                            mo["pe_done"] = sorted(set(mo["pe_done"]) | {d.day})
                            changed = True
                        elif quotes:
                            pending.append(f"{d} 本益比端點失敗")
                        print("otc", d, len(quotes), "檔", "PE", "ok" if pes is not None or not quotes else "pending")
                elif needs_pe:
                    pes = tpex_pe(d)
                    if pes is None:
                        pending.append(f"{d} 本益比端點失敗")
                    else:
                        for code, rows in mo["s"].items():
                            for row in rows:
                                if row[0] == d.day:
                                    while len(row) < 6:
                                        row.append(None)
                                    row[5] = pes.get(code)
                        mo["pe_done"] = sorted(set(mo["pe_done"]) | {d.day})
                        changed = True
                        print("otc PE retry", d, len(pes), "檔")
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
    hist = load_json(hp, {"s": {}})
    tag = f"{today.year}Q{(today.month - 1) // 3 + 1}"
    if hist.get("schema") != 2 or hist.get("built") != tag:
        s = {}
        complete = True
        for (y, m) in months_back(today, 24)[::3]:
            d = dt.date(y, m, 15)
            sample_ok = False
            for _ in range(10):  # 假日、尚未公告或暫時逾時時往後找交易日
                if d.weekday() < 5:
                    pes = tpex_pe(d)
                    if pes is None:
                        d += dt.timedelta(days=1)
                        continue
                    sample_ok = True
                    break
                d += dt.timedelta(days=1)
            if not sample_ok:
                complete = False
                pending.append(f"PE 歷史樣本 {y}-{m:02d} 未抓齊")
                continue
            for code, pe in pes.items():
                s.setdefault(code, []).append(pe)
            print("otc pe-hist", d, len(pes), "檔")
        if complete:
            save_json(hp, {"schema": 2, "built": tag, "s": s})
        else:
            print("::error::上櫃 PE 歷史尚未抓齊，保留舊檔並於下次重試。")

    return pending


if __name__ == "__main__":
    main()
