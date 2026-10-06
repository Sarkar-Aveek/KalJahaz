"""Rebuild data/market.csv from live public sources; keep the old file's values where no live source exists.

Live:     BDI, BCI, BPI, BSI, BHSI  daily Baltic Exchange closes scraped by github.com/yieldchaser/Shipping
          iron_ore_usd (IIV)       IMF iron-ore price via FRED (PIORECRUSDM), monthly
          china_pmi                NBS manufacturing PMI via DBnomics (NBS/M_A0B01/A0B0101), monthly
Bundled:  india_iip, world_trade_idx, vlsfo_usd_t  (no free, current feed; kept from the existing file)

Monthly values enter the week *after* their month ends, so the model never sees a figure before it was published.
Run on its own:  py live_data.py
"""
import csv, json, datetime as dt, urllib.request
from pathlib import Path

DATA = Path(__file__).parent / "data"
CSV, SOURCES = DATA / "market.csv", DATA / "market_sources.json"
START = dt.date(2023, 1, 6)  # first Friday of the window the model trains on
BALTIC = "https://raw.githubusercontent.com/yieldchaser/Shipping/main/data/indices/{}_historical.csv"
BALTIC_FILES = {"bdi": "bdiy", "bci": "cape", "bpi": "panama", "bsi": "suprama", "bhsi": "handysize"}
FRED_IRON = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=PIORECRUSDM"
NBS_PMI = "https://api.db.nomics.world/v22/series/NBS/M_A0B01/A0B0101?observations=1"
COLUMNS = ["date", "bdi", "bci", "bpi", "bsi", "bhsi", "china_pmi", "india_iip", "iron_ore_usd",
           "world_trade_idx", "vlsfo_usd_t"]


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "KalJahaz/1.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read().decode("utf-8")


def baltic_daily(name):
    rows = csv.DictReader(get(BALTIC.format(name)).splitlines())
    s = {}
    for r in rows:
        try:
            s[dt.date.fromisoformat(r["Date"])] = float(r["Index"].replace(",", ""))
        except (ValueError, KeyError, AttributeError):
            continue
    days = sorted(d for d, v in s.items() if v > 0)
    # drop one-day spikes that reverse next day (scrape errors), as in the original fetch script
    spikes = {b for a, b, c in zip(days, days[1:], days[2:])
              if abs(s[b] / s[a] - 1) > 0.2 and abs(s[c] / s[b] - 1) > 0.15 and (s[b] - s[a]) * (s[c] - s[b]) < 0}
    return {d: s[d] for d in days if d not in spikes}


def monthly_fred():
    out = {}
    for r in csv.DictReader(get(FRED_IRON).splitlines()):
        try:
            out[r["observation_date"][:7]] = float(r["PIORECRUSDM"])
        except (ValueError, KeyError):
            continue
    return out


def monthly_pmi():
    doc = json.loads(get(NBS_PMI))["series"]["docs"][0]
    return {p: float(v) for p, v in zip(doc["period"], doc["value"]) if v not in (None, "NA")}


def friday_close(daily, friday):
    """Last close in the week ending on this Friday."""
    for k in range(7):
        v = daily.get(friday - dt.timedelta(days=k))
        if v:
            return v
    return None


def published(monthly, friday):
    """Latest month that had ended before this week (no look-ahead)."""
    m = (friday.replace(day=1) - dt.timedelta(days=1)).strftime("%Y-%m")
    keys = [k for k in monthly if k <= m]
    return monthly[max(keys)] if keys else None


def refresh():
    """Rebuild the CSV. Returns the sources record; raises if the Baltic feed is unreachable."""
    old = {r["date"]: r for r in csv.DictReader(open(CSV, newline="", encoding="utf-8"))} if CSV.exists() else {}
    baltic = {k: baltic_daily(f) for k, f in BALTIC_FILES.items()}  # the core feed: let failure propagate
    monthly, notes = {}, {}
    for col, fn in (("iron_ore_usd", monthly_fred), ("china_pmi", monthly_pmi)):
        try:
            monthly[col] = fn()
        except Exception as e:  # keep going on the old file's values
            notes[col] = f"live fetch failed ({type(e).__name__}); using saved values"
    last = max(baltic["bdi"])
    end = last + dt.timedelta(days=(4 - last.weekday()) % 7)  # Friday of the latest week
    rows, fridays, d = [], [], START
    while d <= end:
        fridays.append(d)
        d += dt.timedelta(days=7)
    carry = {}
    for f in fridays:
        o = old.get(f.isoformat(), {})
        row = {"date": f.isoformat()}
        for k in BALTIC_FILES:
            row[k] = friday_close(baltic[k], f) or o.get(k) or carry.get(k)
        for col in ("iron_ore_usd", "china_pmi"):
            row[col] = (published(monthly[col], f) if col in monthly else None) or o.get(col) or carry.get(col)
        for col in ("india_iip", "world_trade_idx", "vlsfo_usd_t"):
            row[col] = o.get(col) or carry.get(col)  # bundled; carried forward past the file's end
        carry.update({k: v for k, v in row.items() if v})
        rows.append(row)
    missing = [c for c in COLUMNS if not rows[0].get(c)]
    if missing:
        raise RuntimeError(f"no data for {missing} at {rows[0]['date']}")
    with open(CSV, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows)

    def latest_monthly(col):
        return max(monthly[col]) if col in monthly else None

    src = {
        "refreshed": dt.datetime.now(dt.timezone.utc).isoformat(timespec="minutes"),
        "series": {
            **{k: dict(label=k.upper(), source="Baltic Exchange closes (yieldchaser/Shipping scrape)", live=True,
                       latest=max(baltic[k]).isoformat()) for k in BALTIC_FILES},
            "china_pmi": dict(label="China PMI", source="NBS via DBnomics", live="china_pmi" in monthly,
                              latest=latest_monthly("china_pmi"),
                              note=notes.get("china_pmi") or (f"live from {min(monthly['china_pmi'])}; earlier weeks use saved values" if "china_pmi" in monthly else None)),
            "iron_ore_usd": dict(label="IIV: iron-ore price", source="IMF via FRED (PIORECRUSDM)",
                                 live="iron_ore_usd" in monthly, latest=latest_monthly("iron_ore_usd"),
                                 note=notes.get("iron_ore_usd")),
            "india_iip": dict(label="India IIP", source="bundled series (no free current feed)", live=False),
            "world_trade_idx": dict(label="World trade volume", source="bundled series (no free current feed)", live=False),
            "vlsfo_usd_t": dict(label="Bunker price (VLSFO)", source="bundled series (no free current feed)", live=False),
        },
    }
    SOURCES.write_text(json.dumps(src, indent=1), encoding="utf-8")
    return src


if __name__ == "__main__":
    s = refresh()
    for k, v in s["series"].items():
        print(f"{k:16} {'LIVE' if v['live'] else 'saved':6} {v.get('latest') or '':12} {v['source']}")
