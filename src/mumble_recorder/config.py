"""Configuration loading and validation."""
import os
import sys
from dataclasses import dataclass


@dataclass
class Config:
    """Recorder configuration."""
    mumble_host: str
    mumble_port: int
    mumble_username: str
    mumble_password: str
    mumble_channel: str
    recording_seconds: int
    mumble_connect_timeout_seconds: int
    recording_mode: str
    segment_duration_seconds: int
    output_dir: str


def load_config() -> Config:
    """Load config from environment variables."""
    host = os.getenv("MUMBLE_HOST")
    if not host:
        print("ERROR: MUMBLE_HOST environment variable required", file=sys.stderr)
        sys.exit(1)

    channel = os.getenv("MUMBLE_CHANNEL")
    if not channel:
        print("ERROR: MUMBLE_CHANNEL environment variable required", file=sys.stderr)
        sys.exit(1)

    mode = os.getenv("RECORDING_MODE", "continuous").lower()
    if mode not in ("continuous", "received_audio_only"):
        print(
            f"ERROR: RECORDING_MODE must be 'continuous' or 'received_audio_only', got '{mode}'",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        segment_duration = int(os.getenv("SEGMENT_DURATION_SECONDS", "1800"))
        if segment_duration <= 0:
            raise ValueError("must be > 0")
    except ValueError as e:
        print(
            f"ERROR: SEGMENT_DURATION_SECONDS must be positive integer, got '{os.getenv('SEGMENT_DURATION_SECONDS')}': {e}",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        recording_seconds = int(os.getenv("RECORDING_SECONDS", "60"))
    except ValueError:
        print(
            f"ERROR: RECORDING_SECONDS must be integer, got '{os.getenv('RECORDING_SECONDS')}'",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        port = int(os.getenv("MUMBLE_PORT", "64738"))
    except ValueError:
        print(
            f"ERROR: MUMBLE_PORT must be integer, got '{os.getenv('MUMBLE_PORT')}'",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        timeout = int(os.getenv("MUMBLE_CONNECT_TIMEOUT_SECONDS", "30"))
    except ValueError:
        print(
            f"ERROR: MUMBLE_CONNECT_TIMEOUT_SECONDS must be integer, got '{os.getenv('MUMBLE_CONNECT_TIMEOUT_SECONDS')}'",
            file=sys.stderr,
        )
        sys.exit(1)

    output_dir = os.getenv("OUTPUT_DIR", "/recordings")

    return Config(
        mumble_host=host,
        mumble_port=port,
        mumble_username=os.getenv("MUMBLE_USERNAME", "Recorder"),
        mumble_password=os.getenv("MUMBLE_PASSWORD", ""),
        mumble_channel=channel,
        recording_seconds=recording_seconds,
        mumble_connect_timeout_seconds=timeout,
        recording_mode=mode,
        segment_duration_seconds=segment_duration,
        output_dir=output_dir,
    )
