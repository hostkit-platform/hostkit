-- Migration 002: Add schema version tracking
-- This migration adds a schema_version column to booking_configs
-- to track which version of the schema has been applied.
--
-- All existing projects are assumed to be at version 1 (the original schema).

-- Add schema_version column if it doesn't exist
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'booking_configs' AND column_name = 'schema_version'
    ) THEN
        ALTER TABLE booking_configs ADD COLUMN schema_version INT DEFAULT 1;
    END IF;
END $$;

-- Update to version 2 for any rows that still have version 1
-- (indicates they've received this migration)
UPDATE booking_configs SET schema_version = 2 WHERE schema_version = 1 OR schema_version IS NULL;
