"""Business logic for the hosted auto-trading product. Imported by app.py and auth.py."""
import asyncio
import sys
from datetime import datetime
from pathlib import Path

# Make the trader package importable from the Flask app
TRADER_PATH = "/opt/ratsignal-trader"
if TRADER_PATH not in sys.path:
    sys.path.insert(0, TRADER_PATH)

from trader import vault, key_validator
from temporary.ratsignal import models


def is_subscription_active_for_hosted(user) -> bool:
    """The hosted product requires an active or trial sub."""
    if not user:
        return False
    status = (user.get("subscription_status") or "free").lower()
    return status in ("active", "trial")


def save_bot_config(*, user_id, bot, exchange, sizing_mode,
                    position_size_usd, position_size_pct,
                    leverage, copy_leverage, max_leverage,
                    max_total_positions, max_loss_pct, max_hold_hours,
                    api_key, api_secret=None,
                    api_key_index=None, account_index=None):
    """Validate API key, encrypt, persist. Returns (ok: bool, error: str | None)."""
    loop = asyncio.new_event_loop()
    try:
        if exchange == "binance":
            result = loop.run_until_complete(
                key_validator.validate_binance(api_key=api_key, api_secret=api_secret),
            )
        elif exchange == "lighter":
            result = loop.run_until_complete(
                key_validator.validate_lighter(
                    api_key=api_key, api_key_index=int(api_key_index),
                    account_index=str(account_index),
                ),
            )
        else:
            return False, f"Unknown exchange: {exchange}"
    finally:
        loop.close()

    if not result.valid:
        return False, result.error

    fields = dict(
        exchange=exchange,
        api_key_encrypted=vault.encrypt_str(api_key),
        api_secret_encrypted=vault.encrypt_str(api_secret) if api_secret else None,
        api_key_index=int(api_key_index) if api_key_index else None,
        account_index=str(account_index) if account_index else None,
        sizing_mode=sizing_mode,
        position_size_usd=float(position_size_usd) if position_size_usd not in (None, "") else None,
        position_size_pct=float(position_size_pct) if position_size_pct not in (None, "") else None,
        leverage=int(leverage),
        copy_leverage=1 if copy_leverage else 0,
        max_leverage=int(max_leverage),
        max_total_positions=int(max_total_positions),
        max_loss_pct=float(max_loss_pct),
        max_hold_hours=int(max_hold_hours),
        key_validated_at=datetime.now().isoformat(timespec="seconds"),
        key_validation_error=None,
    )
    models.upsert_hosted_subscription(user_id)  # ensure parent row exists
    models.upsert_hosted_bot_config(user_id, bot, **fields)
    return True, None


def set_paused(user_id, bot, paused: bool):
    models.upsert_hosted_bot_config(user_id, bot, paused=1 if paused else 0)


def set_enabled(user_id, bot, enabled: bool):
    models.upsert_hosted_bot_config(user_id, bot, enabled=1 if enabled else 0)


def accept_tos(user_id, ip: str):
    models.upsert_hosted_subscription(
        user_id,
        tos_accepted_at=datetime.now().isoformat(timespec="seconds"),
        tos_accepted_ip=ip,
    )
