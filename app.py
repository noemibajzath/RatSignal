"""RatSignal Dashboard — Flask app.

Serves 9 Lighter trading account stats at ratsignal.com.
Background thread renders HTML every 30s into cache.
"""

import json
import os
import sys
import threading
import time
from pathlib import Path

# Load .env file
_env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', '.env')
if os.path.exists(_env_path):
    with open(_env_path) as _ef:
        for _line in _ef:
            _line = _line.strip()
            if not _line or _line.startswith('#') or '=' not in _line:
                continue
            _key, _, _val = _line.partition('=')
            os.environ.setdefault(_key.strip(), _val.strip())

from flask import Flask, Response, jsonify, request, send_from_directory
import anthropic

_CHAT_CLIENT = anthropic.Anthropic(
    api_key=os.environ.get("ANTHROPIC_API_KEY")
)

_CHAT_SYSTEM_PROMPT = """You are the RatSignal website assistant chatbot. Answer questions based on the following information about RatSignal. Be concise, friendly, and helpful.

About RatSignal: AI-powered crypto trading signal platform. We deliver real-time trading signals via a premium Telegram group.

Live Trading: 9 parallel subaccounts on Lighter DEX, trading 78 crypto pairs on 15-minute candles, governed by 26 AI agents. Strategies derived from 438 quant books. System self-evolves through competing AIs and A/B testing. All PnL shown live on website.

TradingView Indicator: Proprietary momentum scanner on TradingView (BTC/USDT 15m), included with subscription.

Pricing: Monthly 00/mo (first month 0), free cancellation first week, 30-day money-back guarantee if negative results. Lifetime 00,000 includes all source code and IP.

How It Works: 1) ML processes 5+ years of market data 2) Distills into clear signals 3) Real-time Telegram alerts with entry, TP, SL, risk scores.

Exchange: Lighter DEX.

Getting Started: Register at ratsignal.com/auth/register, get added to Premium Telegram group within 24h.

Contact: ratsignalcrypto@gmail.com

RULES: Only answer questions related to RatSignal, crypto, or trading. If truly unrelated, say exactly: Unfortunately, I cannot help with that. Please reach out to our team at ratsignalcrypto@gmail.com and they will be happy to assist you! Keep answers short (2-4 sentences). Respond in the same language the user writes in."""

_APP_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(os.path.dirname(_APP_DIR))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

app = Flask(
    __name__,
    template_folder=os.path.join(_APP_DIR, "templates"),
    static_folder=os.path.join(_APP_DIR, "static"),
)

# ---------------------------------------------------------------------------
# Auth & Payments (graceful if deps missing)
# ---------------------------------------------------------------------------
app.secret_key = os.environ.get("FLASK_SECRET_KEY", os.urandom(32).hex())

_HAS_AUTH = False
try:
    from temporary.ratsignal.auth import auth_bp, login_manager
    from temporary.ratsignal.payments import payments_bp
    from temporary.ratsignal import models as user_models

    login_manager.init_app(app)
    app.register_blueprint(auth_bp)
    app.register_blueprint(payments_bp)
    user_models.init_db()
    _HAS_AUTH = True
    print("[RatSignal] Auth + Payments loaded", flush=True)
except Exception as _auth_err:
    print(f"[RatSignal] Auth not loaded (optional): {_auth_err}", flush=True)

# ---------------------------------------------------------------------------
# Lazy imports (avoid circular + slow startup)
# ---------------------------------------------------------------------------
_data_mod = None
_atlas_data_mod = None


def _get_data():
    global _data_mod
    if _data_mod is None:
        from temporary.ratsignal import data as d
        _data_mod = d
    return _data_mod


def _get_atlas_data():
    global _atlas_data_mod
    if _atlas_data_mod is None:
        from temporary.ratsignal import atlas_data as ad
        _atlas_data_mod = ad
    return _atlas_data_mod


# ---------------------------------------------------------------------------
# Page cache — background thread renders HTML every 30s
# ---------------------------------------------------------------------------
_PAGE_CACHE = {"html": None, "json": None}
_RENDER_INTERVAL = 30  # seconds

_LOADING_HTML = """<!DOCTYPE html>
<html><head><meta charset="UTF-8"><title>RatSignal | Loading...</title>
<style>
body{background:#0a1628;color:#e8eaf6;font-family:Inter,sans-serif;display:flex;
align-items:center;justify-content:center;min-height:100vh;margin:0}
.loader{text-align:center}
.spinner{width:40px;height:40px;border:3px solid rgba(0,212,255,0.2);
border-top-color:#00d4ff;border-radius:50%;animation:spin 0.8s linear infinite;margin:0 auto 16px}
@keyframes spin{to{transform:rotate(360deg)}}
h2{font-size:1.2rem;margin-bottom:8px}
p{font-size:0.8rem;color:rgba(160,170,200,0.7)}
</style>
<meta http-equiv="refresh" content="3">
</head><body><div class="loader">
<div class="spinner"></div>
<h2>🐀 RatSignal</h2>
<p>Loading dashboard data...</p>
</div></body></html>"""


def _render_page():
    """Render the full dashboard HTML from data."""
    try:
        from flask import render_template
        data_mod = _get_data()
        all_data = data_mod.get_all_accounts_data()

        with app.app_context():
            html = render_template(
                "index.html",
                accounts=all_data["accounts"],
                agg=all_data["aggregate"],
                last_updated=all_data["last_updated"],
            )

        _PAGE_CACHE["html"] = html
        _PAGE_CACHE["json"] = all_data
    except Exception as e:
        print(f"[RatSignal] Render error: {e}", flush=True)
        import traceback
        traceback.print_exc()


def _background_renderer():
    """Background thread that renders the page every RENDER_INTERVAL seconds."""
    # Initial render
    time.sleep(2)  # Wait for Flask to be ready
    _render_page()

    while True:
        time.sleep(_RENDER_INTERVAL)
        try:
            _render_page()
        except Exception as e:
            print(f"[RatSignal] Background render failed: {e}", flush=True)


# Start background renderer
_bg_thread = threading.Thread(target=_background_renderer, daemon=True)
_bg_thread.start()


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    """Serve the cached dashboard HTML. Render on first request if cache empty."""
    html = _PAGE_CACHE.get("html")
    if not html:
        # First request — render synchronously instead of showing loading page
        _render_page()
        html = _PAGE_CACHE.get("html")
    if html:
        return Response(html, content_type="text/html; charset=utf-8")
    return Response(_LOADING_HTML, content_type="text/html; charset=utf-8")


@app.route("/showcase")
def showcase():
    """Design showcase — multiple section variants for review."""
    from flask import render_template
    try:
        data_mod = _get_data()
        all_data = data_mod.get_all_accounts_data()
    except Exception:
        all_data = {
            "accounts": [],
            "aggregate": {
                "total_balance": 0, "total_pnl": 0, "today_pnl": 0,
                "total_trades": 0, "overall_win_rate": 0, "total_positions": 0,
                "accounts_live": 0, "accounts_total": 9, "pnl_source": "mock",
            },
            "last_updated": "loading...",
        }
    return render_template(
        "showcase.html",
        accounts=all_data["accounts"],
        agg=all_data["aggregate"],
        last_updated=all_data.get("last_updated", ""),
    )


@app.route("/api/accounts")
def api_accounts():
    """JSON endpoint for JS auto-refresh — returns all accounts summary."""
    cached = _PAGE_CACHE.get("json")
    if cached:
        return jsonify(cached)
    # Fresh fetch if cache empty
    try:
        data_mod = _get_data()
        result = data_mod.get_all_accounts_data()
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/account/<int:account_id>")
def api_account_detail(account_id):
    """JSON endpoint for expanded account detail view."""
    try:
        data_mod = _get_data()
        result = data_mod.get_account_detail(account_id)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/atlas/brain")
def api_atlas_brain():
    """ATLAS brain: identity, mood, confidence, goals, lane budget."""
    try:
        return jsonify(_get_atlas_data().get_atlas_brain())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/atlas/timeline")
def api_atlas_timeline():
    """ATLAS timeline: event journal entries."""
    try:
        limit = int(request.args.get("limit", 100))
        return jsonify(_get_atlas_data().get_atlas_timeline(limit))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/atlas/strategy")
def api_atlas_strategy():
    """ATLAS strategy: scorecard, decider decisions, hypotheses."""
    try:
        return jsonify(_get_atlas_data().get_atlas_strategy())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/atlas/market")
def api_atlas_market():
    """ATLAS market intelligence: funding, OI, fear/greed, regime."""
    try:
        return jsonify(_get_atlas_data().get_atlas_market())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/atlas/system")
def api_atlas_system():
    """ATLAS system health: conductor, agents, wisdom rules."""
    try:
        return jsonify(_get_atlas_data().get_atlas_system())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/benchmark/progress")
def api_benchmark_progress():
    """Benchmark v2 progress — reads atlas/state/benchmark_progress.json."""
    try:
        progress_file = Path(_PROJECT_ROOT) / "atlas" / "state" / "benchmark_progress.json"
        if not progress_file.exists():
            return jsonify({"active": False})

        # Stale check: if file is >120s old, benchmark is likely not running
        file_age = time.time() - progress_file.stat().st_mtime
        if file_age > 120:
            return jsonify({"active": False})

        with open(progress_file, "r") as f:
            data = json.load(f)

        data["active"] = True
        return jsonify(data)
    except Exception as e:
        return jsonify({"active": False, "error": str(e)})


@app.route("/api/benchmark/logs")
def api_benchmark_logs():
    """Tail the benchmark v2 log file — returns last N lines."""
    try:
        import glob as _glob
        lines_requested = min(int(request.args.get("n", 80)), 200)
        log_dir = Path(_PROJECT_ROOT) / "logs"
        # Find the most recent benchmark_v2 log
        candidates = sorted(log_dir.glob("benchmark_v2_*.log"), reverse=True)
        if not candidates:
            return jsonify({"lines": [], "file": None})
        log_file = candidates[0]
        # Read last N lines efficiently
        with open(log_file, "rb") as f:
            # Seek from end
            try:
                f.seek(0, 2)
                fsize = f.tell()
                # Read last 64KB max
                read_size = min(fsize, 65536)
                f.seek(max(0, fsize - read_size))
                raw = f.read().decode("utf-8", errors="replace")
            except Exception:
                raw = ""
        all_lines = raw.splitlines()
        tail = all_lines[-lines_requested:]
        return jsonify({"lines": tail, "file": log_file.name})
    except Exception as e:
        return jsonify({"lines": [f"Error: {e}"], "file": None})


@app.route("/api/portfolio/summary")
def api_portfolio_summary():
    """Returns aggregate portfolio stats across all accounts."""
    try:
        data_mod = _get_data()
        return jsonify(data_mod.get_portfolio_summary())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/signals/recent")
def api_signals_recent():
    """Returns last N trade signals as cards."""
    try:
        limit = min(int(request.args.get("limit", 10)), 50)
        data_mod = _get_data()
        return jsonify(data_mod.get_recent_signals(limit))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/portfolio/equity-history")
def api_equity_history():
    """Returns equity curve data for charts."""
    try:
        account_id = request.args.get("account_id", type=int)
        days = min(int(request.args.get("days", 30)), 365)
        data_mod = _get_data()
        return jsonify(data_mod.get_account_equity_history(account_id, days))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/account/streaks")
def api_account_streaks():
    """Returns win/loss streaks per account."""
    try:
        data_mod = _get_data()
        return jsonify(data_mod.get_account_streaks())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/favicon.ico")
@app.route("/favicon.png")
def favicon():
    """Serve favicon."""
    return send_from_directory(
        os.path.join(_APP_DIR, "static"),
        "favicon-32x32.png",
        mimetype="image/png",
    )


@app.route("/health")
def health():
    """Health check endpoint."""
    return jsonify({
        "status": "ok",
        "cache_ready": _PAGE_CACHE.get("html") is not None,
    })


# ---------------------------------------------------------------------------
# Copycat — paid subscriber zip download
# ---------------------------------------------------------------------------
_COPYCAT_ZIP_DIR = "/opt/copycat/dist"
_COPYCAT_ZIP_NAME = "RatSignal_CopyTrader.zip"


def _serve_copycat_package(filename: str, download_name: str):
    """Gated download: requires login AND active subscription.

    The launcher .exe does not contain any strategy / model, but we still keep
    the download under auth so subscription churn is enforced from day one and
    the file URL cannot be casually shared."""
    from flask import abort, redirect, url_for
    try:
        from flask_login import current_user
    except ImportError:
        abort(503)

    if not getattr(current_user, "is_authenticated", False):
        return redirect(url_for("auth.login", next=request.path))
    if not getattr(current_user, "is_subscribed", False):
        abort(403)

    return send_from_directory(
        _COPYCAT_ZIP_DIR,
        filename,
        as_attachment=True,
        download_name=download_name,
    )


@app.route("/download/copy-trader.zip")
def download_copy_trader_zip():
    """Legacy single-ZIP download; kept for backward compatibility."""
    return _serve_copycat_package(_COPYCAT_ZIP_NAME, "RatSignal_CopyTrader.zip")


@app.route("/download/copycat.zip")
def download_copycat_zip():
    """Legacy alias - serves the Slipstream mirror client."""
    return _serve_copycat_package("Slipstream.zip", "RatSignal_Slipstream.zip")


@app.route("/download/slipstream.zip")
def download_slipstream_zip():
    """Slipstream mirror client - follows RatSignal sub-account #5."""
    return _serve_copycat_package("Slipstream.zip", "RatSignal_Slipstream.zip")


@app.route("/download/quickbite.zip")
def download_quickbite_zip():
    """Quick Bite package (B) - ML SHORT bot."""
    return _serve_copycat_package("QuickBite.zip", "RatSignal_QuickBite.zip")


@app.route("/download/duo.zip")
def download_duo_zip():
    """Duo package (C) - both bots together."""
    return _serve_copycat_package("Duo.zip", "RatSignal_Duo.zip")


import hashlib as _hashlib
import secrets as _secrets
import sqlite3 as _sqlite3

_COPYCAT_DB_PATH = "/opt/copycat/copycat.db"
_BOT_TOKEN_PREFIX = "rs_live_"


def _bot_token_hash(token: str) -> str:
    return _hashlib.sha256(token.strip().encode("utf-8")).hexdigest()


def _bot_token_generate() -> str:
    return f"{_BOT_TOKEN_PREFIX}{_secrets.token_urlsafe(32)}"


def _store_bot_token(token_hash: str, ratsignal_user_id: int, label: str) -> None:
    """Insert a fresh token (hash only) into copycat.db, revoke any prior active ones for the same user."""
    conn = _sqlite3.connect(_COPYCAT_DB_PATH, timeout=10)
    try:
        # Revoke all currently-active tokens for this user (we issue one at a time)
        conn.execute(
            "UPDATE tokens SET revoked_at = datetime('now') "
            "WHERE ratsignal_user_id = ? AND revoked_at IS NULL",
            (ratsignal_user_id,),
        )
        conn.execute(
            "INSERT INTO tokens(token_hash, ratsignal_user_id, label) VALUES(?, ?, ?)",
            (token_hash, ratsignal_user_id, label),
        )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Rate limiting + audit log for bot-facing endpoints (added 2026-04-29)
# Per-worker in-memory sliding window; gunicorn has 2 workers so effective
# rate is ~2x the configured limits. Audit log lives in copycat.db.
# ---------------------------------------------------------------------------
import collections as _collections
import threading as _threading
import time as _time

_RL_LOCK = _threading.Lock()
_RL_BUCKETS: dict = {}
_RL_LAST_GC = [0.0]
_AUDIT_INIT_DONE = [False]

# Policy: (max_hits, window_seconds)
_RL_ISSUE_PER_IP    = (10, 300)   # login throttle: 10 / 5 min / IP
_RL_ISSUE_PER_EMAIL = (5,  300)   # targeted brute-force: 5 / 5 min / email
_RL_REVOKE_PER_IP   = (10, 300)
_RL_VERIFY_PER_IP   = (60, 60)    # bot calls hourly; 60 / min / IP is plenty


def _client_ip() -> str:
    """Return the real client IP, respecting X-Forwarded-For (Caddy reverse proxy)."""
    from flask import request
    xff = request.headers.get("X-Forwarded-For", "")
    if xff:
        return xff.split(",")[0].strip()
    return request.remote_addr or "unknown"


def _rate_limit_check(key: str, max_hits: int, window_sec: int) -> bool:
    """Sliding-window check. Returns True if allowed, False if exceeded."""
    now = _time.time()
    cutoff = now - window_sec
    with _RL_LOCK:
        bucket = _RL_BUCKETS.setdefault(key, _collections.deque())
        while bucket and bucket[0] < cutoff:
            bucket.popleft()
        if len(bucket) >= max_hits:
            return False
        bucket.append(now)
        if now - _RL_LAST_GC[0] > 300:
            _RL_LAST_GC[0] = now
            stale = now - 3600
            for k in list(_RL_BUCKETS.keys()):
                b = _RL_BUCKETS[k]
                if not b or b[-1] < stale:
                    del _RL_BUCKETS[k]
        return True


def _audit_init() -> None:
    if _AUDIT_INIT_DONE[0]:
        return
    try:
        conn = _sqlite3.connect(_COPYCAT_DB_PATH, timeout=10)
        try:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS api_audit_log (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    endpoint    TEXT NOT NULL,
                    ip          TEXT,
                    email       TEXT,
                    outcome     TEXT NOT NULL,
                    user_agent  TEXT,
                    extra       TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_audit_email ON api_audit_log(email);
                CREATE INDEX IF NOT EXISTS idx_audit_ip    ON api_audit_log(ip);
                CREATE INDEX IF NOT EXISTS idx_audit_ts    ON api_audit_log(ts);
                """
            )
            conn.commit()
        finally:
            conn.close()
        _AUDIT_INIT_DONE[0] = True
    except Exception as _e:
        print(f"[audit] init failed: {_e}", flush=True)


def _audit_log(endpoint: str, outcome: str, *, email: str = "", extra: str = "") -> None:
    """Best-effort audit log. Never raises (audit must not break the request path)."""
    try:
        _audit_init()
        from flask import request
        ip = _client_ip()
        ua = (request.headers.get("User-Agent") or "")[:200]
        conn = _sqlite3.connect(_COPYCAT_DB_PATH, timeout=10)
        try:
            conn.execute(
                "INSERT INTO api_audit_log(endpoint, ip, email, outcome, user_agent, extra) "
                "VALUES(?, ?, ?, ?, ?, ?)",
                (endpoint, ip, email, outcome, ua, extra),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as _e:
        print(f"[audit] log failed: {_e}", flush=True)


@app.route("/api/issue-token", methods=["POST"])
def api_issue_token():
    """Authenticate (email + password) and return a fresh bot token.

    Used by the RatSignal Launcher "Login" screen. Replaces any prior token
    for the same user (single-token-per-user policy).
    """
    from flask import jsonify, request
    from temporary.ratsignal import models

    ip = _client_ip()
    if not _rate_limit_check(f"issue:ip:{ip}", *_RL_ISSUE_PER_IP):
        _audit_log("issue-token", "rate_limited_ip")
        return jsonify({"ok": False, "reason": "rate_limited"}), 429

    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""
    if not email or not password:
        _audit_log("issue-token", "missing_credentials", email=email)
        return jsonify({"ok": False, "reason": "missing_credentials"}), 400
    if not _rate_limit_check(f"issue:em:{email}", *_RL_ISSUE_PER_EMAIL):
        _audit_log("issue-token", "rate_limited_email", email=email)
        return jsonify({"ok": False, "reason": "rate_limited"}), 429
    user = models.get_user_by_email(email)
    if not user or not models.verify_password(user, password):
        _audit_log("issue-token", "invalid_credentials", email=email)
        return jsonify({"ok": False, "reason": "invalid_credentials"}), 401
    status = (user.get("subscription_status") or "free").lower()
    if status not in ("active", "trial"):
        _audit_log("issue-token", "not_subscribed", email=email, extra=status)
        return jsonify({
            "ok": False, "reason": "not_subscribed", "status": status,
        }), 403
    # Issue a fresh token, revoke any prior ones
    raw = _bot_token_generate()
    h = _bot_token_hash(raw)
    try:
        _store_bot_token(h, int(user["id"]), label=email)
    except Exception as e:
        _audit_log("issue-token", "db_error", email=email, extra=str(e)[:200])
        return jsonify({"ok": False, "reason": "db_error", "detail": str(e)}), 500
    _audit_log("issue-token", "issued", email=email)
    return jsonify({
        "ok": True,
        "token": raw,
        "email": email,
        "expires": user.get("subscription_end"),
        "plan": user.get("subscription_plan"),
    }), 200


@app.route("/api/revoke-token", methods=["POST"])
def api_revoke_token():
    """Revoke the caller's active token. Auth: email + password (same as issue)."""
    from flask import jsonify, request
    from temporary.ratsignal import models

    ip = _client_ip()
    if not _rate_limit_check(f"revoke:ip:{ip}", *_RL_REVOKE_PER_IP):
        _audit_log("revoke-token", "rate_limited_ip")
        return jsonify({"ok": False, "reason": "rate_limited"}), 429

    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""
    if not email or not password:
        _audit_log("revoke-token", "missing_credentials", email=email)
        return jsonify({"ok": False, "reason": "missing_credentials"}), 400
    user = models.get_user_by_email(email)
    if not user or not models.verify_password(user, password):
        _audit_log("revoke-token", "invalid_credentials", email=email)
        return jsonify({"ok": False, "reason": "invalid_credentials"}), 401
    try:
        conn = _sqlite3.connect(_COPYCAT_DB_PATH, timeout=10)
        try:
            cur = conn.execute(
                "UPDATE tokens SET revoked_at = datetime('now') "
                "WHERE ratsignal_user_id = ? AND revoked_at IS NULL",
                (int(user["id"]),),
            )
            n = cur.rowcount
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        _audit_log("revoke-token", "db_error", email=email, extra=str(e)[:200])
        return jsonify({"ok": False, "reason": "db_error", "detail": str(e)}), 500
    _audit_log("revoke-token", "revoked", email=email, extra=f"count={n}")
    return jsonify({"ok": True, "revoked": n}), 200


@app.route("/api/verify", methods=["GET"])
def api_verify():
    """License-gate endpoint: the bot calls this with ?email=... on startup.

    Response JSON:
      200 {"active": true, "email": ..., "status": ..., "plan": ..., "expires": ...}
      404 {"active": false, "reason": "email_not_found"}
      403 {"active": false, "reason": "not_subscribed", "status": "..."}
      400 {"active": false, "reason": "missing_email"}
      429 {"active": false, "reason": "rate_limited"}
    """
    from flask import jsonify, request
    from temporary.ratsignal import models

    ip = _client_ip()
    if not _rate_limit_check(f"verify:ip:{ip}", *_RL_VERIFY_PER_IP):
        _audit_log("verify", "rate_limited_ip")
        return jsonify({"active": False, "reason": "rate_limited"}), 429

    email = (request.args.get("email") or "").strip().lower()
    if not email:
        _audit_log("verify", "missing_email")
        return jsonify({"active": False, "reason": "missing_email"}), 400
    user = models.get_user_by_email(email)
    if not user:
        _audit_log("verify", "email_not_found", email=email)
        return jsonify({"active": False, "reason": "email_not_found"}), 404
    status = (user.get("subscription_status") or "free").lower()
    if status not in ("active", "trial"):
        _audit_log("verify", "not_subscribed", email=email, extra=status)
        return jsonify({
            "active": False,
            "reason": "not_subscribed",
            "status": status,
        }), 403
    _audit_log("verify", "active", email=email, extra=status)
    return jsonify({
        "active": True,
        "email": email,
        "status": status,
        "plan": user.get("subscription_plan"),
        "expires": user.get("subscription_end"),
    }), 200


# ============================================================================
# Hosted Auto-Trading API (added 2026-04-28)
# ============================================================================

_HOSTED_BOTS = ("slipstream", "quickbite")


def _hosted_user_or_401():
    """Return ((user_id, user_dict), None) or (None, error_response).

    Source of truth for "logged in" is Flask-Login (writes session["_user_id"]),
    NOT a raw session["user_id"] key - that key is never set anywhere.
    """
    from flask import jsonify
    from flask_login import current_user
    from temporary.ratsignal import models
    if not current_user.is_authenticated:
        return None, (jsonify({"ok": False, "error": "not_logged_in"}), 401)
    user_id = current_user.id
    user = models.get_user_by_id(user_id)
    if not user:
        return None, (jsonify({"ok": False, "error": "not_logged_in"}), 401)
    return (user_id, user), None


@app.route("/api/hosted/<bot>/save", methods=["POST"])
def api_hosted_save(bot):
    from flask import jsonify, request
    from temporary.ratsignal import hosted

    if bot not in _HOSTED_BOTS:
        return jsonify({"ok": False, "error": "unknown_bot"}), 400

    auth_result, err = _hosted_user_or_401()
    if err:
        return err
    user_id, user = auth_result

    if not hosted.is_subscription_active_for_hosted(user):
        return jsonify({"ok": False, "error": "subscription_required"}), 403

    payload = request.get_json(silent=True) or {}
    if not payload.get("tos_accepted"):
        return jsonify({"ok": False, "error": "tos_required"}), 400
    hosted.accept_tos(user_id, request.remote_addr or "")

    try:
        ok, error = hosted.save_bot_config(
            user_id=user_id, bot=bot,
            exchange=payload["exchange"],
            sizing_mode=payload["sizing_mode"],
            position_size_usd=payload.get("position_size_usd"),
            position_size_pct=payload.get("position_size_pct"),
            leverage=payload["leverage"],
            copy_leverage=bool(payload.get("copy_leverage")),
            max_leverage=payload["max_leverage"],
            max_total_positions=payload["max_total_positions"],
            max_loss_pct=payload["max_loss_pct"],
            max_hold_hours=payload["max_hold_hours"],
            api_key=payload["api_key"],
            api_secret=payload.get("api_secret"),
            api_key_index=payload.get("api_key_index"),
            account_index=payload.get("account_index"),
        )
    except KeyError as exc:
        return jsonify({"ok": False, "error": f"missing field: {exc}"}), 400
    except Exception as exc:
        app.logger.exception("hosted save failed")
        return jsonify({"ok": False, "error": str(exc)}), 500

    if ok:
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": error}), 400


@app.route("/api/hosted/<bot>/activate", methods=["POST"])
def api_hosted_activate(bot):
    from flask import jsonify
    from temporary.ratsignal import hosted, models

    if bot not in _HOSTED_BOTS:
        return jsonify({"ok": False, "error": "unknown_bot"}), 400
    auth_result, err = _hosted_user_or_401()
    if err:
        return err
    user_id, user = auth_result
    if not hosted.is_subscription_active_for_hosted(user):
        return jsonify({"ok": False, "error": "subscription_required"}), 403
    cfg = models.get_hosted_bot_config(user_id, bot)
    if not cfg or not cfg.get("api_key_encrypted"):
        return jsonify({"ok": False, "error": "configure_first"}), 400
    hosted.set_enabled(user_id, bot, True)
    return jsonify({"ok": True})


@app.route("/api/hosted/<bot>/pause", methods=["POST"])
def api_hosted_pause(bot):
    from flask import jsonify
    from temporary.ratsignal import hosted

    if bot not in _HOSTED_BOTS:
        return jsonify({"ok": False, "error": "unknown_bot"}), 400
    auth_result, err = _hosted_user_or_401()
    if err:
        return err
    user_id, _ = auth_result
    hosted.set_paused(user_id, bot, True)
    return jsonify({"ok": True})


@app.route("/api/hosted/<bot>/resume", methods=["POST"])
def api_hosted_resume(bot):
    from flask import jsonify
    from temporary.ratsignal import hosted

    if bot not in _HOSTED_BOTS:
        return jsonify({"ok": False, "error": "unknown_bot"}), 400
    auth_result, err = _hosted_user_or_401()
    if err:
        return err
    user_id, user = auth_result
    if not hosted.is_subscription_active_for_hosted(user):
        return jsonify({"ok": False, "error": "subscription_required"}), 403
    hosted.set_paused(user_id, bot, False)
    return jsonify({"ok": True})


@app.route("/api/hosted/<bot>/disable", methods=["POST"])
def api_hosted_disable(bot):
    from flask import jsonify
    from temporary.ratsignal import hosted

    if bot not in _HOSTED_BOTS:
        return jsonify({"ok": False, "error": "unknown_bot"}), 400
    auth_result, err = _hosted_user_or_401()
    if err:
        return err
    user_id, _ = auth_result
    hosted.set_enabled(user_id, bot, False)
    return jsonify({"ok": True})


@app.route("/api/hosted/<bot>/trades", methods=["GET"])
def api_hosted_trades(bot):
    from flask import jsonify, request
    from temporary.ratsignal import models

    if bot not in _HOSTED_BOTS:
        return jsonify({"trades": []}), 400
    auth_result, err = _hosted_user_or_401()
    if err:
        return err
    user_id, _ = auth_result
    limit = min(int(request.args.get("limit", 50)), 200)
    trades = models.list_hosted_trades(user_id, bot=bot, limit=limit)
    return jsonify({"trades": trades})


@app.route("/legal/hosted-tos")
def hosted_tos_page():
    """Plain-text Hosted Trading Terms & Disclaimer."""
    from flask import Response
    body = """RatSignal Hosted Auto-Trading - Terms & Disclaimer
====================================================

By using the RatSignal Hosted Auto-Trading service ("Hosted Service"), you
acknowledge and agree:

1. The Hosted Service uses your provided exchange API key to place trades on
   your account, mirroring trades placed by RatSignal's source bots on
   RatSignal's own sub-accounts.

2. You are solely responsible for the API key you provide. We strongly
   recommend creating a TRADING-ONLY key with the WITHDRAW permission
   DISABLED. RatSignal is not liable for losses resulting from API keys with
   withdraw permission enabled.

3. RatSignal does NOT custody your funds. Your funds remain in your own
   exchange account, accessible only by you. RatSignal cannot withdraw,
   transfer, or otherwise move your funds (assuming withdraw permission is
   disabled on the API key as recommended).

4. Trading cryptocurrency carries substantial risk of loss. Past performance
   of RatSignal source bots does NOT guarantee future profits. You may lose
   some or all of your trading capital.

5. The Hosted Service is provided "as is" without warranties. RatSignal does
   not guarantee continuous uptime, trade execution, or any specific
   performance metric.

6. You may pause or stop trading at any time via the dashboard. Pausing does
   not cancel your subscription. To cancel your subscription, use the Billing
   page.

7. RatSignal reserves the right to suspend or terminate the Hosted Service
   for any user found in violation of these terms or applicable law.

8. By clicking "I have read and accept" on the configuration form, you
   confirm you have read these terms and agree to be bound by them.

Last updated: 2026-04-28
"""
    return Response(body, mimetype="text/plain")


@app.route("/download/launcher.exe")
def download_launcher_exe():
    """RatSignal Launcher — recommended Windows app."""
    return _serve_copycat_package("RatSignalLauncher.exe", "RatSignalLauncher.exe")


@app.route("/downloads")
def downloads_page():
    """Downloads page — requires login AND an active subscription."""
    from flask import render_template, redirect, flash
    try:
        from flask_login import current_user
        if not current_user.is_authenticated:
            flash("Please sign in to access downloads.", "info")
            return redirect("/auth/login")
        try:
            from temporary.ratsignal import models as user_models
            user_models.expire_if_needed(current_user.id)
            user = user_models.get_user_by_id(current_user.id) or {}
        except Exception:
            user = {}
        if user.get("subscription_status") != "active":
            flash("An active subscription is required to access downloads.", "error")
            return redirect("/auth/profile")
    except ImportError:
        flash("Please sign in to access downloads.", "info")
        return redirect("/auth/login")
    return render_template("downloads.html")




# ---------------------------------------------------------------------------
# Chatbot — answers based on website content only
# ---------------------------------------------------------------------------
_RATSIGNAL_KNOWLEDGE = """
RatSignal is an ML-powered crypto trading signal platform at ratsignal.com.

STRATEGIES:
1. ATLAS Trading System — Autonomous AI crypto trading system. 9 parallel trading accounts across 78 crypto pairs on 15-minute candles. Governed by ATLAS AI with 26 specialized agents. Strategies derived from 438 processed trading/quant books (136,000 pages), yielding 445 strategies, 1,032 indicators, and 1,679 exit concepts. Self-evolving: rival AIs compete, A/B tests measure effectiveness, Revenue Optimizer re-evaluates every 4 hours.
2. Prediction Market Arbitrage Bot — Cross-exchange arbitrage between Kalshi and Polymarket on binary-outcome events (sports, e-sports, etc). Buys both sides when combined cost < $1.00, locking in guaranteed profit. Sub-second latency, zero directional risk.
3. Smart Money Pattern Discovery — ML-driven strategy extraction from on-chain smart money data on DeFi protocols (GMX). Not copy trading — pattern discovery. Analyzes winning traders' timing, sizing, exit behavior. Deployed as automated bot on Binance.

PERFORMANCE (March 2026):
- 9 live accounts on Lighter exchange
- Best performer: Nest Theta — +9.68, 738 trades, 53.1% win rate (Fisher + Fractal HFT)
- Runner-up: Lab Zeta — +0.69, 95 trades, 48.9% win rate
- Combined portfolio: -74.05 across 3,890 trades, 39.3% win rate
- Live accounts: Rat Alpha, Rat Beta, Sewer Gamma, Sewer Delta, SmartMoney I (paused), SmartMoney II (paused), Tunnel Eta, Nest Theta, Nest Iota

STATS:
- 147 ML indicators monitored simultaneously
- 5.2M+ 15-minute candles processed
- 4+ years of historical data
- 9 active trading accounts

HOW IT WORKS:
1. Process everything on charts — candles, volume, momentum, divergence. ML model digests 5+ years of data.
2. Distill into clear signals — when to open, close, or stay out. No noise.
3. Real-time alerts via premium Telegram group — entry zones, take profits, stop loss, risk scores.

PRICING:
- Monthly: $100/month. First month $50. Free cancellation in first week. 30-day money-back guarantee if results are negative. Full access to premium signal group.
- Lifetime: $100,000 one-time. Everything in Monthly forever + all ML models, source code, strategy logic, indicator codebase, bot infrastructure, future updates. Full IP ownership. Contact: ratsignalcrypto@gmail.com

ACCOUNT:
- Registration at /auth/register — requires First Name, Last Name, Email, Telegram Username, Wallet Address (optional), Password
- Login at /auth/login
- Forgot password at /auth/forgot-password — sends reset email link valid for 1 hour
- Profile at /auth/profile — shows personal info, subscription status, payment history

CONTACT:
- Email: ratsignalcrypto@gmail.com
"""


@app.route("/api/chat", methods=["POST"])
def api_chat():
    """AI-powered chatbot using Anthropic Claude API."""
    from flask import jsonify, request
    data = request.get_json(silent=True) or {}
    user_message = (data.get("message") or "").strip()

    if not user_message:
        return jsonify({"reply": "Please ask me a question about RatSignal!"})

    if len(user_message) > 500:
        return jsonify({"reply": "Please keep your question shorter (max 500 characters)."})

    try:
        response = _CHAT_CLIENT.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=512,
            system=_CHAT_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}],
        )
        reply = response.content[0].text
    except Exception as e:
        app.logger.error(f"Chat AI error: {e}")
        reply = "Sorry, something went wrong. Please try again or reach out to our team at ratsignalcrypto@gmail.com"

    return jsonify({"reply": reply})

def _generate_reply(msg):
    """Generate a reply based on keyword matching against website content and crypto/trading knowledge."""

    words = msg.split()

    # ── Greetings ──
    if len(words) <= 3 and any(w in words for w in ["hi", "hello", "hey", "sup", "yo", "howdy", "hola", "szia", "szevasz", "helo"]):
        return "Hey there! 🐀 I'm the RatSignal assistant. Ask me anything about our trading strategies, pricing, crypto, trading, or how to get started!"

    # ── Hungarian greetings ──
    if len(words) <= 4 and any(w in msg for w in ["szia", "szevasz", "helló", "helo", "jó napot", "üdv"]):
        return "Szia! 🐀 Én vagyok a RatSignal asszisztens. Kérdezz bármit a stratégiáinkról, árakról, kriptóról vagy a tradingről!"

    # ── Pricing ──
    if any(w in msg for w in ["price", "pricing", "cost", "how much", "subscription", "pay", "fee", "cheap", "expensive", "money-back", "refund", "guarantee", "ár", "mennyibe", "előfizetés", "fizetés"]):
        return "💰 **Pricing:**\n\n**Monthly Plan - $100/mo**\n- First month: only $50\n- Free cancellation in the first week\n- 30-day money-back guarantee if results are negative\n- Full access to premium signal group + TradingView indicator\n\n**Lifetime Plan - $100,000**\n- Everything forever + all source code, ML models, strategy logic, and full IP ownership\n- Contact: ratsignalcrypto@gmail.com\n\nReady to start? Head to [Register](/auth/register)!"

    # ── ATLAS Trading System ──
    if any(w in msg for w in ["atlas"]):
        return "🤖 **ATLAS Trading System:**\n\nOur autonomous crypto trading system:\n- 9 parallel accounts trading 78 crypto pairs\n- 15-minute candle timeframe on Lighter DEX\n- Governed by 26 AI agents\n- Strategies derived from 438 quant books (136K pages)\n- Self-evolving through competing AIs and continuous A/B testing\n\nATLAS doesn't follow fixed rules - it learns and adapts continuously."

    # ── Strategies overview ──
    if any(w in msg for w in ["strategy", "strategies", "trading system", "how does it work", "how do you trade", "stratégia"]):
        return "🤖 **We run 3 core strategies:**\n\n1. **ATLAS Trading System** - 9 parallel accounts, 78 crypto pairs, 26 AI agents on Lighter DEX. Self-evolving with rival AI competition.\n\n2. **Prediction Market Arbitrage** - Cross-exchange arb between Kalshi & Polymarket. Zero directional risk, guaranteed profit per trade.\n\n3. **Smart Money Pattern Discovery** - ML extracts patterns from profitable on-chain DeFi traders (GMX), deployed on Binance.\n\nWant more details on any specific strategy?"

    # ── Arbitrage ──
    if any(w in msg for w in ["arbitrage", "arb", "kalshi", "polymarket", "prediction market", "risk-free", "risk free", "arbitrázs"]):
        return "⚡ **Prediction Market Arbitrage Bot:**\n\nExploits real-time price differences between Kalshi and Polymarket. When both sides of a binary event cost < $1.00 combined, we buy both - locking in guaranteed profit regardless of outcome.\n\n- Sub-second latency\n- Zero directional exposure\n- Parallel execution on both exchanges\n\nEvery trade is profitable at the moment of execution!"

    # ── Smart money ──
    if any(w in msg for w in ["smart money", "on-chain", "onchain", "gmx", "copy trad", "pattern discovery"]):
        return "🧬 **Smart Money Pattern Discovery:**\n\nWe analyze consistently profitable DeFi traders on GMX through on-chain data. Not copy trading - **pattern discovery**. Our ML finds what winning traders do unconsciously: timing, sizing, exit behavior, market conditions.\n\nThese patterns become concrete trading rules, backtested rigorously, and deployed as an automated bot on Binance."

    # ── TradingView indicator ──
    if any(w in msg for w in ["tradingview", "indicator", "indikátor", "scanner", "momentum"]):
        return "📊 **TradingView Indicator:**\n\nOur proprietary momentum scanner runs live on TradingView - BTC/USDT 15m timeframe.\n\n- Included with the monthly subscription\n- Based on our ML model's signal generation\n- Visual overlay on your TradingView charts\n\nSubscribe to get access!"

    # ── Performance / results / PnL ──
    if any(w in msg for w in ["performance", "results", "pnl", "p&l", "profit", "loss", "returns", "win rate", "track record", "eredmény", "hozam", "teljesítmény"]):
        return "📈 **Live Performance:**\n\nOur website shows real-time PnL from all 9 Lighter exchange subaccounts, auto-refreshing every 30 seconds.\n\nWe show real numbers - good and bad. Full transparency, always. Check the Live Accounts section on our homepage to see current results."

    # ── How it works / signals ──
    if any(w in msg for w in ["how it works", "how does", "signal group", "telegram", "alert", "notification", "hogyan működik"]) or ("signal" in msg and "ratsignal" not in msg.replace(" ", "")):
        return "📡 **How RatSignal works:**\n\n1. Our ML model processes 5+ years of market data - candles, volume, momentum, divergence\n2. It produces clear signals: when to open, close, or stay out\n3. You receive **real-time alerts** in our premium Telegram group - entry zones, take profits, stop loss, risk scores\n\nOne subscription = everything included (signals + TradingView indicator)!"

    # ── Account / registration ──
    if any(w in msg for w in ["register", "sign up", "signup", "create account", "join", "get started", "regisztrál", "kezd"]):
        return "🚀 **Getting started is easy:**\n\n1. [Create your account](/auth/register) - just name, email, Telegram username, and password\n2. We'll add you to the Signal Group within 24 hours\n3. Start receiving real-time signals on Telegram!\n\nFirst month is only $50, and you can cancel for free in the first week."

    # ── Login / password ──
    if any(w in msg for w in ["login", "log in", "sign in", "password", "forgot", "reset", "can't login", "cant login", "belépés", "jelszó"]):
        return "🔐 **Account access:**\n\n- [Login here](/auth/login) with your email and password\n- Forgot your password? Use the [password reset](/auth/forgot-password) - we'll email you a reset link (valid for 1 hour)\n\nNeed more help? Email us at ratsignalcrypto@gmail.com"

    # ── Stats / numbers ──
    if any(w in msg for w in ["stats", "numbers", "indicators", "candles", "data", "how many", "statisztika"]):
        return "📊 **RatSignal by the numbers:**\n\n- **147** ML indicators monitored simultaneously\n- **5.2M+** 15-minute candles processed\n- **4+ years** of historical data\n- **9** active live trading accounts on Lighter DEX\n- **438** books processed (136,000 pages)\n- **26** AI agents in the ATLAS system\n- **78** crypto pairs traded"

    # ── Lighter DEX ──
    if any(w in msg for w in ["lighter", "dex", "decentralized exchange"]):
        return "**Lighter DEX:**\n\nATLAS trades on the Lighter decentralized exchange. We run 9 parallel subaccounts, each with different strategy configurations. You can see all accounts' live PnL on our homepage dashboard."

    # ── Contact ──
    if any(w in msg for w in ["contact", "reach out", "talk to", "help me", "email", "kapcsolat", "elérhetőség"]):
        return "📧 You can reach us at **ratsignalcrypto@gmail.com** - we typically respond within 24 hours. For the Lifetime plan, email us directly!"

    # ── What is RatSignal ──
    if any(w in msg for w in ["what is ratsignal", "what do you do", "who are you", "tell me about ratsignal", "mi a ratsignal", "mi ez"]):
        return "🐀 **RatSignal** is an AI-powered crypto trading signal platform.\n\nWe run 3 distinct strategies - ATLAS (AI-driven multi-account trading on Lighter DEX), Prediction Market Arbitrage (risk-free cross-exchange arb on Kalshi/Polymarket), and Smart Money Pattern Discovery (on-chain ML on GMX/Binance).\n\nOur signals are delivered in real-time via a premium Telegram group. We also provide a TradingView indicator.\n\nCheck our [strategies](#strategies) or [get started](/auth/register)!"

    # ── Accounts / live dashboard ──
    if any(w in msg for w in ["accounts", "dashboard", "live", "subaccount"]):
        return "📊 **Live Accounts Dashboard:**\n\nOur homepage shows 8-9 Lighter exchange subaccounts in real-time:\n- Current PnL for each account\n- Whether the account is active or paused\n- Auto-refreshes every 30 seconds\n\nFull transparency - you see exactly how each strategy performs."

    # ══════════════════════════════════════════════
    # GENERAL CRYPTO / TRADING KNOWLEDGE
    # ══════════════════════════════════════════════

    # ── Bitcoin ──
    if any(w in msg for w in ["bitcoin", "btc"]):
        return "₿ **Bitcoin (BTC)** is the first and largest cryptocurrency by market cap, created by Satoshi Nakamoto in 2009. It uses proof-of-work consensus and has a fixed supply of 21 million coins.\n\nAt RatSignal, our ATLAS system trades BTC/USDT on 15-minute candles, and our TradingView indicator runs on BTC/USDT 15m. BTC is one of the 78 pairs we actively trade."

    # ── Ethereum ──
    if any(w in msg for w in ["ethereum", "eth"]):
        return "**Ethereum (ETH)** is the second-largest cryptocurrency and the leading smart contract platform. It powers DeFi, NFTs, and thousands of dApps. After 'The Merge' it runs on proof-of-stake.\n\nETH is among the 78 crypto pairs our ATLAS system trades on Lighter DEX."

    # ── DeFi ──
    if any(w in msg for w in ["defi", "decentralized finance", "yield", "liquidity", "amm", "lending", "borrowing"]):
        return "🏦 **DeFi (Decentralized Finance)** encompasses financial services built on blockchain - lending, borrowing, trading, yield farming, and more - without traditional intermediaries.\n\nKey DeFi concepts:\n- **AMM** - Automated Market Makers (Uniswap, Curve)\n- **Lending** - Aave, Compound\n- **Yield Farming** - providing liquidity for rewards\n- **DEXs** - decentralized exchanges\n\nOur Smart Money strategy analyzes profitable DeFi traders on GMX to discover winning patterns."

    # ── Technical Analysis ──
    if any(w in msg for w in ["technical analysis", "ta", "chart", "candle", "candlestick", "support", "resistance", "moving average", "rsi", "macd", "bollinger", "fibonacci", "technikai elemzés"]):
        return "📈 **Technical Analysis (TA):**\n\nTA uses price charts and indicators to predict future price movements. Common tools:\n\n- **Moving Averages** (SMA, EMA) - trend direction\n- **RSI** - overbought/oversold levels\n- **MACD** - momentum and trend changes\n- **Bollinger Bands** - volatility\n- **Fibonacci** - support/resistance levels\n- **Candlestick patterns** - price action signals\n\nOur ML model processes 147+ indicators simultaneously - far beyond what any human trader could track. Check our TradingView indicator for a visual overlay!"

    # ── Fundamental Analysis ──
    if any(w in msg for w in ["fundamental", "fundamentals", "tokenomics", "market cap", "supply"]):
        return "📋 **Fundamental Analysis** in crypto evaluates:\n\n- **Tokenomics** - supply, distribution, inflation\n- **Market cap** - total value of circulating supply\n- **Team & development** - activity, roadmap\n- **Adoption** - users, TVL, transaction volume\n- **On-chain metrics** - active addresses, whale movements\n\nOur Smart Money strategy uses on-chain fundamentals to identify profitable patterns."

    # ── Risk Management ──
    if any(w in msg for w in ["risk management", "stop loss", "take profit", "position size", "risk", "kockázat"]):
        return "🛡️ **Risk Management** is crucial in trading:\n\n- **Stop Loss** - automatic exit to limit losses\n- **Take Profit** - lock in gains at target levels\n- **Position Sizing** - never risk more than 1-2% per trade\n- **Risk/Reward Ratio** - aim for at least 1:2\n- **Diversification** - spread across assets and strategies\n\nEvery RatSignal signal includes entry zones, take profit, stop loss, and a risk score so you always know your risk."

    # ── Trading types ──
    if any(w in msg for w in ["day trading", "swing trading", "scalping", "position trading", "hodl", "hold"]):
        return "📊 **Trading Styles:**\n\n- **Scalping** - seconds to minutes, many small profits\n- **Day Trading** - intraday, no overnight positions\n- **Swing Trading** - days to weeks, catching 'swings'\n- **Position Trading** - weeks to months, trend following\n- **HODLing** - long-term buy and hold\n\nOur ATLAS system primarily uses 15-minute candles, making it a short-term/intraday approach across 78 pairs."

    # ── Exchanges ──
    if any(w in msg for w in ["exchange", "binance", "coinbase", "kraken", "bybit", "tőzsde"]):
        return "🏛️ **Crypto Exchanges:**\n\n**Centralized (CEX):** Binance, Coinbase, Kraken, Bybit, OKX\n**Decentralized (DEX):** Uniswap, Lighter, dYdX, GMX\n\nRatSignal uses:\n- **Lighter DEX** - ATLAS trading (9 accounts)\n- **Kalshi & Polymarket** - arbitrage bot\n- **Binance** - Smart Money bot execution\n\nOur signals work on any exchange - you just follow them on your preferred platform."

    # ── Wallets ──
    if any(w in msg for w in ["wallet", "metamask", "ledger", "cold wallet", "hot wallet", "seed phrase", "private key", "tárca"]):
        return "🔑 **Crypto Wallets:**\n\n- **Hot wallets** (online) - MetaMask, Trust Wallet, Phantom\n- **Cold wallets** (offline) - Ledger, Trezor\n- **Never share your seed phrase or private keys!**\n\nFor RatSignal, you don't need to share any wallet access. You trade on your own exchange account following our signals."

    # ── Market terms ──
    if any(w in msg for w in ["bull", "bear", "pump", "dump", "moon", "crash", "ath", "all time high", "dip", "correction", "rally"]):
        return "📊 **Market Terms:**\n\n- **Bull market** - prices rising, optimism\n- **Bear market** - prices falling, pessimism\n- **ATH** - All-Time High\n- **Dip/Correction** - temporary price drop (5-20%)\n- **Crash** - major price drop (20%+)\n- **Pump & Dump** - artificial price manipulation\n- **Moon** - significant price increase\n\nOur strategies work in both bull and bear markets - ATLAS adapts to market conditions, and arbitrage is market-neutral."

    # ── Leverage / Margin ──
    if any(w in msg for w in ["leverage", "margin", "long", "short", "futures", "perpetual", "liquidat", "tőkeáttétel"]):
        return "⚠️ **Leverage & Margin Trading:**\n\n- **Long** - betting price goes up\n- **Short** - betting price goes down\n- **Leverage** - trading with borrowed funds (2x, 5x, 10x+)\n- **Margin** - collateral required\n- **Liquidation** - forced close when margin runs out\n- **Perpetual futures** - no expiry futures contracts\n\n⚠️ Leverage amplifies both gains AND losses. Our signals include risk scores to help you manage exposure."

    # ── Stablecoins ──
    if any(w in msg for w in ["stablecoin", "usdt", "usdc", "dai", "tether"]):
        return "💵 **Stablecoins** are crypto tokens pegged to fiat currencies (usually $1 USD):\n\n- **USDT (Tether)** - largest by market cap\n- **USDC (Circle)** - fully regulated, audited\n- **DAI** - decentralized, algorithmic\n\nOur ATLAS system trades crypto pairs against USDT on Lighter DEX."

    # ── NFTs ──
    if any(w in msg for w in ["nft", "non-fungible"]):
        return "🖼️ **NFTs (Non-Fungible Tokens)** are unique digital assets on the blockchain - art, collectibles, gaming items, music, etc. They're traded on marketplaces like OpenSea, Blur, and Magic Eden.\n\nRatSignal focuses on trading signals rather than NFTs, but feel free to ask about crypto trading!"

    # ── Mining / Staking ──
    if any(w in msg for w in ["mining", "staking", "validator", "proof of work", "proof of stake", "pow", "pos", "bányászat"]):
        return "⛏️ **Mining & Staking:**\n\n- **Mining (PoW)** - using computing power to validate transactions (Bitcoin)\n- **Staking (PoS)** - locking tokens to validate transactions (Ethereum)\n- **Validators** - nodes that process and verify transactions\n- **Rewards** - earned for securing the network\n\nRatSignal focuses on active trading strategies rather than passive mining/staking."

    # ── Altcoins ──
    if any(w in msg for w in ["altcoin", "solana", "sol", "cardano", "ada", "xrp", "ripple", "doge", "shib", "meme coin", "memecoin"]):
        return "🪙 **Altcoins** are all cryptocurrencies other than Bitcoin:\n\n- **Ethereum (ETH)** - smart contracts leader\n- **Solana (SOL)** - high-speed, low fees\n- **XRP** - cross-border payments\n- **Cardano (ADA)** - research-driven\n- **Meme coins** (DOGE, SHIB) - community-driven, high volatility\n\nOur ATLAS system trades 78 crypto pairs including major altcoins on Lighter DEX."

    # ── Machine Learning in Trading ──
    if any(w in msg for w in ["machine learning", "ml", "ai", "artificial intelligence", "neural network", "deep learning", "model", "gépi tanulás", "mesterséges intelligencia"]):
        return "🧠 **ML/AI in Trading:**\n\nMachine learning can identify patterns humans miss:\n- Process millions of data points simultaneously\n- Learn from historical patterns\n- Adapt to changing market conditions\n- Remove emotional bias\n\nRatSignal's ML model:\n- Trained on 5.2M+ 15-minute candles\n- Monitors 147+ indicators\n- 26 AI agents compete and evolve strategies\n- Derived from 438 quant books\n\nThis is the core of our ATLAS system."

    # ── Backtesting ──
    if any(w in msg for w in ["backtest", "backtesting", "historical", "simulate"]):
        return "🔬 **Backtesting** tests a trading strategy against historical data to evaluate its performance before risking real money.\n\nKey metrics:\n- Win rate, profit factor, max drawdown\n- Sharpe ratio, Sortino ratio\n- Sample size (number of trades)\n\nAll RatSignal strategies are rigorously backtested on 4+ years of data before going live. Our Smart Money patterns are validated through extensive backtesting."

    # ── Trading bots ──
    if any(w in msg for w in ["bot", "automated", "algorithm", "algo", "automat"]):
        return "🤖 **Trading Bots:**\n\nAutomated trading removes emotion and executes 24/7:\n- **Grid bots** - buy low, sell high in a range\n- **DCA bots** - dollar-cost averaging\n- **Signal bots** - execute based on indicators\n- **Arbitrage bots** - exploit price differences\n\nRatSignal runs 3 fully automated bots:\n1. ATLAS (ML-driven, 9 accounts)\n2. Prediction market arbitrage (Kalshi/Polymarket)\n3. Smart money bot (GMX patterns on Binance)"

    # ── Order types ──
    if any(w in msg for w in ["order type", "limit order", "market order", "stop order", "oco"]):
        return "📋 **Order Types:**\n\n- **Market Order** - execute immediately at current price\n- **Limit Order** - execute at a specific price or better\n- **Stop Loss** - sell if price drops to a level\n- **Stop Limit** - stop + limit combined\n- **OCO** - One Cancels Other (paired orders)\n- **Trailing Stop** - moves with the price\n\nOur signals include specific entry zones and stop loss levels you can set as limit/stop orders."

    # ── Regulation ──
    if any(w in msg for w in ["regulation", "legal", "tax", "sec", "regulated", "szabályozás", "adó"]):
        return "⚖️ **Crypto Regulation:**\n\nRegulation varies by country and is evolving rapidly. Key points:\n- Most countries tax crypto gains\n- KYC/AML required on most exchanges\n- Regulations differ for DeFi vs. CeFi\n\nRatSignal provides trading signals - users are responsible for their own tax compliance and local regulations."

    # ── What can you help with ──
    if any(w in msg for w in ["what can you", "help", "menu", "options", "mit tudsz", "segítség"]):
        return "🐀 I can help you with:\n\n**About RatSignal:**\n- Our 3 trading strategies (ATLAS, Arbitrage, Smart Money)\n- Pricing and plans\n- TradingView indicator\n- Live accounts dashboard\n- How to get started\n\n**General Trading & Crypto:**\n- Technical analysis & indicators\n- Trading strategies & styles\n- Risk management\n- Exchanges & wallets\n- DeFi, NFTs, stablecoins\n- Any crypto-related question!\n\nJust ask! 🚀"

    # ── Generic crypto question ──
    if any(w in msg for w in ["crypto", "cryptocurrency", "blockchain", "token", "coin", "kripto", "blokklánc"]):
        return "🔗 **Cryptocurrency** is digital money secured by blockchain technology - a decentralized, transparent ledger.\n\nKey concepts:\n- **Blockchain** - distributed ledger of all transactions\n- **Tokens/Coins** - digital assets on a blockchain\n- **Decentralization** - no single point of control\n- **Smart contracts** - self-executing code on blockchain\n\nRatSignal uses AI and ML to trade crypto profitably. Want to know more about our strategies or trading in general?"

    # ── Trading generic ──
    if any(w in msg for w in ["trading", "trade", "invest", "kereskedés", "befektetés"]):
        return "📊 **Trading** is buying and selling assets to profit from price movements.\n\nKey principles:\n- Always use risk management (stop losses!)\n- Don't invest more than you can afford to lose\n- Have a strategy and stick to it\n- Control emotions - fear and greed are your enemies\n\nRatSignal removes the guesswork with AI-powered signals. Each signal includes entry, take profit, stop loss, and risk score. Want to know more?"

    # ── Fallback ──
    return "🐀 Great question! While I might not have a specific answer for that, I can help with:\n\n- **RatSignal** - strategies, pricing, signals, how to start\n- **Crypto** - Bitcoin, Ethereum, DeFi, stablecoins, NFTs\n- **Trading** - technical analysis, risk management, order types\n- **Exchanges** - CEX vs DEX, wallets, security\n\nTry asking about any of these topics, or email us at **ratsignalcrypto@gmail.com** for specific questions!"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    print(f"🐀 RatSignal Dashboard starting on http://localhost:{port}", flush=True)
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
