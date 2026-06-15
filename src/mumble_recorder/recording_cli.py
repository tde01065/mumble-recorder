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


def main():
    """Main entry point for the recorder."""
    try:
        config = load_config()
    except SystemExit:
        raise

    recorder = Recorder(config)
    success = recorder.run()

    if success:
        logger.info("Recording completed successfully")
        sys.exit(0)
    else:
        logger.error("Recording failed")
        sys.exit(1)


if __name__ == "__main__":
    main()
