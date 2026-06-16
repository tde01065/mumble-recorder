"""CLI entry point for the recorder core."""
import sys
import logging

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


def main():
    """Main entry point for the recorder."""
    try:
        config = load_config()
    except SystemExit:
        raise

    recorder = Recorder(config)
    success = recorder.run()

    exit_code, message, is_error = get_cli_exit_info(recorder.status, success)

    if is_error:
        logger.error(message)
    else:
        logger.info(message)

    if recorder.metadata_path:
        logger.info(f"Session metadata: {recorder.metadata_path}")

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
