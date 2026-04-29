"""ATLAS state data module for ratsignal dashboard.

Reads ATLAS kernel state files and caches them for the dashboard API.
All reads are fail-open: missing/corrupt files return empty defaults.
"""

import json
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_ATLAS_STATE_DIR = os.path.join(_PROJECT_ROOT, "atlas", "state")
_AGENT_RESULTS_DIR = os.path.join(_ATLAS_STATE_DIR, "agent_results")

# ---------------------------------------------------------------------------
# Cache (same pattern as data.py)
# ---------------------------------------------------------------------------
_cache: Dict[str, tuple] = {}
_CACHE_TTL = 30  # 30s — matches pulse interval


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
# JSON file helpers
# ---------------------------------------------------------------------------
def _read_json(filename: str, directory: str = None) -> Optional[dict]:
    """Read a JSON file, return None on any error."""
    base = directory or _ATLAS_STATE_DIR
    path = os.path.join(base, filename)
    try:
        with open(path, "r") as f:
            return json.load(f)
    except Exception:
        return None


def _read_jsonl_tail(filename: str, limit: int = 100) -> List[dict]:
    """Read last N lines of a JSONL file."""
    path = os.path.join(_ATLAS_STATE_DIR, filename)
    try:
        with open(path, "r") as f:
            lines = f.readlines()
        tail = lines[-limit:] if len(lines) > limit else lines
        events = []
        for line in tail:
            line = line.strip()
            if line:
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
        return events
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Brain tab — identity, goals, lane budget, circuit breaker
# ---------------------------------------------------------------------------
def get_atlas_brain() -> Dict[str, Any]:
    """ATLAS identity, mood, confidence, goals, lane budget, circuit breaker."""
    return _get_cached("atlas_brain", _CACHE_TTL, _fetch_brain)


def _fetch_brain() -> Dict[str, Any]:
    identity = _read_json("identity.json") or {}
    goals = _read_json("goals.json") or {}
    budget = _read_json("lane_budget.json") or {}
    kernel = _read_json("kernel_state.json") or {}
    dream = _read_json("dream_state.json") or {}

    # Circuit breaker — check if file exists
    cb_path = os.path.join(_ATLAS_STATE_DIR, "circuit_breaker.json")
    if os.path.exists(cb_path):
        circuit_breaker = _read_json("circuit_breaker.json") or {"active": False}
    else:
        circuit_breaker = {"active": False}

    return {
        "identity": {
            "name": identity.get("name", "Atlas"),
            "version": identity.get("version", "?"),
            "born_at": identity.get("born_at"),
            "personality": identity.get("personality", {}),
            "mood": identity.get("mood", "unknown"),
            "confidence": identity.get("confidence", 0),
            "streak": identity.get("streak", {"wins": 0, "losses": 0}),
            "lifetime_stats": identity.get("lifetime_stats", {}),
            "current_focus": identity.get("current_focus"),
            "updated_at": identity.get("updated_at"),
        },
        "goals": goals.get("goals", {}),
        "risk_level": goals.get("risk_level", "unknown"),
        "risk_levels": goals.get("risk_levels", {}),
        "lane_budget": budget.get("budgets", {}),
        "lane_priority": (budget.get("history", [{}])[-1] or {}).get("priority", "unknown")
            if budget.get("history") else "unknown",
        "kernel": {
            "last_pulse_at": kernel.get("last_pulse_at"),
            "last_think_at": kernel.get("last_think_at"),
            "last_dream_at": kernel.get("last_dream_at"),
            "cycle_count": kernel.get("cycle_count", 0),
            "current_lane": kernel.get("current_lane"),
            "started_at": kernel.get("started_at"),
        },
        "dream": dream if dream else {"status": "idle"},
        "circuit_breaker": circuit_breaker,
    }


# ---------------------------------------------------------------------------
# Timeline tab — event journal
# ---------------------------------------------------------------------------
def get_atlas_timeline(limit: int = 100) -> Dict[str, Any]:
    """Event journal entries, most recent first."""
    return _get_cached(f"atlas_timeline_{limit}", _CACHE_TTL,
                       lambda: _fetch_timeline(limit))


def _fetch_timeline(limit: int) -> Dict[str, Any]:
    events = _read_jsonl_tail("event_journal.jsonl", limit)
    # Reverse so most recent is first
    events.reverse()
    return {
        "events": events,
        "total_count": len(events),
    }


# ---------------------------------------------------------------------------
# Strategy tab — scorecard, decider, hypotheses
# ---------------------------------------------------------------------------
def get_atlas_strategy() -> Dict[str, Any]:
    """Strategy scorecard, decider decisions, hypotheses."""
    return _get_cached("atlas_strategy", _CACHE_TTL, _fetch_strategy)


def _fetch_strategy() -> Dict[str, Any]:
    scorecard_raw = _read_json("strategy_scorecard.json", _AGENT_RESULTS_DIR) or {}
    decider_raw = _read_json("decider.json", _AGENT_RESULTS_DIR) or {}
    hypotheses = _read_json("hypotheses.json") or {}

    # Extract nested data (agent results have status/data/metadata wrapper)
    scorecard_data = scorecard_raw.get("data", scorecard_raw)
    if isinstance(scorecard_data, dict) and "data" in scorecard_data:
        scorecard_data = scorecard_data["data"]

    decider_data = decider_raw.get("data", decider_raw)

    return {
        "scorecard": scorecard_data,
        "decider": {
            "timestamp": decider_data.get("timestamp"),
            "regime": decider_data.get("regime"),
            "decisions": decider_data.get("decisions", {}),
            "dry_run": decider_data.get("dry_run", True),
            "overrides_written": decider_data.get("overrides_written", False),
        },
        "hypotheses": hypotheses.get("hypotheses", []) if isinstance(hypotheses, dict) else [],
    }


# ---------------------------------------------------------------------------
# Market tab — sentinel data, market intel, monitor regime
# ---------------------------------------------------------------------------
def get_atlas_market() -> Dict[str, Any]:
    """Market intelligence: funding, OI, fear/greed, regime per asset."""
    return _get_cached("atlas_market", _CACHE_TTL, _fetch_market)


def _fetch_market() -> Dict[str, Any]:
    sentinel = _read_json("sentinel.json", _AGENT_RESULTS_DIR) or {}
    monitor = _read_json("monitor.json", _AGENT_RESULTS_DIR) or {}
    market_intel = _read_json("market_intel.json") or {}

    # Web intel (from browser-based scraping, may not exist yet)
    web_intel = _read_json("web_intel.json") or {}

    return {
        "funding_rates": sentinel.get("funding_rates", {}),
        "funding_signals": sentinel.get("funding_signals", {}),
        "funding_extreme": sentinel.get("funding_extreme", False),
        "open_interest": sentinel.get("open_interest", {}),
        "oi_spike": sentinel.get("oi_spike", False),
        "fear_greed": sentinel.get("fear_greed", {"value": 0, "classification": "N/A"}),
        "long_short_ratio": sentinel.get("long_short_ratio", {}),
        "top_trader_ratio": sentinel.get("top_trader_ratio", {}),
        "taker_volume": sentinel.get("taker_volume", {}),
        "sentiment": sentinel.get("sentiment", {}),
        "regime": monitor.get("regime", {}),
        "kill_switch": monitor.get("kill_switch", {}),
        "web_intel": web_intel if web_intel else None,
        "updated_at": sentinel.get("updated_at"),
    }


# ---------------------------------------------------------------------------
# System tab — conductor, agent health, wisdom
# ---------------------------------------------------------------------------
def get_atlas_system() -> Dict[str, Any]:
    """System health: conductor, agent statuses, wisdom rules."""
    return _get_cached("atlas_system", _CACHE_TTL, _fetch_system)


def _fetch_system() -> Dict[str, Any]:
    conductor = _read_json("conductor_state.json") or {}
    wisdom = _read_json("atlas_wisdom.json") or {}

    # Build agent health summary from conductor state
    agents = []
    last_runs = conductor.get("agent_last_run", {})
    failures = conductor.get("agent_consecutive_failures", {})

    for agent_name in sorted(last_runs.keys()):
        last_run = last_runs.get(agent_name)
        consec_fail = failures.get(agent_name, 0)

        # Determine health status
        if consec_fail >= 3:
            status = "error"
        elif consec_fail >= 1:
            status = "warning"
        else:
            status = "ok"

        agents.append({
            "name": agent_name,
            "last_run": last_run,
            "consecutive_failures": consec_fail,
            "status": status,
        })

    # Wisdom rules — sort by confidence descending
    rules = wisdom.get("rules", [])
    rules_sorted = sorted(rules, key=lambda r: r.get("confidence", 0), reverse=True)

    return {
        "conductor": {
            "last_cycle_at": conductor.get("last_cycle_at"),
            "cycle_count": conductor.get("cycle_count", 0),
            "cycle_id": conductor.get("cycle_id"),
        },
        "agents": agents,
        "wisdom": {
            "version": wisdom.get("version", 0),
            "rules_count": len(rules),
            "rules": rules_sorted[:30],  # Top 30 by confidence
            "updated_at": wisdom.get("updated_at"),
        },
    }


# ---------------------------------------------------------------------------
# All ATLAS data (for initial page load)
# ---------------------------------------------------------------------------
def get_atlas_all() -> Dict[str, Any]:
    """Aggregate all ATLAS data for initial template render."""
    return {
        "brain": get_atlas_brain(),
        "timeline": get_atlas_timeline(50),
        "strategy": get_atlas_strategy(),
        "market": get_atlas_market(),
        "system": get_atlas_system(),
    }
