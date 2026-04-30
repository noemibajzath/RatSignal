"""RatSignal — Payments blueprint (Stripe + NOWPayments crypto)."""

import hashlib
import hmac
import json
import logging
import os
import time
from datetime import datetime, timedelta

import smtplib
import threading
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import requests as http_requests
from flask import Blueprint, flash, jsonify, redirect, render_template, request, url_for

try:
    import stripe
    HAS_STRIPE = True
except ImportError:
    stripe = None
    HAS_STRIPE = False

try:
    from flask_login import current_user, login_required
    HAS_FLASK_LOGIN = True
except ImportError:
    HAS_FLASK_LOGIN = False
    # Stub decorator
    def login_required(f):
        return f

from temporary.ratsignal import models

# ---------------------------------------------------------------------------
# Blueprint
# ---------------------------------------------------------------------------
payments_bp = Blueprint("payments", __name__, url_prefix="/payments")

# Stripe config (from env)
STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_PUBLISHABLE_KEY = os.environ.get("STRIPE_PUBLISHABLE_KEY", "")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")

# Crypto wallet for USDC payments
CRYPTO_WALLET_ADDRESS = os.environ.get(
    "RATSIGNAL_CRYPTO_WALLET",
    "0x0000000000000000000000000000000000000000"  # placeholder
)

if HAS_STRIPE and STRIPE_SECRET_KEY:
    stripe.api_key = STRIPE_SECRET_KEY

# Price IDs — set these from Stripe dashboard
STRIPE_MONTHLY_PRICE_ID = os.environ.get("STRIPE_MONTHLY_PRICE_ID", "")
STRIPE_LIFETIME_PRICE_ID = os.environ.get("STRIPE_LIFETIME_PRICE_ID", "")

# ---------------------------------------------------------------------------
# NOWPayments config (from env)
# ---------------------------------------------------------------------------
NOWPAYMENTS_API_KEY = os.environ.get("NOWPAYMENTS_API_KEY", "")
NOWPAYMENTS_IPN_SECRET = os.environ.get("NOWPAYMENTS_IPN_SECRET", "")
NOWPAYMENTS_SANDBOX = os.environ.get("NOWPAYMENTS_SANDBOX", "false").lower() == "true"
WALLETCONNECT_PROJECT_ID = os.environ.get("WALLETCONNECT_PROJECT_ID", "")

_NP_BASE = (
    "https://api-sandbox.nowpayments.io/v1"
    if NOWPAYMENTS_SANDBOX
    else "https://api.nowpayments.io/v1"
)

log = logging.getLogger("ratsignal.payments")


def _np_headers():
    return {"x-api-key": NOWPAYMENTS_API_KEY, "Content-Type": "application/json"}


def _np_post(endpoint: str, payload: dict) -> dict:
    """POST to NOWPayments API. Returns response JSON or raises.

    On HTTP error, attach the JSON body's `message` to the exception so
    callers (and our flash messages) can surface the actual reason — the raw
    'HTTPError 400' tells users nothing; 'Crypto amount 0.99 is less than
    minimal' tells them exactly what to change.
    """
    resp = http_requests.post(
        f"{_NP_BASE}/{endpoint}", json=payload, headers=_np_headers(), timeout=15,
    )
    if not resp.ok:
        try:
            body = resp.json()
            msg = body.get("message") or body.get("error") or resp.text[:200]
        except Exception:
            msg = resp.text[:200]
        raise http_requests.HTTPError(
            f"{resp.status_code} {msg}", response=resp,
        )
    return resp.json()


def _np_get(endpoint: str, params: dict = None, timeout: int = 15) -> dict:
    """GET from NOWPayments API."""
    resp = http_requests.get(
        f"{_NP_BASE}/{endpoint}", params=params, headers=_np_headers(), timeout=timeout,
    )
    resp.raise_for_status()
    return resp.json()


def _verify_ipn_signature(body_bytes: bytes, sig: str) -> bool:
    """Verify NOWPayments IPN HMAC-SHA512 signature."""
    if not NOWPAYMENTS_IPN_SECRET or not sig:
        return False
    data = json.loads(body_bytes)
    sorted_data = json.dumps(data, sort_keys=True, separators=(",", ":"))
    expected = hmac.HMAC(
        NOWPAYMENTS_IPN_SECRET.encode(), sorted_data.encode(), hashlib.sha512,
    ).hexdigest()
    return hmac.compare_digest(expected, sig)


# ---------------------------------------------------------------------------
# Checkout
# ---------------------------------------------------------------------------
@payments_bp.route("/start-trial", methods=["POST"])
@login_required
def start_trial():
    """Activate a one-time 7-day free trial for the current user."""
    result = models.start_free_trial(current_user.id, days=7)
    if not result:
        flash("Free trial is no longer available for this account.", "error")
    else:
        flash("Your 7-day free trial is now active. Welcome aboard!", "success")
    return redirect(url_for("auth.profile"))


@payments_bp.route("/create-checkout", methods=["POST"])
@login_required
def create_checkout():
    if not HAS_STRIPE or not STRIPE_SECRET_KEY:
        flash("Payment system is not configured yet.", "error")
        return redirect("/#section-pricing")

    plan = request.form.get("plan", "monthly")
    base_url = request.host_url.rstrip("/")

    try:
        if plan == "lifetime":
            # One-time $100,000 payment
            session_params = {
                "mode": "payment",
                "line_items": [{
                    "price": STRIPE_LIFETIME_PRICE_ID,
                    "quantity": 1,
                }] if STRIPE_LIFETIME_PRICE_ID else [{
                    "price_data": {
                        "currency": "usd",
                        "product_data": {"name": "RatSignal Lifetime Access"},
                        "unit_amount": 10000000,  # $100,000 in cents
                    },
                    "quantity": 1,
                }],
                "success_url": f"{base_url}/payments/success?session_id={{CHECKOUT_SESSION_ID}}",
                "cancel_url": f"{base_url}/#section-pricing",
                "client_reference_id": str(current_user.id),
                "customer_email": current_user.email,
            }
        else:
            # Monthly $100/mo with 7-day trial, $50 first month
            session_params = {
                "mode": "subscription",
                "line_items": [{
                    "price": STRIPE_MONTHLY_PRICE_ID,
                    "quantity": 1,
                }] if STRIPE_MONTHLY_PRICE_ID else [{
                    "price_data": {
                        "currency": "usd",
                        "product_data": {"name": "RatSignal Monthly"},
                        "unit_amount": 10000,  # $100 in cents
                        "recurring": {"interval": "month"},
                    },
                    "quantity": 1,
                }],
                "subscription_data": {
                    "trial_period_days": 7,
                },
                "success_url": f"{base_url}/payments/success?session_id={{CHECKOUT_SESSION_ID}}",
                "cancel_url": f"{base_url}/#section-pricing",
                "client_reference_id": str(current_user.id),
                "customer_email": current_user.email,
            }

        checkout_session = stripe.checkout.Session.create(**session_params)
        return redirect(checkout_session.url, code=303)

    except Exception as e:
        flash(f"Payment error: {str(e)}", "error")
        return redirect("/#section-pricing")


# ---------------------------------------------------------------------------
# Success callback
# ---------------------------------------------------------------------------
@payments_bp.route("/success")
@login_required
def payment_success():
    session_id = request.args.get("session_id")
    if not session_id or not HAS_STRIPE or not STRIPE_SECRET_KEY:
        flash("Payment verification failed.", "error")
        return redirect("/")

    try:
        checkout_session = stripe.checkout.Session.retrieve(session_id)
        user_id = int(checkout_session.client_reference_id)

        if checkout_session.payment_status == "paid" or checkout_session.status == "complete":
            # Determine plan
            if checkout_session.mode == "payment":
                plan = "lifetime"
                status = "active"
                end_date = None  # lifetime = no end
                amount = 10000000
            else:
                plan = "monthly"
                status = "trial" if checkout_session.get("subscription") else "active"
                end_date = (datetime.utcnow() + timedelta(days=37)).isoformat()  # 7 trial + 30 days
                amount = 10000

            models.update_subscription(user_id, status, plan, end_date)
            models.record_payment(
                user_id=user_id,
                amount_cents=amount,
                plan=plan,
                stripe_session_id=session_id,
                status="completed",
            )
            flash("Payment successful! Your subscription is now active.", "success")
        else:
            flash("Payment is still processing. Please check back shortly.", "info")

    except Exception as e:
        flash(f"Error verifying payment: {str(e)}", "error")

    return redirect("/auth/dashboard")


# ---------------------------------------------------------------------------
# Stripe webhook
# ---------------------------------------------------------------------------
@payments_bp.route("/webhook/stripe", methods=["POST"])
def stripe_webhook():
    if not HAS_STRIPE or not STRIPE_WEBHOOK_SECRET:
        return jsonify({"error": "not configured"}), 400

    payload = request.get_data()
    sig_header = request.headers.get("Stripe-Signature", "")

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
    except (ValueError, stripe.error.SignatureVerificationError):
        return jsonify({"error": "invalid signature"}), 400

    event_type = event["type"]

    if event_type == "checkout.session.completed":
        session_obj = event["data"]["object"]
        user_id = session_obj.get("client_reference_id")
        if user_id:
            user_id = int(user_id)
            if session_obj["mode"] == "payment":
                models.update_subscription(user_id, "active", "lifetime", None)
            else:
                end = (datetime.utcnow() + timedelta(days=37)).isoformat()
                models.update_subscription(user_id, "active", "monthly", end)

    elif event_type == "customer.subscription.deleted":
        # Subscription cancelled
        sub = event["data"]["object"]
        customer_email = sub.get("customer_email") or ""
        if customer_email:
            user = models.get_user_by_email(customer_email)
            if user:
                models.update_subscription(user["id"], "cancelled", user.get("subscription_plan"))

    elif event_type == "invoice.payment_failed":
        sub = event["data"]["object"]
        customer_email = sub.get("customer_email") or ""
        if customer_email:
            user = models.get_user_by_email(customer_email)
            if user:
                models.update_subscription(user["id"], "expired", user.get("subscription_plan"))

    return jsonify({"status": "ok"}), 200


# ---------------------------------------------------------------------------
# NOWPayments — Crypto checkout (creates invoice, redirects to hosted page)
# ---------------------------------------------------------------------------
@payments_bp.route("/crypto-checkout", methods=["POST"])
@login_required
def crypto_checkout():
    if not NOWPAYMENTS_API_KEY:
        flash("Crypto payments are not configured yet.", "error")
        return redirect("/#section-pricing")

    plan = request.form.get("plan", "monthly")
    pay_currency = request.form.get("pay_currency", "")  # empty = let user choose
    base_url = request.host_url.rstrip("/")

    if plan == "lifetime":
        price_amount = 100000
        description = "RatSignal Lifetime Access"
    elif plan == "yearly":
        price_amount = 990
        description = "RatSignal Yearly ($990, save $210)"
    elif plan == "test":
        price_amount = 1
        description = "RatSignal Test Subscription ($1)"
    else:
        price_amount = 100
        description = "RatSignal Monthly"

    order_id = f"user_{current_user.id}_{plan}_{int(datetime.utcnow().timestamp())}"

    invoice_payload = {
        "price_amount": price_amount,
        "price_currency": "usd",
        "order_id": order_id,
        "order_description": description,
        "ipn_callback_url": f"{base_url}/payments/webhook/nowpayments",
        "success_url": f"{base_url}/payments/crypto-success",
        "cancel_url": f"{base_url}/#section-pricing",
    }
    if pay_currency:
        invoice_payload["pay_currency"] = pay_currency.lower()

    try:
        result = _np_post("invoice", invoice_payload)
        invoice_id = result.get("id")
        invoice_url = result.get("invoice_url")

        if not invoice_url:
            flash("Could not create crypto invoice. Please try again.", "error")
            return redirect("/#section-pricing")

        # Record pending payment
        models.record_payment(
            user_id=current_user.id,
            amount_cents=price_amount * 100,
            plan=plan,
            nowpayments_id=str(invoice_id),
            status="pending",
            currency="crypto",
        )

        log.info("NOWPayments invoice %s created for user %s plan=%s",
                 invoice_id, current_user.id, plan)

        return redirect(invoice_url, code=303)

    except Exception as e:
        log.error("NOWPayments invoice creation failed: %s", e)
        flash(f"Crypto payment error: {e}", "error")
        return redirect("/#section-pricing")


# ---------------------------------------------------------------------------
# NOWPayments — Success redirect (user lands here after paying)
# ---------------------------------------------------------------------------
@payments_bp.route("/crypto-success")
@login_required
def crypto_success():
    flash("Crypto payment received! It may take a few minutes to confirm.", "success")
    return redirect("/auth/dashboard")


# ---------------------------------------------------------------------------
# NOWPayments — IPN Webhook (payment status updates)
# ---------------------------------------------------------------------------
@payments_bp.route("/webhook/nowpayments", methods=["POST"])
def nowpayments_webhook():
    if not NOWPAYMENTS_IPN_SECRET:
        return jsonify({"error": "not configured"}), 400

    body = request.get_data()
    sig = request.headers.get("x-nowpayments-sig", "")

    if not _verify_ipn_signature(body, sig):
        log.warning("NOWPayments IPN: invalid signature")
        return jsonify({"error": "invalid signature"}), 403

    data = json.loads(body)
    np_status = data.get("payment_status", "")
    np_payment_id = str(data.get("payment_id", ""))
    np_invoice_id = str(data.get("invoice_id", ""))
    order_id = data.get("order_id", "")
    actually_paid = data.get("actually_paid", 0)

    log.info("NOWPayments IPN: invoice=%s payment=%s status=%s order=%s paid=%s",
             np_invoice_id, np_payment_id, np_status, order_id, actually_paid)

    # Find our payment record by invoice ID
    payment = models.get_payment_by_nowpayments_id(np_invoice_id)
    if not payment and np_payment_id:
        payment = models.get_payment_by_nowpayments_id(np_payment_id)

    if not payment:
        log.warning("NOWPayments IPN: no matching payment for invoice=%s", np_invoice_id)
        return jsonify({"status": "ok"}), 200

    # Extract user_id from order_id (format: user_{id}_{plan}_{ts})
    user_id = payment["user_id"]

    if np_status == "finished":
        # Payment complete — activate subscription
        plan = payment["plan"]
        if plan == "lifetime":
            end_date = None
        elif plan == "yearly":
            end_date = (datetime.utcnow() + timedelta(days=365)).isoformat()
        elif plan == "test":
            end_date = (datetime.utcnow() + timedelta(days=1)).isoformat()
        else:
            end_date = (datetime.utcnow() + timedelta(days=30)).isoformat()

        models.update_payment_status(payment["id"], "completed", crypto_tx_hash=np_payment_id)
        models.update_subscription(user_id, "active", plan, end_date)
        log.info("NOWPayments: user %s subscription activated (plan=%s)", user_id, plan)

        # Send payment confirmation email (background thread; errors logged, not raised)
        user = models.get_user_by_id(user_id)
        if user:
            amount_usd = int(payment.get("amount_cents", 0) // 100)
            _send_payment_confirmation_email(user, plan, end_date, amount_usd, tx_hash=np_payment_id)

    elif np_status == "partially_paid":
        models.update_payment_status(payment["id"], "partially_paid")

    elif np_status in ("failed", "expired", "refunded"):
        models.update_payment_status(payment["id"], np_status)

    elif np_status in ("waiting", "confirming", "confirmed", "sending"):
        models.update_payment_status(payment["id"], np_status)

    return jsonify({"status": "ok"}), 200


# ---------------------------------------------------------------------------
# NOWPayments — API status check
# ---------------------------------------------------------------------------
@payments_bp.route("/crypto-status")
def crypto_api_status():
    if not NOWPAYMENTS_API_KEY:
        return jsonify({"status": "not_configured"})
    try:
        result = _np_get("status")
        return jsonify(result)
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# ---------------------------------------------------------------------------
# User dashboard (subscription status)
# ---------------------------------------------------------------------------
@payments_bp.route("/dashboard")
@login_required
def dashboard():
    # This is an alias; the auth blueprint also provides /auth/dashboard
    payments = models.get_user_payments(current_user.id)
    return render_template("user_dashboard.html", payments=payments)


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Payment confirmation email (sent when IPN flips a payment to finished)
# ---------------------------------------------------------------------------
_EMAIL_SENDER = "ratsignalcrypto@gmail.com"

def _send_payment_confirmation_email(user: dict, plan: str, end_date: str | None,
                                     amount_usd: int, tx_hash: str | None = None):
    """Send payment confirmation email in a background thread so the IPN
    webhook returns immediately."""
    def _send():
        password = os.environ.get("GMAIL_APP_PASSWORD", "")
        if not password:
            log.warning("GMAIL_APP_PASSWORD not set — skipping payment confirmation email")
            return
        to_email = user.get("email")
        if not to_email:
            return

        first_name = (user.get("first_name") or "").strip() or "friend"
        end_str = end_date[:10] if end_date else "lifetime"
        plan_label = {
            "monthly": "Monthly - $100/mo",
            "yearly": "Yearly - $990/yr (saved $210)",
            "lifetime": "Lifetime access",
            "test": "$1 test (1-day access)",
        }.get(plan, plan)
        tx_block_html = ""
        tx_block_text = ""
        if tx_hash and len(tx_hash) > 20:
            tx_block_html = f'<p style="font-family:JetBrains Mono,monospace;font-size:12px;color:#55556a;word-break:break-all;">Transaction: {tx_hash}</p>'
            tx_block_text = f"\nTransaction: {tx_hash}\n"

        subject = "Payment received — you're in the kitchen"
        if plan == "lifetime":
            subject = "Payment received — you're in the kitchen forever"

        html = f"""<!DOCTYPE html>
<html><head><style>
body {{{{ font-family: 'Inter', Arial, sans-serif; background: #0a1628; color: #f0f0f5; margin: 0; padding: 0; }}}}
.container {{{{ max-width: 560px; margin: 0 auto; padding: 40px 24px; }}}}
.header {{{{ text-align: center; margin-bottom: 32px; }}}}
.header h1 {{{{ font-size: 24px; margin: 0; }}}}
.header h1 span {{{{ color: #ff6b2b; }}}}
.card {{{{ background: #0f1f35; border: 1px solid #1a2d4a; border-radius: 12px; padding: 32px; }}}}
.card h2 {{{{ font-size: 20px; margin: 0 0 16px; }}}}
.card p {{{{ color: #8888a0; line-height: 1.7; margin: 0 0 16px; font-size: 15px; }}}}
.highlight {{{{ color: #ff6b2b; font-weight: 600; }}}}
.receipt {{{{ background: #0a1628; border: 1px solid #1a2d4a; border-radius: 8px; padding: 18px; margin: 20px 0; font-family: 'JetBrains Mono', monospace; font-size: 13px; }}}}
.receipt .row {{{{ display: flex; justify-content: space-between; padding: 6px 0; }}}}
.receipt .label {{{{ color: #55556a; }}}}
.receipt .val {{{{ color: #f0f0f5; }}}}
.cta {{{{ display: inline-block; padding: 14px 28px; background: linear-gradient(135deg,#ff6b2b,#ff8f5e); color: white !important; text-decoration: none; border-radius: 8px; font-weight: 700; margin-top: 12px; }}}}
.divider {{{{ border: none; border-top: 1px solid #1a2d4a; margin: 24px 0; }}}}
.signature {{{{ color: #8888a0; font-size: 14px; line-height: 1.6; }}}}
.signature strong {{{{ color: #ff6b2b; }}}}
.footer {{{{ text-align: center; margin-top: 32px; color: #55556a; font-size: 12px; }}}}
</style></head><body>
<div class="container">
    <div class="header"><h1>Rat<span>Signal</span></h1></div>
    <div class="card">
        <h2>Welcome in, {first_name}.</h2>
        <p>Your payment landed, the rats are cooking, and your seat at the table is officially warm.</p>
        <div class="receipt">
            <div class="row"><span class="label">Plan</span><span class="val">{plan_label}</span></div>
            <div class="row"><span class="label">Amount</span><span class="val">${amount_usd} USD</span></div>
            <div class="row"><span class="label">Active until</span><span class="val">{end_str}</span></div>
        </div>
        {tx_block_html}
        <p>You'll be added to the <span class="highlight">Signal Group</span> on Telegram within 24 hours — entries, take profits, stop losses, and risk scores straight to your phone.</p>
        <p>Your live PnL dashboard and signals feed are already unlocked on ratsignal.com:</p>
        <p style="text-align:center;"><a class="cta" href="https://ratsignal.com/auth/dashboard">Open Dashboard →</a></p>
        <hr class="divider">
        <div class="signature"><strong>Thanks for cooking with us,</strong><br>The RatSignal Team</div>
    </div>
    <div class="footer">&copy; 2026 RatSignal. Receipt for your records.</div>
</div>
</body></html>"""

        text = f"""Welcome in, {first_name}.

Your payment landed, the rats are cooking, and your seat at the table is officially warm.

---
Plan:         {plan_label}
Amount:       ${amount_usd} USD
Active until: {end_str}
{tx_block_text}---

You'll be added to the Signal Group on Telegram within 24 hours — entries, take profits, stop losses, and risk scores straight to your phone.

Your live PnL dashboard and signals feed are already unlocked:
→ https://ratsignal.com/auth/dashboard

Thanks for cooking with us,
The RatSignal Team
"""

        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = f"RatSignal <{_EMAIL_SENDER}>"
            msg["To"] = to_email
            msg.attach(MIMEText(text, "plain"))
            msg.attach(MIMEText(html, "html"))
            with smtplib.SMTP("smtp.gmail.com", 587, timeout=15) as server:
                server.starttls()
                server.login(_EMAIL_SENDER, password)
                server.sendmail(_EMAIL_SENDER, to_email, msg.as_string())
            log.info("Payment confirmation email sent to %s (plan=%s)", to_email, plan)
        except Exception as e:
            log.error("Failed to send payment confirmation email to %s: %s", to_email, e)

    threading.Thread(target=_send, daemon=True).start()


# NOWPayments — Direct wallet payment flow (bypasses hosted Deposit iframe)
# ---------------------------------------------------------------------------
# EVM chain configs keyed by NOWPayments `network` code (returned by
# /v1/full-currencies). Only chains listed here get the in-browser wallet-pay
# flow; anything else (BTC, SOL, TRX, …) falls back to manual-send in the UI.
EVM_NETWORKS = {
    "eth": {
        "chainIdHex": "0x1",
        "chainName": "Ethereum",
        "nativeCurrency": {"name": "Ether", "symbol": "ETH", "decimals": 18},
        "rpcUrls": ["https://eth.drpc.org", "https://ethereum-rpc.publicnode.com", "https://1rpc.io/eth"],
        "blockExplorerUrls": ["https://etherscan.io"],
        "label": "Ethereum",
    },
    "bsc": {
        "chainIdHex": "0x38",
        "chainName": "BNB Smart Chain",
        "nativeCurrency": {"name": "BNB", "symbol": "BNB", "decimals": 18},
        "rpcUrls": ["https://bsc-dataseed.binance.org", "https://bsc.publicnode.com"],
        "blockExplorerUrls": ["https://bscscan.com"],
        "label": "BSC",
    },
    "matic": {
        "chainIdHex": "0x89",
        "chainName": "Polygon",
        "nativeCurrency": {"name": "POL", "symbol": "POL", "decimals": 18},
        "rpcUrls": ["https://polygon.drpc.org", "https://polygon-bor-rpc.publicnode.com", "https://1rpc.io/matic"],
        "blockExplorerUrls": ["https://polygonscan.com"],
        "label": "Polygon",
    },
    "polygon": {
        "chainIdHex": "0x89",
        "chainName": "Polygon",
        "nativeCurrency": {"name": "POL", "symbol": "POL", "decimals": 18},
        "rpcUrls": ["https://polygon.drpc.org", "https://polygon-bor-rpc.publicnode.com", "https://1rpc.io/matic"],
        "blockExplorerUrls": ["https://polygonscan.com"],
        "label": "Polygon",
    },
    "arb": {
        "chainIdHex": "0xa4b1",
        "chainName": "Arbitrum One",
        "nativeCurrency": {"name": "Ether", "symbol": "ETH", "decimals": 18},
        "rpcUrls": ["https://arb1.arbitrum.io/rpc", "https://arbitrum.drpc.org"],
        "blockExplorerUrls": ["https://arbiscan.io"],
        "label": "Arbitrum",
    },
    "arbitrum": {
        "chainIdHex": "0xa4b1",
        "chainName": "Arbitrum One",
        "nativeCurrency": {"name": "Ether", "symbol": "ETH", "decimals": 18},
        "rpcUrls": ["https://arb1.arbitrum.io/rpc", "https://arbitrum.drpc.org"],
        "blockExplorerUrls": ["https://arbiscan.io"],
        "label": "Arbitrum",
    },
    "op": {
        "chainIdHex": "0xa",
        "chainName": "Optimism",
        "nativeCurrency": {"name": "Ether", "symbol": "ETH", "decimals": 18},
        "rpcUrls": ["https://mainnet.optimism.io", "https://optimism.drpc.org"],
        "blockExplorerUrls": ["https://optimistic.etherscan.io"],
        "label": "Optimism",
    },
    "optimism": {
        "chainIdHex": "0xa",
        "chainName": "Optimism",
        "nativeCurrency": {"name": "Ether", "symbol": "ETH", "decimals": 18},
        "rpcUrls": ["https://mainnet.optimism.io", "https://optimism.drpc.org"],
        "blockExplorerUrls": ["https://optimistic.etherscan.io"],
        "label": "Optimism",
    },
    "base": {
        "chainIdHex": "0x2105",
        "chainName": "Base",
        "nativeCurrency": {"name": "Ether", "symbol": "ETH", "decimals": 18},
        "rpcUrls": ["https://mainnet.base.org", "https://base.drpc.org"],
        "blockExplorerUrls": ["https://basescan.org"],
        "label": "Base",
    },
    "avax": {
        "chainIdHex": "0xa86a",
        "chainName": "Avalanche C-Chain",
        "nativeCurrency": {"name": "AVAX", "symbol": "AVAX", "decimals": 18},
        "rpcUrls": ["https://api.avax.network/ext/bc/C/rpc", "https://avalanche.drpc.org"],
        "blockExplorerUrls": ["https://snowtrace.io"],
        "label": "Avalanche",
    },
    "avaxc": {
        "chainIdHex": "0xa86a",
        "chainName": "Avalanche C-Chain",
        "nativeCurrency": {"name": "AVAX", "symbol": "AVAX", "decimals": 18},
        "rpcUrls": ["https://api.avax.network/ext/bc/C/rpc", "https://avalanche.drpc.org"],
        "blockExplorerUrls": ["https://snowtrace.io"],
        "label": "Avalanche",
    },
    "ftm": {
        "chainIdHex": "0xfa",
        "chainName": "Fantom",
        "nativeCurrency": {"name": "FTM", "symbol": "FTM", "decimals": 18},
        "rpcUrls": ["https://rpc.ftm.tools", "https://fantom.drpc.org"],
        "blockExplorerUrls": ["https://ftmscan.com"],
        "label": "Fantom",
    },
    "xdai": {
        "chainIdHex": "0x64",
        "chainName": "Gnosis",
        "nativeCurrency": {"name": "xDAI", "symbol": "xDAI", "decimals": 18},
        "rpcUrls": ["https://rpc.gnosischain.com", "https://gnosis.drpc.org"],
        "blockExplorerUrls": ["https://gnosisscan.io"],
        "label": "Gnosis",
    },
    "cro": {
        "chainIdHex": "0x19",
        "chainName": "Cronos",
        "nativeCurrency": {"name": "CRO", "symbol": "CRO", "decimals": 18},
        "rpcUrls": ["https://evm.cronos.org", "https://cronos.drpc.org"],
        "blockExplorerUrls": ["https://cronoscan.com"],
        "label": "Cronos",
    },
    "zksync": {
        "chainIdHex": "0x144",
        "chainName": "zkSync Era",
        "nativeCurrency": {"name": "Ether", "symbol": "ETH", "decimals": 18},
        "rpcUrls": ["https://mainnet.era.zksync.io"],
        "blockExplorerUrls": ["https://explorer.zksync.io"],
        "label": "zkSync Era",
    },
    "linea": {
        "chainIdHex": "0xe708",
        "chainName": "Linea",
        "nativeCurrency": {"name": "Ether", "symbol": "ETH", "decimals": 18},
        "rpcUrls": ["https://rpc.linea.build", "https://linea.drpc.org"],
        "blockExplorerUrls": ["https://lineascan.build"],
        "label": "Linea",
    },
    "celo": {
        "chainIdHex": "0xa4ec",
        "chainName": "Celo",
        "nativeCurrency": {"name": "CELO", "symbol": "CELO", "decimals": 18},
        "rpcUrls": ["https://forno.celo.org"],
        "blockExplorerUrls": ["https://celoscan.io"],
        "label": "Celo",
    },
    "mantle": {
        "chainIdHex": "0x1388",
        "chainName": "Mantle",
        "nativeCurrency": {"name": "MNT", "symbol": "MNT", "decimals": 18},
        "rpcUrls": ["https://rpc.mantle.xyz"],
        "blockExplorerUrls": ["https://explorer.mantle.xyz"],
        "label": "Mantle",
    },
    "moonbeam": {
        "chainIdHex": "0x504",
        "chainName": "Moonbeam",
        "nativeCurrency": {"name": "GLMR", "symbol": "GLMR", "decimals": 18},
        "rpcUrls": ["https://rpc.api.moonbeam.network"],
        "blockExplorerUrls": ["https://moonscan.io"],
        "label": "Moonbeam",
    },
}

# NOWPayments sometimes returns networks as "ethereum", "binance-smart-chain",
# etc. Normalize those to our keys.
_NETWORK_ALIASES = {
    "ethereum": "eth",
    "binance-smart-chain": "bsc",
    "binance_smart_chain": "bsc",
    "bep20": "bsc",
    "polygon": "matic",
    "arbitrum-one": "arb",
    "arbitrum_one": "arb",
    "optimism-mainnet": "op",
    "avalanche": "avax",
    "avalanche-c": "avax",
    "avalanche_c": "avax",
    "fantom": "ftm",
    "gnosis": "xdai",
    "cronos": "cro",
}


def _normalize_network(network: str) -> str:
    if not network:
        return ""
    key = network.strip().lower().replace(" ", "-")
    return _NETWORK_ALIASES.get(key, key)


# In-memory cache of /v1/full-currencies (1h TTL, per-worker).
# The lock prevents a thundering herd: while one thread fetches from
# NOWPayments, concurrent readers wait briefly and then hit the warm cache
# instead of each triggering their own ~5s API call.
_currencies_cache = {"ts": 0, "data": None}
_currencies_lock = threading.Lock()
_CURRENCIES_TTL = 3600
_CURRENCIES_FETCH_TIMEOUT = 4  # seconds — keep it short so a slow NOWPayments
                               # API doesn't starve a gunicorn worker.


def _get_full_currencies(force: bool = False):
    """Return the NOWPayments /v1/full-currencies list (cached 1h).

    Returns [] on error so callers don't need to handle None. On cache miss
    under contention, only one thread fetches; others wait on the lock and
    then read the now-warm cache.
    """
    now = time.time()
    cached = _currencies_cache["data"]
    if not force and cached is not None and now - _currencies_cache["ts"] < _CURRENCIES_TTL:
        return cached

    with _currencies_lock:
        # Double-check after acquiring the lock — another thread may have
        # populated the cache while we were waiting.
        now = time.time()
        cached = _currencies_cache["data"]
        if not force and cached is not None and now - _currencies_cache["ts"] < _CURRENCIES_TTL:
            return cached
        try:
            resp = _np_get("full-currencies", timeout=_CURRENCIES_FETCH_TIMEOUT)
            rows = resp.get("currencies", resp) if isinstance(resp, dict) else resp
            if not isinstance(rows, list):
                rows = []
            _currencies_cache["data"] = rows
            _currencies_cache["ts"] = now
            return rows
        except Exception as e:
            log.error("NOWPayments /full-currencies fetch failed: %s", e)
            # Serve stale cache if we have it; otherwise empty list so the
            # frontend falls back to its hardcoded "USDT on Polygon" option.
            return cached or []


def _prewarm_currencies():
    """Background prefetch at worker boot so the first real user request
    doesn't pay the cold-cache cost."""
    def _run():
        try:
            _get_full_currencies()
            log.info("NOWPayments currencies cache pre-warmed (%d rows)",
                     len(_currencies_cache["data"] or []))
        except Exception as e:
            log.warning("Currencies prewarm failed: %s", e)
    if NOWPAYMENTS_API_KEY:
        threading.Thread(target=_run, daemon=True).start()


_prewarm_currencies()


def _find_currency(code: str):
    """Look up a row in the cached /full-currencies list by code. Case-insensitive."""
    code = (code or "").lower()
    if not code:
        return None
    for row in _get_full_currencies():
        if (row.get("code") or "").lower() == code:
            return row
    return None


def _infer_network(pay_currency: str) -> str:
    cur = (pay_currency or "").lower()
    if cur.endswith("matic"):
        return "Polygon"
    if cur.endswith("erc20"):
        return "Ethereum"
    if cur.endswith("bsc") or cur.endswith("bep20"):
        return "BSC"
    if cur.endswith("trc20"):
        return "Tron"
    if cur.endswith("sol"):
        return "Solana"
    return cur.upper() or "Unknown"


# ---------------------------------------------------------------------------
# EVM-compatible currencies endpoint (powers the profile page picker)
# ---------------------------------------------------------------------------
@payments_bp.route("/currencies")
def crypto_currencies():
    """Return the list of NOWPayments currencies that are payable via the
    in-browser wallet flow (i.e. EVM chains we know how to render)."""
    rows = _get_full_currencies()
    by_network: dict[str, dict] = {}
    for row in rows:
        if not row.get("enable", True):
            continue
        net = _normalize_network(row.get("network") or "")
        if net not in EVM_NETWORKS:
            continue
        code = (row.get("code") or "").lower()
        if not code:
            continue
        entry = {
            "code": code,
            "ticker": (row.get("ticker") or code).upper(),
            "name": row.get("name") or code.upper(),
            "network": net,
            "network_label": EVM_NETWORKS[net]["label"],
            "logo_url": row.get("logo_url") or "",
            "is_native": not row.get("smart_contract"),
        }
        by_network.setdefault(net, {
            "network": net,
            "label": EVM_NETWORKS[net]["label"],
            "currencies": [],
        })["currencies"].append(entry)

    # Sort currencies within each network: native first, then alphabetical
    for group in by_network.values():
        group["currencies"].sort(key=lambda c: (not c["is_native"], c["ticker"]))

    # Sort networks by a sensible priority (Polygon first — cheapest gas)
    priority = {"matic": 0, "base": 1, "arb": 2, "op": 3, "bsc": 4, "eth": 5}
    networks = sorted(by_network.values(), key=lambda g: (priority.get(g["network"], 99), g["label"]))
    return jsonify({"networks": networks})


@payments_bp.route("/crypto-wallet-pay", methods=["POST"])
@login_required
def crypto_wallet_pay():
    """Create a NOWPayments direct payment and render an in-house wallet UI.

    Unlike /crypto-checkout which redirects to NOWPayments' hosted invoice page
    (whose in-iframe MetaMask Deposit button errors with 'Version of JSON-RPC
    protocol is not supported'), this calls /v1/payment directly and renders
    our own page that uses window.ethereum to perform an ERC-20 transfer.
    """
    if not NOWPAYMENTS_API_KEY:
        flash("Crypto payments are not configured yet.", "error")
        return redirect("/#section-pricing")

    plan = request.form.get("plan", "monthly")
    pay_currency = request.form.get("pay_currency", "usdtmatic").lower()
    base_url = request.host_url.rstrip("/")

    if plan == "lifetime":
        price_amount = 100000
        description = "RatSignal Lifetime Access"
    elif plan == "yearly":
        price_amount = 990
        description = "RatSignal Yearly ($990, save $210)"
    elif plan == "test":
        price_amount = 1
        description = "RatSignal Test Subscription ($1)"
    else:
        price_amount = 100
        description = "RatSignal Monthly"

    # For test mode, user might have picked a currency whose minimum payment is
    # above $1. Stay on the profile page (with the picker) on failure so they
    # can try a different one, instead of bouncing them to the landing page.
    failure_redirect = "/auth/profile?test=1" if plan == "test" else "/#section-pricing"

    order_id = f"user_{current_user.id}_{plan}_{int(datetime.utcnow().timestamp())}"

    payment_payload = {
        "price_amount": price_amount,
        "price_currency": "usd",
        "pay_currency": pay_currency,
        "order_id": order_id,
        "order_description": description,
        "ipn_callback_url": f"{base_url}/payments/webhook/nowpayments",
    }

    try:
        result = _np_post("payment", payment_payload)
    except Exception as e:
        log.error("NOWPayments /payment creation failed: %s (currency=%s amount=%s)",
                  e, pay_currency, price_amount)
        # _np_post now surfaces NOWPayments' own `message` in the exception,
        # so str(e) looks like "400 Crypto amount 0.99 is less than minimal".
        err_text = str(e)
        flash(f"Crypto payment error: {err_text}. Try a different token or chain.", "error")
        return redirect(failure_redirect)

    payment_id = str(result.get("payment_id", ""))
    pay_address = result.get("pay_address", "")
    pay_amount = result.get("pay_amount", "")

    # Resolve chain config from NOWPayments /full-currencies + our EVM map.
    # Precedence for the network code: /v1/payment response → /full-currencies
    # row → _infer_network fallback.
    currency_row = _find_currency(pay_currency) or {}
    network_code = _normalize_network(
        result.get("network") or currency_row.get("network") or ""
    )
    chain_config = EVM_NETWORKS.get(network_code)
    token_contract = (currency_row.get("smart_contract") or "").strip() or None
    token_decimals = currency_row.get("precision")
    if token_decimals is None and chain_config and not token_contract:
        token_decimals = chain_config["nativeCurrency"]["decimals"]
    explorer_tx_base = ""
    if currency_row.get("explorer_link_hash"):
        explorer_tx_base = currency_row["explorer_link_hash"].replace("{}", "")
    elif chain_config:
        explorer_tx_base = chain_config["blockExplorerUrls"][0].rstrip("/") + "/tx/"
    network_label = (chain_config["label"] if chain_config else _infer_network(pay_currency))

    if not pay_address or not payment_id:
        flash("Could not create crypto payment. Please try again.", "error")
        return redirect("/#section-pricing")

    models.record_payment(
        user_id=current_user.id,
        amount_cents=price_amount * 100,
        plan=plan,
        nowpayments_id=payment_id,
        status="pending",
        currency="crypto",
    )

    log.info("NOWPayments /payment %s created user=%s plan=%s currency=%s network=%s",
             payment_id, current_user.id, plan, pay_currency, network_code)

    return render_template(
        "wallet_pay.html",
        payment_id=payment_id,
        pay_address=pay_address,
        pay_amount=str(pay_amount),
        pay_currency=pay_currency,
        network=network_label,
        plan=plan,
        price_amount=price_amount,
        order_id=order_id,
        wc_project_id=WALLETCONNECT_PROJECT_ID,
        chain_config=chain_config,
        token_contract=token_contract,
        token_decimals=token_decimals,
        explorer_tx_base=explorer_tx_base,
    )


@payments_bp.route("/payment-status/<payment_id>")
@login_required
def payment_status(payment_id):
    """Return current payment status from local DB (updated by IPN webhook)."""
    payment = models.get_payment_by_nowpayments_id(payment_id)
    if not payment or payment.get("user_id") != current_user.id:
        return jsonify({"status": "unknown"}), 404
    return jsonify({"status": payment.get("status", "pending")})


# ---------------------------------------------------------------------------
# 30-day money-back guarantee / cancel
# ---------------------------------------------------------------------------
REFUND_WINDOW_DAYS = 30


def _latest_completed_payment(user_id: int) -> dict | None:
    payments = models.get_user_payments(user_id) or []
    for p in payments:
        if (p.get("status") or "").lower() in ("completed", "confirmed", "finished", "paid"):
            return p
    return None


def _parse_db_time(ts: str) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.strptime(ts[:19], "%Y-%m-%d %H:%M:%S")
    except Exception:
        return None


def refund_window_for_user(user_id: int):
    """Return (is_eligible, deadline_datetime_utc_or_none, hours_remaining_or_none)."""
    paid = _latest_completed_payment(user_id)
    if not paid:
        return False, None, None
    pay_time = _parse_db_time(paid.get("created_at", ""))
    if not pay_time:
        return False, None, None
    deadline = pay_time + timedelta(days=REFUND_WINDOW_DAYS)
    now = datetime.utcnow()
    if now >= deadline:
        return False, deadline, 0
    remaining = deadline - now
    return True, deadline, int(remaining.total_seconds() // 3600)


def _send_refund_user_email(to_email: str, first_name: str, refund_wallet: str, amount_cents: int):
    """Confirmation email to the user after they request a refund."""
    def _send():
        password = os.environ.get("GMAIL_APP_PASSWORD", "")
        if not password or not to_email:
            return
        name = (first_name or "").strip() or "friend"
        amt = f"${amount_cents / 100:.2f}" if amount_cents else "the full amount"
        subject = "Refund request received - RatSignal"
        html = f"""<!DOCTYPE html>
<html><body style="font-family:'Inter',Arial,sans-serif;background:#0a1628;color:#f0f0f5;margin:0;padding:0;">
<div style="max-width:560px;margin:0 auto;padding:40px 24px;">
    <div style="text-align:center;margin-bottom:32px;"><h1 style="font-size:24px;margin:0;">Rat<span style="color:#ff6b2b;">Signal</span></h1></div>
    <div style="background:#0f1f35;border:1px solid #1a2d4a;border-radius:12px;padding:32px;">
        <h2 style="font-size:20px;margin:0 0 16px;">Refund request received, {name}.</h2>
        <p style="color:#8888a0;line-height:1.7;margin:0 0 16px;font-size:15px;">We've got your cancellation. Our team will send {amt} back to the wallet you provided within <strong style="color:#00e676;">24 hours</strong>.</p>
        <div style="background:#0a1628;border:1px solid #1a2d4a;border-radius:8px;padding:18px;margin:20px 0;font-family:'JetBrains Mono',monospace;font-size:13px;">
            <div style="color:#55556a;margin-bottom:6px;">Refund will be sent to:</div>
            <div style="color:#f0f0f5;word-break:break-all;">{refund_wallet}</div>
        </div>
        <p style="color:#8888a0;line-height:1.7;margin:0 0 16px;font-size:15px;">Your access has ended. If you change your mind, you can resubscribe anytime at <a href="https://ratsignal.com/auth/profile" style="color:#ff6b2b;">ratsignal.com</a>.</p>
        <p style="color:#8888a0;line-height:1.7;margin:0 0 16px;font-size:15px;">If you have any issues, just reply to this email.</p>
        <hr style="border:none;border-top:1px solid #1a2d4a;margin:24px 0;">
        <div style="color:#8888a0;font-size:14px;"><strong style="color:#ff6b2b;">Thanks for giving us a shot,</strong><br>The RatSignal Team</div>
    </div>
</div></body></html>"""
        text = f"""Refund request received, {name}.

We've got your cancellation. Our team will send {amt} back to the wallet you provided within 24 hours.

Refund will be sent to:
{refund_wallet}

Your access has ended. If you change your mind, you can resubscribe anytime at https://ratsignal.com/auth/profile.

If you have any issues, just reply to this email.

Thanks for giving us a shot,
The RatSignal Team
"""
        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = f"RatSignal <{_EMAIL_SENDER}>"
            msg["To"] = to_email
            msg.attach(MIMEText(text, "plain"))
            msg.attach(MIMEText(html, "html"))
            with smtplib.SMTP("smtp.gmail.com", 587, timeout=15) as server:
                server.starttls()
                server.login(_EMAIL_SENDER, password)
                server.sendmail(_EMAIL_SENDER, to_email, msg.as_string())
            log.info("Refund confirmation email sent to %s", to_email)
        except Exception as e:
            log.error("Failed to send refund confirmation email to %s: %s", to_email, e)
    threading.Thread(target=_send, daemon=True).start()


def _send_refund_admin_email(user_email: str, user_id: int, amount_cents: int, refund_wallet: str,
                              reason: str, nowpayments_id: str, tx_hash: str):
    """Notify admin via email of a new refund request."""
    def _send():
        password = os.environ.get("GMAIL_APP_PASSWORD", "")
        admin_email = os.environ.get("REFUND_ADMIN_EMAIL", _EMAIL_SENDER)
        if not password or not admin_email:
            return
        amt = f"${amount_cents / 100:.2f}" if amount_cents else "unknown"
        subject = f"[RatSignal] Refund request: {user_email} ({amt})"
        reason_line_html = f"<p><strong>Reason:</strong><br>{reason}</p>" if reason else ""
        reason_line_text = f"Reason:\n{reason}\n\n" if reason else ""
        nowp_line_html = f"<li>NOWPayments ID: <code>{nowpayments_id}</code></li>" if nowpayments_id else ""
        tx_line_html = f"<li>TX hash: <code>{tx_hash}</code></li>" if tx_hash else ""
        nowp_line_text = f"NOWPayments ID: {nowpayments_id}\n" if nowpayments_id else ""
        tx_line_text = f"TX hash: {tx_hash}\n" if tx_hash else ""
        html = f"""<!DOCTYPE html>
<html><body style="font-family:Arial,sans-serif;padding:24px;">
<h2 style="margin:0 0 16px;color:#c0392b;">Refund request - action needed within 24h</h2>
<p>A user has requested a refund within the 7-day window.</p>
<ul style="line-height:1.7;">
    <li><strong>User:</strong> {user_email} (id={user_id})</li>
    <li><strong>Amount:</strong> {amt}</li>
    <li><strong>Refund to wallet:</strong> <code>{refund_wallet}</code></li>
    {nowp_line_html}
    {tx_line_html}
</ul>
{reason_line_html}
<p>Process the refund via the NOWPayments dashboard or by sending USDT Polygon directly to the wallet above. The user's subscription_status is now <code>cancel_pending</code>.</p>
</body></html>"""
        text = f"""RatSignal refund request - action needed within 24h

User:  {user_email} (id={user_id})
Amount: {amt}
Refund to wallet: {refund_wallet}
{nowp_line_text}{tx_line_text}
{reason_line_text}Process the refund via NOWPayments dashboard or send USDT Polygon directly.
User's subscription_status is now cancel_pending.
"""
        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = f"RatSignal <{_EMAIL_SENDER}>"
            msg["To"] = admin_email
            msg.attach(MIMEText(text, "plain"))
            msg.attach(MIMEText(html, "html"))
            with smtplib.SMTP("smtp.gmail.com", 587, timeout=15) as server:
                server.starttls()
                server.login(_EMAIL_SENDER, password)
                server.sendmail(_EMAIL_SENDER, admin_email, msg.as_string())
            log.info("Refund admin email sent to %s", admin_email)
        except Exception as e:
            log.error("Failed to send refund admin email to %s: %s", admin_email, e)
    threading.Thread(target=_send, daemon=True).start()


def _send_refund_request_telegram(user_email, user_id, amount_cents, refund_wallet, reason,
                                   nowpayments_id, tx_hash):
    """Notify admin of a refund request via Telegram."""
    def _send():
        try:
            import urllib.request, urllib.parse, json as _j
            bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
            chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
            if not bot_token or not chat_id:
                print("[RatSignal] Telegram not configured, skipping refund notification", flush=True)
                return
            amt = f"${amount_cents / 100:.2f}" if amount_cents else "unknown"
            reason_line = f"📝 <b>Reason:</b> {reason}\n" if reason else ""
            nowp_line = f"🧾 <b>NOWPayments ID:</b> <code>{nowpayments_id}</code>\n" if nowpayments_id else ""
            tx_line = f"🔗 <b>TX:</b> <code>{tx_hash}</code>\n" if tx_hash else ""
            text = (
                "💸 <b>Refund Request — RatSignal</b>\n\n"
                f"👤 <b>User:</b> {user_email} (id={user_id})\n"
                f"💰 <b>Amount:</b> {amt}\n"
                f"🎯 <b>Refund to:</b> <code>{refund_wallet}</code>\n"
                f"{nowp_line}{tx_line}{reason_line}"
                "\nProcess refund manually via NOWPayments dashboard within 24h."
            )
            url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
            payload = urllib.parse.urlencode({
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": "true",
            }).encode("utf-8")
            req = urllib.request.Request(url, data=payload, method="POST")
            urllib.request.urlopen(req, timeout=5).read()
        except Exception as e:
            print(f"[RatSignal] Refund TG notification failed: {e}", flush=True)
    threading.Thread(target=_send, daemon=True).start()


@payments_bp.route("/cancel-refund", methods=["POST"])
@login_required
def cancel_refund():
    user = models.get_user_by_id(current_user.id) or {}
    status = (user.get("subscription_status") or "").lower()
    if status not in ("active", "trial"):
        flash("No active subscription to cancel.", "error")
        return redirect("/auth/profile")

    eligible, deadline, _hours = refund_window_for_user(current_user.id)
    if not eligible:
        flash("Refund window has expired (7 days after payment).", "error")
        return redirect("/auth/profile")

    refund_wallet = request.form.get("refund_wallet", "").strip()
    reason = request.form.get("reason", "").strip()

    if not refund_wallet or len(refund_wallet) < 10:
        flash("Please provide a valid wallet address for the refund.", "error")
        return redirect("/auth/profile")

    paid = _latest_completed_payment(current_user.id) or {}

    try:
        models.update_user_profile(current_user.id, {"subscription_status": "cancel_pending"})
    except Exception as e:
        print(f"[RatSignal] Refund status update error: {e}", flush=True)

    _send_refund_request_telegram(
        user_email=user.get("email", ""),
        user_id=current_user.id,
        amount_cents=paid.get("amount_cents", 0),
        refund_wallet=refund_wallet,
        reason=reason,
        nowpayments_id=paid.get("nowpayments_id", ""),
        tx_hash=paid.get("crypto_tx_hash", ""),
    )

    _send_refund_user_email(
        to_email=user.get("email", ""),
        first_name=user.get("first_name", ""),
        refund_wallet=refund_wallet,
        amount_cents=paid.get("amount_cents", 0),
    )

    _send_refund_admin_email(
        user_email=user.get("email", ""),
        user_id=current_user.id,
        amount_cents=paid.get("amount_cents", 0),
        refund_wallet=refund_wallet,
        reason=reason,
        nowpayments_id=paid.get("nowpayments_id", ""),
        tx_hash=paid.get("crypto_tx_hash", ""),
    )

    flash("Refund request received. We'll send the funds back to your wallet within 24 hours. A confirmation email is on its way.", "success")
    return redirect("/auth/profile")
