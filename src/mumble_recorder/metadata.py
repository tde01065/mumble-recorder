"""Session and segment metadata."""
import json
import os
from dataclasses import dataclass, asdict
from datetime import datetime


@dataclass
class SegmentMetadata:
    """Metadata for a single segment."""
    segment_index: int
    segment_started_at_local: str
    segment_started_at_utc: str
    segment_stopped_at_local: str
    segment_stopped_at_utc: str
    wall_clock_duration_seconds: float
    audio_duration_seconds: float
    file_name: str
    file_path: str
    size_bytes: int


@dataclass
class SessionMetadata:
    """Session-level metadata."""
    session_id: str
    session_short_id: str
    recording_mode: str
    channel_name: str
    session_started_at_local: str
    session_started_at_utc: str
    session_stopped_at_local: str
    session_stopped_at_utc: str
    wall_clock_duration_seconds: float
    audio_duration_seconds: float
    segment_duration_seconds: int
    segments: list[SegmentMetadata]
    status: str = "completed"
    stop_reason: str = "duration_reached"
    planned_duration_seconds: int = 0

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)

    def to_json_str(self) -> str:
        """Convert to JSON string."""
        return json.dumps(self.to_dict(), indent=2)

    def save_to_file(self, file_path: str) -> None:
        """Save metadata to JSON file."""
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        with open(file_path, "w") as f:
            f.write(self.to_json_str())
