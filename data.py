"""Per-account data module for ratsignal dashboard.

Fetches stats from per-account trade_log DBs and Lighter API.
"""

import asyncio
import itertools
import os
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from temporary.ratsignal.account_registry import ACCOUNTS, ACCOUNTS_BY_ID, get_db_path
from temporary.ratsignal.bot_log_parser import parse_quick_bite_stats, parse_slipstream_stats

# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------
_cache: Dict[str, tuple] = {}
_DB_CACHE_TTL = 30       # 30s for cheap DB queries
_API_CACHE_TTL = 300      # 5 min for expensive Lighter API calls

# PnL tracking start date — all stats counted from this date onwards
PNL_START_DATE = "2026-04-23"

# Per-account PnL start date overrides (e.g. after bug fixes)
PNL_START_OVERRIDES = {
    5: "2026-04-23",  # Slipstream-2: deployed on new master 63915 SUB1
    6: "2026-04-23",  # Quick-Bite-2: deployed on new master 63915 SUB2
}

# Starting balances at deploy date — used for balance-based ROI calculation
# (avoids contamination from old combo strategy cumulative PnL in the Lighter API)
INITIAL_BALANCES = {
    5: 152.00,   # Slipstream-2: starting collateral on new sub on 2026-04-23
    6: 152.00,   # Quick-Bite-2: starting collateral on new sub on 2026-04-23
}

# Paths to live bot state files — these have authoritative trading PnL
# (not affected by deposits/withdrawals, only tracks closed trade PnL)
BOT_STATE_PATHS = {
    6: "/opt/quick-bite-2/state/bot_state.json",  # Quick-Bite-2 on new sub
}


def _read_bot_state_pnl(account_id: int) -> float | None:
    """Read total_pnl from bot's state file. Returns None if unavailable."""
    import json
    state_path = BOT_STATE_PATHS.get(account_id)
    if not state_path or not os.path.exists(state_path):
        return None
    try:
        with open(state_path) as f:
            state = json.load(f)
        return float(state.get("total_pnl", 0))
    except Exception:
        return None


def _get_cached(key: str, ttl: int, fetcher):
    """Generic cache-or-fetch."""
    now = time.time()
    if key in _cache:
        data, ts = _cache[key]
        if now - ts < ttl:
            return data
    try:
        data = fetcher()
    except Exception as e:
        data = {"error": str(e)}
    _cache[key] = (data, now)
    return data


# ---------------------------------------------------------------------------
# Per-account DB stats
# ---------------------------------------------------------------------------
def get_account_stats(account_id: int) -> Dict[str, Any]:
    """Fetch stats for a single account from its trade_log DB."""
    return _get_cached(
        f"stats_{account_id}", _DB_CACHE_TTL,
        lambda: _fetch_account_stats(account_id)
    )


def _fetch_account_stats(account_id: int) -> Dict[str, Any]:
    """Read trade stats. For new sub-accounts (id 5,6) read from bot logs;
    for legacy/external accounts read from SQLite trade_log DB."""

    # New master 63915 sub-accounts: read directly from live bot logs
    if account_id == 5:
        start = PNL_START_OVERRIDES.get(5, PNL_START_DATE)
        ib = INITIAL_BALANCES.get(5, 152.0)
        return parse_slipstream_stats(start, ib)
    if account_id == 6:
        start = PNL_START_OVERRIDES.get(6, PNL_START_DATE)
        ib = INITIAL_BALANCES.get(6, 152.0)
        return parse_quick_bite_stats(start, ib)

    result = {
        "account_id": account_id,
        "total_pnl": 0.0,
        "today_pnl": 0.0,
        "total_trades": 0,
        "today_trades": 0,
        "win_count": 0,
        "loss_count": 0,
        "win_rate": 0.0,
        "avg_bars_held": 0.0,
        "sharpe": 0.0,
        "max_drawdown": 0.0,
        "equity_curve": [],
        "recent_trades": [],
        "direction_breakdown": [],
        "exit_breakdown": [],
        "has_data": False,
        "error": None,
    }

    db_path = get_db_path(account_id)
    if not os.path.exists(db_path):
        result["error"] = "No trade log yet"
        return result

    try:
        with sqlite3.connect(db_path, timeout=5) as conn:
            conn.row_factory = sqlite3.Row
            today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            start = PNL_START_DATE

            # Aggregate stats — only trades from PNL_START_DATE onwards
            row = conn.execute("""
                SELECT
                    COALESCE(SUM(pnl), 0) as total_pnl,
                    COALESCE(SUM(CASE WHEN timestamp >= ? THEN COALESCE(pnl, 0) ELSE 0 END), 0) as today_pnl,
                    COUNT(*) as total_trades,
                    SUM(CASE WHEN timestamp >= ? THEN 1 ELSE 0 END) as today_trades,
                    SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins,
                    SUM(CASE WHEN pnl <= 0 THEN 1 ELSE 0 END) as losses,
                    AVG(bars_held) as avg_bars
                FROM trades WHERE action = 'CLOSE' AND timestamp >= ?
            """, (today_str, today_str, start)).fetchone()

            if row and row["total_trades"] and row["total_trades"] > 0:
                result["has_data"] = True
                result["total_pnl"] = round(row["total_pnl"] or 0, 4)
                result["today_pnl"] = round(row["today_pnl"] or 0, 4)
                result["total_trades"] = row["total_trades"] or 0
                result["today_trades"] = row["today_trades"] or 0
                result["win_count"] = row["wins"] or 0
                result["loss_count"] = row["losses"] or 0
                total = (row["wins"] or 0) + (row["losses"] or 0)
                result["win_rate"] = round((row["wins"] or 0) / total * 100, 1) if total > 0 else 0.0
                result["avg_bars_held"] = round(row["avg_bars"] or 0, 1)
            else:
                # Check if DB has ANY data (pre-start-date trades exist)
                any_row = conn.execute("SELECT COUNT(*) as c FROM trades WHERE action = 'CLOSE'").fetchone()
                if any_row and any_row["c"] > 0:
                    result["has_data"] = True  # Account is active, just no trades since start date

            # Daily ROI calculation: sum of (daily_pnl / balance_at_start_of_day)
            # Deposit/withdrawal proof — balance reconstructed from trades only
            daily_rows = conn.execute("""
                SELECT DATE(timestamp) as day, SUM(pnl) as day_pnl
                FROM trades WHERE action = 'CLOSE' AND timestamp >= ?
                GROUP BY DATE(timestamp) ORDER BY day ASC
            """, (start,)).fetchall()

            _init_bal = INITIAL_BALANCES.get(account_id, 0)
            _running_bal = _init_bal if _init_bal > 0 else 100.0
            _cumulative_roi = 0.0
            for dr in daily_rows:
                day_pnl = dr["day_pnl"] or 0
                if _running_bal > 0:
                    _cumulative_roi += (day_pnl / _running_bal) * 100
                _running_bal += day_pnl
            result["cumulative_roi_pct"] = round(_cumulative_roi, 2)

            # Equity curve — only from PNL_START_DATE
            pnl_rows = conn.execute("""
                SELECT pnl FROM trades
                WHERE action = 'CLOSE' AND timestamp >= ?
                ORDER BY id ASC
            """, (start,)).fetchall()
            pnl_list = [r["pnl"] for r in pnl_rows if r["pnl"] is not None]
            if pnl_list:
                result["equity_curve"] = list(itertools.accumulate(pnl_list))
                result["sharpe"] = _compute_sharpe(pnl_list)
                result["max_drawdown"] = _compute_max_drawdown(result["equity_curve"])

            # Per-direction/combo breakdown — from PNL_START_DATE
            dir_rows = conn.execute("""
                SELECT combo_name, direction,
                    COUNT(*) as trades,
                    COALESCE(SUM(pnl), 0) as total_pnl,
                    ROUND(AVG(CASE WHEN pnl > 0 THEN 1.0 ELSE 0.0 END) * 100, 1) as win_rate,
                    AVG(bars_held) as avg_bars
                FROM trades WHERE action = 'CLOSE' AND timestamp >= ?
                GROUP BY combo_name, direction
            """, (start,)).fetchall()
            result["direction_breakdown"] = [dict(r) for r in dir_rows]

            # Exit reason breakdown — from PNL_START_DATE
            exit_rows = conn.execute("""
                SELECT exit_reason, COUNT(*) as count, COALESCE(SUM(pnl), 0) as total_pnl
                FROM trades WHERE action = 'CLOSE' AND exit_reason IS NOT NULL AND timestamp >= ?
                GROUP BY exit_reason ORDER BY count DESC
            """, (start,)).fetchall()
            result["exit_breakdown"] = [dict(r) for r in exit_rows]

            # Recent trades — from PNL_START_DATE
            recent = conn.execute("""
                SELECT timestamp, symbol, direction, pnl, pnl_pct, bars_held, exit_reason, combo_name
                FROM trades WHERE action = 'CLOSE' AND timestamp >= ?
                ORDER BY timestamp DESC LIMIT 20
            """, (start,)).fetchall()
            result["recent_trades"] = [dict(r) for r in recent]

    except Exception as e:
        result["error"] = str(e)

    return result


def _compute_sharpe(pnl_list: List[float]) -> float:
    """Compute Sharpe ratio from PnL series."""
    if len(pnl_list) < 2:
        return 0.0
    try:
        import numpy as np
        arr = np.array(pnl_list, dtype=float)
        std = arr.std()
        if std == 0:
            return 0.0
        raw = arr.mean() / std
        # Annualize: 15-min bars, ~96 bars/day, ~252 trading days
        annualized = raw * (96 * 252) ** 0.5
        return round(max(min(annualized, 50.0), -50.0), 2)
    except Exception:
        return 0.0


def _compute_max_drawdown(equity_curve: List[float]) -> float:
    """Compute max drawdown from cumulative PnL curve."""
    if not equity_curve:
        return 0.0
    peak = 0.0
    max_dd = 0.0
    for eq in equity_curve:
        if eq > peak:
            peak = eq
        dd = peak - eq
        if dd > max_dd:
            max_dd = dd
    return round(max_dd, 4)


# ---------------------------------------------------------------------------
# Lighter API — real exchange PnL
# ---------------------------------------------------------------------------
def get_exchange_pnl() -> Dict[str, Any]:
    """Fetch real PnL from Lighter exchange for all accounts (cached 5min)."""
    return _get_cached("exchange_pnl_all", _API_CACHE_TTL, _fetch_all_exchange_pnl)


def _fetch_all_exchange_pnl() -> Dict[str, Any]:
    """Fetch real trade PnL from Lighter accountPnL API for all 5 active bot accounts."""
    today_midnight_dt = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    today_midnight_ts = int(today_midnight_dt.timestamp())
    now_ms = int(time.time() * 1000)

    try:
        import lighter
    except ImportError:
        return {"per_account": {}, "total_pnl": 0.0, "today_pnl": 0.0,
                "error": "lighter SDK not available"}

    async def _fetch_all():
        per_account: Dict[int, Dict] = {}
        total_pnl = 0.0
        total_today = 0.0
        errors = []

        for acct in ACCOUNTS:
            aid = acct["id"]
            env_keys = acct["env_keys"]
            pk = os.getenv(env_keys["api_private_key"], "")
            ai = int(os.getenv(env_keys["account_index"], "0"))
            ki = int(os.getenv(env_keys["api_key_index"], "0"))

            # Per-account start date (override or global default)
            acct_start = PNL_START_OVERRIDES.get(aid, PNL_START_DATE)
            start_date_dt = datetime.strptime(acct_start, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            start_date_ts = int(start_date_dt.timestamp())
            query_start_ms = (start_date_ts - 86400) * 1000

            if not pk:
                per_account[aid] = {"pnl": 0.0, "today_pnl": 0.0, "error": "no_key"}
                continue

            try:
                signer = lighter.SignerClient(
                    url="https://mainnet.zklighter.elliot.ai",
                    account_index=ai,
                    api_private_keys={ki: pk},
                )
                token = signer.create_auth_token_with_expiry()
                token = token[0] if isinstance(token, tuple) else token

                config = lighter.Configuration(host="https://mainnet.zklighter.elliot.ai")
                api_client = lighter.ApiClient(config)
                acc_api = lighter.AccountApi(api_client)

                try:
                    result = await acc_api.pnl(
                        by="index", value=str(ai), resolution="1d",
                        start_timestamp=query_start_ms, end_timestamp=now_ms,
                        count_back=500, auth=token,
                    )
                    entries = result.to_dict().get("pnl", [])
                finally:
                    await api_client.close()

                if not entries:
                    per_account[aid] = {"pnl": 0.0, "today_pnl": 0.0, "error": None}
                    continue

                # Baseline: last cumulative trade_pnl at/before PNL_START_DATE
                baseline_pnl = 0.0
                for e in entries:
                    if e.get("timestamp", 0) <= start_date_ts:
                        baseline_pnl = float(e.get("trade_pnl", 0))

                # Today baseline: last cumulative trade_pnl at/before midnight UTC
                today_baseline = baseline_pnl
                for e in entries:
                    if e.get("timestamp", 0) <= today_midnight_ts:
                        today_baseline = float(e.get("trade_pnl", 0))

                last_pnl = float(entries[-1].get("trade_pnl", 0))
                delta = last_pnl - baseline_pnl
                today_delta = last_pnl - today_baseline

                # Compute sum of daily ROIs: daily_pnl / balance_at_start_of_day
                # This is deposit/withdrawal proof
                _ib = INITIAL_BALANCES.get(aid, 0)
                _daily_roi_sum = 0.0
                if _ib and _ib > 0 and len(entries) >= 2:
                    _running_bal = _ib
                    _prev_cum_pnl = baseline_pnl
                    for _ei in range(1, len(entries)):
                        _e = entries[_ei]
                        _e_ts = _e.get("timestamp", 0)
                        # Only count entries after the per-account start date
                        if _e_ts <= start_date_ts:
                            _prev_cum_pnl = float(_e.get("trade_pnl", 0))
                            continue
                        _cur_cum_pnl = float(_e.get("trade_pnl", 0))
                        _day_pnl = _cur_cum_pnl - _prev_cum_pnl
                        if _running_bal > 0 and abs(_day_pnl) > 0.0001:
                            _daily_roi_sum += (_day_pnl / _running_bal) * 100
                        _running_bal += _day_pnl
                        _prev_cum_pnl = _cur_cum_pnl

                per_account[aid] = {
                    "pnl": round(delta, 4),
                    "today_pnl": round(today_delta, 4),
                    "daily_roi_pct": round(_daily_roi_sum, 2),
                    "error": None,
                }
                total_pnl += delta
                total_today += today_delta

            except Exception as e:
                per_account[aid] = {"pnl": 0.0, "today_pnl": 0.0, "error": str(e)[:80]}
                errors.append(f"acct{aid}: {str(e)[:40]}")

            await asyncio.sleep(1.5)

        return {
            "per_account": per_account,
            "total_pnl": round(total_pnl, 4),
            "today_pnl": round(total_today, 4),
            "error": "; ".join(errors) if errors else None,
        }

    try:
        return asyncio.run(_fetch_all())
    except Exception as e:
        return {"per_account": {}, "total_pnl": 0.0, "today_pnl": 0.0,
                "error": str(e)[:80]}


# ---------------------------------------------------------------------------
# Lighter API — live balance + positions
# ---------------------------------------------------------------------------
def get_account_live(account_id: int) -> Dict[str, Any]:
    """Fetch live balance + open positions from Lighter API (cached)."""
    return _get_cached(
        f"live_{account_id}", _API_CACHE_TTL,
        lambda: _fetch_account_live(account_id)
    )


def _fetch_account_live(account_id: int) -> Dict[str, Any]:
    """Fetch balance + positions from Lighter API for one account."""
    result = {"balance": 0.0, "open_positions": [], "error": None}

    acct = ACCOUNTS_BY_ID.get(account_id)
    if not acct:
        result["error"] = "Unknown account"
        return result

    env_keys = acct["env_keys"]
    api_key = os.getenv(env_keys["api_private_key"], "")
    if not api_key:
        result["error"] = "No API key"
        return result

    try:
        from pmkit.config import load_env
        load_env(os.path.join(_PROJECT_ROOT, ".env"))
        from pmkit.exchanges.lighter import LighterClient

        account_index = int(os.getenv(env_keys["account_index"], "0"))
        api_key_index = int(os.getenv(env_keys["api_key_index"], "0"))

        async def _do():
            c = LighterClient(
                api_private_key=api_key,
                account_index=account_index,
                api_key_index=api_key_index,
                max_leverage=1,
            )
            try:
                await c.connect()
                bal = await c.get_balance()
                pos = await c.get_positions()
                return bal, pos
            finally:
                try:
                    await c.disconnect()
                except Exception:
                    pass

        bal, positions = asyncio.run(asyncio.wait_for(_do(), timeout=5.0))
        result["balance"] = round(bal, 2)
        for p in positions:
            result["open_positions"].append({
                "symbol": p.symbol,
                "side": p.side,
                "quantity": p.quantity,
                "entry_price": p.entry_price,
                "unrealized_pnl": round(p.unrealized_pnl, 4) if p.unrealized_pnl else 0.0,
            })
    except Exception as e:
        result["error"] = str(e)

    return result


# ---------------------------------------------------------------------------
# All accounts — aggregate
# ---------------------------------------------------------------------------
def get_all_accounts_data() -> Dict[str, Any]:
    """Fetch data for all 5 active bot accounts."""
    return _get_cached("all_accounts", _DB_CACHE_TTL, _fetch_all_accounts_data)


def _fetch_all_accounts_data() -> Dict[str, Any]:
    """Build the complete dashboard data structure."""
    accounts_data = []
    total_pnl = 0.0
    total_today_pnl = 0.0
    total_trades = 0
    total_wins = 0
    total_losses = 0
    total_balance = 0.0
    total_positions = 0
    accounts_live = 0

    # Fetch real exchange PnL (cached 5min)
    exchange_pnl = get_exchange_pnl()
    has_exchange_pnl = bool(exchange_pnl.get("per_account")) and not exchange_pnl.get("error")

    for acct in ACCOUNTS:
        aid = acct["id"]
        stats = get_account_stats(aid)
        live = get_account_live(aid)

        # PnL priority: bot state file > exchange API > DB sum(pnl)
        # Bot state = authoritative trading PnL (deposit/withdrawal proof)
        acct_ex = exchange_pnl.get("per_account", {}).get(aid, {})
        use_exchange = has_exchange_pnl and acct_ex.get("error") is None
        initial_bal = INITIAL_BALANCES.get(aid)
        current_bal = live.get("balance", 0)

        bot_pnl = _read_bot_state_pnl(aid)
        if bot_pnl is not None:
            # Bot state file: authoritative trading PnL (deposit/withdrawal proof)
            display_pnl = round(bot_pnl, 4)
        elif use_exchange:
            display_pnl = acct_ex.get("pnl", 0)
        elif initial_bal and current_bal > 0:
            # Balance-based fallback (for Slipstream, Long Accumulator)
            display_pnl = round(current_bal - initial_bal, 4)
        else:
            display_pnl = stats.get("total_pnl", 0)
        display_today = acct_ex.get("today_pnl", 0) if use_exchange else stats.get("today_pnl", 0)

        # Aggregate
        total_pnl += display_pnl
        total_today_pnl += display_today
        total_trades += stats.get("total_trades", 0)
        total_wins += stats.get("win_count", 0)
        total_losses += stats.get("loss_count", 0)
        total_balance += live.get("balance", 0)
        total_positions += len(live.get("open_positions", []))
        if stats.get("has_data") or live.get("balance", 0) > 0:
            accounts_live += 1

        # Compact sparkline data (last 50 points), prefer %-based curve
        eq_pct = stats.get("equity_curve_pct") or []
        eq_dollar = stats.get("equity_curve", [])
        eq = eq_pct if eq_pct else eq_dollar
        if len(eq) > 50:
            step = len(eq) // 50
            eq_compact = eq[::step][:50]
        else:
            eq_compact = eq

        # Calculate days live from PNL_START_OVERRIDES
        from datetime import datetime as _dt
        _start_str = PNL_START_OVERRIDES.get(aid, PNL_START_DATE)
        _start_dt = _dt.strptime(_start_str, "%Y-%m-%d")
        _days_live = (_dt.utcnow() - _start_dt).days

        accounts_data.append({
            "id": aid,
            "display_name": acct["display_name"],
            "description": acct["description"],
            "days_live": _days_live,
            "initial_balance": initial_bal or 0,
            "pnl_start_date": _start_str,
            "position_size": acct["position_size"],
            "long_combo": acct["long_combo"],
            "short_combo": acct["short_combo"],
            "total_pnl": display_pnl,
            "pnl_pct": round((display_pnl / initial_bal * 100), 2) if initial_bal and initial_bal > 0 else 0.0,
            "today_pnl": display_today,
            "db_pnl": stats.get("total_pnl", 0),
            "pnl_source": "exchange" if use_exchange else "db",
            "total_trades": stats.get("total_trades", 0),
            "win_rate": stats.get("win_rate", 0),
            "sharpe": stats.get("sharpe", 0),
            "max_drawdown": stats.get("max_drawdown", 0),
            "max_drawdown_pct": stats.get("max_drawdown_pct", 0),
            "avg_bars_held": stats.get("avg_bars_held", 0),
            "equity_curve": eq_compact,
            "has_data": stats.get("has_data", False),
            "balance": live.get("balance", 0),
            "open_positions": live.get("open_positions", []),
            "positions_count": len(live.get("open_positions", [])),
            "status": "live" if (stats.get("has_data") or live.get("balance", 0) > 0) else "waiting",
            "api_error": live.get("error"),
            "db_error": stats.get("error"),
        })

    # ---- Synthetic DUO card: BOTH bots run from ONE shared wallet ----
    # Slipstream + Quick Bite both use fixed-size positions; their combined worst-case
    # margin requirement fits inside a single deposit, so the buyer can run both from
    # one wallet. Duo ROI = (pnl_5 + pnl_6) / wallet_balance = sum of individual ROIs.
    # NOT counted in aggregates (would double-count); accounts_total stays len(ACCOUNTS).
    _acct5 = next((a for a in accounts_data if a["id"] == 5), None)
    _acct6 = next((a for a in accounts_data if a["id"] == 6), None)
    if _acct5 and _acct6:
        _duo_init = _acct5.get("initial_balance") or INITIAL_BALANCES.get(5, 152.0)
        _duo_pnl = _acct5.get("total_pnl", 0) + _acct6.get("total_pnl", 0)
        _duo_today = _acct5.get("today_pnl", 0) + _acct6.get("today_pnl", 0)
        _duo_pct = round(_duo_pnl / _duo_init * 100, 2) if _duo_init > 0 else 0.0
        # Combined equity curve = elementwise SUM of two %-curves (same wallet)
        _eq5 = _acct5.get("equity_curve") or []
        _eq6 = _acct6.get("equity_curve") or []
        _n = min(len(_eq5), len(_eq6))
        _duo_curve = [round(_eq5[i] + _eq6[i], 4) for i in range(_n)]

        accounts_data.append({
            "id": 56,
            "display_name": "Duo",
            "description": "DUO: Slipstream + Quick Bite egyutt",
            "days_live": min(_acct5.get("days_live", 0), _acct6.get("days_live", 0)),
            "initial_balance": _duo_init,
            "pnl_start_date": _acct5.get("pnl_start_date"),
            "position_size": (_acct5.get("position_size", 0) or 0) + (_acct6.get("position_size", 0) or 0),
            "long_combo": None,
            "short_combo": None,
            "total_pnl": round(_duo_pnl, 4),
            "pnl_pct": _duo_pct,
            "today_pnl": round(_duo_today, 4),
            "db_pnl": _acct5.get("db_pnl", 0) + _acct6.get("db_pnl", 0),
            "pnl_source": "synthetic",
            "total_trades": _acct5.get("total_trades", 0) + _acct6.get("total_trades", 0),
            "win_rate": 0,
            "sharpe": 0,
            "max_drawdown": 0,
            "max_drawdown_pct": 0,
            "avg_bars_held": 0,
            "equity_curve": _duo_curve,
            "has_data": True,
            "balance": (_acct5.get("balance", 0) or 0) + (_acct6.get("balance", 0) or 0),
            "open_positions": [],
            "positions_count": (_acct5.get("positions_count", 0) or 0) + (_acct6.get("positions_count", 0) or 0),
            "status": "live",
            "api_error": None,
            "db_error": None,
        })

    overall_wr = round(total_wins / (total_wins + total_losses) * 100, 1) if (total_wins + total_losses) > 0 else 0.0

    return {
        "accounts": accounts_data,
        "aggregate": {
            "total_balance": round(total_balance, 2),
            "total_pnl": round(total_pnl, 4),
            "today_pnl": round(total_today_pnl, 4),
            "total_trades": total_trades,
            "overall_win_rate": overall_wr,
            "total_positions": total_positions,
            "accounts_live": accounts_live,
            "accounts_total": len(ACCOUNTS),
            "pnl_source": "exchange" if has_exchange_pnl else "db",
        },
        "last_updated": datetime.now(timezone.utc).isoformat(),
    }


def _build_duo_detail() -> Dict[str, Any]:
    """Synthetic Duo account detail = #5 Slipstream + #6 Quick Bite combined.

    Treats the buyer as running both bots from a single shared wallet.
    Combined ROI = weighted average of individual ROIs.
    Combined dollars-earned = sum of individual dollars-earned.
    """
    s5 = get_account_stats(5)
    s6 = get_account_stats(6)
    l5 = get_account_live(5)
    l6 = get_account_live(6)
    ib5 = INITIAL_BALANCES.get(5, 152.0)
    ib6 = INITIAL_BALANCES.get(6, 152.0)
    # Single-wallet model: one wallet covers both bots, fixed-size positions
    # share the margin budget. ROI computed on the shared wallet base.
    duo_init = ib5

    pnl5 = _read_bot_state_pnl(5)
    if pnl5 is None:
        pnl5 = s5.get("total_pnl", 0)
    pnl6 = _read_bot_state_pnl(6)
    if pnl6 is None:
        pnl6 = s6.get("total_pnl", 0)
    total_pnl = pnl5 + pnl6
    cum_pct = round(total_pnl / duo_init * 100, 2) if duo_init > 0 else 0.0

    # Combined %-equity curve = elementwise SUM (same wallet, percentages add)
    eq5 = s5.get("equity_curve_pct") or []
    eq6 = s6.get("equity_curve_pct") or []
    n = min(len(eq5), len(eq6))
    duo_eq_pct = [round(eq5[i] + eq6[i], 4) for i in range(n)]

    # Trade-level merge
    total_trades = (s5.get("total_trades", 0) or 0) + (s6.get("total_trades", 0) or 0)
    win5 = s5.get("win_count", 0) or 0
    win6 = s6.get("win_count", 0) or 0
    loss5 = s5.get("loss_count", 0) or 0
    loss6 = s6.get("loss_count", 0) or 0
    closed = (win5 + win6 + loss5 + loss6)
    win_rate = round((win5 + win6) / closed * 100, 1) if closed > 0 else 0.0

    # Drawdown on a single shared wallet: worst-case = sum of both DDs
    # (would only realize if both bottomed simultaneously; LONG/SHORT
    # being partially anticorrelated usually keeps it lower in practice).
    max_dd_pct = (s5.get("max_drawdown_pct", 0) or 0) + (s6.get("max_drawdown_pct", 0) or 0)
    max_dd_dollar = (s5.get("max_drawdown", 0) or 0) + (s6.get("max_drawdown", 0) or 0)

    # Sharpe: simple avg
    sharpe = round(((s5.get("sharpe", 0) or 0) + (s6.get("sharpe", 0) or 0)) / 2, 2)

    avg_bars5 = s5.get("avg_bars_held", 0) or 0
    avg_bars6 = s6.get("avg_bars_held", 0) or 0
    weights5 = s5.get("total_trades", 0) or 0
    weights6 = s6.get("total_trades", 0) or 0
    if weights5 + weights6 > 0:
        avg_bars = round((avg_bars5 * weights5 + avg_bars6 * weights6) / (weights5 + weights6), 1)
    else:
        avg_bars = 0

    # Recent trades — merge & sort
    rt5 = s5.get("recent_trades", []) or []
    rt6 = s6.get("recent_trades", []) or []
    merged_trades = sorted(
        list(rt5) + list(rt6),
        key=lambda t: t.get("timestamp", ""),
        reverse=True,
    )[:20]

    pnl_start = PNL_START_OVERRIDES.get(5, PNL_START_DATE)
    _sd = datetime.strptime(pnl_start, "%Y-%m-%d")
    days_live = (datetime.utcnow() - _sd).days

    return {
        "id": 56,
        "display_name": "Duo",
        "description": "DUO PACK: Slipstream + Quick Bite egyutt, kozos tarcarol",
        "position_size": None,
        "leverage": "3x + 10x",
        "long_combo": None,
        "short_combo": None,
        "pnl_since": pnl_start,
        "days_live": days_live,
        "initial_balance": duo_init,
        "pnl_pct": cum_pct,
        "stats": {
            "total_pnl": round(total_pnl, 4),
            "today_pnl": round((s5.get("today_pnl", 0) or 0) + (s6.get("today_pnl", 0) or 0), 4),
            "today_trades": (s5.get("today_trades", 0) or 0) + (s6.get("today_trades", 0) or 0),
            "total_trades": total_trades,
            "win_count": win5 + win6,
            "loss_count": loss5 + loss6,
            "win_rate": win_rate,
            "sharpe": sharpe,
            "max_drawdown": round(max_dd_dollar, 4),
            "max_drawdown_pct": round(max_dd_pct, 2),
            "avg_bars_held": avg_bars,
            "cumulative_roi_pct": cum_pct,
            "equity_curve_pct": duo_eq_pct,
            "equity_curve": duo_eq_pct,
            "recent_trades": merged_trades,
            "direction_breakdown": {},
            "exit_breakdown": {},
            "has_data": True,
            "error": None,
        },
        "live": {
            "balance": (l5.get("balance", 0) or 0) + (l6.get("balance", 0) or 0),
            "open_positions": (l5.get("open_positions", []) or []) + (l6.get("open_positions", []) or []),
            "error": None,
        },
        "components": [
            {"id": 5, "name": "Slipstream", "pnl": round(pnl5, 2), "pnl_pct": round(pnl5/ib5*100, 2) if ib5 else 0},
            {"id": 6, "name": "Quick Bite", "pnl": round(pnl6, 2), "pnl_pct": round(pnl6/ib6*100, 2) if ib6 else 0},
        ],
    }



def get_account_detail(account_id: int) -> Dict[str, Any]:
    """Get detailed data for a single account (for expanded view)."""
    if account_id == 56:
        return _build_duo_detail()
    acct = ACCOUNTS_BY_ID.get(account_id)
    if not acct:
        return {"error": "Unknown account"}

    stats = get_account_stats(account_id)
    live = get_account_live(account_id)

    pnl_start = PNL_START_OVERRIDES.get(account_id, PNL_START_DATE)
    _ib = INITIAL_BALANCES.get(account_id, 0)
    _sd = datetime.strptime(pnl_start, "%Y-%m-%d")
    _days = (datetime.utcnow() - _sd).days

    # Keep DB-based total_pnl (sum of trade PnLs) — no balance override
    # The cumulative_roi_pct in stats is already the correct sum-of-daily-ROIs

    return {
        "id": account_id,
        "display_name": acct["display_name"],
        "description": acct["description"],
        "position_size": acct["position_size"],
        "leverage": acct["leverage"],
        "long_combo": acct["long_combo"],
        "short_combo": acct["short_combo"],
        "pnl_since": pnl_start,
        "days_live": _days,
        "initial_balance": _ib,
        "stats": stats,
        "live": live,
    }


# ---------------------------------------------------------------------------
# Portfolio summary — aggregate across all accounts
# ---------------------------------------------------------------------------
def get_portfolio_summary() -> Dict[str, Any]:
    """Aggregate stats across all 5 active bot accounts."""
    return _get_cached("portfolio_summary", _DB_CACHE_TTL, _fetch_portfolio_summary)


def _fetch_portfolio_summary() -> Dict[str, Any]:
    """Build aggregate portfolio summary."""
    all_data = get_all_accounts_data()
    agg = all_data.get("aggregate", {})
    accounts = all_data.get("accounts", [])

    # Find best/worst bot by total_pnl
    best_bot = {"name": "-", "pnl": 0.0}
    worst_bot = {"name": "-", "pnl": 0.0}
    for acct in accounts:
        if not acct.get("has_data"):
            continue
        pnl = acct.get("total_pnl", 0)
        if pnl > best_bot["pnl"] or best_bot["name"] == "-":
            best_bot = {"name": acct["display_name"], "pnl": round(pnl, 4)}
        if pnl < worst_bot["pnl"] or worst_bot["name"] == "-":
            worst_bot = {"name": acct["display_name"], "pnl": round(pnl, 4)}

    # Time-window PnL from trade DBs
    today_pnl = 0.0
    week_pnl = 0.0
    month_pnl = 0.0
    now = datetime.now(timezone.utc)
    today_str = now.strftime("%Y-%m-%d")
    week_ago = (now - timedelta(days=7)).strftime("%Y-%m-%d")
    month_ago = (now - timedelta(days=30)).strftime("%Y-%m-%d")

    for acct_info in ACCOUNTS:
        aid = acct_info["id"]
        db_path = get_db_path(aid)
        if not os.path.exists(db_path):
            continue
        try:
            with sqlite3.connect(db_path, timeout=5) as conn:
                row = conn.execute("""
                    SELECT
                        COALESCE(SUM(CASE WHEN timestamp >= ? THEN pnl ELSE 0 END), 0) as today,
                        COALESCE(SUM(CASE WHEN timestamp >= ? THEN pnl ELSE 0 END), 0) as week,
                        COALESCE(SUM(CASE WHEN timestamp >= ? THEN pnl ELSE 0 END), 0) as month
                    FROM trades WHERE action = 'CLOSE' AND timestamp >= ?
                """, (today_str, week_ago, month_ago, PNL_START_DATE)).fetchone()
                if row:
                    today_pnl += row[0] or 0
                    week_pnl += row[1] or 0
                    month_pnl += row[2] or 0
        except Exception:
            continue

    return {
        "total_aum": agg.get("total_balance", 0),
        "total_pnl": agg.get("total_pnl", 0),
        "total_trades": agg.get("total_trades", 0),
        "avg_win_rate": agg.get("overall_win_rate", 0),
        "best_bot": best_bot,
        "worst_bot": worst_bot,
        "today_pnl": round(today_pnl, 4),
        "week_pnl": round(week_pnl, 4),
        "month_pnl": round(month_pnl, 4),
    }


# ---------------------------------------------------------------------------
# Recent signals — last N closed trades as signal cards
# ---------------------------------------------------------------------------
def get_recent_signals(limit: int = 10) -> List[Dict[str, Any]]:
    """Last N closed trades formatted as signal cards."""
    return _get_cached(
        f"recent_signals_{limit}", _DB_CACHE_TTL,
        lambda: _fetch_recent_signals(limit)
    )


def _compute_risk_score(combo_name: str, db_path: str) -> int:
    """Calculate risk score 1-5 from combo win rate."""
    try:
        with sqlite3.connect(db_path, timeout=5) as conn:
            row = conn.execute("""
                SELECT COUNT(*) as total,
                       SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins
                FROM trades WHERE action = 'CLOSE' AND combo_name = ? AND timestamp >= ?
            """, (combo_name, PNL_START_DATE)).fetchone()
            if row and row[0] and row[0] > 0:
                wr = (row[1] or 0) / row[0] * 100
                if wr > 65:
                    return 5
                elif wr > 60:
                    return 4
                elif wr > 55:
                    return 3
                elif wr > 50:
                    return 2
    except Exception:
        pass
    return 1


def _compute_tp_levels(entry_price: float, direction: str) -> List[float]:
    """Compute TP levels from entry_price as approximate display values."""
    if not entry_price or entry_price <= 0:
        return []
    pcts = [0.01, 0.02, 0.03, 0.05, 0.08]
    if direction == "long":
        return [round(entry_price * (1 + p), 6) for p in pcts]
    else:
        return [round(entry_price * (1 - p), 6) for p in pcts]


def _fetch_recent_signals(limit: int) -> List[Dict[str, Any]]:
    """Fetch recent closed trades across all accounts."""
    all_trades = []
    for acct_info in ACCOUNTS:
        aid = acct_info["id"]
        db_path = get_db_path(aid)
        if not os.path.exists(db_path):
            continue
        try:
            with sqlite3.connect(db_path, timeout=5) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute("""
                    SELECT timestamp, symbol, direction, entry_price, exit_price,
                           pnl, pnl_pct, bars_held, exit_reason, combo_name, sl_price
                    FROM trades WHERE action = 'CLOSE' AND timestamp >= ?
                    ORDER BY timestamp DESC LIMIT ?
                """, (PNL_START_DATE, limit * 2)).fetchall()

                for r in rows:
                    entry = r["entry_price"] or 0
                    direction = r["direction"] or "long"
                    risk_score = _compute_risk_score(r["combo_name"] or "", db_path)
                    all_trades.append({
                        "pair": r["symbol"] or "?",
                        "direction": direction,
                        "entry_price": entry,
                        "exit_price": r["exit_price"] or 0,
                        "tp_levels": _compute_tp_levels(entry, direction),
                        "sl_price": r["sl_price"] or 0,
                        "risk_score": risk_score,
                        "pnl": round(r["pnl"] or 0, 4),
                        "pnl_pct": round((r["pnl_pct"] or 0) * 100, 2),
                        "timestamp": r["timestamp"] or "",
                        "combo_name": r["combo_name"] or "",
                        "result": "win" if (r["pnl"] or 0) > 0 else "loss",
                        "bars_held": r["bars_held"] or 0,
                        "exit_reason": r["exit_reason"] or "",
                        "account_id": aid,
                        "account_name": acct_info["display_name"],
                    })
        except Exception:
            continue

    # Sort by timestamp descending, take limit
    all_trades.sort(key=lambda x: x["timestamp"], reverse=True)
    return all_trades[:limit]


# ---------------------------------------------------------------------------
# Equity history — for portfolio-wide equity curve chart
# ---------------------------------------------------------------------------
def get_account_equity_history(account_id: Optional[int] = None, days: int = 30) -> List[Dict[str, Any]]:
    """Historical equity data points for charting."""
    cache_key = f"equity_hist_{account_id}_{days}"
    return _get_cached(cache_key, _DB_CACHE_TTL, lambda: _fetch_equity_history(account_id, days))


def _fetch_equity_history(account_id: Optional[int], days: int) -> List[Dict[str, Any]]:
    """Build equity curve from trade timestamps across accounts."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    start = max(PNL_START_DATE, cutoff)

    # Collect all (timestamp, pnl) pairs
    all_points = []

    accounts_to_query = [ACCOUNTS_BY_ID[account_id]] if account_id and account_id in ACCOUNTS_BY_ID else ACCOUNTS

    for acct_info in accounts_to_query:
        aid = acct_info["id"]
        db_path = get_db_path(aid)
        if not os.path.exists(db_path):
            continue
        try:
            with sqlite3.connect(db_path, timeout=5) as conn:
                rows = conn.execute("""
                    SELECT timestamp, pnl FROM trades
                    WHERE action = 'CLOSE' AND timestamp >= ?
                    ORDER BY timestamp ASC
                """, (start,)).fetchall()
                for r in rows:
                    if r[1] is not None:
                        all_points.append((r[0], float(r[1])))
        except Exception:
            continue

    if not all_points:
        return []

    # Sort by timestamp and compute cumulative equity
    all_points.sort(key=lambda x: x[0])
    cumulative = 0.0
    result = []
    for ts, pnl in all_points:
        cumulative += pnl
        result.append({"timestamp": ts, "equity": round(cumulative, 4)})

    return result


# ---------------------------------------------------------------------------
# Account streaks — current win/loss streak per account
# ---------------------------------------------------------------------------
def get_account_streaks() -> Dict[str, Dict[str, Any]]:
    """Current win/loss streak per account."""
    return _get_cached("account_streaks", _DB_CACHE_TTL, _fetch_account_streaks)


def _fetch_account_streaks() -> Dict[str, Dict[str, Any]]:
    """Compute current streak for each account."""
    streaks = {}
    for acct_info in ACCOUNTS:
        aid = acct_info["id"]
        name = acct_info["display_name"]
        db_path = get_db_path(aid)
        if not os.path.exists(db_path):
            streaks[name] = {"current_streak": 0, "streak_type": "none"}
            continue
        try:
            with sqlite3.connect(db_path, timeout=5) as conn:
                rows = conn.execute("""
                    SELECT pnl FROM trades
                    WHERE action = 'CLOSE' AND timestamp >= ?
                    ORDER BY timestamp DESC LIMIT 100
                """, (PNL_START_DATE,)).fetchall()

                if not rows:
                    streaks[name] = {"current_streak": 0, "streak_type": "none"}
                    continue

                # Count streak from most recent trade
                first_pnl = rows[0][0] or 0
                streak_type = "win" if first_pnl > 0 else "loss"
                streak_count = 0
                for r in rows:
                    pnl = r[0] or 0
                    if (streak_type == "win" and pnl > 0) or (streak_type == "loss" and pnl <= 0):
                        streak_count += 1
                    else:
                        break

                streaks[name] = {"current_streak": streak_count, "streak_type": streak_type}
        except Exception:
            streaks[name] = {"current_streak": 0, "streak_type": "none"}

    return streaks
