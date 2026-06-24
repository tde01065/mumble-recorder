"""Filename and session ID generation."""
import re
import time
import secrets
from datetime import datetime


def generate_session_id() -> tuple[str, str]:
    """Generate a session_id and short_id.

    Returns (session_id, session_short_id).
    session_id is a hex-based unique ID.
    session_short_id is the first 5 chars for use in filenames.
    """
    # Use current time + random bytes for uniqueness
    random_part = secrets.token_hex(4)  # 8 hex chars
    session_id = f"s{random_part}"
    session_short_id = session_id[:5]
    return session_id, session_short_id


def channel_to_slug(channel_name: str) -> str:
    """Convert channel name to a filename-safe slug.

    Converts to lowercase, replaces spaces/special chars with underscores.
    """
    # Convert to lowercase
    slug = channel_name.lower()
    # Replace non-alphanumeric with underscore
    slug = re.sub(r"[^a-z0-9]+", "_", slug)
    # Strip leading/trailing underscores
    slug = slug.strip("_")
    return slug or "channel"


def segment_filename(
    session_started_at: datetime,
    session_short_id: str,
    channel_slug: str,
    segment_index: int,
    segment_started_at: datetime,
) -> str:
    """Generate segment filename.

    Pattern: YYYYMMDD-HHMMSS_channelslug_sessionshortid_seg-YYYYMMDD-HHMMSS.wav
    """
    session_ts = session_started_at.strftime("%Y%m%d-%H%M%S")
    segment_ts = segment_started_at.strftime("%Y%m%d-%H%M%S")
    return f"{session_ts}_{channel_slug}_{session_short_id}_seg-{segment_ts}.wav"
