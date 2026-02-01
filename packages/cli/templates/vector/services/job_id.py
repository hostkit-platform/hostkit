"""Job ID generation using ULID."""

import ulid


def generate_job_id() -> str:
    """
    Generate a unique job ID using ULID.

    ULID is preferred over UUID because:
    - Lexicographically sortable (time-ordered)
    - More compact string representation
    - Contains timestamp information

    Format: job_{ulid}
    Example: job_01HQXK5J8NZQW0R3Y6M7P2V4T9
    """
    return f"job_{ulid.new().str}"


def extract_timestamp_from_job_id(job_id: str) -> float:
    """Extract Unix timestamp from job ID."""
    ulid_str = job_id.replace("job_", "")
    return ulid.from_str(ulid_str).timestamp().timestamp
