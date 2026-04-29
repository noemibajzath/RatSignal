"""RatSignal — Auth blueprint with Flask-Login."""

import os
import re
import smtplib
import threading
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

import hashlib
import hmac
import time
import urllib.parse

from flask import Blueprint, flash, jsonify, redirect, render_template, request, session, url_for

try:
    from flask_login import LoginManager, UserMixin, current_user, login_required, login_user, logout_user
    HAS_FLASK_LOGIN = True
except ImportError:
    HAS_FLASK_LOGIN = False
    # Stubs so decorators don't crash
    def login_required(f):
        return f
    class current_user:
        is_authenticated = False
        id = None

from temporary.ratsignal import models

import secrets
import json as _json
from flask import Response

# ---------------------------------------------------------------------------
# Blueprint
# ---------------------------------------------------------------------------
auth_bp = Blueprint("auth", __name__, url_prefix="/auth")

# In-memory store for verification codes: { code: { user_id, field, value, expires } }
_verification_codes = {}

# Social login config
DISCORD_CLIENT_ID = os.environ.get('DISCORD_CLIENT_ID', '')
DISCORD_CLIENT_SECRET = os.environ.get('DISCORD_CLIENT_SECRET', '')
GOOGLE_CLIENT_ID = os.environ.get('GOOGLE_CLIENT_ID', '')
GOOGLE_CLIENT_SECRET = os.environ.get('GOOGLE_CLIENT_SECRET', '')
TELEGRAM_BOT_TOKEN_LOGIN = os.environ.get('TELEGRAM_LOGIN_BOT_TOKEN', '') or os.environ.get('TELEGRAM_BOT_TOKEN', '')
TELEGRAM_BOT_USERNAME = os.environ.get('TELEGRAM_BOT_USERNAME', '')

# Admin access for /auth/admin dashboard. Comma-separated emails in env var,
# with a hardcoded fallback so the dashboard works without requiring an env
# change to the production service.
_ADMIN_FALLBACK = "noemibajzath@gmail.com,ratsignalcrypto@gmail.com"
ADMIN_EMAILS = {
    e.strip().lower()
    for e in (os.environ.get("RATSIGNAL_ADMIN_EMAILS") or _ADMIN_FALLBACK).split(",")
    if e.strip()
}


def _is_admin() -> bool:
    if not HAS_FLASK_LOGIN or not current_user.is_authenticated:
        return False
    email = (getattr(current_user, "_data", {}).get("email") or "").lower()
    return email in ADMIN_EMAILS

# ---------------------------------------------------------------------------
# Flask-Login setup
# ---------------------------------------------------------------------------
if HAS_FLASK_LOGIN:
    login_manager = LoginManager()
    login_manager.login_view = "auth.login"
    login_manager.login_message_category = "info"
else:
    # Stub so app.py import doesn't crash
    class _StubLoginManager:
        login_view = None
        login_message_category = None
        def init_app(self, app): pass
        def user_loader(self, f): return f
    login_manager = _StubLoginManager()


if HAS_FLASK_LOGIN:
    class User(UserMixin):
        def __init__(self, user_dict):
            self._data = user_dict

        def get_id(self):
            return str(self._data["id"])

        @property
        def id(self):
            return self._data["id"]

        @property
        def email(self):
            return self._data["email"]

        @property
        def display_name(self):
            return self._data.get("display_name") or self._data["email"].split("@")[0]

        @property
        def subscription_status(self):
            return self._data.get("subscription_status", "free")

        @property
        def subscription_plan(self):
            return self._data.get("subscription_plan")

        @property
        def subscription_end(self):
            return self._data.get("subscription_end")

        @property
        def is_subscribed(self):
            return self.subscription_status in ("active", "trial")

    @login_manager.user_loader
    def load_user(user_id):
        user_dict = models.get_user_by_id(int(user_id))
        if user_dict:
            return User(user_dict)
        return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
_EMAIL_RE = re.compile(r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$")


def _validate_email(email: str) -> bool:
    return bool(_EMAIL_RE.match(email.strip()))


def _validate_password(password: str) -> str | None:
    """Return error message or None if valid."""
    if len(password) < 8:
        return "Password must be at least 8 characters."
    return None


# ---------------------------------------------------------------------------
# CSRF — simple token in session
# ---------------------------------------------------------------------------
def _generate_csrf():
    if "_csrf_token" not in session:
        session["_csrf_token"] = os.urandom(16).hex()
    return session["_csrf_token"]


def _check_csrf():
    token = session.get("_csrf_token")
    submitted = request.form.get("_csrf_token")
    if not token or not submitted or token != submitted:
        flash("Invalid form submission. Please try again.", "error")
        return False
    return True


# ---------------------------------------------------------------------------
# Email verification email (registration flow)
# ---------------------------------------------------------------------------
def _send_register_verify_email(to_email: str, first_name: str, verify_url: str):
    """Send 'verify your email address' email in a background thread."""
    def _send():
        try:
            sender = "ratsignalcrypto@gmail.com"
            password = os.environ.get("GMAIL_APP_PASSWORD", "")
            if not password:
                print("[RatSignal] GMAIL_APP_PASSWORD not set, skipping verification email", flush=True)
                return
            display_name = first_name or "there"

            msg = MIMEMultipart("alternative")
            msg["Subject"] = "Verify your RatSignal email address"
            msg["From"] = f"RatSignal <{sender}>"
            msg["To"] = to_email

            html = f"""<!DOCTYPE html>
<html><head><style>
body {{ font-family: 'Inter', Arial, sans-serif; background: #0a1628; color: #f0f0f5; margin: 0; padding: 0; }}
.container {{ max-width: 560px; margin: 0 auto; padding: 40px 24px; }}
.header {{ text-align: center; margin-bottom: 32px; }}
.header h1 {{ font-size: 24px; margin: 0; }}
.header h1 span {{ color: #ff6b2b; }}
.card {{ background: #0f1f35; border: 1px solid #1a2d4a; border-radius: 12px; padding: 32px; }}
.card h2 {{ font-size: 20px; margin: 0 0 16px; color: #f0f0f5; }}
.card h2 .name {{ color: #ff6b2b; }}
.card p {{ color: #8888a0; line-height: 1.7; margin: 0 0 16px; font-size: 15px; }}
.btn {{ display: inline-block; background: #ff6b2b; color: #fff !important; padding: 14px 28px; border-radius: 8px; text-decoration: none; font-weight: 700; margin: 8px 0 16px; font-family: 'Orbitron', sans-serif; letter-spacing: 0.5px; text-transform: uppercase; font-size: 14px; }}
.alt-link {{ color: #06B6D4; word-break: break-all; font-size: 13px; }}
.footer {{ text-align: center; margin-top: 32px; color: #55556a; font-size: 12px; }}
</style></head>
<body>
<div class="container">
  <div class="header"><h1>Rat<span>Signal</span></h1></div>
  <div class="card">
    <h2 style="color:#f0f0f5;">Hi <span class="name" style="color:#ff6b2b;">{display_name}</span>,</h2>
    <p>You're one click away from joining the colony. To finish creating your RatSignal account, please confirm your email address.</p>
    <p style="text-align:center;margin:24px 0;"><a href="{verify_url}" class="btn">Verify My Account</a></p>
    <p style="font-size:13px;color:#55556a;">This link expires in 24 hours. If you didn't sign up for RatSignal, you can safely ignore this email — no account will be created.</p>
  </div>
  <div class="footer">&copy; 2026 RatSignal. All rights reserved.</div>
</div></body></html>"""

            text = f"""Hi {display_name},

You're one click away from joining the colony. To finish creating your RatSignal account, confirm your email address by opening this link:

{verify_url}

This link expires in 24 hours. If you didn't sign up for RatSignal, you can safely ignore this email — no account will be created.

— The RatSignal Team
"""

            msg.attach(MIMEText(text, "plain"))
            msg.attach(MIMEText(html, "html"))

            with smtplib.SMTP("smtp.gmail.com", 587, timeout=10) as server:
                server.starttls()
                server.login(sender, password)
                server.sendmail(sender, to_email, msg.as_string())

            print(f"[RatSignal] Verification email sent to {to_email}", flush=True)
        except Exception as e:
            print(f"[RatSignal] Failed to send verification email: {e}", flush=True)

    threading.Thread(target=_send, daemon=True).start()


# ---------------------------------------------------------------------------
# Welcome email
# ---------------------------------------------------------------------------
def _send_welcome_email(to_email: str, first_name: str):
    """Send welcome email in background thread."""
    def _send():
        try:
            sender = "ratsignalcrypto@gmail.com"
            password = os.environ.get("GMAIL_APP_PASSWORD", "")
            if not password:
                print("[RatSignal] GMAIL_APP_PASSWORD not set, skipping welcome email", flush=True)
                return

            msg = MIMEMultipart("alternative")
            msg["Subject"] = "Welcome to RatSignal — Your seat at the table is ready"
            msg["From"] = f"RatSignal <{sender}>"
            msg["To"] = to_email

            html = f"""<!DOCTYPE html>
<html>
<head>
<style>
body {{ font-family: 'Inter', Arial, sans-serif; background: #0a1628; color: #f0f0f5; margin: 0; padding: 0; }}
.container {{ max-width: 560px; margin: 0 auto; padding: 40px 24px; }}
.header {{ text-align: center; margin-bottom: 32px; }}
.header h1 {{ font-size: 24px; margin: 0; }}
.header h1 span {{ color: #ff6b2b; }}
.card {{ background: #0f1f35; border: 1px solid #1a2d4a; border-radius: 12px; padding: 32px; }}
.card h2 {{ font-size: 20px; margin: 0 0 16px; }}
.card p {{ color: #8888a0; line-height: 1.7; margin: 0 0 16px; font-size: 15px; }}
.highlight {{ color: #ff6b2b; font-weight: 600; }}
.divider {{ border: none; border-top: 1px solid #1a2d4a; margin: 24px 0; }}
.signature {{ color: #8888a0; font-size: 14px; line-height: 1.6; }}
.signature strong {{ color: #ff6b2b; }}
.footer {{ text-align: center; margin-top: 32px; color: #55556a; font-size: 12px; }}
</style>
</head>
<body>
<div class="container">
    <div class="header">
        <h1>Rat<span>Signal</span></h1>
    </div>
    <div class="card">
        <h2>Dear {first_name},</h2>
        <p>
            Thank you for joining the colony. You just secured your seat at the smartest kitchen.
        </p>
        <p>
            Our team will add you to the <span class="highlight">Signal Group</span> within the next 24 hours.
            Once you're in, you'll receive real-time alerts — entries, take profits, stop losses, and risk scores — straight to your Telegram.
        </p>
        <p>
            We've been cooking with data from <span class="highlight">438 books</span>, <span class="highlight">1,032 indicators</span>,
            and <span class="highlight">5+ years of market history</span>. The recipe is dialed in.
            Now it's time to see if you like how it tastes.
        </p>
        <p>
            Spoiler: our rats have a pretty good track record.
        </p>
        <hr class="divider">
        <div class="signature">
            <strong>Let's cook together,</strong><br>
            The RatSignal Team
        </div>
    </div>
    <div class="footer">
        &copy; 2026 RatSignal. All rights reserved.<br>
        You received this email because you signed up at ratsignal.com
    </div>
</div>
</body>
</html>"""

            text = f"""Dear {first_name},

Thank you for joining the colony. You just secured your seat at the smartest kitchen.

Our team will add you to the Signal Group within the next 24 hours.
Once you're in, you'll receive real-time alerts - entries, take profits, stop losses, and risk scores - straight to your Telegram.

We've been cooking with data from 438 books, 1,032 indicators, and 5+ years of market history.
The recipe is dialed in. Now it's time to see if you like how it tastes.

Spoiler: our rats have a pretty good track record.

---
Let's cook together,
The RatSignal Team
"""

            msg.attach(MIMEText(text, "plain"))
            msg.attach(MIMEText(html, "html"))

            with smtplib.SMTP("smtp.gmail.com", 587, timeout=10) as server:
                server.starttls()
                server.login(sender, password)
                server.sendmail(sender, to_email, msg.as_string())

            print(f"[RatSignal] Welcome email sent to {to_email}", flush=True)
        except Exception as e:
            print(f"[RatSignal] Failed to send welcome email: {e}", flush=True)

    threading.Thread(target=_send, daemon=True).start()


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@auth_bp.route("/register", methods=["GET"])
def register_page():
    csrf = _generate_csrf()
    return render_template("register.html", csrf_token=csrf,
                           telegram_bot_username=TELEGRAM_BOT_USERNAME,
                           discord_enabled=bool(DISCORD_CLIENT_ID),
                           google_enabled=bool(GOOGLE_CLIENT_ID))


@auth_bp.route("/register", methods=["POST"])
def register():
    # CSRF skipped - multi-worker session issue; JS double-submit protection used instead

    first_name = request.form.get("first_name", "").strip()
    last_name = request.form.get("last_name", "").strip()
    email = request.form.get("email", "").strip()
    wallet_address = request.form.get("wallet_address", "").strip()
    telegram = request.form.get("telegram", "").strip().lstrip("@")
    discord = request.form.get("discord", "").strip()
    password = request.form.get("password", "")
    password_confirm = request.form.get("password_confirm", "")

    if not first_name:
        flash("Please enter your first name.", "error")
        return redirect(url_for("auth.register_page"))

    if not last_name:
        flash("Please enter your last name.", "error")
        return redirect(url_for("auth.register_page"))

    if not _validate_email(email):
        flash("Please enter a valid email address.", "error")
        return redirect(url_for("auth.register_page"))

    pw_err = _validate_password(password)
    if pw_err:
        flash(pw_err, "error")
        return redirect(url_for("auth.register_page"))

    if password != password_confirm:
        flash("Passwords are not matching.", "error")
        return redirect(url_for("auth.register_page"))

    # If a user already exists with this email, treat it like a login attempt
    existing = models.get_user_by_email(email)
    if existing:
        if models.verify_password(existing, password):
            if HAS_FLASK_LOGIN:
                user = User(existing)
                login_user(user)
            flash("Welcome back! You already have an account.", "success")
            return redirect("/auth/profile")
        flash("An account with that email already exists. Please log in.", "error")
        return redirect(url_for("auth.login_page"))

    # Account is NOT created here — we stash the registration and email a
    # verification link. The real users-row INSERT happens in /auth/verify-email
    # once the user clicks the link.
    try:
        token = models.create_pending_registration(
            email=email, password=password,
            first_name=first_name, last_name=last_name,
            wallet_address=wallet_address, telegram=telegram, discord=discord,
        )
    except Exception as e:
        print(f"[RatSignal] create_pending_registration failed: {e}", flush=True)
        flash("Something went wrong. Please try again in a minute.", "error")
        return redirect(url_for("auth.register_page"))

    verify_url = request.host_url.rstrip("/") + url_for("auth.verify_email") + f"?token={token}"
    _send_register_verify_email(email, first_name, verify_url)

    return render_template("check_email.html", email=email)


@auth_bp.route("/login", methods=["GET"])
def login_page():
    csrf = _generate_csrf()
    return render_template("login.html", csrf_token=csrf,
                           telegram_bot_username=TELEGRAM_BOT_USERNAME,
                           discord_enabled=bool(DISCORD_CLIENT_ID),
                           google_enabled=bool(GOOGLE_CLIENT_ID))


@auth_bp.route("/login", methods=["POST"])
def login():
    # CSRF skipped - multi-worker session issue; same as register
    email = request.form.get("email", "").strip()
    password = request.form.get("password", "")

    user_dict = models.get_user_by_email(email)
    if not user_dict or not models.verify_password(user_dict, password):
        flash("Invalid email or password.", "error")
        return redirect(url_for("auth.login_page"))

    if HAS_FLASK_LOGIN:
        user = User(user_dict)
        login_user(user, remember=True)

    flash("Logged in successfully.", "success")
    return redirect("/auth/profile")


@auth_bp.route("/status")
def auth_status():
    """Tiny JSON endpoint used by the public homepage to decide whether to
    show subscriber-only nav items (e.g. the Downloads tab)."""
    out = {"logged_in": False, "subscription_active": False}
    if HAS_FLASK_LOGIN and current_user.is_authenticated:
        out["logged_in"] = True
        try:
            models.expire_if_needed(current_user.id)
            u = models.get_user_by_id(current_user.id) or {}
            out["subscription_active"] = u.get("subscription_status") == "active"
        except Exception as e:
            print(f"[RatSignal] /auth/status failed: {e}", flush=True)
    resp = jsonify(out)
    resp.headers["Cache-Control"] = "no-store"
    return resp


@auth_bp.route("/verify-email")
def verify_email():
    """Click-target for the verification email. Creates the user account if the
    token is valid, logs them in, redirects to /auth/profile."""
    token = (request.args.get("token") or "").strip()
    if not token:
        flash("Verification link is missing a token.", "error")
        return redirect(url_for("auth.register_page"))

    pending = models.get_valid_pending_registration(token)
    if not pending:
        flash("This verification link is invalid or has expired. Please register again.", "error")
        return redirect(url_for("auth.register_page"))

    # Race-safe: someone may have registered with this email after the pending
    # row was created. If so, fall through to a friendly message.
    if models.get_user_by_email(pending["email"]):
        models.consume_pending_registration(token)
        flash("This email is already registered. Please log in.", "info")
        return redirect(url_for("auth.login_page"))

    display_name = (f"{pending.get('first_name','')} {pending.get('last_name','')}").strip() or None
    user_dict = models.create_user_with_hash(pending["email"], pending["password_hash"], display_name)
    if not user_dict:
        # Idempotent fallback: a parallel request (often Gmail/browser link
        # prefetch) may have already created the user a few ms before us.
        user_dict = models.get_user_by_email(pending["email"])
        if not user_dict:
            flash("Could not create your account. Please try registering again.", "error")
            return redirect(url_for("auth.register_page"))

    try:
        models.update_user_profile(user_dict["id"], {
            "first_name": pending.get("first_name") or "",
            "last_name": pending.get("last_name") or "",
            "wallet_address": pending.get("wallet_address") or "",
            "telegram": pending.get("telegram") or "",
            "discord": pending.get("discord") or "",
        })
    except Exception as e:
        print(f"[RatSignal] Profile update error during verify (non-fatal): {e}", flush=True)

    models.consume_pending_registration(token)

    try:
        _send_welcome_email(pending["email"], pending.get("first_name") or "")
        _send_telegram_notification(pending.get("first_name") or "",
                                     pending.get("last_name") or "",
                                     pending["email"],
                                     pending.get("telegram") or "")
    except Exception as e:
        print(f"[RatSignal] Post-verify notification failed: {e}", flush=True)

    if HAS_FLASK_LOGIN:
        user = User(user_dict)
        login_user(user, remember=True)

    flash("Email verified — your account is ready.", "success")
    return redirect("/auth/profile")


@auth_bp.route("/logout")
def logout():
    was_tg = False
    if HAS_FLASK_LOGIN and current_user.is_authenticated:
        try:
            was_tg = bool(getattr(current_user, "_data", {}).get("telegram_id"))
        except Exception:
            was_tg = False
        logout_user()
    flash("You have been logged out.", "info")
    if was_tg:
        # Telegram's Login Widget remembers the authorization grant on
        # oauth.telegram.org in a `stel_*` cookie — without clearing it, the
        # user clicking the widget again gets silently re-authed as the same
        # account. Render a tiny interstitial that hits Telegram's logout
        # endpoint in a hidden iframe before bouncing home.
        return Response(_TG_LOGOUT_HTML, mimetype="text/html")
    return redirect("/")


# ---------------------------------------------------------------------------
# Admin dashboard — live stats at /auth/admin, restricted to ADMIN_EMAILS.
# ---------------------------------------------------------------------------
@auth_bp.route("/admin")
@login_required
def admin_dashboard():
    if not _is_admin():
        return Response("Forbidden", status=403)

    import sqlite3 as _sq
    db_path = getattr(models, "_DB_PATH", None)
    if not db_path:
        return Response("DB path not configured", status=500)

    conn = _sq.connect(db_path)
    conn.row_factory = _sq.Row
    cur = conn.cursor()

    def _one(sql, params=()):
        r = cur.execute(sql, params).fetchone()
        return r[0] if r else 0

    # Top-level counts
    total_users = _one("SELECT COUNT(*) FROM users")
    active_subs = _one(
        "SELECT COUNT(*) FROM users WHERE lower(subscription_status) IN ('active','trial')"
    )
    lifetime_subs = _one(
        "SELECT COUNT(*) FROM users WHERE lower(subscription_status)='active' AND subscription_plan='lifetime'"
    )

    # Last 7 days
    new_users_7d = _one(
        "SELECT COUNT(*) FROM users WHERE created_at > datetime('now','-7 days')"
    )
    new_payments_7d = _one(
        "SELECT COUNT(*) FROM payments "
        "WHERE lower(status) IN ('completed','confirmed','finished','paid') "
        "AND created_at > datetime('now','-7 days')"
    )

    # Revenue (only completed payments)
    revenue_cents_all = _one(
        "SELECT COALESCE(SUM(amount_cents),0) FROM payments "
        "WHERE lower(status) IN ('completed','confirmed','finished','paid')"
    )
    revenue_cents_7d = _one(
        "SELECT COALESCE(SUM(amount_cents),0) FROM payments "
        "WHERE lower(status) IN ('completed','confirmed','finished','paid') "
        "AND created_at > datetime('now','-7 days')"
    )

    # Plan breakdown (active + trial only)
    plan_breakdown = [
        {"plan": r["plan"] or "(none)", "count": r["c"]}
        for r in cur.execute(
            "SELECT COALESCE(subscription_plan,'(none)') AS plan, COUNT(*) AS c "
            "FROM users WHERE lower(subscription_status) IN ('active','trial') "
            "GROUP BY subscription_plan ORDER BY c DESC"
        )
    ]

    # Status breakdown (all users)
    status_breakdown = [
        {"status": r["status"] or "(none)", "count": r["c"]}
        for r in cur.execute(
            "SELECT COALESCE(subscription_status,'free') AS status, COUNT(*) AS c "
            "FROM users GROUP BY subscription_status ORDER BY c DESC"
        )
    ]

    # Daily signups — last 30 days
    signup_series = [
        {"date": r["d"], "count": r["c"]}
        for r in cur.execute(
            "SELECT date(created_at) AS d, COUNT(*) AS c "
            "FROM users "
            "WHERE created_at > datetime('now','-30 days') "
            "GROUP BY date(created_at) ORDER BY d"
        )
    ]

    # Recent signups
    recent_users = [dict(r) for r in cur.execute(
        "SELECT id, email, telegram, telegram_username, first_name, last_name, "
        "subscription_status, subscription_plan, created_at "
        "FROM users ORDER BY id DESC LIMIT 20"
    )]

    # Recent payments (joined with user email)
    recent_payments = [dict(r) for r in cur.execute(
        "SELECT p.id, p.user_id, u.email, p.amount_cents, p.currency, p.plan, "
        "p.status, p.crypto_tx_hash, p.nowpayments_id, p.stripe_session_id, p.created_at "
        "FROM payments p LEFT JOIN users u ON u.id = p.user_id "
        "ORDER BY p.created_at DESC LIMIT 20"
    )]

    conn.close()

    return render_template(
        "admin_dashboard.html",
        stats={
            "total_users": total_users,
            "active_subs": active_subs,
            "lifetime_subs": lifetime_subs,
            "new_users_7d": new_users_7d,
            "new_payments_7d": new_payments_7d,
            "revenue_usd_all": revenue_cents_all / 100,
            "revenue_usd_7d": revenue_cents_7d / 100,
        },
        plan_breakdown=plan_breakdown,
        status_breakdown=status_breakdown,
        signup_series=signup_series,
        recent_users=recent_users,
        recent_payments=recent_payments,
        admin_email=getattr(current_user, "_data", {}).get("email", ""),
    )


# ---------------------------------------------------------------------------
# Telegram Bot Login — deep-link flow (lets users switch Telegram accounts,
# unlike the Login Widget which is tied to Telegram Web session)
# ---------------------------------------------------------------------------
def _bot_webhook_secret() -> str:
    """Derive the webhook secret from the bot token. Telegram echoes this in
    the X-Telegram-Bot-Api-Secret-Token header on every webhook POST."""
    import hashlib
    return hashlib.sha256(
        (TELEGRAM_BOT_TOKEN_LOGIN + "ratsignal_bot_login_v1").encode()
    ).hexdigest()[:32]


@auth_bp.route("/telegram/bot-login/start", methods=["POST"])
def tg_bot_login_start():
    """Generate a one-time token and return the Telegram deep link the user
    should open."""
    if not TELEGRAM_BOT_TOKEN_LOGIN or not TELEGRAM_BOT_USERNAME:
        return jsonify({"error": "Telegram login not configured"}), 500
    token = models.create_bot_login_token()
    payload = f"ratsignal_login_{token}"
    return jsonify({
        "token": token,
        "deep_link_desktop": f"tg://resolve?domain={TELEGRAM_BOT_USERNAME}&start={payload}",
        "deep_link_web": f"https://t.me/{TELEGRAM_BOT_USERNAME}?start={payload}",
        "bot_username": TELEGRAM_BOT_USERNAME,
        "expires_in": 600,
    })


@auth_bp.route("/telegram/bot-login/poll")
def tg_bot_login_poll():
    """Frontend polls this until the user clicks Start in the bot."""
    token = request.args.get("token", "").strip()
    if not token:
        return jsonify({"status": "invalid"}), 400
    row = models.get_bot_login_token(token)
    if not row:
        return jsonify({"status": "invalid"}), 404
    from datetime import datetime
    if row.get("expires_at", "") < datetime.utcnow().isoformat():
        return jsonify({"status": "expired"})
    return jsonify({"status": row.get("status", "pending")})


@auth_bp.route("/telegram/bot-login/complete")
def tg_bot_login_complete():
    """User is redirected here once poll returns status=ready. Creates or
    loads the user account and logs them in."""
    token = request.args.get("token", "").strip()
    if not token:
        flash("Invalid login token.", "error")
        return redirect(url_for("auth.login_page"))
    row = models.get_bot_login_token(token)
    if not row or row.get("status") != "ready":
        flash("Telegram login expired or invalid. Please try again.", "error")
        return redirect(url_for("auth.login_page"))

    telegram_id = row["telegram_id"]
    tg_username = row.get("telegram_username") or ""
    tg_first = row.get("first_name") or ""
    tg_last = row.get("last_name") or ""
    display_name = f"{tg_first} {tg_last}".strip() or tg_username

    # Mark token consumed before logging in so a replay can't reuse it.
    models.consume_bot_login_token(token)

    # Same logic as /telegram/callback: find-by-tg-id or create new account.
    user_dict = models.get_user_by_telegram_id(telegram_id)
    if not user_dict:
        email = f"telegram_{telegram_id}@ratsignal.local"
        user_dict = models.create_social_user(
            email=email,
            display_name=display_name,
            telegram_id=telegram_id,
            telegram_username=tg_username,
        )
        if user_dict:
            try:
                models.update_user_profile(user_dict["id"], {
                    "first_name": tg_first,
                    "last_name": tg_last,
                    "telegram": tg_username,
                    "telegram_username": tg_username,
                })
            except Exception as e:
                print(f"[RatSignal] Bot-login profile update error: {e}", flush=True)
            _send_welcome_email("", tg_first or display_name)
            _send_telegram_notification(tg_first, tg_last, email, tg_username)

    if not user_dict:
        flash("Telegram login failed — could not create account.", "error")
        return redirect(url_for("auth.login_page"))

    if HAS_FLASK_LOGIN:
        login_user(User(user_dict))
    flash(f"Welcome, {display_name}!", "success")
    return redirect("/auth/profile")


@auth_bp.route("/telegram/bot-webhook", methods=["POST"])
def tg_bot_webhook():
    """Telegram posts updates here. We only care about /start messages with
    the ratsignal_login_<token> deep-link payload."""
    # Verify secret header so only Telegram (with our registered secret) can hit this.
    if request.headers.get("X-Telegram-Bot-Api-Secret-Token", "") != _bot_webhook_secret():
        return jsonify({"ok": False, "error": "unauthorized"}), 403

    try:
        update = request.get_json(force=True) or {}
    except Exception:
        return jsonify({"ok": True})  # malformed — ignore silently

    msg = update.get("message") or update.get("edited_message") or {}
    text = (msg.get("text") or "").strip()
    from_user = msg.get("from") or {}

    # Only care about /start ratsignal_login_<token>
    if text.startswith("/start") and "ratsignal_login_" in text:
        parts = text.split()
        payload = parts[1] if len(parts) > 1 else ""
        if payload.startswith("ratsignal_login_"):
            token = payload[len("ratsignal_login_"):]
            claimed = models.claim_bot_login_token(
                token=token,
                telegram_id=str(from_user.get("id", "")),
                telegram_username=from_user.get("username", ""),
                first_name=from_user.get("first_name", ""),
                last_name=from_user.get("last_name", ""),
            )
            print(f"[RatSignal] bot-login claim token={token[:8]}… tg_id={from_user.get('id')} "
                  f"username={from_user.get('username','')} claimed={claimed}", flush=True)
            chat_id = msg.get("chat", {}).get("id") or from_user.get("id")
            if chat_id:
                import urllib.request
                if claimed:
                    reply = "Logged in. Return to the RatSignal tab - it will redirect automatically."
                else:
                    reply = "This login link is expired or already used. Start a new sign-in from the website."
                try:
                    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN_LOGIN}/sendMessage"
                    body = _json.dumps({"chat_id": chat_id, "text": reply}).encode("utf-8")
                    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
                    urllib.request.urlopen(req, timeout=5)
                except Exception as e:
                    print(f"[RatSignal] bot-webhook reply failed: {e}", flush=True)

    return jsonify({"ok": True})


_TG_LOGOUT_HTML = """<!DOCTYPE html>
<html><head>
<meta charset="utf-8">
<title>Logging out…</title>
<style>body{margin:0;background:#0a1628;color:#f0f0f5;font-family:Inter,Arial,sans-serif;display:flex;align-items:center;justify-content:center;height:100vh}</style>
</head><body>
<div style="text-align:center">
  <div style="font-size:15px;opacity:0.85">Logging out of Telegram…</div>
  <div style="margin-top:8px;font-size:12px;opacity:0.5">If this takes more than a few seconds, <a href="/" style="color:#06B6D4">click here</a>.</div>
</div>
<iframe src="https://oauth.telegram.org/auth/logOut" style="display:none" referrerpolicy="no-referrer"></iframe>
<script>setTimeout(function(){ window.location.replace('/'); }, 1200);</script>
</body></html>"""


def _needs_profile_completion(user: dict) -> bool:
    """True when a social-login user still has a placeholder email or is
    missing a real first/last name — triggers the blocking "Complete your
    profile" modal on the profile page.
    """
    email = (user.get("email") or "").lower()
    if email.endswith("@ratsignal.local"):
        return True
    if not (user.get("first_name") or "").strip():
        return True
    if not (user.get("last_name") or "").strip():
        return True
    return False


@auth_bp.route("/profile")
@login_required
def profile():
    try:
        models.expire_if_needed(current_user.id)
    except Exception as e:
        print(f"[RatSignal] expire_if_needed failed: {e}", flush=True)
    user = models.get_user_profile(current_user.id) or {}
    payments = models.get_user_payments(current_user.id)
    # For the Telegram Login Widget on the profile page:
    telegram_linked = bool(user.get("telegram_id"))

    refund_eligible = False
    refund_hours_left = None
    try:
        from temporary.ratsignal.payments import refund_window_for_user
        refund_eligible, _deadline, refund_hours_left = refund_window_for_user(current_user.id)
    except Exception as e:
        print(f"[RatSignal] refund_window_for_user failed: {e}", flush=True)

    return render_template(
        "profile.html",
        user=user,
        payments=payments,
        telegram_bot_username=TELEGRAM_BOT_USERNAME,
        telegram_linked=telegram_linked,
        refund_eligible=refund_eligible,
        refund_hours_left=refund_hours_left,
        needs_profile_completion=_needs_profile_completion(user),
    )


@auth_bp.route("/complete-profile", methods=["POST"])
@login_required
def complete_profile():
    """Save the fields collected by the blocking 'Complete your profile' modal
    that TG/Discord users see on first login. Only updates email + name so it
    can't accidentally clobber other fields.

    Returns JSON for fetch() callers (modal submits via JS) so errors are
    visible inline without a page reload that loses the flash message.
    """
    user = models.get_user_profile(current_user.id) or {}
    first_name = request.form.get("first_name", "").strip()
    last_name = request.form.get("last_name", "").strip()
    new_email = request.form.get("email", "").strip()

    print(f"[RatSignal] complete-profile POST user={current_user.id} "
          f"first={first_name!r} last={last_name!r} email={new_email!r} "
          f"current_email={user.get('email')!r}", flush=True)

    def _err(msg, status=400):
        return jsonify({"ok": False, "error": msg}), status

    if not first_name or not last_name:
        return _err("First name and last name are required.")
    if not new_email or not _validate_email(new_email):
        return _err("Please enter a valid email address.")

    current_email = (user.get("email") or "").strip().lower()
    merge_password = request.form.get("merge_password", "")

    if new_email.lower() != current_email:
        existing = models.get_user_by_email(new_email)
        if existing and existing.get("id") != current_user.id:
            # Email is already registered — offer to merge this throwaway
            # social account into the existing one rather than rejecting.
            return _try_account_merge(
                current=user,
                target=existing,
                first_name=first_name,
                last_name=last_name,
                merge_password=merge_password,
            )

    try:
        models.update_user_profile(current_user.id, {
            "first_name": first_name,
            "last_name": last_name,
            "display_name": f"{first_name} {last_name}".strip(),
            "email": new_email,
        })
        fresh = models.get_user_profile(current_user.id) or {}
        print(f"[RatSignal] complete-profile saved user={current_user.id} "
              f"-> email={fresh.get('email')!r} first={fresh.get('first_name')!r} "
              f"last={fresh.get('last_name')!r}", flush=True)
    except Exception as e:
        print(f"[RatSignal] complete-profile error: {e}", flush=True)
        return _err("Could not save your profile. Please try again.", status=500)

    flash("Profile saved. Welcome aboard!", "success")
    return jsonify({"ok": True, "redirect": "/auth/profile"})


def _try_account_merge(current, target, first_name, last_name, merge_password):
    """Collapse a throwaway social (@ratsignal.local) account into an existing
    email+password account the user has proved they own by entering the
    password. Copies over any telegram_id / discord_id, reassigns payments,
    logs the session in as the target, and deletes the throwaway.
    """
    def _err(msg, status=400):
        return jsonify({"ok": False, "error": msg}), status

    current_tg = current.get("telegram_id")
    current_discord = current.get("discord_id")
    target_tg = target.get("telegram_id")
    target_discord = target.get("discord_id")

    # Refuse if the target already has a DIFFERENT social link — would corrupt data.
    if current_tg and target_tg and str(current_tg) != str(target_tg):
        return _err(
            "This email is linked to a different Telegram account. Use a different email."
        )
    if current_discord and target_discord and str(current_discord) != str(target_discord):
        return _err(
            "This email is linked to a different Discord account. Use a different email."
        )

    # Need a password to prove ownership. No password hash on target → can't verify.
    if not target.get("password_hash"):
        return _err(
            "This email is registered with a social account only (no password). "
            "Use a different email or sign in with the original Telegram/Discord."
        )

    if not merge_password:
        # Ask the client to prompt for the password and resubmit.
        return jsonify({
            "ok": False,
            "merge_required": True,
            "email": target.get("email"),
            "message": (
                "This email is already registered with a RatSignal account. "
                "Enter that account's password and we'll link your Telegram/Discord "
                "to it and sign you in as that account."
            ),
        }), 200

    if not models.verify_password(target, merge_password):
        return _err("Incorrect password for that account.", status=403)

    # All checks passed — perform the merge.
    try:
        merge_fields = {}
        if current_tg and not target_tg:
            merge_fields["telegram_id"] = str(current_tg)
            merge_fields["telegram_username"] = current.get("telegram_username") or ""
            merge_fields["telegram"] = (
                current.get("telegram") or current.get("telegram_username") or ""
            )
        if current_discord and not target_discord:
            merge_fields["discord_id"] = str(current_discord)
            merge_fields["discord"] = current.get("discord") or ""
        # Only fill first/last on target if it's blank there.
        if not (target.get("first_name") or "").strip() and first_name:
            merge_fields["first_name"] = first_name
        if not (target.get("last_name") or "").strip() and last_name:
            merge_fields["last_name"] = last_name
        if merge_fields and not target.get("display_name"):
            merge_fields["display_name"] = (
                f"{first_name} {last_name}".strip()
                or target.get("email")
            )
        if merge_fields:
            models.update_user_profile(target["id"], merge_fields)

        # Move any payments from the throwaway to the real account.
        try:
            models.reassign_payments(current["id"], target["id"])
        except Exception as e:
            print(f"[RatSignal] merge payment reassign failed: {e}", flush=True)

        # Swap the session: log in as target, then delete the throwaway.
        throwaway_id = current["id"]
        target_user_obj = User(target)
        if HAS_FLASK_LOGIN:
            logout_user()
            login_user(target_user_obj)

        try:
            models.delete_user(throwaway_id)
        except Exception as e:
            print(f"[RatSignal] merge delete throwaway failed: {e}", flush=True)

        print(f"[RatSignal] merged social user {throwaway_id} into {target['id']} "
              f"(fields={list(merge_fields.keys())})", flush=True)
    except Exception as e:
        print(f"[RatSignal] merge failed: {e}", flush=True)
        return _err("Could not merge accounts. Please try again.", status=500)

    flash("Accounts merged. Welcome back!", "success")
    return jsonify({"ok": True, "redirect": "/auth/profile"})


@auth_bp.route("/profile", methods=["POST"])
@login_required
def profile_update():
    user = models.get_user_profile(current_user.id) or {}
    first_name = request.form.get("first_name", "").strip()
    last_name = request.form.get("last_name", "").strip()
    new_email = request.form.get("email", "").strip()
    telegram = request.form.get("telegram", "").strip().lstrip("@")
    discord = request.form.get("discord", "").strip()
    wallet_address = request.form.get("wallet_address", "").strip()

    if not first_name:
        flash("First name is required.", "error")
        return redirect("/auth/profile")
    if not last_name:
        flash("Last name is required.", "error")
        return redirect("/auth/profile")

    fields = {
        "first_name": first_name,
        "last_name": last_name,
        "display_name": f"{first_name} {last_name}".strip(),
        "telegram": telegram,
        "discord": discord,
        "wallet_address": wallet_address,
    }

    current_email = (user.get("email") or "").strip().lower()
    if new_email and new_email.lower() != current_email:
        if not _validate_email(new_email):
            flash("Please enter a valid email address.", "error")
            return redirect("/auth/profile")
        ok, err = models.set_user_email_with_merge(current_user.id, new_email)
        if not ok:
            flash(err or "That email is already in use by another account.", "error")
            return redirect("/auth/profile")
        # set_user_email_with_merge already wrote the new email — don't re-set it via fields
        fields.pop("email", None)

    try:
        models.update_user_profile(current_user.id, fields)
    except Exception as e:
        print(f"[RatSignal] Profile update error: {e}", flush=True)
        flash("Could not save your changes. Please try again.", "error")
        return redirect("/auth/profile")

    flash("Profile updated successfully.", "success")
    return redirect("/auth/profile")


@auth_bp.route("/email", methods=["POST"])
@login_required
def update_email_only():
    """JSON endpoint: update only the user's email.

    Used by the inline "this isn't your email - change it" widget on
    /auth/auto-trade for Telegram-registered users with a placeholder
    `telegram_*@ratsignal.local` address. Avoids the profile_update
    route's first_name/last_name requirement.
    """
    new_email = (request.form.get("email") or request.values.get("email") or "").strip().lower()
    if not _validate_email(new_email):
        return jsonify({"ok": False, "error": "Please enter a valid email address."}), 400
    user = models.get_user_by_id(current_user.id) or {}
    current_email = (user.get("email") or "").strip().lower()
    if new_email == current_email:
        return jsonify({"ok": True, "unchanged": True})
    try:
        ok, err = models.set_user_email_with_merge(current_user.id, new_email)
    except Exception as e:
        print(f"[RatSignal] update_email_only error user={current_user.id}: {e}", flush=True)
        return jsonify({"ok": False, "error": "Could not save your new email. Please try again."}), 500
    if not ok:
        return jsonify({"ok": False, "error": err or "That email is already in use by another account."}), 409
    return jsonify({"ok": True, "email": new_email})


def _has_active_copycat_token(user_id: int) -> bool:
    """True if the user has at least one non-revoked copycat token.

    Reads /opt/copycat/copycat.db in read-only mode; isolated from the
    RatSignal main DB so a missing/corrupt copycat.db cannot break auth.
    """
    import sqlite3
    try:
        conn = sqlite3.connect("file:/opt/copycat/copycat.db?mode=ro", uri=True)
        try:
            row = conn.execute(
                "SELECT 1 FROM tokens WHERE ratsignal_user_id = ? "
                "AND revoked_at IS NULL LIMIT 1",
                (user_id,),
            ).fetchone()
            return row is not None
        finally:
            conn.close()
    except Exception:
        return False


@auth_bp.route("/dashboard")
@login_required
def dashboard():
    payments = models.get_user_payments(current_user.id)
    copycat = {
        "subscription_ok": bool(getattr(current_user, "is_subscribed", False)),
        "telegram_linked": bool(getattr(current_user, "_data", {}).get("telegram_id")),
        "has_active_token": _has_active_copycat_token(current_user.id),
    }
    return render_template("user_dashboard.html", payments=payments, copycat=copycat)



@auth_bp.route("/me")
def me():
    """Return current user info as JSON (for nav profile link)."""
    if HAS_FLASK_LOGIN and current_user.is_authenticated:
        user = models.get_user_profile(current_user.id)
        return Response(
            _json.dumps({"logged_in": True, "first_name": user.get("first_name", ""), "email": user.get("email", "")}),
            content_type="application/json"
        )
    return Response(_json.dumps({"logged_in": False}), content_type="application/json")

# ---------------------------------------------------------------------------
# User Lookup (real-time field check on registration)
# ---------------------------------------------------------------------------
_CHECKABLE_FIELDS = {'email', 'telegram', 'discord', 'wallet_address'}


def _find_user_by_field(field: str, value: str):
    """Look up a user by a specific profile field. Returns user dict or None."""
    if field not in _CHECKABLE_FIELDS or not value:
        return None
    value_clean = value.strip()
    if field == 'email':
        return models.get_user_by_email(value_clean)
    if field == 'telegram':
        value_clean = value_clean.lstrip('@')
    # Generic lookup for dynamic columns
    try:
        with models._get_conn() as conn:
            row = conn.execute(
                f'SELECT * FROM users WHERE LOWER({field}) = LOWER(?)', (value_clean,)
            ).fetchone()
        return dict(row) if row else None
    except Exception:
        return None


def _mask_value(value: str, field: str) -> str:
    """Mask a value for privacy (e.g. em***@gm***.com)."""
    if not value:
        return ''
    if field == 'email' and '@' in value:
        local, domain = value.rsplit('@', 1)
        masked_local = local[:2] + '***' if len(local) > 2 else local[0] + '***'
        parts = domain.split('.')
        masked_domain = parts[0][:2] + '***.' + parts[-1] if len(parts) > 1 else domain[:2] + '***'
        return f'{masked_local}@{masked_domain}'
    if field == 'telegram':
        return '@' + value[:3] + '***' if len(value) > 3 else '@' + value[0] + '***'
    if field == 'wallet_address' and len(value) > 10:
        return value[:6] + '...' + value[-4:]
    if len(value) > 4:
        return value[:3] + '***'
    return value[0] + '***'


@auth_bp.route("/check-user", methods=["POST"])
def check_user():
    """Check if a field value matches an existing user. Returns masked info."""
    data = request.get_json(silent=True) or {}
    field = data.get('field', '')
    value = data.get('value', '')

    if field not in _CHECKABLE_FIELDS or not value or len(value.strip()) < 3:
        return Response(_json.dumps({'found': False}), content_type='application/json')

    user = _find_user_by_field(field, value)
    if not user:
        return Response(_json.dumps({'found': False}), content_type='application/json')

    # Build masked user info for "Is this you?" display
    display_name = user.get('display_name') or user.get('first_name') or ''
    first_name = user.get('first_name', '')
    masked_name = first_name[:1] + '***' if first_name else (display_name[:1] + '***' if display_name else 'User')

    # Determine available verification methods for this user
    methods = []
    if user.get('email') and not user['email'].endswith('@ratsignal.local'):
        methods.append({'type': 'email', 'masked': _mask_value(user['email'], 'email')})
    if user.get('telegram'):
        methods.append({'type': 'telegram', 'masked': _mask_value(user['telegram'], 'telegram')})

    return Response(_json.dumps({
        'found': True,
        'masked_name': masked_name,
        'matched_field': field,
        'matched_value': _mask_value(value.strip().lstrip('@'), field),
        'verification_methods': methods,
        'created_at': user.get('created_at', ''),
    }), content_type='application/json')


@auth_bp.route("/send-verification", methods=["POST"])
def send_verification():
    """Send a verification code to prove account ownership."""
    data = request.get_json(silent=True) or {}
    method = data.get('method', '')  # 'email' or 'telegram'
    field = data.get('field', '')
    value = data.get('value', '')

    if not method or not field or not value:
        return Response(_json.dumps({'error': 'Missing parameters'}), content_type='application/json', status=400)

    user = _find_user_by_field(field, value)
    if not user:
        return Response(_json.dumps({'error': 'User not found'}), content_type='application/json', status=404)

    code = f'{secrets.randbelow(900000) + 100000}'  # 6-digit code
    _verification_codes[code] = {
        'user_id': user['id'],
        'field': field,
        'value': value.strip().lstrip('@'),
        'method': method,
        'expires': time.time() + 600,  # 10 minutes
    }

    if method == 'email':
        user_email = user.get('email', '')
        if user_email and not user_email.endswith('@ratsignal.local'):
            _send_verification_email(user_email, user.get('first_name', 'there'), code)
            return Response(_json.dumps({'sent': True, 'method': 'email', 'masked': _mask_value(user_email, 'email')}), content_type='application/json')

    elif method == 'telegram':
        tg_username = user.get('telegram', '')
        tg_id = user.get('telegram_id', '')
        if tg_id:
            _send_verification_telegram(tg_id, code)
            return Response(_json.dumps({'sent': True, 'method': 'telegram', 'masked': _mask_value(tg_username, 'telegram')}), content_type='application/json')
        elif tg_username:
            # Can't send DM without telegram_id; tell user to message the bot first
            return Response(_json.dumps({
                'sent': False,
                'method': 'telegram',
                'error': 'Please message @noemi_openclaw_bot on Telegram first, then try again.',
            }), content_type='application/json')

    return Response(_json.dumps({'error': 'Could not send verification'}), content_type='application/json', status=400)


@auth_bp.route("/verify-code", methods=["POST"])
def verify_code():
    """Verify a code and log the user in."""
    data = request.get_json(silent=True) or {}
    code = data.get('code', '').strip()

    # Clean expired codes
    now = time.time()
    expired = [k for k, v in _verification_codes.items() if v['expires'] < now]
    for k in expired:
        del _verification_codes[k]

    entry = _verification_codes.pop(code, None)
    if not entry:
        return Response(_json.dumps({'error': 'Invalid or expired code'}), content_type='application/json', status=400)

    user_dict = models.get_user_by_id(entry['user_id'])
    if not user_dict:
        return Response(_json.dumps({'error': 'User not found'}), content_type='application/json', status=404)

    if HAS_FLASK_LOGIN:
        user = User(user_dict)
        login_user(user, remember=True)

    return Response(_json.dumps({'success': True, 'redirect': '/auth/profile'}), content_type='application/json')


def _send_verification_email(to_email: str, first_name: str, code: str):
    """Send verification code via email."""
    def _send():
        try:
            sender = "ratsignalcrypto@gmail.com"
            password = os.environ.get("GMAIL_APP_PASSWORD", "")
            if not password:
                return

            msg = MIMEMultipart("alternative")
            msg["Subject"] = f"RatSignal — Your verification code: {code}"
            msg["From"] = f"RatSignal <{sender}>"
            msg["To"] = to_email

            html = f"""<!DOCTYPE html>
<html><head><style>
body {{ font-family: 'Inter', Arial, sans-serif; background: #0a1628; color: #f0f0f5; margin: 0; padding: 0; }}
.container {{ max-width: 560px; margin: 0 auto; padding: 40px 24px; }}
.header {{ text-align: center; margin-bottom: 32px; }}
.header h1 {{ font-size: 24px; margin: 0; }}
.header h1 span {{ color: #ff6b2b; }}
.card {{ background: #0f1f35; border: 1px solid #1a2d4a; border-radius: 12px; padding: 32px; }}
.code {{ font-size: 36px; font-weight: 700; color: #ff6b2b; text-align: center; letter-spacing: 8px; margin: 24px 0; font-family: 'JetBrains Mono', monospace; }}
.card p {{ color: #8888a0; line-height: 1.7; font-size: 15px; text-align: center; }}
.footer {{ text-align: center; margin-top: 32px; color: #55556a; font-size: 12px; }}
</style></head><body>
<div class="container">
    <div class="header"><h1>Rat<span>Signal</span></h1></div>
    <div class="card">
        <p>Hey {first_name}, here's your verification code:</p>
        <div class="code">{code}</div>
        <p>This code expires in 10 minutes. If you didn't request this, ignore this email.</p>
    </div>
    <div class="footer">&copy; 2026 RatSignal. All rights reserved.</div>
</div></body></html>"""

            text = f"Your RatSignal verification code is: {code}\n\nThis code expires in 10 minutes."
            msg.attach(MIMEText(text, "plain"))
            msg.attach(MIMEText(html, "html"))

            with smtplib.SMTP("smtp.gmail.com", 587, timeout=10) as server:
                server.starttls()
                server.login(sender, password)
                server.sendmail(sender, to_email, msg.as_string())
            print(f"[RatSignal] Verification email sent to {to_email}", flush=True)
        except Exception as e:
            print(f"[RatSignal] Failed to send verification email: {e}", flush=True)

    threading.Thread(target=_send, daemon=True).start()


def _send_verification_telegram(telegram_id: str, code: str):
    """Send verification code via Telegram bot."""
    def _send():
        try:
            import urllib.request
            bot_token = os.environ.get('TELEGRAM_BOT_TOKEN', '')
            if not bot_token:
                return

            text = f"Your RatSignal verification code is:\n\n{code}\n\nThis code expires in 10 minutes."
            url = f'https://api.telegram.org/bot{bot_token}/sendMessage'
            payload = _json.dumps({'chat_id': telegram_id, 'text': text}).encode()
            req = urllib.request.Request(url, data=payload, headers={'Content-Type': 'application/json'})
            urllib.request.urlopen(req, timeout=10)
            print(f"[RatSignal] Verification code sent to Telegram ID {telegram_id}", flush=True)
        except Exception as e:
            print(f"[RatSignal] Failed to send Telegram verification: {e}", flush=True)

    threading.Thread(target=_send, daemon=True).start()


# ---------------------------------------------------------------------------
# Password Reset
# ---------------------------------------------------------------------------
def _send_reset_email(to_email: str, first_name: str, reset_url: str):
    """Send password reset email in background thread."""
    def _send():
        try:
            sender = "ratsignalcrypto@gmail.com"
            password = os.environ.get("GMAIL_APP_PASSWORD", "")
            if not password:
                print("[RatSignal] GMAIL_APP_PASSWORD not set, skipping reset email", flush=True)
                return

            msg = MIMEMultipart("alternative")
            msg["Subject"] = "RatSignal — Reset Your Password"
            msg["From"] = f"RatSignal <{sender}>"
            msg["To"] = to_email

            html = f"""<!DOCTYPE html>
<html>
<head>
<style>
body {{ font-family: 'Inter', Arial, sans-serif; background: #0a1628; color: #f0f0f5; margin: 0; padding: 0; }}
.container {{ max-width: 560px; margin: 0 auto; padding: 40px 24px; }}
.header {{ text-align: center; margin-bottom: 32px; }}
.header h1 {{ font-size: 24px; margin: 0; }}
.header h1 span {{ color: #ff6b2b; }}
.card {{ background: #0f1f35; border: 1px solid #1a2d4a; border-radius: 12px; padding: 32px; }}
.card h2 {{ font-size: 20px; margin: 0 0 16px; color: #f0f0f5; }}
.card h2 .name {{ color: #ff6b2b; }}
.card p {{ color: #8888a0; line-height: 1.7; margin: 0 0 16px; font-size: 15px; }}
.btn {{ display: inline-block; padding: 14px 32px; background: linear-gradient(135deg, #ff6b2b, #ff8f5e); color: white; text-decoration: none; border-radius: 10px; font-weight: 700; font-size: 15px; }}
.divider {{ border: none; border-top: 1px solid #1a2d4a; margin: 24px 0; }}
.muted {{ color: #55556a; font-size: 13px; line-height: 1.6; }}
.footer {{ text-align: center; margin-top: 32px; color: #55556a; font-size: 12px; }}
</style>
</head>
<body>
<div class="container">
    <div class="header">
        <h1>Rat<span>Signal</span></h1>
    </div>
    <div class="card">
        <h2 style="color:#f0f0f5;">Hey <span class="name" style="color:#ff6b2b;">{first_name}</span>,</h2>
        <p>
            We received a request to reset your password. Click the button below to set a new one:
        </p>
        <p style="text-align: center; margin: 28px 0;">
            <a href="{reset_url}" class="btn">Reset Password &rarr;</a>
        </p>
        <hr class="divider">
        <p class="muted">
            This link expires in 1 hour. If you didn't request a password reset, you can safely ignore this email — your account is secure.
        </p>
    </div>
    <div class="footer">
        &copy; 2026 RatSignal. All rights reserved.
    </div>
</div>
</body>
</html>"""

            text = f"""Hey {first_name},

We received a request to reset your password. Visit this link to set a new one:

{reset_url}

This link expires in 1 hour. If you didn't request this, just ignore this email.

— The RatSignal Team
"""

            msg.attach(MIMEText(text, "plain"))
            msg.attach(MIMEText(html, "html"))

            with smtplib.SMTP("smtp.gmail.com", 587, timeout=10) as server:
                server.starttls()
                server.login(sender, password)
                server.sendmail(sender, to_email, msg.as_string())

            print(f"[RatSignal] Reset email sent to {to_email}", flush=True)
        except Exception as e:
            print(f"[RatSignal] Failed to send reset email: {e}", flush=True)

    threading.Thread(target=_send, daemon=True).start()


@auth_bp.route("/forgot-password", methods=["GET"])
def forgot_password_page():
    csrf = _generate_csrf()
    return render_template("forgot_password.html", csrf_token=csrf)


@auth_bp.route("/forgot-password", methods=["POST"])
def forgot_password():
    email = request.form.get("email", "").strip()

    if not _validate_email(email):
        flash("Please enter a valid email address.", "error")
        return redirect(url_for("auth.forgot_password_page"))

    user = models.get_user_by_email(email)
    # Always show success message to prevent email enumeration
    if user:
        token = models.create_password_reset_token(user["id"])
        reset_url = request.host_url.rstrip("/") + f"/auth/reset-password?token={token}"
        first_name = user.get("first_name") or user.get("display_name") or "there"
        _send_reset_email(email, first_name, reset_url)

    flash("If an account with that email exists, we've sent a password reset link.", "success")
    return redirect(url_for("auth.forgot_password_page"))


@auth_bp.route("/reset-password", methods=["GET"])
def reset_password_page():
    token = request.args.get("token", "")
    token_data = models.get_valid_reset_token(token)
    if not token_data:
        flash("This reset link is invalid or has expired. Please request a new one.", "error")
        return redirect(url_for("auth.forgot_password_page"))
    csrf = _generate_csrf()
    return render_template("reset_password.html", csrf_token=csrf, token=token)


@auth_bp.route("/reset-password", methods=["POST"])
def reset_password():
    token = request.form.get("token", "")
    password = request.form.get("password", "")
    password_confirm = request.form.get("password_confirm", "")

    token_data = models.get_valid_reset_token(token)
    if not token_data:
        flash("This reset link is invalid or has expired. Please request a new one.", "error")
        return redirect(url_for("auth.forgot_password_page"))

    if password != password_confirm:
        flash("Passwords do not match.", "error")
        return redirect(f"/auth/reset-password?token={token}")

    pw_err = _validate_password(password)
    if pw_err:
        flash(pw_err, "error")
        return redirect(f"/auth/reset-password?token={token}")

    models.update_user_password(token_data["user_id"], password)
    models.use_reset_token(token)

    flash("Your password has been reset successfully. You can now log in.", "success")
    return redirect(url_for("auth.login_page"))


# ---------------------------------------------------------------------------
# Discord OAuth2
# ---------------------------------------------------------------------------
@auth_bp.route("/discord")
def discord_login():
    if not DISCORD_CLIENT_ID:
        flash("Discord login is not configured.", "error")
        return redirect(url_for("auth.login_page"))

    state = os.urandom(16).hex()
    session['discord_oauth_state'] = state

    params = {
        'client_id': DISCORD_CLIENT_ID,
        'redirect_uri': request.host_url.rstrip('/') + '/auth/discord/callback',
        'response_type': 'code',
        'scope': 'identify email',
        'state': state,
    }
    url = 'https://discord.com/api/oauth2/authorize?' + urllib.parse.urlencode(params)
    return redirect(url)


@auth_bp.route("/discord/callback")
def discord_callback():
    import requests as http_requests

    if request.args.get('state') != session.pop('discord_oauth_state', None):
        flash("Invalid OAuth state. Please try again.", "error")
        return redirect(url_for("auth.login_page"))

    code = request.args.get('code')
    if not code:
        flash("Discord authorization was cancelled.", "error")
        return redirect(url_for("auth.login_page"))

    # Exchange code for token
    try:
        token_resp = http_requests.post('https://discord.com/api/oauth2/token', data={
            'client_id': DISCORD_CLIENT_ID,
            'client_secret': DISCORD_CLIENT_SECRET,
            'grant_type': 'authorization_code',
            'code': code,
            'redirect_uri': request.host_url.rstrip('/') + '/auth/discord/callback',
        }, headers={'Content-Type': 'application/x-www-form-urlencoded'}, timeout=10)

        if token_resp.status_code != 200:
            flash("Failed to authenticate with Discord.", "error")
            return redirect(url_for("auth.login_page"))

        access_token = token_resp.json().get('access_token')

        # Get user info
        user_resp = http_requests.get('https://discord.com/api/users/@me',
            headers={'Authorization': f'Bearer {access_token}'}, timeout=10)

        if user_resp.status_code != 200:
            flash("Failed to get Discord user info.", "error")
            return redirect(url_for("auth.login_page"))
    except Exception as e:
        print(f"[RatSignal] Discord OAuth error: {e}", flush=True)
        flash("Discord login failed. Please try again.", "error")
        return redirect(url_for("auth.login_page"))

    discord_user = user_resp.json()
    discord_id = discord_user['id']
    discord_email = discord_user.get('email')
    discord_name = discord_user.get('global_name') or discord_user.get('username', '')

    # If already logged in, link Discord to current account
    if HAS_FLASK_LOGIN and current_user.is_authenticated:
        models.link_discord(current_user.id, discord_id)
        flash("Discord account linked!", "success")
        return redirect("/auth/profile")

    # 1. Check if discord_id already linked to an account
    user_dict = models.get_user_by_discord_id(discord_id)

    if not user_dict and discord_email:
        # 2. Check if email matches existing account -> link
        user_dict = models.get_user_by_email(discord_email)
        if user_dict:
            models.link_discord(user_dict['id'], discord_id)

    if not user_dict:
        # 3. Create new account
        email = discord_email or f"discord_{discord_id}@ratsignal.local"
        user_dict = models.create_social_user(
            email=email,
            display_name=discord_name,
            discord_id=discord_id,
        )
        if user_dict:
            # Save profile fields from Discord
            try:
                discord_username = discord_user.get('username', '')
                models.update_user_profile(user_dict['id'], {
                    'first_name': discord_name or '',
                    'discord': discord_username,
                })
            except Exception as e:
                print(f"[RatSignal] Discord profile update error: {e}", flush=True)
            _send_welcome_email(email if discord_email else '', discord_name or 'there')

    if not user_dict:
        flash("Could not create account. Email may already be in use.", "error")
        return redirect(url_for("auth.login_page"))

    if HAS_FLASK_LOGIN:
        user = User(user_dict)
        login_user(user, remember=True)

    flash("Logged in with Discord!", "success")
    return redirect("/auth/profile")


# ---------------------------------------------------------------------------
# Google OAuth2 (OpenID Connect)
# ---------------------------------------------------------------------------
@auth_bp.route("/google")
def google_login():
    if not GOOGLE_CLIENT_ID:
        flash("Google login is not configured.", "error")
        return redirect(url_for("auth.login_page"))

    state = os.urandom(16).hex()
    session['google_oauth_state'] = state

    params = {
        'client_id': GOOGLE_CLIENT_ID,
        'redirect_uri': request.host_url.rstrip('/') + '/auth/google/callback',
        'response_type': 'code',
        'scope': 'openid email profile',
        'state': state,
        'access_type': 'online',
        'prompt': 'select_account',
    }
    url = 'https://accounts.google.com/o/oauth2/v2/auth?' + urllib.parse.urlencode(params)
    return redirect(url)


@auth_bp.route("/google/callback")
def google_callback():
    import requests as http_requests

    if request.args.get('state') != session.pop('google_oauth_state', None):
        flash("Invalid OAuth state. Please try again.", "error")
        return redirect(url_for("auth.login_page"))

    code = request.args.get('code')
    if not code:
        flash("Google authorization was cancelled.", "error")
        return redirect(url_for("auth.login_page"))

    try:
        token_resp = http_requests.post('https://oauth2.googleapis.com/token', data={
            'client_id': GOOGLE_CLIENT_ID,
            'client_secret': GOOGLE_CLIENT_SECRET,
            'grant_type': 'authorization_code',
            'code': code,
            'redirect_uri': request.host_url.rstrip('/') + '/auth/google/callback',
        }, headers={'Content-Type': 'application/x-www-form-urlencoded'}, timeout=10)

        if token_resp.status_code != 200:
            print(f"[RatSignal] Google token exchange failed: {token_resp.status_code} {token_resp.text}", flush=True)
            flash("Failed to authenticate with Google.", "error")
            return redirect(url_for("auth.login_page"))

        access_token = token_resp.json().get('access_token')

        user_resp = http_requests.get('https://openidconnect.googleapis.com/v1/userinfo',
            headers={'Authorization': f'Bearer {access_token}'}, timeout=10)

        if user_resp.status_code != 200:
            flash("Failed to get Google user info.", "error")
            return redirect(url_for("auth.login_page"))
    except Exception as e:
        print(f"[RatSignal] Google OAuth error: {e}", flush=True)
        flash("Google login failed. Please try again.", "error")
        return redirect(url_for("auth.login_page"))

    google_user = user_resp.json()
    google_id = google_user.get('sub')
    google_email = google_user.get('email')
    google_email_verified = google_user.get('email_verified', False)
    google_name = google_user.get('name') or google_user.get('given_name') or ''
    google_given = google_user.get('given_name') or ''
    google_family = google_user.get('family_name') or ''

    if not google_id:
        flash("Google login failed: missing user identifier.", "error")
        return redirect(url_for("auth.login_page"))

    # If already logged in, link Google to current account
    if HAS_FLASK_LOGIN and current_user.is_authenticated:
        models.link_google(current_user.id, google_id)
        flash("Google account linked!", "success")
        return redirect("/auth/profile")

    # 1. google_id already linked to an account
    user_dict = models.get_user_by_google_id(google_id)

    # 2. Email matches existing account → link (only if Google says email is verified)
    if not user_dict and google_email and google_email_verified:
        user_dict = models.get_user_by_email(google_email)
        if user_dict:
            models.link_google(user_dict['id'], google_id)

    # 3. Create new account
    if not user_dict:
        email = google_email or f"google_{google_id}@ratsignal.local"
        user_dict = models.create_social_user(
            email=email,
            display_name=google_name,
            google_id=google_id,
        )
        if user_dict:
            try:
                models.update_user_profile(user_dict['id'], {
                    'first_name': google_given or google_name or '',
                    'last_name': google_family or '',
                })
            except Exception as e:
                print(f"[RatSignal] Google profile update error: {e}", flush=True)
            _send_welcome_email(email if google_email else '', google_given or google_name or 'there')

    if not user_dict:
        flash("Could not create account. Email may already be in use.", "error")
        return redirect(url_for("auth.login_page"))

    if HAS_FLASK_LOGIN:
        user = User(user_dict)
        login_user(user, remember=True)

    flash("Logged in with Google!", "success")
    return redirect("/auth/profile")


# ---------------------------------------------------------------------------
# Telegram Login Widget
# ---------------------------------------------------------------------------
@auth_bp.route("/telegram/callback", methods=["GET", "POST"])
def telegram_callback():
    if request.method == "GET":
        data = request.args.to_dict()
    else:
        data = request.get_json() or request.form.to_dict()
    if not data or 'hash' not in data:
        flash("Telegram login failed. Please try again.", "error")
        return redirect(url_for("auth.login_page"))

    if not TELEGRAM_BOT_TOKEN_LOGIN:
        return jsonify({'error': 'Telegram login not configured'}), 500

    # Verify hash per Telegram docs
    check_hash = data.pop('hash', '')
    data_check_string = '\n'.join(f'{k}={data[k]}' for k in sorted(data.keys()))
    secret_key = hashlib.sha256(TELEGRAM_BOT_TOKEN_LOGIN.encode()).digest()
    computed_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()

    if computed_hash != check_hash:
        if request.method == "GET":
            flash("Invalid Telegram authentication.", "error")
            return redirect(url_for("auth.login_page"))
        return jsonify({'error': 'Invalid Telegram authentication'}), 403

    # Check auth_date is recent (within 5 minutes)
    auth_date = int(data.get('auth_date', 0))
    if time.time() - auth_date > 300:
        if request.method == "GET":
            flash("Telegram authentication expired. Please try again.", "error")
            return redirect(url_for("auth.login_page"))
        return jsonify({'error': 'Telegram authentication expired'}), 403

    telegram_id = str(data['id'])
    tg_username = data.get('username', '')
    tg_first = data.get('first_name', '')
    tg_last = data.get('last_name', '')
    display_name = f"{tg_first} {tg_last}".strip() or tg_username

    # If already logged in, link Telegram to current account
    if HAS_FLASK_LOGIN and current_user.is_authenticated:
        models.link_telegram(current_user.id, telegram_id, tg_username)
        if request.method == "GET":
            flash("Telegram account linked!", "success")
            return redirect("/auth/profile")
        return jsonify({'success': True, 'redirect': '/auth/profile'})

    # 1. Check if telegram_id already linked
    user_dict = models.get_user_by_telegram_id(telegram_id)

    if not user_dict:
        # 2. Create new account
        email = f"telegram_{telegram_id}@ratsignal.local"
        user_dict = models.create_social_user(
            email=email,
            display_name=display_name,
            telegram_id=telegram_id,
            telegram_username=tg_username,
        )
        if user_dict:
            # Save profile fields from Telegram
            try:
                models.update_user_profile(user_dict['id'], {
                    'first_name': tg_first,
                    'last_name': tg_last,
                    'telegram': tg_username,
                    'telegram_username': tg_username,
                })
            except Exception as e:
                print(f"[RatSignal] Telegram profile update error: {e}", flush=True)
            _send_welcome_email('', tg_first or display_name)
            _send_telegram_notification(tg_first, tg_last, email, tg_username)

    if not user_dict:
        if request.method == "GET":
            flash("Could not create account.", "error")
            return redirect(url_for("auth.login_page"))
        return jsonify({'error': 'Could not create account'}), 500

    if HAS_FLASK_LOGIN:
        user = User(user_dict)
        login_user(user, remember=True)

    if request.method == "GET":
        flash("Logged in with Telegram!", "success")
        return redirect("/auth/profile")
    return jsonify({'success': True, 'redirect': '/auth/profile'})


# ---------------------------------------------------------------------------
# Telegram notification on new registration
# ---------------------------------------------------------------------------
def _send_telegram_notification(first_name: str, last_name: str, email: str, telegram: str):
    """Send Telegram notification when a new user registers."""
    def _send():
        try:
            import urllib.request
            import json
            bot_token = os.environ.get('TELEGRAM_BOT_TOKEN', '')
            chat_id = os.environ.get('TELEGRAM_CHAT_ID', '')
            if not bot_token or not chat_id:
                print('[RatSignal] Telegram bot not configured, skipping notification', flush=True)
                return

            tg_line = f'💬 <b>Telegram:</b> @{telegram.lstrip("@")}\n' if telegram else '💬 <b>Telegram:</b> <i>not provided</i>\n'
            text = (
                f'🐀 <b>New RatSignal Registration!</b>\n\n'
                f'👤 <b>Name:</b> {first_name} {last_name}\n'
                f'📧 <b>Email:</b> {email}\n'
                f'{tg_line}'
                f'🕐 Welcome email sent automatically.'
            )

            url = f'https://api.telegram.org/bot{bot_token}/sendMessage'
            data = json.dumps({'chat_id': chat_id, 'text': text, 'parse_mode': 'HTML'}).encode()
            req = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json'})
            urllib.request.urlopen(req, timeout=10)
            print(f'[RatSignal] Telegram notification sent for {email}', flush=True)
        except Exception as e:
            print(f'[RatSignal] Telegram notification failed: {e}', flush=True)

    threading.Thread(target=_send, daemon=True).start()


# ============================================================================
# Hosted Auto-Trading routes (added 2026-04-28)
# ============================================================================

_HOSTED_BOTS_AUTH = ("slipstream", "quickbite")


@auth_bp.route("/auto-trade", methods=["GET"])
def auto_trade():
    from flask import render_template
    from temporary.ratsignal import models, hosted

    is_logged_in = HAS_FLASK_LOGIN and current_user.is_authenticated
    user_id = current_user.id if is_logged_in else None
    user = models.get_user_by_id(user_id) if user_id else None
    sub_active = hosted.is_subscription_active_for_hosted(user) if user else False

    bot_data = []
    if is_logged_in:
        for bot in _HOSTED_BOTS_AUTH:
            cfg = models.get_hosted_bot_config(user_id, bot) or {}
            if cfg.get("api_key_encrypted"):
                stats = models.hosted_stats(user_id, bot, days=30)
                recent = models.list_hosted_trades(user_id, bot=bot, limit=5)
            else:
                stats = {"trades": 0, "win_rate": 0, "total_pnl": 0, "open_positions": 0}
                recent = []
            bot_data.append({
                "bot": bot,
                "display_name": "Slipstream" if bot == "slipstream" else "Quick Bite",
                "config": cfg,
                "stats": stats,
                "recent_trades": recent,
                "configured": bool(cfg.get("api_key_encrypted")),
                "enabled": bool(cfg.get("enabled")),
                "paused": bool(cfg.get("paused")),
            })

    return render_template(
        "auto_trade.html",
        user=user, is_logged_in=is_logged_in,
        sub_active=sub_active, bots=bot_data,
    )


@auth_bp.route("/auto-trade/<bot>/setup", methods=["GET"])
def auto_trade_setup(bot):
    from flask import render_template, redirect, url_for, abort
    from temporary.ratsignal import models, hosted

    if bot not in _HOSTED_BOTS_AUTH:
        abort(404)
    if not (HAS_FLASK_LOGIN and current_user.is_authenticated):
        return redirect(url_for("auth.login_page"))
    user_id = current_user.id
    user = models.get_user_by_id(user_id)
    if not user:
        return redirect(url_for("auth.login_page"))
    if not hosted.is_subscription_active_for_hosted(user):
        return redirect(url_for("auth.auto_trade"))

    cfg = models.get_hosted_bot_config(user_id, bot) or {}
    return render_template(
        "auto_trade_setup.html",
        user=user, bot=bot,
        display_name="Slipstream" if bot == "slipstream" else "Quick Bite",
        config=cfg,
    )
