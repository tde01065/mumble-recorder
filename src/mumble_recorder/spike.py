#!/usr/bin/env python3
"""
Mumble recorder spike: connect, join channel, record audio to WAV.

Audio callback receives already-decoded PCM at 48kHz, 16-bit mono.
"""

import os
import sys
import time
import wave
import logging
import queue
from threading import Event, Lock, Thread

# Import pymumble as pymumble_py3
try:
    import pymumble_py3 as pymumble
except ImportError:
    print("ERROR: pymumble_py3 not installed. Run: pip install pymumble", file=sys.stderr)
    sys.exit(1)

logging.basicConfig(
    level=logging.INFO,
    format="[%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

AUDIO_SAMPLE_RATE = 48000
AUDIO_CHANNELS = 1
AUDIO_SAMPLE_WIDTH = 2  # 16-bit


def wait_until_ready(mumble, timeout_seconds):
    """
    Wait for Mumble connection to be ready with timeout.

    Runs is_ready() in a daemon thread to avoid blocking the timeout loop.
    On success returns True. On timeout or error, calls mumble.stop() and exits with code 1.
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
            mumble.stop()
            sys.exit(1)
        return True
    except queue.Empty:
        logger.error(f"Connection timeout after {timeout_seconds}s")
        mumble.stop()
        sys.exit(1)


def get_channel_name(channel):
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


def get_channel_id(channel):
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


class AudioRecorder:
    """Records audio from Mumble channel callback. Thread-safe audio collection."""

    def __init__(self, output_file, duration_seconds):
        self.output_file = output_file
        self.duration_seconds = duration_seconds
        self.callback_count = 0
        self.total_audio_bytes = 0
        self.audio_frames = []
        self.start_time = None
        self.done = Event()
        self.lock = Lock()

    def on_sound_received(self, user, sound_chunk):
        """Callback: received audio from a user. Thread-safe."""
        if sound_chunk.pcm:
            pcm_data = bytes(sound_chunk.pcm)
            with self.lock:
                if self.start_time is None:
                    return
                self.callback_count += 1
                self.total_audio_bytes += len(pcm_data)
                self.audio_frames.append(pcm_data)

    def start_recording(self):
        """Start recording timer. Must be called before enabling receive sound."""
        with self.lock:
            self.start_time = time.time()

    def check_done(self):
        """Check if recording duration has elapsed."""
        if self.start_time is not None and time.time() - self.start_time >= self.duration_seconds:
            self.done.set()

    def get_frames_copy(self):
        """Get a copy of audio frames under lock."""
        with self.lock:
            return self.audio_frames.copy(), self.callback_count, self.total_audio_bytes

    def write_wav(self):
        """Write accumulated audio frames to WAV file."""
        frames_copy, callback_count, total_bytes = self.get_frames_copy()

        if not frames_copy:
            logger.warning(
                f"No audio frames recorded (callbacks: {callback_count}, bytes: {total_bytes})"
            )
        else:
            logger.info(f"Collected {callback_count} callbacks, {total_bytes} bytes")

        logger.info(f"Writing WAV to {self.output_file}")

        # Ensure output directory exists
        output_dir = os.path.dirname(self.output_file)
        if output_dir and not os.path.exists(output_dir):
            os.makedirs(output_dir, exist_ok=True)

        try:
            with wave.open(self.output_file, "wb") as wav_file:
                wav_file.setnchannels(AUDIO_CHANNELS)
                wav_file.setsampwidth(AUDIO_SAMPLE_WIDTH)
                wav_file.setframerate(AUDIO_SAMPLE_RATE)

                for frame in frames_copy:
                    wav_file.writeframes(frame)

            file_size = os.path.getsize(self.output_file)
            logger.info(f"WAV file written: {self.output_file} ({file_size} bytes)")

            if file_size < 100:
                logger.warning(
                    "Output file is very small (< 100 bytes)—no PCM payload was recorded"
                )

        except Exception as e:
            logger.error(f"Failed to write WAV: {e}", exc_info=True)
            return False

        return True


def main():
    """Connect to Mumble, join channel, record audio."""

    # Parse environment variables
    host = os.getenv("MUMBLE_HOST")
    port = int(os.getenv("MUMBLE_PORT", "64738"))
    username = os.getenv("MUMBLE_USERNAME", "Recorder")
    password = os.getenv("MUMBLE_PASSWORD", "")
    channel_name = os.getenv("MUMBLE_CHANNEL")
    recording_seconds = int(os.getenv("RECORDING_SECONDS", "60"))
    output_file = os.getenv("OUTPUT_FILE", "/recordings/spike-recording.wav")
    connect_timeout = int(os.getenv("MUMBLE_CONNECT_TIMEOUT_SECONDS", "30"))

    if not host:
        logger.error("MUMBLE_HOST environment variable required")
        sys.exit(1)
    if not channel_name:
        logger.error("MUMBLE_CHANNEL environment variable required")
        sys.exit(1)

    logger.info(f"Mumble recorder spike starting")
    logger.info(f"  Host: {host}:{port}")
    logger.info(f"  Username: {username}")
    logger.info(f"  Channel: {channel_name}")
    logger.info(f"  Recording duration: {recording_seconds}s")
    logger.info(f"  Connection timeout: {connect_timeout}s")
    logger.info(f"  Output file: {output_file}")

    # Create recorder
    recorder = AudioRecorder(output_file, recording_seconds)

    # Connect to Mumble server
    logger.info("Connecting to Mumble server...")
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
        sys.exit(1)

    # Register callbacks
    mumble.callbacks.add_callback(
        pymumble.constants.PYMUMBLE_CLBK_CONNECTED,
        lambda: logger.info("Connected to Mumble server"),
    )
    mumble.callbacks.add_callback(
        pymumble.constants.PYMUMBLE_CLBK_SOUNDRECEIVED,
        recorder.on_sound_received,
    )

    # Start the connection thread
    mumble.start()
    logger.info("Connection thread started; waiting for connection...")

    # Wait for connection with timeout
    wait_until_ready(mumble, connect_timeout)

    # Verify we actually connected
    if not mumble.channels:
        logger.error("Connected but no channels available—server may have rejected connection")
        mumble.stop()
        sys.exit(1)

    logger.info("Connected!")

    # List channels
    logger.info("Available channels:")
    for channel_id, channel in mumble.channels.items():
        name = get_channel_name(channel)
        user_count = len(channel.get_users())
        logger.info(f"  [{channel_id}] {name} (users: {user_count})")

    # Find and join target channel
    target_channel = None
    for channel_id, channel in mumble.channels.items():
        if get_channel_name(channel) == channel_name:
            target_channel = channel
            break

    if not target_channel:
        available = ", ".join([get_channel_name(ch) for ch in mumble.channels.values()])
        logger.error(f"Channel '{channel_name}' not found. Available: {available}")
        mumble.stop()
        sys.exit(1)

    target_name = get_channel_name(target_channel)
    target_id = get_channel_id(target_channel)
    logger.info(f"Joining channel: {target_name} (ID: {target_id})")
    try:
        target_channel.move_in()
    except Exception as e:
        logger.error(
            f"Failed to join channel '{target_name}' (ID: {target_id}): {e}",
            exc_info=True,
        )
        mumble.stop()
        sys.exit(1)
    time.sleep(1)  # Give server time to process move

    # Start recording timer BEFORE enabling audio receive to avoid race condition
    logger.info(f"Recording for {recording_seconds} seconds...")
    recorder.start_recording()

    # Enable audio receive after start_time is set
    logger.info("Enabling audio receive...")
    mumble.set_receive_sound(True)

    # Record until duration elapsed or interrupted
    try:
        while not recorder.done.is_set():
            recorder.check_done()
            time.sleep(0.1)
    except KeyboardInterrupt:
        logger.info("Interrupted by user")

    # Stop receiving audio before writing
    mumble.set_receive_sound(False)

    elapsed = time.time() - recorder.start_time
    logger.info(f"Recording stopped after {elapsed:.1f}s")

    # Get final counts
    frames_copy, callback_count, total_bytes = recorder.get_frames_copy()
    logger.info(f"Audio callbacks received: {callback_count}")
    logger.info(f"Total audio bytes: {total_bytes}")

    success_flags = (callback_count > 0 and total_bytes > 0)
    if not success_flags:
        if callback_count == 0:
            logger.error("NO AUDIO CALLBACKS RECEIVED")
            logger.error("  Check: channel name, speaker presence, bot permissions")
        if total_bytes == 0:
            logger.error("NO AUDIO DATA RECEIVED")
            logger.error("  No PCM payload was collected from callbacks")

    # Write WAV file
    write_success = recorder.write_wav()
    if not write_success:
        logger.error("Failed to write WAV file")
        mumble.stop()
        sys.exit(1)

    # Cleanup
    logger.info("Disconnecting...")
    mumble.stop()

    # Exit with appropriate code
    if not success_flags:
        logger.error("Spike FAILED: No audio was received")
        sys.exit(2)  # Distinct exit code for no-audio failure

    logger.info("Spike completed successfully")
    sys.exit(0)


if __name__ == "__main__":
    main()
