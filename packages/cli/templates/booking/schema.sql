-- HostKit Booking Service Database Schema
-- Version: 2.0.0 (with resource and class booking modes)
-- Created: 2025-12-24

-- Enable UUID extension
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ============================================================================
-- CORE TABLES (Phase 1)
-- ============================================================================

-- Booking configuration per project
CREATE TABLE IF NOT EXISTS booking_configs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    project_id VARCHAR(100) UNIQUE NOT NULL,
    business_name VARCHAR(255),
    timezone VARCHAR(50) DEFAULT 'America/New_York',

    -- Slot configuration
    slot_duration_minutes INT DEFAULT 30,
    buffer_minutes INT DEFAULT 0,
    min_notice_hours INT DEFAULT 1,
    max_advance_days INT DEFAULT 90,

    -- Booking modes (Phase 3)
    provider_mode_enabled BOOLEAN DEFAULT true,
    resource_mode_enabled BOOLEAN DEFAULT false,
    class_mode_enabled BOOLEAN DEFAULT false,

    -- Flow configuration (Phase 2 - Request #11)
    flow_type VARCHAR(50) DEFAULT 'spa',  -- spa, salon, restaurant, fitness, medical
    flow_steps JSONB DEFAULT '["provider", "date", "duration", "time", "service", "contact", "confirm"]',

    -- Features
    allow_any_provider BOOLEAN DEFAULT true,
    require_payment BOOLEAN DEFAULT false,

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Service providers (therapists, stylists, doctors, etc.)
CREATE TABLE IF NOT EXISTS providers (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    config_id UUID NOT NULL REFERENCES booking_configs(id) ON DELETE CASCADE,

    name VARCHAR(100) NOT NULL,
    email VARCHAR(255),
    phone VARCHAR(20),
    bio TEXT,
    avatar_url VARCHAR(500),

    is_active BOOLEAN DEFAULT true,
    is_visible BOOLEAN DEFAULT true,  -- Show in "Any Available" pool
    sort_order INT DEFAULT 0,

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_providers_config ON providers(config_id);
CREATE INDEX IF NOT EXISTS idx_providers_active ON providers(config_id, is_active);

-- Physical rooms/treatment spaces
CREATE TABLE IF NOT EXISTS rooms (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    config_id UUID NOT NULL REFERENCES booking_configs(id) ON DELETE CASCADE,

    name VARCHAR(100) NOT NULL,
    type VARCHAR(50),  -- treatment, consultation, office
    description TEXT,
    capacity INT DEFAULT 1,

    is_active BOOLEAN DEFAULT true,
    sort_order INT DEFAULT 0,

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_rooms_config ON rooms(config_id);

-- Services offered (massage types, haircuts, consultations, etc.)
CREATE TABLE IF NOT EXISTS services (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    config_id UUID NOT NULL REFERENCES booking_configs(id) ON DELETE CASCADE,

    name VARCHAR(100) NOT NULL,
    description TEXT,
    duration_minutes INT NOT NULL,
    price_cents INT NOT NULL,

    -- Phase 2 - Request #10: Service categories
    category VARCHAR(50),  -- consultation, treatment, procedure, follow-up
    requires_new_patient BOOLEAN DEFAULT false,
    min_notice_hours INT,  -- Override config default
    max_advance_days INT,  -- Override config default

    is_active BOOLEAN DEFAULT true,
    sort_order INT DEFAULT 0,

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_services_config ON services(config_id);
CREATE INDEX IF NOT EXISTS idx_services_category ON services(config_id, category);
CREATE INDEX IF NOT EXISTS idx_services_duration ON services(config_id, duration_minutes);

-- Link providers to services they can perform
CREATE TABLE IF NOT EXISTS provider_services (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    provider_id UUID NOT NULL REFERENCES providers(id) ON DELETE CASCADE,
    service_id UUID NOT NULL REFERENCES services(id) ON DELETE CASCADE,

    -- Optional price override per provider
    price_override_cents INT,

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    UNIQUE(provider_id, service_id)
);

CREATE INDEX IF NOT EXISTS idx_provider_services_provider ON provider_services(provider_id);
CREATE INDEX IF NOT EXISTS idx_provider_services_service ON provider_services(service_id);

-- Link rooms to services they can host
CREATE TABLE IF NOT EXISTS room_services (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    room_id UUID NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
    service_id UUID NOT NULL REFERENCES services(id) ON DELETE CASCADE,

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    UNIQUE(room_id, service_id)
);

CREATE INDEX IF NOT EXISTS idx_room_services_room ON room_services(room_id);
CREATE INDEX IF NOT EXISTS idx_room_services_service ON room_services(service_id);

-- Provider weekly schedules
CREATE TABLE IF NOT EXISTS provider_schedules (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    provider_id UUID NOT NULL REFERENCES providers(id) ON DELETE CASCADE,

    day_of_week INT NOT NULL CHECK (day_of_week >= 0 AND day_of_week <= 6),  -- 0=Monday, 6=Sunday
    start_time TIME NOT NULL,
    end_time TIME NOT NULL,
    is_active BOOLEAN DEFAULT true,

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    UNIQUE(provider_id, day_of_week)
);

CREATE INDEX IF NOT EXISTS idx_provider_schedules_provider ON provider_schedules(provider_id);
CREATE INDEX IF NOT EXISTS idx_provider_schedules_day ON provider_schedules(provider_id, day_of_week);

-- Schedule overrides (day-offs, special hours)
CREATE TABLE IF NOT EXISTS schedule_overrides (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    provider_id UUID NOT NULL REFERENCES providers(id) ON DELETE CASCADE,

    override_date DATE NOT NULL,
    is_available BOOLEAN DEFAULT false,  -- false = day off
    start_time TIME,  -- NULL if not available
    end_time TIME,    -- NULL if not available
    reason VARCHAR(255),

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    UNIQUE(provider_id, override_date)
);

CREATE INDEX IF NOT EXISTS idx_schedule_overrides_provider ON schedule_overrides(provider_id);
CREATE INDEX IF NOT EXISTS idx_schedule_overrides_date ON schedule_overrides(override_date);

-- Customers
CREATE TABLE IF NOT EXISTS customers (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    config_id UUID NOT NULL REFERENCES booking_configs(id) ON DELETE CASCADE,

    email VARCHAR(255) NOT NULL,
    name VARCHAR(255) NOT NULL,
    phone VARCHAR(20),

    -- Phase 2 - Request #10: Track new vs returning
    first_appointment_at TIMESTAMP,
    last_appointment_at TIMESTAMP,
    appointment_count INT DEFAULT 0,

    notes TEXT,

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    UNIQUE(config_id, email)
);

CREATE INDEX IF NOT EXISTS idx_customers_config ON customers(config_id);
CREATE INDEX IF NOT EXISTS idx_customers_email ON customers(config_id, email);

-- Appointments
CREATE TABLE IF NOT EXISTS appointments (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    config_id UUID NOT NULL REFERENCES booking_configs(id) ON DELETE CASCADE,

    -- Request #4: Confirmation code for lookup
    confirmation_code VARCHAR(20) NOT NULL,

    customer_id UUID NOT NULL REFERENCES customers(id),
    provider_id UUID REFERENCES providers(id),  -- NULL if resource/class booking
    service_id UUID REFERENCES services(id),    -- NULL if class booking
    room_id UUID REFERENCES rooms(id),

    -- Phase 3: Resource and class booking
    resource_id UUID,  -- References resources(id)
    class_schedule_id UUID,  -- References class_schedules(id)

    start_time TIMESTAMP NOT NULL,
    end_time TIMESTAMP NOT NULL,
    duration_minutes INT NOT NULL,

    status VARCHAR(20) DEFAULT 'confirmed',  -- confirmed, cancelled, completed, no_show

    -- Pricing
    price_cents INT,
    payment_status VARCHAR(20) DEFAULT 'pending',  -- pending, paid, refunded
    payment_id VARCHAR(100),  -- Stripe payment ID

    -- Party size for resource booking
    party_size INT DEFAULT 1,

    notes TEXT,
    cancellation_reason TEXT,
    cancelled_at TIMESTAMP,

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_appointments_config ON appointments(config_id);
CREATE INDEX IF NOT EXISTS idx_appointments_code ON appointments(confirmation_code);
CREATE INDEX IF NOT EXISTS idx_appointments_customer ON appointments(customer_id);
CREATE INDEX IF NOT EXISTS idx_appointments_provider ON appointments(provider_id);
CREATE INDEX IF NOT EXISTS idx_appointments_start ON appointments(start_time);
CREATE INDEX IF NOT EXISTS idx_appointments_status ON appointments(config_id, status);
CREATE INDEX IF NOT EXISTS idx_appointments_provider_date ON appointments(provider_id, start_time);

-- Intake form templates
CREATE TABLE IF NOT EXISTS intake_templates (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    config_id UUID NOT NULL REFERENCES booking_configs(id) ON DELETE CASCADE,

    name VARCHAR(100) NOT NULL,
    description TEXT,
    schema JSONB NOT NULL,  -- JSON Schema for form fields

    is_active BOOLEAN DEFAULT true,

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_intake_templates_config ON intake_templates(config_id);

-- Completed intake forms
CREATE TABLE IF NOT EXISTS intake_forms (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    template_id UUID NOT NULL REFERENCES intake_templates(id) ON DELETE CASCADE,
    appointment_id UUID NOT NULL REFERENCES appointments(id) ON DELETE CASCADE,
    customer_id UUID NOT NULL REFERENCES customers(id),

    data JSONB NOT NULL,  -- Form responses

    submitted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_intake_forms_appointment ON intake_forms(appointment_id);
CREATE INDEX IF NOT EXISTS idx_intake_forms_customer ON intake_forms(customer_id);

-- Link services to required intake forms
CREATE TABLE IF NOT EXISTS service_intake_forms (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    service_id UUID NOT NULL REFERENCES services(id) ON DELETE CASCADE,
    template_id UUID NOT NULL REFERENCES intake_templates(id) ON DELETE CASCADE,

    is_required BOOLEAN DEFAULT true,

    UNIQUE(service_id, template_id)
);

-- Notifications sent
CREATE TABLE IF NOT EXISTS notifications (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    appointment_id UUID NOT NULL REFERENCES appointments(id) ON DELETE CASCADE,

    type VARCHAR(50) NOT NULL,  -- confirmation, reminder_24h, reminder_1h, cancellation
    channel VARCHAR(20) NOT NULL,  -- email, sms
    sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    status VARCHAR(20) DEFAULT 'sent',  -- sent, delivered, failed
    external_id VARCHAR(100)  -- SMS/email provider ID
);

CREATE INDEX IF NOT EXISTS idx_notifications_appointment ON notifications(appointment_id);

-- ============================================================================
-- PHASE 3: RESOURCE BOOKING (Request #8)
-- Tables, bays, rooms with capacity
-- ============================================================================

-- Bookable resources (tables, bays, rooms)
CREATE TABLE IF NOT EXISTS resources (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    config_id UUID NOT NULL REFERENCES booking_configs(id) ON DELETE CASCADE,

    name VARCHAR(100) NOT NULL,  -- "Table 5", "Bay 2", "Room A"
    resource_type VARCHAR(50) NOT NULL,  -- table, bay, room, desk
    description TEXT,

    capacity INT NOT NULL DEFAULT 1,  -- For party size matching
    attributes JSONB DEFAULT '{}',  -- {"outdoor": true, "accessible": true, "window": true}

    is_active BOOLEAN DEFAULT true,
    sort_order INT DEFAULT 0,

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_resources_config ON resources(config_id);
CREATE INDEX IF NOT EXISTS idx_resources_type ON resources(config_id, resource_type);
CREATE INDEX IF NOT EXISTS idx_resources_capacity ON resources(config_id, capacity);

-- Resource weekly schedules
CREATE TABLE IF NOT EXISTS resource_schedules (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    resource_id UUID NOT NULL REFERENCES resources(id) ON DELETE CASCADE,

    day_of_week INT NOT NULL CHECK (day_of_week >= 0 AND day_of_week <= 6),
    start_time TIME NOT NULL,
    end_time TIME NOT NULL,
    is_active BOOLEAN DEFAULT true,

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    UNIQUE(resource_id, day_of_week)
);

CREATE INDEX IF NOT EXISTS idx_resource_schedules_resource ON resource_schedules(resource_id);

-- Add foreign key for appointments.resource_id
ALTER TABLE appointments
    ADD CONSTRAINT fk_appointments_resource
    FOREIGN KEY (resource_id) REFERENCES resources(id) ON DELETE SET NULL;

-- ============================================================================
-- PHASE 3: CLASS BOOKING (Request #9)
-- Fitness classes, workshops, tours with capacity
-- ============================================================================

-- Class/event types
CREATE TABLE IF NOT EXISTS classes (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    config_id UUID NOT NULL REFERENCES booking_configs(id) ON DELETE CASCADE,

    name VARCHAR(100) NOT NULL,  -- "Morning Yoga", "Spin Class"
    description TEXT,

    instructor_id UUID REFERENCES providers(id),  -- Optional instructor

    duration_minutes INT NOT NULL,
    capacity INT NOT NULL,  -- Max attendees
    price_cents INT NOT NULL,

    category VARCHAR(50),  -- yoga, spin, workshop, tour

    is_active BOOLEAN DEFAULT true,
    sort_order INT DEFAULT 0,

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_classes_config ON classes(config_id);
CREATE INDEX IF NOT EXISTS idx_classes_category ON classes(config_id, category);

-- Scheduled class instances
CREATE TABLE IF NOT EXISTS class_schedules (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    class_id UUID NOT NULL REFERENCES classes(id) ON DELETE CASCADE,

    start_time TIMESTAMP NOT NULL,
    end_time TIMESTAMP NOT NULL,

    -- Override class defaults
    capacity_override INT,
    price_override_cents INT,
    instructor_override_id UUID REFERENCES providers(id),

    spots_remaining INT NOT NULL,
    status VARCHAR(20) DEFAULT 'open',  -- open, full, cancelled

    cancellation_reason TEXT,

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_class_schedules_class ON class_schedules(class_id);
CREATE INDEX IF NOT EXISTS idx_class_schedules_start ON class_schedules(start_time);
CREATE INDEX IF NOT EXISTS idx_class_schedules_status ON class_schedules(status);

-- Class bookings (many customers per class)
CREATE TABLE IF NOT EXISTS class_bookings (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    class_schedule_id UUID NOT NULL REFERENCES class_schedules(id) ON DELETE CASCADE,
    customer_id UUID NOT NULL REFERENCES customers(id),

    confirmation_code VARCHAR(20) NOT NULL,
    spots_booked INT DEFAULT 1,  -- For group bookings

    status VARCHAR(20) DEFAULT 'confirmed',  -- confirmed, cancelled, attended, no_show

    price_cents INT,
    payment_status VARCHAR(20) DEFAULT 'pending',
    payment_id VARCHAR(100),

    waitlist_position INT,  -- NULL if not on waitlist

    cancelled_at TIMESTAMP,
    cancellation_reason TEXT,

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    UNIQUE(class_schedule_id, customer_id)
);

CREATE INDEX IF NOT EXISTS idx_class_bookings_schedule ON class_bookings(class_schedule_id);
CREATE INDEX IF NOT EXISTS idx_class_bookings_customer ON class_bookings(customer_id);
CREATE INDEX IF NOT EXISTS idx_class_bookings_code ON class_bookings(confirmation_code);

-- Add foreign key for appointments.class_schedule_id
ALTER TABLE appointments
    ADD CONSTRAINT fk_appointments_class_schedule
    FOREIGN KEY (class_schedule_id) REFERENCES class_schedules(id) ON DELETE SET NULL;

-- ============================================================================
-- HELPER FUNCTIONS
-- ============================================================================

-- Generate confirmation code
CREATE OR REPLACE FUNCTION generate_confirmation_code() RETURNS VARCHAR(20) AS $$
DECLARE
    chars VARCHAR := 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789';  -- No I, O, 0, 1 for clarity
    code VARCHAR := '';
    i INT;
BEGIN
    -- Format: ABC-12345
    FOR i IN 1..3 LOOP
        code := code || substr(chars, floor(random() * 24 + 1)::int, 1);
    END LOOP;
    code := code || '-';
    FOR i IN 1..5 LOOP
        code := code || substr(chars, floor(random() * 34 + 1)::int, 1);
    END LOOP;
    RETURN code;
END;
$$ LANGUAGE plpgsql;

-- Auto-generate confirmation code on appointment insert
CREATE OR REPLACE FUNCTION set_confirmation_code() RETURNS TRIGGER AS $$
BEGIN
    IF NEW.confirmation_code IS NULL OR NEW.confirmation_code = '' THEN
        NEW.confirmation_code := generate_confirmation_code();
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trigger_appointment_confirmation_code ON appointments;
CREATE TRIGGER trigger_appointment_confirmation_code
    BEFORE INSERT ON appointments
    FOR EACH ROW
    EXECUTE FUNCTION set_confirmation_code();

DROP TRIGGER IF EXISTS trigger_class_booking_confirmation_code ON class_bookings;
CREATE TRIGGER trigger_class_booking_confirmation_code
    BEFORE INSERT ON class_bookings
    FOR EACH ROW
    EXECUTE FUNCTION set_confirmation_code();

-- Auto-update updated_at timestamp
CREATE OR REPLACE FUNCTION update_updated_at() RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at := CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Apply updated_at trigger to relevant tables
DROP TRIGGER IF EXISTS trigger_booking_configs_updated_at ON booking_configs;
CREATE TRIGGER trigger_booking_configs_updated_at
    BEFORE UPDATE ON booking_configs
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at();

DROP TRIGGER IF EXISTS trigger_providers_updated_at ON providers;
CREATE TRIGGER trigger_providers_updated_at
    BEFORE UPDATE ON providers
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at();

DROP TRIGGER IF EXISTS trigger_services_updated_at ON services;
CREATE TRIGGER trigger_services_updated_at
    BEFORE UPDATE ON services
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at();

DROP TRIGGER IF EXISTS trigger_appointments_updated_at ON appointments;
CREATE TRIGGER trigger_appointments_updated_at
    BEFORE UPDATE ON appointments
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at();

DROP TRIGGER IF EXISTS trigger_customers_updated_at ON customers;
CREATE TRIGGER trigger_customers_updated_at
    BEFORE UPDATE ON customers
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at();

-- Update customer appointment stats
CREATE OR REPLACE FUNCTION update_customer_appointment_stats() RETURNS TRIGGER AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        UPDATE customers SET
            appointment_count = appointment_count + 1,
            first_appointment_at = COALESCE(first_appointment_at, NEW.start_time),
            last_appointment_at = NEW.start_time
        WHERE id = NEW.customer_id;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trigger_update_customer_stats ON appointments;
CREATE TRIGGER trigger_update_customer_stats
    AFTER INSERT ON appointments
    FOR EACH ROW
    EXECUTE FUNCTION update_customer_appointment_stats();

-- Decrement spots_remaining on class booking
CREATE OR REPLACE FUNCTION update_class_spots() RETURNS TRIGGER AS $$
BEGIN
    IF TG_OP = 'INSERT' AND NEW.status = 'confirmed' THEN
        UPDATE class_schedules SET
            spots_remaining = spots_remaining - NEW.spots_booked,
            status = CASE WHEN spots_remaining - NEW.spots_booked <= 0 THEN 'full' ELSE status END
        WHERE id = NEW.class_schedule_id;
    ELSIF TG_OP = 'UPDATE' AND OLD.status = 'confirmed' AND NEW.status = 'cancelled' THEN
        UPDATE class_schedules SET
            spots_remaining = spots_remaining + OLD.spots_booked,
            status = CASE WHEN status = 'full' THEN 'open' ELSE status END
        WHERE id = NEW.class_schedule_id;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trigger_update_class_spots ON class_bookings;
CREATE TRIGGER trigger_update_class_spots
    AFTER INSERT OR UPDATE ON class_bookings
    FOR EACH ROW
    EXECUTE FUNCTION update_class_spots();
