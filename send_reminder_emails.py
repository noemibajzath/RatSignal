#!/usr/bin/env python3
"""RatSignal — Daily reminder email script.

Sends two kinds of pre-expiry reminders:
  1. Trial users: 24h before subscription_end (one-shot, trial_reminder_sent flag)
  2. Paid users (monthly/yearly): ~3 days before subscription_end
     (paid_reminder_sent_for stores the subscription_end value at send,
      so renewals reset eligibility automatically)

Run from cron / systemd timer. Loads env from /root/prediction_market_strategies/.env
if not already set.
"""
import os
import sys
import sqlite3
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime, timedelta

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(SCRIPT_DIR, "ratsignal_users.db")
ENV_PATH = "/root/prediction_market_strategies/.env"


def _load_env():
    """Manually load .env if GMAIL_APP_PASSWORD is not already set."""
    if os.environ.get("GMAIL_APP_PASSWORD"):
        return
    if not os.path.isfile(ENV_PATH):
        return
    with open(ENV_PATH, "r") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = val


def _send_email(to_email: str, subject: str, html: str, text: str):
    sender = "ratsignalcrypto@gmail.com"
    password = os.environ.get("GMAIL_APP_PASSWORD", "")
    if not password:
        print(f"[reminders] GMAIL_APP_PASSWORD not set, cannot send to {to_email}", flush=True)
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"RatSignal <{sender}>"
    msg["To"] = to_email
    msg.attach(MIMEText(text, "plain"))
    msg.attach(MIMEText(html, "html"))

    try:
        with smtplib.SMTP("smtp.gmail.com", 587, timeout=20) as server:
            server.starttls()
            server.login(sender, password)
            server.sendmail(sender, to_email, msg.as_string())
        print(f"[reminders] sent to {to_email}: {subject}", flush=True)
        return True
    except Exception as e:
        print(f"[reminders] FAILED to send to {to_email}: {e}", flush=True)
        return False


# ---------------------------------------------------------------------------
# Email templates
# ---------------------------------------------------------------------------
_BASE_CSS = """
body { font-family: 'Inter', Arial, sans-serif; background: #0a1628; color: #f0f0f5; margin: 0; padding: 0; }
.container { max-width: 560px; margin: 0 auto; padding: 40px 24px; }
.header { text-align: center; margin-bottom: 32px; }
.header h1 { font-size: 24px; margin: 0; }
.header h1 span { color: #ff6b2b; }
.card { background: #0f1f35; border: 1px solid #1a2d4a; border-radius: 12px; padding: 32px; }
.card h2 { font-size: 20px; margin: 0 0 16px; }
.card p { color: #8888a0; line-height: 1.7; margin: 0 0 16px; font-size: 15px; }
.cta-wrap { text-align: center; margin: 28px 0 8px; }
.cta { display: inline-block; background: #ff6b2b; color: #ffffff !important; text-decoration: none;
       padding: 14px 32px; border-radius: 8px; font-weight: 600; font-size: 15px; }
.divider { border: none; border-top: 1px solid #1a2d4a; margin: 24px 0; }
.signature { color: #8888a0; font-size: 14px; line-height: 1.6; }
.signature strong { color: #ff6b2b; }
.footer { text-align: center; margin-top: 32px; color: #55556a; font-size: 12px; }
"""


def _trial_email(first_name: str) -> tuple[str, str, str]:
    safe_name = first_name.strip() or "there"
    subject = "Your free trial ends tomorrow"
    html = f"""<!DOCTYPE html>
<html><head><style>{_BASE_CSS}</style></head><body>
<div class="container">
  <div class="header"><h1>Rat<span>Signal</span></h1></div>
  <div class="card">
    <h2>Hey {safe_name},</h2>
    <p>The kitchen's about to close. Your 7-day taste test ends in 24 hours.</p>
    <p>Lock in your seat at the table to keep getting live signals.</p>
    <div class="cta-wrap">
      <a class="cta" href="https://ratsignal.com/auth/profile">Subscribe Now</a>
    </div>
    <hr class="divider">
    <div class="signature">
      <strong>See you in the kitchen,</strong><br>
      The RatSignal Team
    </div>
  </div>
  <div class="footer">
    &copy; 2026 RatSignal. All rights reserved.<br>
    You received this email because your free trial is ending soon.
  </div>
</div>
</body></html>"""
    text = f"""Hey {safe_name},

The kitchen's about to close. Your 7-day taste test ends in 24 hours.

Lock in your seat at the table to keep getting live signals.

Subscribe now: https://ratsignal.com/auth/profile

See you in the kitchen,
The RatSignal Team
"""
    return subject, html, text


def _paid_email(first_name: str) -> tuple[str, str, str]:
    safe_name = first_name.strip() or "there"
    subject = "Your subscription expires in 3 days"
    html = f"""<!DOCTYPE html>
<html><head><style>{_BASE_CSS}</style></head><body>
<div class="container">
  <div class="header"><h1>Rat<span>Signal</span></h1></div>
  <div class="card">
    <h2>Hey {safe_name},</h2>
    <p>Your seat at the table is reserved for 3 more days.</p>
    <p>Renew now to keep the signals flowing.</p>
    <div class="cta-wrap">
      <a class="cta" href="https://ratsignal.com/auth/profile">Renew Now</a>
    </div>
    <hr class="divider">
    <div class="signature">
      <strong>See you in the kitchen,</strong><br>
      The RatSignal Team
    </div>
  </div>
  <div class="footer">
    &copy; 2026 RatSignal. All rights reserved.<br>
    You received this email because your RatSignal subscription is ending soon.
  </div>
</div>
</body></html>"""
    text = f"""Hey {safe_name},

Your seat at the table is reserved for 3 more days.

Renew now to keep the signals flowing.

Renew now: https://ratsignal.com/auth/profile

See you in the kitchen,
The RatSignal Team
"""
    return subject, html, text


# ---------------------------------------------------------------------------
# DB queries + send loops
# ---------------------------------------------------------------------------
def _first_name_of(row) -> str:
    return (row["first_name"] or row["display_name"] or "").strip()


def send_trial_reminders(conn) -> int:
    """Trial users whose subscription_end is in the next 36h, not yet reminded."""
    rows = conn.execute("""
        SELECT id, email, first_name, display_name, subscription_end
        FROM users
        WHERE subscription_plan = 'trial'
          AND subscription_status = 'active'
          AND COALESCE(trial_reminder_sent, 0) = 0
          AND subscription_end IS NOT NULL
          AND subscription_end > datetime('now')
          AND subscription_end <= datetime('now', '+36 hours')
          AND email IS NOT NULL
          AND email NOT LIKE '%@ratsignal.local'
    """).fetchall()

    sent = 0
    for row in rows:
        subject, html, text = _trial_email(_first_name_of(row))
        if _send_email(row["email"], subject, html, text):
            conn.execute("UPDATE users SET trial_reminder_sent = 1 WHERE id = ?", (row["id"],))
            conn.commit()
            sent += 1
    return sent


def send_paid_reminders(conn) -> int:
    """Monthly/yearly users whose subscription_end is in 2-4 days, not yet reminded for THIS period."""
    rows = conn.execute("""
        SELECT id, email, first_name, display_name, subscription_end
        FROM users
        WHERE subscription_plan IN ('monthly', 'yearly')
          AND subscription_status = 'active'
          AND subscription_end IS NOT NULL
          AND subscription_end > datetime('now', '+2 days')
          AND subscription_end <= datetime('now', '+4 days')
          AND (paid_reminder_sent_for IS NULL OR paid_reminder_sent_for != subscription_end)
          AND email IS NOT NULL
          AND email NOT LIKE '%@ratsignal.local'
    """).fetchall()

    sent = 0
    for row in rows:
        subject, html, text = _paid_email(_first_name_of(row))
        if _send_email(row["email"], subject, html, text):
            conn.execute(
                "UPDATE users SET paid_reminder_sent_for = ? WHERE id = ?",
                (row["subscription_end"], row["id"]),
            )
            conn.commit()
            sent += 1
    return sent


def main():
    _load_env()

    if not os.path.isfile(DB_PATH):
        print(f"[reminders] DB not found at {DB_PATH}", flush=True)
        sys.exit(1)

    print(f"[reminders] starting at {datetime.utcnow().isoformat()}Z", flush=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        n_trial = send_trial_reminders(conn)
        n_paid = send_paid_reminders(conn)
    finally:
        conn.close()

    print(f"[reminders] done. trial={n_trial} paid={n_paid}", flush=True)


if __name__ == "__main__":
    main()
