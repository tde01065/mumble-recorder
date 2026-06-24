"""CLI entry point for the recorder core."""
import sys
import time
import logging
import uuid
from dataclasses import replace

from mumble_recorder.config import load_config
from mumble_recorder.recording import Recorder

logging.basicConfig(
    level=logging.INFO,
    format="[%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def get_cli_exit_info(status: str | None, success: bool) -> tuple[int, str, bool]:
    """Determine exit code, message, and whether to log as error.

    Args:
        status: Recorder status from metadata, or None if not set.
        success: Whether recorder.run() returned True.

    Returns:
        (exit_code, message, is_error_log) where is_error_log=True means log.error(),
        False means log.info().
    """
    final_status = status or ("completed" if success else "failed")

    if final_status == "completed":
        return 0, "Recording completed successfully", False
    elif final_status == "interrupted":
        return 0, "Recording interrupted and finalized cleanly", False
    else:  # failed
        return 1, "Recording failed", True


def should_reconnect(
    recorder: Recorder,
    reconnect_attempt: int,
    cli_elapsed_seconds: float,
    total_planned_seconds: int,
) -> bool:
    """Determine if we should reconnect after a session.

    Args:
        recorder: The completed recorder instance.
        reconnect_attempt: The reconnect attempt number (0-indexed).
        cli_elapsed_seconds: Total elapsed time since CLI invocation.
        total_planned_seconds: Total planned recording duration from config.

    Returns:
        True if we should reconnect, False otherwise.
    """
    if not recorder.config.reconnect_enabled:
        return False

    if recorder.status != "interrupted" or recorder.stop_reason != "mumble_disconnected":
        return False

    if reconnect_attempt >= recorder.config.reconnect_max_attempts:
        return False

    remaining_seconds = total_planned_seconds - cli_elapsed_seconds
    if remaining_seconds <= 0:
        return False

    return True


def calculate_remaining_duration(
    cli_start_mono: float, total_planned_seconds: int
) -> int:
    """Calculate remaining recording duration from CLI start.

    Args:
        cli_start_mono: Monotonic timestamp of CLI invocation start.
        total_planned_seconds: Total planned duration from config.

    Returns:
        Remaining duration in seconds (clamped to >= 0).
    """
    elapsed = time.monotonic() - cli_start_mono
    remaining = max(0, int(total_planned_seconds - elapsed))
    return remaining


def main():
    """Main entry point for the recorder."""
    try:
        config = load_config()
    except SystemExit:
        raise

    cli_start_mono = time.monotonic()
    total_planned_seconds = config.recording_seconds
    recording_group_id = str(uuid.uuid4())
    reconnect_attempt = 0
    parent_session_id = None
    last_session_metadata_path = None

    while True:
        remaining_duration = calculate_remaining_duration(cli_start_mono, total_planned_seconds)
        session_config = replace(config, recording_seconds=remaining_duration)

        recorder = Recorder(
            session_config,
            recording_group_id=recording_group_id,
            reconnect_attempt=reconnect_attempt,
            parent_session_id=parent_session_id,
        )
        success = recorder.run()
        last_session_metadata_path = recorder.metadata_path

        cli_elapsed = time.monotonic() - cli_start_mono

        if should_reconnect(recorder, reconnect_attempt, cli_elapsed, total_planned_seconds):
            parent_session_id = recorder.session_id
            reconnect_attempt += 1
            remaining_duration = calculate_remaining_duration(cli_start_mono, total_planned_seconds)

            logger.info(
                f"Mumble disconnected; reconnecting in {config.reconnect_delay_seconds}s "
                f"(attempt {reconnect_attempt}/{config.reconnect_max_attempts}, "
                f"{remaining_duration}s remaining)"
            )
            time.sleep(config.reconnect_delay_seconds)
            continue

        exit_code, message, is_error = get_cli_exit_info(recorder.status, success)
        if is_error:
            logger.error(message)
        else:
            logger.info(message)

        if last_session_metadata_path:
            logger.info(f"Session metadata: {last_session_metadata_path}")

        sys.exit(exit_code)


if __name__ == "__main__":
    main()
