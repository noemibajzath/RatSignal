"""RatSignal — User database (raw SQLite).

Tables: users, payments
Database: temporary/ratsignal/ratsignal_users.db
"""

import hashlib
import os
import sqlite3
import time

_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ratsignal_users.db")

# ---------------------------------------------------------------------------
# Password hashing — prefer bcrypt, fall back to werkzeug, last resort PBKDF2
# ---------------------------------------------------------------------------
_HASH_BACKEND = None

try:
    import bcrypt
    _HASH_BACKEND = "bcrypt"
except ImportError:
    pass

if _HASH_BACKEND is None:
    try:
        from werkzeug.security import generate_password_hash, check_password_hash
        _HASH_BACKEND = "werkzeug"
    except ImportError:
        _HASH_BACKEND = "pbkdf2"


def _hash_password(password: str) -> str:
    if _HASH_BACKEND == "bcrypt":
        return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    elif _HASH_BACKEND == "werkzeug":
        return generate_password_hash(password)
    else:
        salt = os.urandom(16).hex()
        h = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 260000).hex()
        return f"pbkdf2:{salt}:{h}"


def _check_password(stored_hash: str, password: str) -> bool:
    if _HASH_BACKEND == "bcrypt":
        try:
            return bcrypt.checkpw(password.encode(), stored_hash.encode())
        except Exception:
            return False
    elif _HASH_BACKEND == "werkzeug":
        return check_password_hash(stored_hash, password)
    else:
        parts = stored_hash.split(":")
        if len(parts) != 3:
            return False
        _, salt, expected = parts
        h = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 260000).hex()
        return h == expected


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------
def _get_conn():
    conn = sqlite3.connect(_DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    """Create tables if they don't exist. Migrate existing tables."""
    with _get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                display_name TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                subscription_status TEXT NOT NULL DEFAULT 'free',
                subscription_plan TEXT,
                subscription_end TEXT
            );

            CREATE TABLE IF NOT EXISTS payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id),
                amount_cents INTEGER NOT NULL,
                currency TEXT NOT NULL DEFAULT 'usd',
                stripe_session_id TEXT,
                crypto_tx_hash TEXT,
                nowpayments_id TEXT,
                plan TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
        """)
        # Migrate: add nowpayments_id column if missing
        cols = [r[1] for r in conn.execute("PRAGMA table_info(payments)").fetchall()]
        if "nowpayments_id" not in cols:
            try:
                conn.execute("ALTER TABLE payments ADD COLUMN nowpayments_id TEXT")
            except sqlite3.OperationalError:
                pass  # Another worker raced us
        # Migrate: add trial_used column if missing (one-time 7-day free trial)
        ucols = [r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()]
        if "trial_used" not in ucols:
            try:
                conn.execute("ALTER TABLE users ADD COLUMN trial_used INTEGER NOT NULL DEFAULT 0")
            except sqlite3.OperationalError:
                pass  # Another worker raced us
        # Migrate: add google_id column if missing (Google OAuth login)
        if "google_id" not in ucols:
            try:
                conn.execute("ALTER TABLE users ADD COLUMN google_id TEXT")
            except sqlite3.OperationalError:
                pass  # Another worker raced us
        # Migrate: add trial_reminder_sent column (1-day pre-expiry email)
        if "trial_reminder_sent" not in ucols:
            try:
                conn.execute("ALTER TABLE users ADD COLUMN trial_reminder_sent INTEGER NOT NULL DEFAULT 0")
            except sqlite3.OperationalError:
                pass  # Another worker raced us
        # Migrate: add paid_reminder_sent column (3-day pre-expiry email for monthly/yearly)
        # Stores the subscription_end value at time of send so re-renewals reset reminder eligibility.
        if "paid_reminder_sent_for" not in ucols:
            try:
                conn.execute("ALTER TABLE users ADD COLUMN paid_reminder_sent_for TEXT")
            except sqlite3.OperationalError:
                pass  # Another worker raced us


# ---------------------------------------------------------------------------
# User CRUD
# ---------------------------------------------------------------------------
def create_user(email: str, password: str, display_name: str = None) -> dict | None:
    """Create a new user. Returns user dict or None if email exists."""
    pw_hash = _hash_password(password)
    try:
        with _get_conn() as conn:
            cur = conn.execute(
                "INSERT INTO users (email, password_hash, display_name) VALUES (?, ?, ?)",
                (email.lower().strip(), pw_hash, display_name),
            )
            return get_user_by_id(cur.lastrowid)
    except sqlite3.IntegrityError:
        return None


def get_user_by_email(email: str) -> dict | None:
    """Return user dict by email or None."""
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE email = ?", (email.lower().strip(),)
        ).fetchone()
    return dict(row) if row else None


def get_user_by_id(user_id: int) -> dict | None:
    """Return user dict by ID or None."""
    with _get_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return dict(row) if row else None


def get_user_profile(user_id: int) -> dict | None:
    """Return full user profile by ID. Alias for get_user_by_id."""
    return get_user_by_id(user_id)


def update_user_profile(user_id: int, fields: dict):
    """Update any user profile fields that exist as columns."""
    if not fields:
        return
    with _get_conn() as conn:
        existing_cols = [r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()]
        valid = {k: v for k, v in fields.items() if k in existing_cols and k not in ("id", "password_hash")}
        if not valid:
            return
        set_clause = ", ".join(f"{k}=?" for k in valid.keys())
        values = list(valid.values()) + [user_id]
        conn.execute(f"UPDATE users SET {set_clause} WHERE id=?", values)


def update_user_password(user_id: int, new_password: str):
    """Update a user's password."""
    new_hash = _hash_password(new_password)
    with _get_conn() as conn:
        conn.execute("UPDATE users SET password_hash=? WHERE id=?", (new_hash, user_id))


def delete_user(user_id: int):
    """Delete a user row. Used by the social-account merge flow when we
    collapse a throwaway telegram_N@ratsignal.local account into a real one."""
    with _get_conn() as conn:
        conn.execute("DELETE FROM users WHERE id=?", (user_id,))


def reassign_payments(from_user_id: int, to_user_id: int):
    """Move any payment rows from one user to another. Called during merge
    so we don't orphan (unlikely but defensive) any pending payments."""
    with _get_conn() as conn:
        conn.execute("UPDATE payments SET user_id=? WHERE user_id=?",
                     (to_user_id, from_user_id))

def set_user_email_with_merge(user_id: int, new_email: str) -> tuple[bool, str | None]:
    """Update a user's email, auto-merging any account that already holds
    that email into the current user. The merge:

    - copies social IDs (telegram, discord, google) and profile fields where
      the current user's slot is empty, so primary identity is preserved;
    - inherits the stronger subscription: if the other account has an active
      / trialing / lifetime / paid subscription and the current user does
      not, copy subscription_status / plan / end onto the current user;
    - ORs the trial_used flag so a user cannot burn their 7-day free trial
      on one account and then start it again on another account with the
      same email after merging;
    - copies password_hash if the other account has one;
    - reassigns any payments;
    - deletes the orphan + its password reset tokens;
    - finally writes the new email onto the current user.

    Returns (success, error_message). Only fails if `new_email` is empty.
    """
    new_email = (new_email or "").strip().lower()
    if not new_email:
        return (False, "Email is required.")
    fields = (
        "id", "telegram_id", "telegram_username", "google_id", "discord_id",
        "subscription_status", "subscription_plan", "subscription_end",
        "trial_used", "password_hash", "display_name",
        "first_name", "last_name",
    )
    SUB_RANK = {
        "lifetime": 4, "active": 3, "trialing": 2, "paid": 2, "trial": 2,
        "expired": 1, "free": 0, "": 0, None: 0,
    }
    with _get_conn() as conn:
        cur = conn.cursor()
        cur.execute(f"SELECT {', '.join(fields)} FROM users WHERE id=?", (user_id,))
        current_row = cur.fetchone()
        if not current_row:
            return (False, "User not found.")
        cur.execute(
            f"SELECT {', '.join(fields)} FROM users WHERE lower(email)=? AND id!=?",
            (new_email, user_id),
        )
        other = cur.fetchone()
        if not other:
            cur.execute("UPDATE users SET email=? WHERE id=?", (new_email, user_id))
            return (True, None)
        other_id = other["id"]

        # Copy social/profile fields from the orphan -> current, only where
        # the current user has nothing yet so primary identity is kept.
        backfill = {}
        for f in (
            "telegram_id", "telegram_username", "google_id", "discord_id",
            "display_name", "first_name", "last_name",
        ):
            other_val = other[f]
            current_val = current_row[f]
            current_filled = (
                bool((current_val or "").strip()) if isinstance(current_val, str)
                else bool(current_val)
            )
            if other_val and not current_filled:
                backfill[f] = other_val

        # Inherit the stronger subscription (so the active sub never gets lost
        # in a merge — it follows the email onto the surviving account).
        cur_rank = SUB_RANK.get(current_row["subscription_status"] or "", 0)
        oth_rank = SUB_RANK.get(other["subscription_status"] or "", 0)
        if oth_rank > cur_rank:
            backfill["subscription_status"] = other["subscription_status"]
            backfill["subscription_plan"] = other["subscription_plan"]
            backfill["subscription_end"] = other["subscription_end"]

        # Trial dedup: if either account has burned the 7-day free trial,
        # the surviving account stays burned.
        if (current_row["trial_used"] or 0) or (other["trial_used"] or 0):
            backfill["trial_used"] = 1

        if backfill:
            assignments = ", ".join(f"{k}=?" for k in backfill)
            cur.execute(
                f"UPDATE users SET {assignments} WHERE id=?",
                (*backfill.values(), user_id),
            )
        if other["password_hash"]:
            cur.execute(
                "UPDATE users SET password_hash=? WHERE id=?",
                (other["password_hash"], user_id),
            )

        cur.execute("UPDATE payments SET user_id=? WHERE user_id=?", (user_id, other_id))
        cur.execute("DELETE FROM password_reset_tokens WHERE user_id=?", (other_id,))
        cur.execute("DELETE FROM users WHERE id=?", (other_id,))
        cur.execute("UPDATE users SET email=? WHERE id=?", (new_email, user_id))
        return (True, None)



def link_discord(user_id: int, discord_id: str):
    """Link a Discord ID to an existing user."""
    with _get_conn() as conn:
        conn.execute("UPDATE users SET discord_id=? WHERE id=?", (str(discord_id), user_id))


def link_google(user_id: int, google_id: str):
    """Link a Google ID to an existing user."""
    with _get_conn() as conn:
        conn.execute("UPDATE users SET google_id=? WHERE id=?", (str(google_id), user_id))


def link_telegram(user_id: int, telegram_id: str, telegram_username: str = None):
    """Link a Telegram ID to an existing user."""
    with _get_conn() as conn:
        conn.execute(
            "UPDATE users SET telegram_id=?, telegram_username=COALESCE(?, telegram_username) WHERE id=?",
            (str(telegram_id), telegram_username, user_id),
        )


def get_user_by_discord_id(discord_id: str) -> dict | None:
    with _get_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE discord_id = ?", (str(discord_id),)).fetchone()
    return dict(row) if row else None


def get_user_by_telegram_id(telegram_id: str) -> dict | None:
    with _get_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE telegram_id = ?", (str(telegram_id),)).fetchone()
    return dict(row) if row else None


def get_user_by_google_id(google_id: str) -> dict | None:
    with _get_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE google_id = ?", (str(google_id),)).fetchone()
    return dict(row) if row else None


def create_social_user(email: str, display_name: str = None,
                       discord_id: str = None, telegram_id: str = None,
                       telegram_username: str = None,
                       google_id: str = None) -> dict | None:
    """Create a user without password (social login). Random password hash as placeholder."""
    import secrets
    random_pw = secrets.token_hex(32)
    pw_hash = _hash_password(random_pw)
    try:
        with _get_conn() as conn:
            cur = conn.execute(
                "INSERT INTO users (email, password_hash, display_name, discord_id, telegram_id, telegram_username, google_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (email.lower().strip(), pw_hash, display_name,
                 str(discord_id) if discord_id else None,
                 str(telegram_id) if telegram_id else None,
                 telegram_username,
                 str(google_id) if google_id else None),
            )
            return get_user_by_id(cur.lastrowid)
    except sqlite3.IntegrityError:
        return None


def _ensure_reset_tokens_table():
    with _get_conn() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS password_reset_tokens (
            token TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            expires_at TEXT NOT NULL,
            used INTEGER NOT NULL DEFAULT 0
        )""")
        cols = [r[1] for r in conn.execute("PRAGMA table_info(password_reset_tokens)").fetchall()]
        if "expires_at" not in cols:
            try:
                conn.execute("ALTER TABLE password_reset_tokens ADD COLUMN expires_at TEXT")
            except sqlite3.OperationalError:
                pass


def create_password_reset_token(user_id: int) -> str:
    """Create a 1-hour valid reset token. Returns token string."""
    import secrets
    from datetime import datetime, timedelta
    _ensure_reset_tokens_table()
    token = secrets.token_urlsafe(32)
    expires = (datetime.utcnow() + timedelta(hours=1)).isoformat()
    with _get_conn() as conn:
        conn.execute("INSERT INTO password_reset_tokens (token, user_id, expires_at) VALUES (?, ?, ?)",
                     (token, user_id, expires))
    return token


def get_valid_reset_token(token: str) -> dict | None:
    from datetime import datetime
    _ensure_reset_tokens_table()
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM password_reset_tokens WHERE token=? AND used=0 AND expires_at > ?",
            (token, datetime.utcnow().isoformat())
        ).fetchone()
    return dict(row) if row else None


def use_reset_token(token: str):
    _ensure_reset_tokens_table()
    with _get_conn() as conn:
        conn.execute("UPDATE password_reset_tokens SET used=1 WHERE token=?", (token,))


# ---------------------------------------------------------------------------
# Pending email-verification registrations — user fills out the register form,
# we stash the data here, send a verify email, and only create the real user
# row once they click the link.
# ---------------------------------------------------------------------------
def _ensure_pending_registrations_table():
    with _get_conn() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS pending_registrations (
            token TEXT PRIMARY KEY,
            email TEXT NOT NULL,
            password_hash TEXT NOT NULL,
            first_name TEXT,
            last_name TEXT,
            wallet_address TEXT,
            telegram TEXT,
            discord TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            expires_at TEXT NOT NULL
        )""")


def create_pending_registration(email: str, password: str, first_name: str = "",
                                last_name: str = "", wallet_address: str = "",
                                telegram: str = "", discord: str = "",
                                ttl_hours: int = 24) -> str:
    """Stash a registration attempt; return a verification token."""
    import secrets
    from datetime import datetime, timedelta
    _ensure_pending_registrations_table()
    pw_hash = _hash_password(password)
    token = secrets.token_urlsafe(32)
    expires = (datetime.utcnow() + timedelta(hours=ttl_hours)).isoformat()
    with _get_conn() as conn:
        # Drop any older pending row for this email so re-registering refreshes the token
        conn.execute("DELETE FROM pending_registrations WHERE email = ?", (email.lower().strip(),))
        conn.execute(
            """INSERT INTO pending_registrations
               (token, email, password_hash, first_name, last_name, wallet_address, telegram, discord, expires_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (token, email.lower().strip(), pw_hash, first_name, last_name,
             wallet_address, telegram, discord, expires),
        )
    return token


def get_valid_pending_registration(token: str) -> dict | None:
    from datetime import datetime
    _ensure_pending_registrations_table()
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM pending_registrations WHERE token=? AND expires_at > ?",
            (token, datetime.utcnow().isoformat()),
        ).fetchone()
    return dict(row) if row else None


def consume_pending_registration(token: str):
    _ensure_pending_registrations_table()
    with _get_conn() as conn:
        conn.execute("DELETE FROM pending_registrations WHERE token=?", (token,))


def create_user_with_hash(email: str, password_hash: str, display_name: str = None) -> dict | None:
    """Insert a user row using a pre-hashed password (used by verify-email flow)."""
    try:
        with _get_conn() as conn:
            cur = conn.execute(
                "INSERT INTO users (email, password_hash, display_name) VALUES (?, ?, ?)",
                (email.lower().strip(), password_hash, display_name),
            )
            return get_user_by_id(cur.lastrowid)
    except sqlite3.IntegrityError:
        return None


# ---------------------------------------------------------------------------
# Bot login tokens — lets the user sign in by clicking "Start" in the Telegram
# bot instead of using the Login Widget (which is tied to Telegram Web session
# and can't switch accounts). Token lives for 10 minutes, single-use.
# ---------------------------------------------------------------------------
def _ensure_bot_login_tokens_table():
    with _get_conn() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS bot_login_tokens (
            token TEXT PRIMARY KEY,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            expires_at TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            telegram_id TEXT,
            telegram_username TEXT,
            first_name TEXT,
            last_name TEXT,
            claimed_at TEXT,
            consumed_at TEXT
        )""")


def create_bot_login_token() -> str:
    import secrets
    from datetime import datetime, timedelta
    _ensure_bot_login_tokens_table()
    token = secrets.token_urlsafe(24)
    expires = (datetime.utcnow() + timedelta(minutes=10)).isoformat()
    with _get_conn() as conn:
        conn.execute("INSERT INTO bot_login_tokens (token, expires_at) VALUES (?, ?)",
                     (token, expires))
    return token


def claim_bot_login_token(token: str, telegram_id: str, telegram_username: str,
                          first_name: str, last_name: str) -> bool:
    """Called by the bot webhook when user clicks Start. Returns True if the
    token existed, was pending, and is now marked 'ready' with the TG user info."""
    from datetime import datetime
    _ensure_bot_login_tokens_table()
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT status, expires_at FROM bot_login_tokens WHERE token=?",
            (token,)
        ).fetchone()
        if not row:
            return False
        if row["status"] != "pending":
            return False
        if row["expires_at"] < datetime.utcnow().isoformat():
            return False
        conn.execute(
            "UPDATE bot_login_tokens SET status='ready', telegram_id=?, telegram_username=?, "
            "first_name=?, last_name=?, claimed_at=datetime('now') WHERE token=?",
            (str(telegram_id), telegram_username or "", first_name or "", last_name or "", token),
        )
    return True


def get_bot_login_token(token: str) -> dict | None:
    _ensure_bot_login_tokens_table()
    with _get_conn() as conn:
        row = conn.execute("SELECT * FROM bot_login_tokens WHERE token=?", (token,)).fetchone()
    return dict(row) if row else None


def consume_bot_login_token(token: str):
    """Mark token as consumed so it can't be reused. Called after the user is
    logged in via this token."""
    _ensure_bot_login_tokens_table()
    with _get_conn() as conn:
        conn.execute(
            "UPDATE bot_login_tokens SET status='consumed', consumed_at=datetime('now') WHERE token=?",
            (token,),
        )


def verify_password(user: dict, password: str) -> bool:
    """Check password against stored hash."""
    if not user:
        return False
    return _check_password(user["password_hash"], password)


def update_subscription(user_id: int, status: str, plan: str = None, end_date: str = None):
    """Update user subscription fields."""
    with _get_conn() as conn:
        conn.execute(
            "UPDATE users SET subscription_status=?, subscription_plan=?, subscription_end=? WHERE id=?",
            (status, plan, end_date, user_id),
        )


def start_free_trial(user_id: int, days: int = 7) -> dict | None:
    """Activate a one-time N-day free trial. Returns end_date dict or None if already used."""
    from datetime import datetime, timedelta
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT trial_used, subscription_status FROM users WHERE id=?", (user_id,)
        ).fetchone()
        if not row:
            return None
        if row["trial_used"]:
            return None
        if row["subscription_status"] == "active":
            return None
        end_date = (datetime.utcnow() + timedelta(days=days)).isoformat()
        conn.execute(
            "UPDATE users SET subscription_status='active', subscription_plan='trial', "
            "subscription_end=?, trial_used=1 WHERE id=?",
            (end_date, user_id),
        )
    return {"end_date": end_date}


def expire_if_needed(user_id: int) -> bool:
    """If subscription_end is in the past and status is 'active', mark as 'expired'."""
    from datetime import datetime
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT subscription_status, subscription_end FROM users WHERE id=?", (user_id,)
        ).fetchone()
        if not row or row["subscription_status"] != "active" or not row["subscription_end"]:
            return False
        if row["subscription_end"] < datetime.utcnow().isoformat():
            conn.execute("UPDATE users SET subscription_status='expired' WHERE id=?", (user_id,))
            return True
    return False


# ---------------------------------------------------------------------------
# Payments
# ---------------------------------------------------------------------------
def record_payment(user_id: int, amount_cents: int, plan: str,
                   stripe_session_id: str = None, crypto_tx_hash: str = None,
                   nowpayments_id: str = None,
                   status: str = "completed", currency: str = "usd") -> int:
    """Record a payment. Returns payment ID."""
    with _get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO payments (user_id, amount_cents, currency, stripe_session_id,
               crypto_tx_hash, nowpayments_id, plan, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (user_id, amount_cents, currency, stripe_session_id, crypto_tx_hash,
             nowpayments_id, plan, status),
        )
        return cur.lastrowid


def get_payment_by_nowpayments_id(np_id: str) -> dict | None:
    """Find payment by NOWPayments payment/invoice ID."""
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM payments WHERE nowpayments_id = ?", (str(np_id),)
        ).fetchone()
    return dict(row) if row else None


def update_payment_status(payment_id: int, status: str, crypto_tx_hash: str = None):
    """Update payment status and optionally the tx hash."""
    with _get_conn() as conn:
        if crypto_tx_hash:
            conn.execute(
                "UPDATE payments SET status=?, crypto_tx_hash=? WHERE id=?",
                (status, crypto_tx_hash, payment_id),
            )
        else:
            conn.execute(
                "UPDATE payments SET status=? WHERE id=?", (status, payment_id),
            )


def get_user_payments(user_id: int) -> list[dict]:
    """Get all payments for a user."""
    with _get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM payments WHERE user_id = ? ORDER BY created_at DESC", (user_id,)
        ).fetchall()
    return [dict(r) for r in rows]


# ============================================================================
# Hosted Auto-Trading helpers (added 2026-04-28)
# ============================================================================

def get_hosted_subscription(user_id: int) -> dict | None:
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM hosted_subscriptions WHERE user_id=?", (user_id,),
        ).fetchone()
        return dict(row) if row else None


def upsert_hosted_subscription(user_id: int, *, tos_accepted_at: str | None = None,
                                tos_accepted_ip: str | None = None) -> None:
    with _get_conn() as conn:
        conn.execute(
            """
            INSERT INTO hosted_subscriptions (user_id, active, tos_accepted_at, tos_accepted_ip)
            VALUES (?, 1, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                active=1,
                tos_accepted_at=COALESCE(excluded.tos_accepted_at, hosted_subscriptions.tos_accepted_at),
                tos_accepted_ip=COALESCE(excluded.tos_accepted_ip, hosted_subscriptions.tos_accepted_ip),
                updated_at=datetime('now')
            """,
            (user_id, tos_accepted_at, tos_accepted_ip),
        )


def get_hosted_bot_config(user_id: int, bot: str) -> dict | None:
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM hosted_bot_configs WHERE user_id=? AND bot=?",
            (user_id, bot),
        ).fetchone()
        return dict(row) if row else None


def list_hosted_bot_configs(user_id: int) -> list[dict]:
    with _get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM hosted_bot_configs WHERE user_id=?", (user_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def upsert_hosted_bot_config(user_id: int, bot: str, **fields) -> None:
    """fields may include exchange, enabled, paused, api_key_encrypted, etc."""
    if not fields:
        return
    cols = list(fields.keys())
    placeholders = ",".join("?" * len(cols))
    update_set = ",".join(f"{c}=excluded.{c}" for c in cols)
    sql = f"""
        INSERT INTO hosted_bot_configs (user_id, bot, {",".join(cols)})
        VALUES (?, ?, {placeholders})
        ON CONFLICT(user_id, bot) DO UPDATE SET
            {update_set},
            updated_at=datetime('now')
    """
    with _get_conn() as conn:
        conn.execute(sql, [user_id, bot] + list(fields.values()))


def list_hosted_trades(user_id: int, bot: str | None = None, limit: int = 50) -> list[dict]:
    sql = "SELECT * FROM hosted_trades WHERE user_id=?"
    params = [user_id]
    if bot:
        sql += " AND bot=?"
        params.append(bot)
    sql += " ORDER BY entry_at DESC LIMIT ?"
    params.append(limit)
    with _get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]


def hosted_stats(user_id: int, bot: str, days: int = 30) -> dict:
    """Aggregated stats for the bot card."""
    with _get_conn() as conn:
        closed = conn.execute(
            f"""
            SELECT COUNT(*) as n, COALESCE(SUM(pnl_usd), 0) as total_pnl,
                   COALESCE(SUM(CASE WHEN pnl_usd > 0 THEN 1 ELSE 0 END), 0) as wins
            FROM hosted_trades
            WHERE user_id=? AND bot=? AND exit_at IS NOT NULL
              AND exit_at >= datetime('now', '-{int(days)} days')
            """,
            (user_id, bot),
        ).fetchone()
        open_count = conn.execute(
            "SELECT COUNT(*) FROM hosted_trades WHERE user_id=? AND bot=? AND exit_at IS NULL",
            (user_id, bot),
        ).fetchone()[0]

    n = closed["n"]
    return {
        "trades": n,
        "win_rate": (closed["wins"] / n * 100) if n else 0,
        "total_pnl": closed["total_pnl"],
        "open_positions": open_count,
    }
