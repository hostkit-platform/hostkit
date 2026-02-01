"""Stripe webhook handler."""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
import stripe

from config import Settings, get_settings
from dependencies import verify_stripe_signature
from database import get_cursor

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/payments", tags=["webhooks"])


@router.post("/webhooks/stripe")
async def handle_stripe_webhook(
    request: Request,
    signature: str = Depends(verify_stripe_signature),
    settings: Settings = Depends(get_settings),
):
    """Handle Stripe webhook events.

    Args:
        request: FastAPI request object
        signature: Verified Stripe signature
        settings: Application settings

    Returns:
        Success acknowledgment
    """
    payload = await request.body()

    try:
        stripe.api_key = settings.stripe_secret_key

        event = stripe.Webhook.construct_event(
            payload, signature, settings.stripe_webhook_secret
        )

    except ValueError:
        logger.error("Invalid webhook payload")
        raise HTTPException(status_code=400, detail="Invalid payload")
    except stripe.error.SignatureVerificationError:
        logger.error("Invalid webhook signature")
        raise HTTPException(status_code=400, detail="Invalid signature")

    # Route event to handler
    event_type = event["type"]
    logger.info(f"Received webhook event: {event_type}")

    try:
        if event_type == "payment_intent.succeeded":
            await handle_payment_succeeded(event["data"]["object"])
        elif event_type == "payment_intent.payment_failed":
            await handle_payment_failed(event["data"]["object"])
        elif event_type == "charge.refunded":
            await handle_charge_refunded(event["data"]["object"])
        elif event_type == "account.updated":
            await handle_account_updated(event["data"]["object"])
        else:
            logger.info(f"Unhandled event type: {event_type}")

    except Exception as e:
        logger.error(f"Error handling webhook event {event_type}: {e}")
        # Return 200 to avoid retries for unrecoverable errors
        # Stripe will retry 5xx responses

    return {"received": True}


async def handle_payment_succeeded(payment_intent: dict):
    """Handle successful payment intent."""
    logger.info(f"Payment succeeded: {payment_intent['id']}")

    with get_cursor() as cur:
        cur.execute(
            """
            UPDATE payment_transactions
            SET status = 'succeeded',
                stripe_charge_id = %s,
                updated_at = NOW()
            WHERE stripe_payment_intent_id = %s
            """,
            (payment_intent.get("latest_charge"), payment_intent["id"]),
        )


async def handle_payment_failed(payment_intent: dict):
    """Handle failed payment intent."""
    logger.info(f"Payment failed: {payment_intent['id']}")

    failure_message = payment_intent.get("last_payment_error", {}).get("message", "Unknown error")

    with get_cursor() as cur:
        cur.execute(
            """
            UPDATE payment_transactions
            SET status = 'failed',
                failure_reason = %s,
                updated_at = NOW()
            WHERE stripe_payment_intent_id = %s
            """,
            (failure_message, payment_intent["id"]),
        )


async def handle_charge_refunded(charge: dict):
    """Handle charge refund."""
    logger.info(f"Charge refunded: {charge['id']}")

    # Record refund in database
    with get_cursor() as cur:
        # Find transaction by charge ID
        cur.execute(
            """
            SELECT id FROM payment_transactions
            WHERE stripe_charge_id = %s
            """,
            (charge["id"],),
        )
        result = cur.fetchone()

        if result:
            transaction_id = result["id"]

            # Check if refund already recorded
            cur.execute(
                """
                SELECT id FROM refunds WHERE stripe_refund_id = %s
                """,
                (charge["refunds"]["data"][0]["id"],),
            )

            if not cur.fetchone():
                # Insert refund record
                cur.execute(
                    """
                    INSERT INTO refunds (
                        transaction_id, stripe_refund_id,
                        amount_cents, reason, status
                    )
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (
                        transaction_id,
                        charge["refunds"]["data"][0]["id"],
                        charge["amount_refunded"],
                        charge["refunds"]["data"][0].get("reason", "requested_by_customer"),
                        "succeeded",
                    ),
                )


async def handle_account_updated(account: dict):
    """Handle account update (onboarding completion, etc)."""
    logger.info(f"Account updated: {account['id']}")
    logger.info(f"Charges enabled: {account.get('charges_enabled')}")
    logger.info(f"Payouts enabled: {account.get('payouts_enabled')}")

    # Update account status in database if needed
    with get_cursor() as cur:
        cur.execute(
            """
            UPDATE payment_accounts
            SET status = CASE
                    WHEN %s AND %s THEN 'active'
                    ELSE 'pending'
                END,
                updated_at = NOW()
            WHERE stripe_account_id = %s
            """,
            (account.get("charges_enabled"), account.get("payouts_enabled"), account["id"]),
        )
