"""Live options dashboard.

Scans Yahoo's most-active stocks + today's biggest gainers/losers on a loop and
serves a dashboard at http://localhost:8790 that refreshes itself.

    .venv/bin/python server.py            # then open http://localhost:8790
    .venv/bin/python server.py --port 9000
"""
import json
import math
import sys
import threading
import time
import warnings
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from zoneinfo import ZoneInfo

warnings.filterwarnings("ignore")
import yfinance as yf  # noqa: E402

ROOT = Path(__file__).parent
ET = ZoneInfo("America/New_York")
ALWAYS = ["SPY", "QQQ", "IWM", "NVDA", "META", "TSLA", "AMD", "AAPL", "AMZN", "MSFT", "GOOGL", "COST"]
MIN_PRICE, MAX_PRICE = 0.03, 1.00   # per-share premium; x100 = cost per contract
MIN_OI = 50
MAX_SPREAD = 0.50
WORKERS = 6

state = {"updated": None, "scanning": False, "progress": [0, 0], "rows": [], "errors": 0}
lock = threading.Lock()


def universe():
    quotes = {}
    for screen, n in (("most_actives", 100), ("day_gainers", 25), ("day_losers", 25)):
        try:
            for q in yf.screen(screen, count=n)["quotes"]:
                quotes.setdefault(q["symbol"], {**q, "_list": screen})
        except Exception as e:
            print(f"screen {screen} failed: {e}")
    for s in ALWAYS:
        quotes.setdefault(s, {"symbol": s, "_list": "watchlist"})
    return quotes


def next_earnings(q):
    now = time.time()
    for k in ("earningsTimestamp", "earningsTimestampStart"):
        ts = q.get(k)
        if ts and ts >= now - 86400:
            return datetime.fromtimestamp(ts, ET).date().isoformat()
    return None


def pick_contracts(df, side, price, em_pct):
    out = []
    otm = df[(df.strike > price) if side == "call" else (df.strike < price)]
    for _, r in otm.iterrows():
        bid, ask = float(r["bid"] or 0), float(r["ask"] or 0)
        if bid <= 0 or ask <= 0 or not (MIN_PRICE <= ask <= MAX_PRICE):
            continue
        if (r.get("openInterest") or 0) < MIN_OI or (ask - bid) / ask > MAX_SPREAD:
            continue
        sign = 1 if side == "call" else -1
        need = lambda mult: abs((r.strike + sign * mult * ask - price) / price * 100)
        out.append({
            "strike": float(r.strike), "ask": ask, "bid": bid,
            "oi": int(r.get("openInterest") or 0), "vol": int(r.get("volume") or 0),
            "need2": need(2), "need10": need(10), "need30": need(30),
            "xem": need(30) / em_pct if em_pct else 99,
        })
    out.sort(key=lambda p: p["xem"])
    return out[:5]


def scan_one(sym, q):
    t = yf.Ticker(sym)
    exps = t.options
    if not exps:
        return None
    today = date.today().isoformat()
    exp = next((e for e in exps if e > today), exps[0])
    chain = t.option_chain(exp)
    price = q.get("regularMarketPrice") or t.fast_info["last_price"]
    chg = q.get("regularMarketChangePercent")
    if chg is None:
        fi = t.fast_info
        chg = (fi["last_price"] / fi["previous_close"] - 1) * 100

    calls, puts = chain.calls, chain.puts
    if calls.empty or puts.empty:
        return None
    atm = calls.iloc[(calls.strike - price).abs().argsort()[:1]].strike.iloc[0]
    c, p = calls[calls.strike == atm].iloc[0], puts[puts.strike == atm]
    if p.empty:
        return None
    p = p.iloc[0]
    mid = lambda r: (r.bid + r.ask) / 2 if r.bid > 0 and r.ask > 0 else r.lastPrice
    em_pct = (mid(c) + mid(p)) / price * 100
    if not all(math.isfinite(float(x)) for x in (price, chg, em_pct)) or em_pct <= 0:
        return None
    earn = next_earnings(q)
    if q["_list"] == "watchlist":  # not from a screener, so no quote fields
        try:
            info = t.info
            q = {**q, "shortName": info.get("shortName"), "regularMarketVolume": info.get("volume")}
            earn = next_earnings(info)
        except Exception:
            pass
    return {
        "sym": sym, "name": q.get("shortName") or sym, "list": q["_list"],
        "price": float(price), "chg": float(chg), "volume": int(q.get("regularMarketVolume") or 0),
        "exp": exp, "dte": (date.fromisoformat(exp) - date.today()).days,
        "em": float(em_pct), "earnings": earn, "earnBeforeExp": bool(earn and earn <= exp),
        "optVol": int(calls.volume.fillna(0).sum() + puts.volume.fillna(0).sum()),
        "calls": pick_contracts(calls, "call", price, em_pct),
        "puts": pick_contracts(puts, "put", price, em_pct),
    }


def run_scan():
    with lock:
        state["scanning"] = True
    quotes = universe()
    syms = list(quotes)
    rows, errors, done = [], 0, 0
    with lock:
        state["progress"] = [0, len(syms)]

    def job(s):
        try:
            return scan_one(s, quotes[s])
        except Exception:
            return "err"

    with ThreadPoolExecutor(WORKERS) as ex:
        for r in ex.map(job, syms):
            done += 1
            if r == "err":
                errors += 1
            elif r:
                rows.append(r)
            with lock:
                state["progress"] = [done, len(syms)]
    with lock:
        state.update(rows=rows, errors=errors, scanning=False,
                     updated=datetime.now(timezone.utc).isoformat())
    print(f"{datetime.now():%H:%M:%S} scanned {len(rows)} tickers ({errors} errors)")


def market_open():
    now = datetime.now(ET)
    return now.weekday() < 5 and (9, 0) <= (now.hour, now.minute) <= (16, 30)


def loop():
    while True:
        try:
            run_scan()
        except Exception as e:
            print("scan failed:", e)
            with lock:
                state["scanning"] = False
        time.sleep(180 if market_open() else 1800)


def clean(o):
    """Yahoo data has NaNs; browsers reject NaN in JSON."""
    if isinstance(o, float):
        return o if math.isfinite(o) else None
    if isinstance(o, dict):
        return {k: clean(v) for k, v in o.items()}
    if isinstance(o, list):
        return [clean(v) for v in o]
    return o


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(ROOT / "web"), **kw)

    def do_GET(self):
        if self.path.startswith("/api/scan"):
            with lock:
                body = json.dumps(clean(state)).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return
        super().do_GET()

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    if "--once" in sys.argv:  # used by the GitHub Action: scan once, write JSON, exit
        out = Path(sys.argv[sys.argv.index("--once") + 1])
        run_scan()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(clean(state)))
        sys.exit(0 if state["rows"] else 1)
    port = int(sys.argv[sys.argv.index("--port") + 1]) if "--port" in sys.argv else 8790
    threading.Thread(target=loop, daemon=True).start()
    print(f"Dashboard: http://localhost:{port}")
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
