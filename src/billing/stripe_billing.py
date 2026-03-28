"""
Stripe billing integration for ForgeOS SaaS.

Manages subscriptions, usage-based billing, and webhook handling.
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

try:
    import stripe
    HAS_STRIPE = True
except ImportError:
    HAS_STRIPE = False


class StripeBilling:
    """
    Stripe subscription and billing manager.

    Handles customer creation, subscription management,
    usage reporting, and webhook processing.
    """

    # Stripe Price IDs (configured in Stripe Dashboard)
    PRICE_IDS = {
        "starter": os.environ.get("STRIPE_STARTER_PRICE_ID", ""),
        "growth": os.environ.get("STRIPE_GROWTH_PRICE_ID", ""),
        "enterprise": os.environ.get("STRIPE_ENTERPRISE_PRICE_ID", ""),
    }

    # Metered price for token overage
    OVERAGE_PRICE_ID = os.environ.get("STRIPE_OVERAGE_PRICE_ID", "")

    def __init__(self, api_key: str | None = None):
        self._enabled = False

        if not HAS_STRIPE:
            logger.info("Stripe SDK not installed — billing unavailable")
            return

        key = api_key or os.environ.get("STRIPE_API_KEY", "")
        if key:
            stripe.api_key = key
            self._enabled = True
            logger.info("Stripe billing initialized")

    @property
    def is_enabled(self) -> bool:
        return self._enabled

    def create_customer(self, tenant_id: str, name: str, email: str) -> str | None:
        """Create a Stripe customer for a new tenant."""
        if not self._enabled:
            return None

        customer = stripe.Customer.create(
            name=name,
            email=email,
            metadata={"tenant_id": tenant_id, "platform": "forgeos"},
        )
        logger.info("Stripe customer created: %s for tenant %s", customer.id, tenant_id)
        return customer.id

    def create_subscription(
        self,
        customer_id: str,
        plan: str,
        tenant_id: str,
    ) -> dict | None:
        """Create a subscription for a customer."""
        if not self._enabled:
            return None

        price_id = self.PRICE_IDS.get(plan)
        if not price_id:
            logger.error("No Stripe price ID for plan: %s", plan)
            return None

        items = [{"price": price_id}]

        # Add metered overage item if configured
        if self.OVERAGE_PRICE_ID:
            items.append({"price": self.OVERAGE_PRICE_ID})

        subscription = stripe.Subscription.create(
            customer=customer_id,
            items=items,
            metadata={"tenant_id": tenant_id, "plan": plan},
        )

        logger.info("Subscription created: %s (plan: %s)", subscription.id, plan)
        return {
            "subscription_id": subscription.id,
            "status": subscription.status,
            "current_period_end": subscription.current_period_end,
        }

    def report_usage(
        self,
        subscription_item_id: str,
        quantity: int,
        timestamp: int | None = None,
    ) -> bool:
        """Report metered usage to Stripe."""
        if not self._enabled:
            return False

        try:
            stripe.SubscriptionItem.create_usage_record(
                subscription_item_id,
                quantity=quantity,
                timestamp=timestamp,
                action="increment",
            )
            return True
        except Exception as e:
            logger.error("Failed to report usage: %s", e)
            return False

    def create_portal_session(self, customer_id: str, return_url: str) -> str | None:
        """Create a Stripe Customer Portal session for self-service billing."""
        if not self._enabled:
            return None

        session = stripe.billing_portal.Session.create(
            customer=customer_id,
            return_url=return_url,
        )
        return session.url

    def handle_webhook(self, payload: bytes, sig_header: str) -> dict:
        """Process a Stripe webhook event."""
        if not self._enabled:
            return {"status": "billing_disabled"}

        webhook_secret = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
        if not webhook_secret:
            return {"status": "no_webhook_secret"}

        try:
            event = stripe.Webhook.construct_event(payload, sig_header, webhook_secret)
        except Exception as e:
            logger.error("Webhook verification failed: %s", e)
            return {"status": "invalid_signature"}

        event_type = event["type"]

        if event_type == "customer.subscription.updated":
            subscription = event["data"]["object"]
            tenant_id = subscription.get("metadata", {}).get("tenant_id")
            logger.info(
                "Subscription updated: tenant=%s status=%s",
                tenant_id, subscription["status"],
            )
            return {"status": "processed", "event": event_type, "tenant_id": tenant_id}

        elif event_type == "customer.subscription.deleted":
            subscription = event["data"]["object"]
            tenant_id = subscription.get("metadata", {}).get("tenant_id")
            logger.warning("Subscription cancelled: tenant=%s", tenant_id)
            return {"status": "processed", "event": event_type, "tenant_id": tenant_id}

        elif event_type == "invoice.payment_failed":
            invoice = event["data"]["object"]
            customer_id = invoice["customer"]
            logger.warning("Payment failed: customer=%s", customer_id)
            return {"status": "processed", "event": event_type}

        return {"status": "ignored", "event": event_type}
