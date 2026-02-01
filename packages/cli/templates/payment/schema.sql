-- HostKit Payment Service Database Schema
-- Complete schema for payment transactions, gift certificates, promotions, and packages

-- Stripe Connect accounts per project
CREATE TABLE IF NOT EXISTS payment_accounts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID NOT NULL,
    stripe_account_id TEXT UNIQUE NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    currency TEXT NOT NULL DEFAULT 'usd',
    tax_enabled BOOLEAN NOT NULL DEFAULT false,
    tipping_enabled BOOLEAN NOT NULL DEFAULT false,
    tipping_percentages JSONB DEFAULT '[15, 20, 25]'::jsonb,
    onboarding_url TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Payment configuration per project
CREATE TABLE IF NOT EXISTS payment_configs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID NOT NULL,
    payment_timing TEXT NOT NULL DEFAULT 'full_upfront',
    deposit_percent INTEGER DEFAULT 50,
    remainder_timing TEXT DEFAULT '24h_before',
    cancellation_policy_id UUID,
    no_show_grace_minutes INTEGER NOT NULL DEFAULT 15,
    max_payment_retries INTEGER NOT NULL DEFAULT 3,
    retry_interval_hours INTEGER NOT NULL DEFAULT 8,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Cancellation policy templates + custom
CREATE TABLE IF NOT EXISTS cancellation_policies (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID,
    name TEXT NOT NULL,
    is_template BOOLEAN NOT NULL DEFAULT false,
    rules JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- All payment transactions
CREATE TABLE IF NOT EXISTS payment_transactions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID NOT NULL,
    stripe_payment_intent_id TEXT UNIQUE,
    stripe_charge_id TEXT,
    service_type TEXT NOT NULL,
    reference_id UUID,
    customer_id UUID,
    customer_email TEXT NOT NULL,
    amount_cents INTEGER NOT NULL,
    tip_cents INTEGER DEFAULT 0,
    currency TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    failure_reason TEXT,
    metadata JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Payment splits for deposit + remainder
CREATE TABLE IF NOT EXISTS payment_splits (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    transaction_id UUID NOT NULL,
    split_type TEXT NOT NULL,
    amount_cents INTEGER NOT NULL,
    scheduled_for TIMESTAMPTZ,
    stripe_payment_intent_id TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER DEFAULT 0,
    last_attempt_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Refunds
CREATE TABLE IF NOT EXISTS refunds (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    transaction_id UUID NOT NULL,
    stripe_refund_id TEXT UNIQUE,
    amount_cents INTEGER NOT NULL,
    reason TEXT NOT NULL,
    policy_applied TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Tips (separate from main transaction for reporting)
CREATE TABLE IF NOT EXISTS tips (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    transaction_id UUID NOT NULL,
    amount_cents INTEGER NOT NULL,
    percentage INTEGER,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Gift certificates
CREATE TABLE IF NOT EXISTS gift_certificates (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID NOT NULL,
    code TEXT UNIQUE NOT NULL,
    initial_amount_cents INTEGER NOT NULL,
    remaining_cents INTEGER NOT NULL,
    currency TEXT NOT NULL,
    purchaser_email TEXT NOT NULL,
    purchaser_name TEXT,
    purchase_transaction_id UUID,
    recipient_email TEXT NOT NULL,
    recipient_name TEXT,
    personal_message TEXT,
    delivery_method TEXT NOT NULL DEFAULT 'email',
    scheduled_delivery_at TIMESTAMPTZ,
    delivered_at TIMESTAMPTZ,
    status TEXT NOT NULL DEFAULT 'pending',
    linked_customer_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Gift certificate redemptions
CREATE TABLE IF NOT EXISTS gift_certificate_redemptions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    certificate_id UUID NOT NULL,
    transaction_id UUID,
    amount_cents INTEGER NOT NULL,
    remaining_after_cents INTEGER NOT NULL,
    redeemed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Gift certificate balance reminders
CREATE TABLE IF NOT EXISTS gift_certificate_reminders (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    certificate_id UUID NOT NULL,
    reminder_type TEXT NOT NULL,
    sent_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Gift certificate denomination presets per project
CREATE TABLE IF NOT EXISTS gift_certificate_configs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID NOT NULL,
    preset_amounts_cents JSONB NOT NULL DEFAULT '[2500, 5000, 10000, 20000]'::jsonb,
    custom_min_cents INTEGER NOT NULL DEFAULT 2500,
    custom_max_cents INTEGER NOT NULL DEFAULT 50000,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Promotions/Coupons
CREATE TABLE IF NOT EXISTS promotions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID NOT NULL,
    code TEXT NOT NULL,
    name TEXT NOT NULL,
    discount_type TEXT NOT NULL,
    discount_value INTEGER NOT NULL,
    applies_to TEXT DEFAULT 'all',
    service_ids JSONB,
    max_uses INTEGER,
    max_uses_per_customer INTEGER DEFAULT 1,
    current_uses INTEGER NOT NULL DEFAULT 0,
    starts_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ,
    is_referral BOOLEAN NOT NULL DEFAULT false,
    referrer_customer_id UUID,
    referrer_reward_cents INTEGER,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(project_id, code)
);

-- Promotion usage tracking
CREATE TABLE IF NOT EXISTS promotion_uses (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    promotion_id UUID NOT NULL,
    customer_id UUID,
    transaction_id UUID,
    discount_applied_cents INTEGER NOT NULL,
    used_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Multi-session packages
CREATE TABLE IF NOT EXISTS packages (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID NOT NULL,
    name TEXT NOT NULL,
    description TEXT,
    service_ids JSONB NOT NULL,
    total_sessions INTEGER NOT NULL,
    price_cents INTEGER NOT NULL,
    expires_days INTEGER,
    transferable BOOLEAN NOT NULL DEFAULT false,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Customer-owned packages
CREATE TABLE IF NOT EXISTS customer_packages (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    package_id UUID NOT NULL,
    customer_id UUID NOT NULL,
    purchase_transaction_id UUID,
    purchased_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    is_gift BOOLEAN NOT NULL DEFAULT false,
    gifted_by_customer_id UUID,
    gift_message TEXT,
    sessions_remaining INTEGER NOT NULL,
    expires_at TIMESTAMPTZ,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Package session usage
CREATE TABLE IF NOT EXISTS package_redemptions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    customer_package_id UUID NOT NULL,
    appointment_id UUID NOT NULL,
    sessions_used INTEGER NOT NULL DEFAULT 1,
    redeemed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Event bus infrastructure (PostgreSQL LISTEN/NOTIFY)
CREATE TABLE IF NOT EXISTS payment_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_type TEXT NOT NULL,
    project_id UUID NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    processed_at TIMESTAMPTZ
);

-- Indexes for performance
CREATE INDEX IF NOT EXISTS idx_payment_accounts_project_id ON payment_accounts(project_id);
CREATE INDEX IF NOT EXISTS idx_payment_accounts_stripe_account_id ON payment_accounts(stripe_account_id);

CREATE INDEX IF NOT EXISTS idx_payment_configs_project_id ON payment_configs(project_id);

CREATE INDEX IF NOT EXISTS idx_cancellation_policies_project_id ON cancellation_policies(project_id);
CREATE INDEX IF NOT EXISTS idx_cancellation_policies_template ON cancellation_policies(is_template) WHERE is_template = true;

CREATE INDEX IF NOT EXISTS idx_payment_transactions_project_id ON payment_transactions(project_id);
CREATE INDEX IF NOT EXISTS idx_payment_transactions_customer_id ON payment_transactions(customer_id);
CREATE INDEX IF NOT EXISTS idx_payment_transactions_stripe_payment_intent ON payment_transactions(stripe_payment_intent_id);
CREATE INDEX IF NOT EXISTS idx_payment_transactions_reference ON payment_transactions(service_type, reference_id);
CREATE INDEX IF NOT EXISTS idx_payment_transactions_status ON payment_transactions(status);
CREATE INDEX IF NOT EXISTS idx_payment_transactions_created_at ON payment_transactions(created_at DESC);

CREATE INDEX IF NOT EXISTS idx_payment_splits_transaction_id ON payment_splits(transaction_id);
CREATE INDEX IF NOT EXISTS idx_payment_splits_scheduled ON payment_splits(scheduled_for) WHERE status = 'scheduled';

CREATE INDEX IF NOT EXISTS idx_refunds_transaction_id ON refunds(transaction_id);
CREATE INDEX IF NOT EXISTS idx_refunds_stripe_refund_id ON refunds(stripe_refund_id);

CREATE INDEX IF NOT EXISTS idx_tips_transaction_id ON tips(transaction_id);

CREATE INDEX IF NOT EXISTS idx_gift_certificates_project_id ON gift_certificates(project_id);
CREATE INDEX IF NOT EXISTS idx_gift_certificates_code ON gift_certificates(code);
CREATE INDEX IF NOT EXISTS idx_gift_certificates_status ON gift_certificates(status);
CREATE INDEX IF NOT EXISTS idx_gift_certificates_purchaser_email ON gift_certificates(purchaser_email);
CREATE INDEX IF NOT EXISTS idx_gift_certificates_recipient_email ON gift_certificates(recipient_email);
CREATE INDEX IF NOT EXISTS idx_gift_certificates_scheduled ON gift_certificates(scheduled_delivery_at) WHERE delivered_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_gift_certificates_updated_at ON gift_certificates(updated_at) WHERE status = 'active';

CREATE INDEX IF NOT EXISTS idx_gift_certificate_redemptions_certificate_id ON gift_certificate_redemptions(certificate_id);
CREATE INDEX IF NOT EXISTS idx_gift_certificate_redemptions_transaction_id ON gift_certificate_redemptions(transaction_id);

CREATE INDEX IF NOT EXISTS idx_gift_certificate_reminders_certificate_id ON gift_certificate_reminders(certificate_id);

CREATE INDEX IF NOT EXISTS idx_gift_certificate_configs_project_id ON gift_certificate_configs(project_id);

CREATE INDEX IF NOT EXISTS idx_promotions_project_id ON promotions(project_id);
CREATE INDEX IF NOT EXISTS idx_promotions_code ON promotions(project_id, code);
CREATE INDEX IF NOT EXISTS idx_promotions_status ON promotions(status);
CREATE INDEX IF NOT EXISTS idx_promotions_expires ON promotions(expires_at) WHERE status = 'active';

CREATE INDEX IF NOT EXISTS idx_promotion_uses_promotion_id ON promotion_uses(promotion_id);
CREATE INDEX IF NOT EXISTS idx_promotion_uses_customer_id ON promotion_uses(customer_id);
CREATE INDEX IF NOT EXISTS idx_promotion_uses_transaction_id ON promotion_uses(transaction_id);

CREATE INDEX IF NOT EXISTS idx_packages_project_id ON packages(project_id);
CREATE INDEX IF NOT EXISTS idx_packages_status ON packages(status);

CREATE INDEX IF NOT EXISTS idx_customer_packages_package_id ON customer_packages(package_id);
CREATE INDEX IF NOT EXISTS idx_customer_packages_customer_id ON customer_packages(customer_id);
CREATE INDEX IF NOT EXISTS idx_customer_packages_status ON customer_packages(status);
CREATE INDEX IF NOT EXISTS idx_customer_packages_expires ON customer_packages(expires_at) WHERE status = 'active';

CREATE INDEX IF NOT EXISTS idx_package_redemptions_customer_package_id ON package_redemptions(customer_package_id);
CREATE INDEX IF NOT EXISTS idx_package_redemptions_appointment_id ON package_redemptions(appointment_id);

CREATE INDEX IF NOT EXISTS idx_payment_events_event_type ON payment_events(event_type);
CREATE INDEX IF NOT EXISTS idx_payment_events_project_id ON payment_events(project_id);
CREATE INDEX IF NOT EXISTS idx_payment_events_processed ON payment_events(processed_at) WHERE processed_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_payment_events_created_at ON payment_events(created_at DESC);

-- Seed cancellation policy templates
INSERT INTO cancellation_policies (name, is_template, rules) VALUES
    ('Flexible', true, '[{"window_hours": 24, "refund_percent": 100}, {"window_hours": 0, "refund_percent": 50}]'::jsonb),
    ('Moderate', true, '[{"window_hours": 48, "refund_percent": 100}, {"window_hours": 24, "refund_percent": 50}, {"window_hours": 0, "refund_percent": 0}]'::jsonb),
    ('Strict', true, '[{"window_hours": 168, "refund_percent": 50}, {"window_hours": 0, "refund_percent": 0}]'::jsonb),
    ('Non-refundable', true, '[{"window_hours": 0, "refund_percent": 0}]'::jsonb)
ON CONFLICT DO NOTHING;

-- Foreign key constraints (added after indexes for better performance)
ALTER TABLE payment_configs ADD CONSTRAINT fk_payment_configs_cancellation_policy
    FOREIGN KEY (cancellation_policy_id) REFERENCES cancellation_policies(id);

ALTER TABLE payment_splits ADD CONSTRAINT fk_payment_splits_transaction
    FOREIGN KEY (transaction_id) REFERENCES payment_transactions(id) ON DELETE CASCADE;

ALTER TABLE refunds ADD CONSTRAINT fk_refunds_transaction
    FOREIGN KEY (transaction_id) REFERENCES payment_transactions(id) ON DELETE CASCADE;

ALTER TABLE tips ADD CONSTRAINT fk_tips_transaction
    FOREIGN KEY (transaction_id) REFERENCES payment_transactions(id) ON DELETE CASCADE;

ALTER TABLE gift_certificate_redemptions ADD CONSTRAINT fk_gift_certificate_redemptions_certificate
    FOREIGN KEY (certificate_id) REFERENCES gift_certificates(id) ON DELETE CASCADE;

ALTER TABLE gift_certificate_redemptions ADD CONSTRAINT fk_gift_certificate_redemptions_transaction
    FOREIGN KEY (transaction_id) REFERENCES payment_transactions(id);

ALTER TABLE gift_certificate_reminders ADD CONSTRAINT fk_gift_certificate_reminders_certificate
    FOREIGN KEY (certificate_id) REFERENCES gift_certificates(id) ON DELETE CASCADE;

ALTER TABLE promotion_uses ADD CONSTRAINT fk_promotion_uses_promotion
    FOREIGN KEY (promotion_id) REFERENCES promotions(id) ON DELETE CASCADE;

ALTER TABLE promotion_uses ADD CONSTRAINT fk_promotion_uses_transaction
    FOREIGN KEY (transaction_id) REFERENCES payment_transactions(id);

ALTER TABLE customer_packages ADD CONSTRAINT fk_customer_packages_package
    FOREIGN KEY (package_id) REFERENCES packages(id);

ALTER TABLE customer_packages ADD CONSTRAINT fk_customer_packages_transaction
    FOREIGN KEY (purchase_transaction_id) REFERENCES payment_transactions(id);

ALTER TABLE package_redemptions ADD CONSTRAINT fk_package_redemptions_customer_package
    FOREIGN KEY (customer_package_id) REFERENCES customer_packages(id) ON DELETE CASCADE;
