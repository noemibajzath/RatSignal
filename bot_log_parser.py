"""Parse stats from live bot log files (new sub-accounts on master 63915).

Old sub-accounts wrote to trade_log_5.db / trade_log_6.db. New sub-accounts
do NOT — instead Quick-Bite-2 writes its trades to its log file, and
Slipstream-2 writes [TRADE LOG] events to its log.

This module parses those files and returns the same stat shape as the DB-based
_fetch_account_stats() in data.py, so the rest of the pipeline is unchanged.
"""

import os
import re
import itertools
from datetime import datetime, timezone
from typing import Any, Dict, List

# ============================================================
# Quick-Bite-2 log parser
# ============================================================
_QB_LOG = "/opt/quick-bite-2/logs/quick_bite.log"

_RE_QB_EXIT = re.compile(
    r"^(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2}).*EXIT: (\w+) @ \$([\d.]+), "
    r"PnL=\$([+-][\d.]+) \((WIN|LOSS)\), held (\d+) bars"
)


def parse_quick_bite_stats(start_date: str, initial_balance: float) -> Dict[str, Any]:
    """Parse Quick-Bite-2 log into stats dict matching DB shape."""
    pnls = []
    if not os.path.exists(_QB_LOG):
        return _empty_stats()

    with open(_QB_LOG, encoding="utf-8", errors="replace") as f:
        for line in f:
            m = _RE_QB_EXIT.search(line)
            if not m:
                continue
            ts_date, ts_time, sym, px, pnl, result, bars = m.groups()
            if ts_date < start_date:
                continue
            pnls.append({
                "ts": ts_date + " " + ts_time,
                "date": ts_date,
                "pnl": float(pnl),
                "result": result,
                "bars": int(bars),
            })
    return _build_stats(pnls, start_date, initial_balance)


# ============================================================
# Slipstream-2 log parser
# ============================================================
_SS_LOG = "/opt/slipstream-2/logs/copytrading.log"

_RE_SS_CLOSE = re.compile(
    r"^(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2}).*\[TRADE LOG\] CLOSE "
    r"(\w+) (LONG|SHORT) (\w+).*PnL=\$([+-][\d.]+)"
)
_RE_SS_DECREASE = re.compile(
    r"^(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2}).*\[TRADE LOG\] DECREASE "
    r"(\w+) (LONG|SHORT) (\w+).*PnL=\$([+-][\d.]+)"
)


def parse_slipstream_stats(start_date: str, initial_balance: float) -> Dict[str, Any]:
    """Parse Slipstream-2 log into stats dict matching DB shape.

    Counts CLOSE events as trades (denominator for win rate).
    Both CLOSE and DECREASE PnL feed into total_pnl and equity curve.
    """
    if not os.path.exists(_SS_LOG):
        return _empty_stats()

    events = []
    closes = []

    with open(_SS_LOG, encoding="utf-8", errors="replace") as f:
        for line in f:
            m = _RE_SS_CLOSE.search(line)
            if m:
                ts_date, ts_time, wallet, side, sym, pnl = m.groups()
                if ts_date < start_date:
                    continue
                pnl_f = float(pnl)
                ev = {
                    "ts": ts_date + " " + ts_time,
                    "date": ts_date,
                    "pnl": pnl_f,
                    "result": "WIN" if pnl_f > 0 else "LOSS",
                    "bars": 0,
                    "kind": "close",
                }
                events.append(ev)
                closes.append(ev)
                continue
            m = _RE_SS_DECREASE.search(line)
            if m:
                ts_date, ts_time, wallet, side, sym, pnl = m.groups()
                if ts_date < start_date:
                    continue
                events.append({
                    "ts": ts_date + " " + ts_time,
                    "date": ts_date,
                    "pnl": float(pnl),
                    "result": "DECREASE",
                    "bars": 0,
                    "kind": "decrease",
                })

    return _build_stats(closes, start_date, initial_balance, all_events=events)


# ============================================================
# Common builder
# ============================================================
def _empty_stats() -> Dict[str, Any]:
    return {
        "total_pnl": 0.0, "today_pnl": 0.0, "total_trades": 0, "today_trades": 0,
        "win_count": 0, "loss_count": 0, "win_rate": 0.0, "avg_bars_held": 0.0,
        "sharpe": 0.0, "max_drawdown": 0.0, "equity_curve": [], "equity_curve_pct": [],
        "recent_trades": [], "direction_breakdown": [], "exit_breakdown": [],
        "has_data": False, "error": None, "cumulative_roi_pct": 0.0,
    }


def _build_stats(closes: List[Dict], start_date: str, initial_balance: float,
                 all_events: List[Dict] = None) -> Dict[str, Any]:
    if all_events is None:
        all_events = closes

    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    total_pnl = sum(e["pnl"] for e in all_events)
    today_pnl = sum(e["pnl"] for e in all_events if e["date"] == today_str)
    total_trades = len(closes)
    today_trades = len([c for c in closes if c["date"] == today_str])
    wins = len([c for c in closes if c["pnl"] > 0])
    losses = len([c for c in closes if c["pnl"] <= 0])
    wr = (wins / max(wins + losses, 1)) * 100 if (wins + losses) > 0 else 0.0
    avg_bars = (sum(c["bars"] for c in closes) / len(closes)) if closes else 0.0

    sorted_events = sorted(all_events, key=lambda e: e["ts"])
    eq_dollar = list(itertools.accumulate(e["pnl"] for e in sorted_events))

    if initial_balance > 0:
        eq_pct = [(v / initial_balance) * 100 for v in eq_dollar]
    else:
        eq_pct = []

    daily = {}
    for e in sorted_events:
        daily.setdefault(e["date"], 0.0)
        daily[e["date"]] += e["pnl"]
    running_bal = initial_balance if initial_balance > 0 else 100.0
    cum_roi = 0.0
    for d in sorted(daily.keys()):
        if running_bal > 0:
            cum_roi += (daily[d] / running_bal) * 100
        running_bal += daily[d]

    sharpe = 0.0
    if len(closes) >= 2:
        try:
            import numpy as np
            arr = np.array([c["pnl"] for c in closes], dtype=float)
            std = arr.std()
            if std > 0:
                raw = arr.mean() / std
                sharpe = round(max(min(raw * (96 * 252) ** 0.5, 50.0), -50.0), 2)
        except Exception:
            pass

    max_dd = 0.0
    max_dd_pct = 0.0
    if eq_dollar:
        peak = 0.0
        peak_pct = 0.0
        for i, v in enumerate(eq_dollar):
            if v > peak:
                peak = v
            dd = peak - v
            if dd > max_dd:
                max_dd = dd
            if eq_pct:
                if eq_pct[i] > peak_pct:
                    peak_pct = eq_pct[i]
                dd_pct = peak_pct - eq_pct[i]
                if dd_pct > max_dd_pct:
                    max_dd_pct = dd_pct

    return {
        "total_pnl": round(total_pnl, 4),
        "today_pnl": round(today_pnl, 4),
        "total_trades": total_trades,
        "today_trades": today_trades,
        "win_count": wins,
        "loss_count": losses,
        "win_rate": round(wr, 1),
        "avg_bars_held": round(avg_bars, 1),
        "sharpe": sharpe,
        "max_drawdown": round(max_dd, 4),
        "max_drawdown_pct": round(max_dd_pct, 2),
        "equity_curve": eq_dollar,
        "equity_curve_pct": [round(v, 4) for v in eq_pct],
        "recent_trades": [
            {"timestamp": c["ts"], "pnl": c["pnl"], "result": c["result"], "bars_held": c["bars"]}
            for c in closes[-20:]
        ],
        "direction_breakdown": [],
        "exit_breakdown": [],
        "has_data": total_trades > 0 or len(all_events) > 0,
        "error": None,
        "cumulative_roi_pct": round(cum_roi, 2),
    }
