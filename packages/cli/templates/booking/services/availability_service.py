"""Availability calculation service - Core time-first booking logic.

This module handles:
- Available date calculation for a month
- Available time slot calculation for a date
- Provider pooling ("Any Available")
- Room availability checking
"""

from datetime import datetime, date, time, timedelta
from typing import List, Dict, Optional, Tuple
from sqlalchemy.orm import Session


class AvailabilityService:
    """Service for calculating available booking slots."""

    def __init__(self, db: Session, config_id: str):
        """Initialize availability service.

        Args:
            db: Database session
            config_id: Booking config ID for this project
        """
        self.db = db
        self.config_id = config_id

    def get_available_dates(
        self, month: str, provider_id: Optional[str] = None
    ) -> List[str]:
        """Calculate available dates for a month.

        Args:
            month: Month in YYYY-MM format
            provider_id: Optional provider filter

        Returns:
            List of dates in YYYY-MM-DD format that have availability
        """
        # TODO: Implement date availability calculation
        # This requires:
        # 1. Parse month to get start/end dates
        # 2. Query provider schedules (weekly recurring)
        # 3. Apply schedule overrides (day-offs, special hours)
        # 4. Check for existing appointments (conflicts)
        # 5. Check room availability
        # 6. Return dates with at least one available slot

        return []

    def get_available_slots(
        self, target_date: str, provider_id: Optional[str] = None
    ) -> List[Dict]:
        """Calculate available time slots for a specific date.

        Args:
            target_date: Date in YYYY-MM-DD format
            provider_id: Optional provider filter (None = pool all providers)

        Returns:
            List of time slots with:
            - start_time: ISO timestamp
            - end_time: ISO timestamp
            - providers: List of available provider IDs/names
            - available_count: Number of slots available
            - max_duration_minutes: Longest service that fits
        """
        # TODO: Implement slot calculation
        # This requires:
        # 1. Parse target_date
        # 2. Get provider schedules for day of week
        # 3. Apply schedule overrides for specific date
        # 4. Subtract existing appointments
        # 5. Pool providers if provider_id is None
        # 6. Check room availability
        # 7. Group into time ranges
        # 8. Calculate max duration for each range

        return []

    def get_available_durations(
        self, start_time: str, provider_id: Optional[str] = None
    ) -> List[Dict]:
        """Calculate available durations for a time slot.

        Args:
            start_time: Start time in ISO format
            provider_id: Optional provider filter

        Returns:
            List of durations with:
            - minutes: Duration in minutes
            - price_cents: Price for this duration
            - available_count: Number of providers/rooms available
        """
        # TODO: Implement duration calculation
        # This requires:
        # 1. Parse start_time
        # 2. Calculate end of available window (next appointment or day end)
        # 3. Query services by duration that fit in window
        # 4. Get pricing (from services table, with provider overrides)
        # 5. Count availability (providers + rooms)

        return []

    def check_slot_availability(
        self,
        start_time: datetime,
        duration_minutes: int,
        provider_id: Optional[str] = None,
        room_id: Optional[str] = None
    ) -> bool:
        """Check if a specific slot is available.

        Args:
            start_time: Start time
            duration_minutes: Duration in minutes
            provider_id: Provider ID (None = auto-assign)
            room_id: Room ID (None = auto-assign)

        Returns:
            True if slot is available, False otherwise
        """
        # TODO: Implement conflict checking
        # This requires:
        # 1. Check provider schedule (if provider_id specified)
        # 2. Check provider has no conflicting appointments
        # 3. Check room availability (if room_id specified)
        # 4. Check room has no conflicting appointments
        # 5. Apply buffer_minutes from config

        return False

    def auto_assign_provider(
        self,
        start_time: datetime,
        duration_minutes: int,
        service_id: str
    ) -> Optional[str]:
        """Auto-assign a provider for a time slot.

        Uses round-robin based on appointment count for the day.

        Args:
            start_time: Start time
            duration_minutes: Duration in minutes
            service_id: Service ID

        Returns:
            Provider ID or None if no provider available
        """
        # TODO: Implement provider auto-assignment
        # This requires:
        # 1. Find providers who can perform this service
        # 2. Filter to providers available at this time
        # 3. Count appointments for each provider on this day
        # 4. Return provider with fewest appointments (load balancing)

        return None

    def auto_assign_room(
        self,
        start_time: datetime,
        duration_minutes: int,
        service_id: str,
        provider_id: str
    ) -> Optional[str]:
        """Auto-assign a room for a time slot.

        Args:
            start_time: Start time
            duration_minutes: Duration in minutes
            service_id: Service ID
            provider_id: Provider ID

        Returns:
            Room ID or None if no room available
        """
        # TODO: Implement room auto-assignment
        # This requires:
        # 1. Find rooms that can host this service
        # 2. Filter to rooms available at this time
        # 3. Return first available room

        return None

    def _get_provider_schedule(
        self, provider_id: str, target_date: date
    ) -> Optional[Tuple[time, time]]:
        """Get provider's schedule for a specific date.

        Checks schedule overrides first, then falls back to weekly schedule.

        Args:
            provider_id: Provider ID
            target_date: Target date

        Returns:
            Tuple of (start_time, end_time) or None if not available
        """
        # TODO: Implement schedule lookup
        # 1. Check schedule_overrides for specific date
        # 2. If override exists and is_available=false, return None
        # 3. If override exists with times, return those times
        # 4. Otherwise, query provider_schedules for day_of_week
        # 5. Return (start_time, end_time) or None

        return None

    def _check_conflicts(
        self,
        start_time: datetime,
        end_time: datetime,
        provider_id: Optional[str] = None,
        room_id: Optional[str] = None,
        exclude_appointment_id: Optional[str] = None
    ) -> bool:
        """Check for appointment conflicts.

        Args:
            start_time: Start time
            end_time: End time
            provider_id: Provider ID to check
            room_id: Room ID to check
            exclude_appointment_id: Appointment ID to exclude (for rescheduling)

        Returns:
            True if there are conflicts, False if slot is free
        """
        # TODO: Implement conflict checking
        # Query appointments table for overlapping appointments
        # Consider buffer_minutes from config

        return False
