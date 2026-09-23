"""Lottery-ticket options scanner.

For each ticker: expected move (ATM straddle), next earnings, and the cheap
out-of-the-money contracts ranked by how big a stock move they need to 10x / 30x.

Usage:
    .venv/bin/python scan.py                 # default watchlist
    .venv/bin/python scan.py NVDA META TSLA  # custom tickers
    .venv/bin/python scan.py --expiry 2026-09-25 NVDA
Data is Yahoo Finance (free, ~15 min delayed). Always confirm prices in Robinhood.
"""
import sys
import warnings
from datetime import date, datetime

warnings.filterwarnings("ignore")
import yfinance as yf  # noqa: E402

WATCHLIST = ["COST", "LEN", "NVDA", "META", "TSLA", "AMD", "PLTR", "AAPL", "AMZN", "SPY", "QQQ"]
MIN_PRICE, MAX_PRICE = 0.05, 0.60   # per-share premium (x100 = cost per contract)
MIN_OI = 100                         # skip dead contracts you can't sell
MAX_SPREAD = 0.50                    # skip if (ask-bid)/ask > 50%


def mid(row):
    b, a = row["bid"], row["ask"]
    return (b + a) / 2 if b > 0 and a > 0 else row["lastPrice"]


def next_earnings(t):
    try:
        cal = t.calendar
        d = cal.get("Earnings Date") if isinstance(cal, dict) else None
        if d:
            d = d[0] if isinstance(d, list) else d
            return d if d >= date.today() else None
    except Exception:
        pass
    return None


def scan(ticker, expiry=None):
    t = yf.Ticker(ticker)
    exps = t.options
    if not exps:
        return None
    exp = expiry if expiry in exps else exps[0]
    chain = t.option_chain(exp)
    price = t.fast_info["last_price"]

    calls, puts = chain.calls.copy(), chain.puts.copy()
    for df in (calls, puts):
        df["mid"] = df.apply(mid, axis=1)
    atm = calls.iloc[(calls["strike"] - price).abs().argsort()[:1]]["strike"].iloc[0]
    straddle = calls.loc[calls.strike == atm, "mid"].iloc[0] + puts.loc[puts.strike == atm, "mid"].iloc[0]
    em_pct = straddle / price * 100

    picks = []
    for side, df in (("CALL", calls), ("PUT", puts)):
        otm = df[(df.strike > price) if side == "CALL" else (df.strike < price)]
        for _, r in otm.iterrows():
            ask = r["ask"] if r["ask"] > 0 else r["mid"]
            if not (MIN_PRICE <= ask <= MAX_PRICE):
                continue
            if (r.get("openInterest") or 0) < MIN_OI:
                continue
            if r["bid"] <= 0 or (r["ask"] - r["bid"]) / r["ask"] > MAX_SPREAD:
                continue
            sign = 1 if side == "CALL" else -1
            # stock price at expiry needed for the contract to be worth 10x / 30x what you paid
            need10 = (r.strike + sign * 10 * ask - price) / price * 100
            need30 = (r.strike + sign * 30 * ask - price) / price * 100
            picks.append({
                "side": side, "strike": r.strike, "ask": ask, "bid": r["bid"],
                "oi": int(r.get("openInterest") or 0),
                "need10": abs(need10), "need30": abs(need30),
                "x_em30": abs(need30) / em_pct if em_pct else 99,
            })
    picks.sort(key=lambda p: p["x_em30"])
    return {"ticker": ticker, "price": price, "exp": exp, "em_pct": em_pct,
            "straddle": straddle, "earnings": next_earnings(t), "picks": picks[:4]}


def main():
    args = sys.argv[1:]
    expiry = None
    if "--expiry" in args:
        i = args.index("--expiry")
        expiry = args[i + 1]
        args = args[:i] + args[i + 2:]
    tickers = [a.upper() for a in args] or WATCHLIST

    print(f"\nScan @ {datetime.now():%a %b %d %I:%M %p}  (Yahoo data, ~15 min delayed)\n")
    results = []
    for tk in tickers:
        try:
            r = scan(tk, expiry)
            if r:
                results.append(r)
        except Exception as e:
            print(f"  {tk}: skipped ({e})")

    results.sort(key=lambda r: r["em_pct"], reverse=True)
    print(f"{'TICKER':<6} {'PRICE':>9} {'EXPIRY':>11} {'EXP MOVE':>9}  EARNINGS")
    for r in results:
        e = r["earnings"]
        flag = f"{e:%a %m/%d}" + ("  <-- before expiry" if e and str(e) <= r["exp"] else "") if e else "-"
        print(f"{r['ticker']:<6} {r['price']:>9.2f} {r['exp']:>11} {r['em_pct']:>8.1f}%  {flag}")

    print("\nBest lottery contracts (smallest move needed for 30x, as a multiple of the expected move)")
    print("  x_EM = 1.0 means the market's expected move alone gets you 30x; 2.0 means you need double that.\n")
    for r in results:
        if not r["picks"]:
            continue
        print(f"{r['ticker']}  (exp {r['exp']}, expected move ±{r['em_pct']:.1f}%)")
        for p in r["picks"]:
            print(f"   {p['side']:<4} ${p['strike']:<8g} ask ${p['ask']:.2f} (${p['ask']*100:.0f}/contract)"
                  f"  bid {p['bid']:.2f}  OI {p['oi']:>6}   10x needs {p['need10']:.1f}%  30x needs {p['need30']:.1f}%"
                  f"  (x_EM {p['x_em30']:.1f})")
        print()


if __name__ == "__main__":
    main()
