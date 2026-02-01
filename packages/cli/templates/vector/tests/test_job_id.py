"""Tests for job ID generation."""

import time
import pytest
from services.job_id import generate_job_id, extract_timestamp_from_job_id


class TestGenerateJobId:
    """Tests for job ID generation."""

    def test_format(self):
        """Test job ID has correct format."""
        job_id = generate_job_id()
        assert job_id.startswith("job_")
        # ULID is 26 characters
        assert len(job_id) == 4 + 26  # "job_" + ULID

    def test_uniqueness(self):
        """Test job IDs are unique."""
        ids = [generate_job_id() for _ in range(100)]
        assert len(ids) == len(set(ids))

    def test_sortable(self):
        """Test job IDs are lexicographically sortable by time."""
        ids = []
        for _ in range(10):
            ids.append(generate_job_id())
            time.sleep(0.001)  # Small delay to ensure different timestamps

        # Sorted should match original order
        assert ids == sorted(ids)


class TestExtractTimestamp:
    """Tests for timestamp extraction."""

    def test_extract_timestamp(self):
        """Test extracting timestamp from job ID."""
        before = time.time()
        job_id = generate_job_id()
        after = time.time()

        timestamp = extract_timestamp_from_job_id(job_id)

        # Should be between before and after
        assert before <= timestamp <= after

    def test_timestamp_precision(self):
        """Test timestamp has reasonable precision."""
        job_id = generate_job_id()
        timestamp = extract_timestamp_from_job_id(job_id)

        # Should be within last few seconds
        now = time.time()
        assert now - timestamp < 5
