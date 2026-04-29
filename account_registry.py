"""Account registry — single source of truth for all 9 Lighter trading accounts.

Imports combo configs from strategies/combo_lighter/instances/account{1-9}.py
and provides structured metadata for the dashboard.
"""

import importlib
import os
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

_DISPLAY_NAMES = {
    2: "Long Accumulator",
    5: "Slipstream",
    6: "Quick Bite",
    7: "TITAN",
    8: "PEGASUS",
}

# Short descriptions for each account
_DESCRIPTIONS = {
    2: "DCA LONG: AVAX+LIT+DUSK mechanikus",
    5: "COPY TRADE: 10 Hyperliquid wallet",
    6: "ML SHORT: XGBoost v2, 51 feature",
    7: "ML LONG: XGBoost h24, 84 feature",
    8: "ML LONG: LightGBM h20, 84 feature",
}


def _parse_combo(combo_dict: dict) -> dict:
    """Parse a combo config dict into a clean structure."""
    exit_cfg = combo_dict.get("exit", {})
    return {
        "name": combo_dict["name"],
        "direction": combo_dict["direction"],
        "logic": combo_dict.get("logic", "or"),
        "indicators": combo_dict.get("indicators", []),
        "exit_mode": exit_cfg.get("mode", "unknown"),
        "exit_params": {
            k: v for k, v in exit_cfg.items() if k != "mode"
        },
    }


# Accounts 5 & 6 run a separate trading bot — no instance config file,
# but we still display their balance/PnL on the dashboard (API-only).
_EXTERNAL_ACCOUNTS = {
    2: {
        "env_keys": {
            "account_index": "LIGHTER_ACCOUNT_INDEX_2",
            "api_key_index": "LIGHTER_API_KEY_INDEX_2",
            "api_private_key": "LIGHTER_API_PRIVATE_KEY_2",
        },
        "position_size": 4.5,
        "leverage": 3,
    },
    5: {
        # Slipstream-2 on master 63915 SUB1 (deployed 2026-04-23)
        "env_keys": {
            "account_index": "LIGHTER_ACCOUNT_INDEX_63915_SUB1",
            "api_key_index": "LIGHTER_API_KEY_INDEX_63915_SUB1",
            "api_private_key": "LIGHTER_API_PRIVATE_KEY_63915_SUB1",
        },
        "position_size": 25.0,
        "leverage": 3,
    },
    6: {
        # Quick-Bite-2 on master 63915 SUB2 (deployed 2026-04-23)
        "env_keys": {
            "account_index": "LIGHTER_ACCOUNT_INDEX_63915_SUB2",
            "api_key_index": "LIGHTER_API_KEY_INDEX_63915_SUB2",
            "api_private_key": "LIGHTER_API_PRIVATE_KEY_63915_SUB2",
        },
        "position_size": 20.0,
        "leverage": 10,
    },
    7: {
        "env_keys": {
            "account_index": "LIGHTER_ACCOUNT_INDEX_7",
            "api_key_index": "LIGHTER_API_KEY_INDEX_7",
            "api_private_key": "LIGHTER_API_PRIVATE_KEY_7",
        },
        "position_size": 9.0,
        "leverage": 3,
    },
    8: {
        "env_keys": {
            "account_index": "LIGHTER_ACCOUNT_INDEX_8",
            "api_key_index": "LIGHTER_API_KEY_INDEX_8",
            "api_private_key": "LIGHTER_API_PRIVATE_KEY_8",
        },
        "position_size": 9.0,
        "leverage": 3,
    },
}


def _build_registry() -> list:
    """Build account registry from instance configs + external accounts."""
    accounts = []
    for i in [5, 6]:  # only the 2 active bots on new master 63915  # Only active bot accounts
        # Check for external (API-only) account first
        if i in _EXTERNAL_ACCOUNTS:
            ext = _EXTERNAL_ACCOUNTS[i]
            accounts.append({
                "id": i,
                "display_name": _DISPLAY_NAMES.get(i, f"Account {i}"),
                "description": _DESCRIPTIONS.get(i, ""),
                "db_filename": f"trade_log_{i}.db",
                "position_size": ext["position_size"],
                "leverage": ext["leverage"],
                "long_combo": None,
                "short_combo": None,
                "env_keys": ext["env_keys"],
                "external": True,
            })
            continue

        try:
            mod = importlib.import_module(f"strategies.combo_lighter.instances.account{i}")
        except ImportError:
            continue

        combos = getattr(mod, "COMBOS", [])
        long_combo = None
        short_combo = None
        for c in combos:
            parsed = _parse_combo(c)
            if parsed["direction"] == "long":
                long_combo = parsed
            elif parsed["direction"] == "short":
                short_combo = parsed

        # DB path: account 1 uses trade_log.db, others use trade_log_{N}.db
        if i == 1:
            db_filename = "trade_log.db"
        else:
            db_filename = f"trade_log_{i}.db"

        accounts.append({
            "id": i,
            "display_name": _DISPLAY_NAMES.get(i, f"Account {i}"),
            "description": _DESCRIPTIONS.get(i, ""),
            "db_filename": db_filename,
            "position_size": getattr(mod, "POSITION_SIZE_USDT", 5.0),
            "leverage": getattr(mod, "LEVERAGE", 1),
            "long_combo": long_combo,
            "short_combo": short_combo,
            "env_keys": {
                "account_index": getattr(mod, "ACCOUNT_INDEX_ENV", f"LIGHTER_ACCOUNT_INDEX_{i}"),
                "api_key_index": getattr(mod, "API_KEY_INDEX_ENV", f"LIGHTER_API_KEY_INDEX_{i}"),
                "api_private_key": getattr(mod, "API_PRIVATE_KEY_ENV", f"LIGHTER_API_PRIVATE_KEY_{i}"),
            },
            "external": False,
        })

    return accounts


ACCOUNTS = _build_registry()

# Quick lookup by ID
ACCOUNTS_BY_ID = {a["id"]: a for a in ACCOUNTS}


def get_db_path(account_id: int) -> str:
    """Get the trade_log DB path for an account."""
    acct = ACCOUNTS_BY_ID.get(account_id)
    if not acct:
        return ""
    combo_dir = os.path.join(_PROJECT_ROOT, "strategies", "combo_lighter")
    # VPS and local paths
    candidates = [
        os.path.join("/root/prediction_market_strategies/strategies/combo_lighter", acct["db_filename"]),
        os.path.join(combo_dir, acct["db_filename"]),
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    return candidates[-1]  # fallback to local path


def get_short_indicators(account_id: int) -> str:
    """Get a short summary string of indicators for card display."""
    acct = ACCOUNTS_BY_ID.get(account_id)
    if not acct:
        return ""
    parts = []
    if acct["long_combo"]:
        names = [_short_name(n) for n in acct["long_combo"]["indicators"][:2]]
        parts.append(f"L: {'+'.join(names)}")
    if acct["short_combo"]:
        names = [_short_name(n) for n in acct["short_combo"]["indicators"][:2]]
        parts.append(f"S: {'+'.join(names)}")
    return " | ".join(parts)


def _short_name(indicator_name: str) -> str:
    """Shorten an indicator name for compact display."""
    replacements = {
        "Market-Neutral Arbitrage": "MN Arb",
        "UPside Gap 3 Methods": "UPside",
        "Fade Move Outside Keltner Channels": "Fade Keltner",
        "Ladder Bottom": "Ladder",
        "One-Day Reversal": "One-Day",
        "Dark Cloud Cover": "Dark Cloud",
        "Stock Buyback/Issuance Strategy": "Buyback",
        "Transfer Function Model": "Transfer",
        "ATR": "ATR",
        "Rising Window": "Rising",
        "Falling Window": "Falling",
        "Seasonal Entries": "Seasonal",
        "Volatility (General)": "Vol",
        "Bayesian Mean-Variance Portfolio Optimization": "Bayesian MV",
        "The Five Rules for Successful Stock Investing": "5 Rules",
        "Eight New Price Lines (Shinne Hatte)": "Eight Lines",
        "Momentum Oscillator": "Momentum",
        "I-Star Market Impact Model": "I-Star",
        "Optimal Consumption/Portfolio Process": "OptConsump",
        "Sector Confirmation Strategy": "Sector",
        "Static Hedging Strategy (Bonds)": "Static",
        "Aggressive Strategy (High Lambda)": "Aggressive",
        "Event driven": "Event",
    }
    return replacements.get(indicator_name, indicator_name[:12])
