"""Payment intent and subscription endpoints."""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr
import stripe

from config import Settings, get_settings
from database import get_cursor

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/payments", tags=["payments"])


class CreatePaymentIntentRequest(BaseModel):
    """Request to create a payment intent."""

    amount_cents: int
    customer_email: EmailStr
    currency: str = "usd"
    metadata: Optional[dict] = None


class CreatePaymentIntentResponse(BaseModel):
    """Response from creating payment intent."""

    client_secret: str
    payment_intent_id: str


@router.post("/intents", response_model=CreatePaymentIntentResponse)
async def create_payment_intent(
    request: CreatePaymentIntentRequest,
    settings: Settings = Depends(get_settings),
):
    """Create a payment intent for one-time payment.

    Args:
        request: Payment intent creation request

    Returns:
        Client secret and payment intent ID
    """
    try:
        stripe.api_key = settings.stripe_secret_key

        # Create payment intent on connected account
        intent = stripe.PaymentIntent.create(
            amount=request.amount_cents,
            currency=request.currency,
            stripe_account=settings.stripe_account_id,
            automatic_payment_methods={"enabled": True},
            receipt_email=request.customer_email,
            metadata=request.metadata or {},
        )

        # Record transaction in database
        with get_cursor() as cur:
            cur.execute(
                """
                INSERT INTO payment_transactions (
                    project_id, stripe_payment_intent_id, service_type,
                    customer_email, amount_cents, currency, status, metadata
                )
                VALUES (
                    (SELECT id FROM projects WHERE name = %s LIMIT 1),
                    %s, %s, %s, %s, %s, %s, %s
                )
                """,
                (
                    settings.project_name,
                    intent.id,
                    request.metadata.get("service_type", "one_time") if request.metadata else "one_time",
                    request.customer_email,
                    request.amount_cents,
                    request.currency,
                    "pending",
                    request.metadata,
                ),
            )

        return CreatePaymentIntentResponse(
            client_secret=intent.client_secret,
            payment_intent_id=intent.id,
        )

    except stripe.error.StripeError as e:
        logger.error(f"Stripe error creating payment intent: {e}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error creating payment intent: {e}")
        raise HTTPException(status_code=500, detail="Failed to create payment intent")
