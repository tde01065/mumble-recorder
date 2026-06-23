"""Mumble client connection and channel management."""
import sys
import time
import queue
import logging
from threading import Thread
from typing import Any

try:
    import pymumble_py3 as pymumble
except ImportError:
    print("ERROR: pymumble_py3 not installed. Run: pip install pymumble", file=sys.stderr)
    sys.exit(1)

logger = logging.getLogger(__name__)


def get_channel_name(channel) -> str | None:
    """Get channel name from pymumble Channel object."""
    try:
        if hasattr(channel, "get_property"):
            return channel.get_property("name")
    except (AttributeError, KeyError):
        pass
    try:
        if hasattr(channel, "get"):
            return channel.get("name")
    except (AttributeError, KeyError):
        pass
    return None


def get_channel_id(channel) -> int | None:
    """Get channel ID from pymumble Channel object."""
    try:
        if hasattr(channel, "get_property"):
            return channel.get_property("channel_id")
    except (AttributeError, KeyError):
        pass
    try:
        if hasattr(channel, "get_id"):
            return channel.get_id()
    except (AttributeError, KeyError):
        pass
    try:
        if hasattr(channel, "get"):
            return channel.get("channel_id")
    except (AttributeError, KeyError):
        pass
    return None


def wait_until_ready(mumble, timeout_seconds: int) -> bool:
    """Wait for Mumble connection to be ready with timeout.

    Returns True on success. On timeout, calls mumble.stop() and returns False.
    """
    result_queue = queue.Queue()

    def check_ready():
        try:
            mumble.is_ready()
            result_queue.put("ready")
        except Exception as e:
            result_queue.put(("error", e))

    thread = Thread(target=check_ready, daemon=True)
    thread.start()

    try:
        result = result_queue.get(timeout=timeout_seconds)
        if isinstance(result, tuple) and result[0] == "error":
            logger.error(f"Connection check failed: {result[1]}")
            try:
                mumble.stop()
            except Exception as e:
                logger.error(f"Failed to stop Mumble after connection error: {e}")
            return False
        return True
    except queue.Empty:
        logger.error(f"Connection timeout after {timeout_seconds}s")
        try:
            mumble.stop()
        except Exception as e:
            logger.error(f"Failed to stop Mumble after timeout: {e}")
        return False


def connect_to_mumble(
    host: str,
    port: int,
    username: str,
    password: str,
    timeout_seconds: int,
) -> Any:
    """Connect to Mumble server.

    Returns mumble client on success, None on failure.
    """
    logger.info(f"Connecting to Mumble at {host}:{port}")
    try:
        mumble = pymumble.Mumble(
            host,
            user=username,
            port=port,
            password=password,
            certfile=None,
            keyfile=None,
            tokens=[],
        )
    except Exception as e:
        logger.error(f"Failed to create Mumble connection: {e}", exc_info=True)
        return None

    mumble.callbacks.add_callback(
        pymumble.constants.PYMUMBLE_CLBK_CONNECTED,
        lambda: logger.info("Connected to Mumble server"),
    )

    mumble.start()
    logger.info("Connection thread started; waiting for connection...")

    if not wait_until_ready(mumble, timeout_seconds):
        return None

    if not mumble.channels:
        logger.error("Connected but no channels available—server may have rejected connection")
        try:
            mumble.stop()
        except Exception as e:
            logger.error(f"Failed to stop Mumble after channel check: {e}")
        return None

    logger.info("Connected!")
    return mumble


def list_channels(mumble: Any) -> None:
    """Log available channels."""
    logger.info("Available channels:")
    for channel_id, channel in mumble.channels.items():
        name = get_channel_name(channel)
        user_count = len(channel.get_users())
        logger.info(f"  [{channel_id}] {name} (users: {user_count})")


def find_and_join_channel(
    mumble: Any, channel_name: str
) -> Any:
    """Find channel by name and join it.

    Returns channel on success, None on failure.
    """
    target_channel = None
    for channel_id, channel in mumble.channels.items():
        if get_channel_name(channel) == channel_name:
            target_channel = channel
            break

    if not target_channel:
        available = ", ".join([get_channel_name(ch) for ch in mumble.channels.values()])
        logger.error(f"Channel '{channel_name}' not found. Available: {available}")
        return None

    target_name = get_channel_name(target_channel)
    target_id = get_channel_id(target_channel)
    logger.info(f"Joining channel: {target_name} (ID: {target_id})")

    try:
        target_channel.move_in()
        time.sleep(1)  # Give server time to process move
        logger.info(f"Joined channel {target_name}")
        return target_channel
    except Exception as e:
        logger.error(f"Failed to join channel '{target_name}': {e}", exc_info=True)
        return None
