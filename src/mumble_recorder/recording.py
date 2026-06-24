"""Main recording orchestration."""
import os
import json
import signal
import time
import logging
import queue
from datetime import datetime, timezone, timedelta
from threading import Thread, Event
from pathlib import Path

from mumble_recorder.config import Config
from mumble_recorder.filenames import (
    generate_session_id,
    channel_to_slug,
    segment_filename,
)
from mumble_recorder.metadata import SessionMetadata, SegmentMetadata
from mumble_recorder.mumble_client import (
    connect_to_mumble,
    list_channels,
    find_and_join_channel,
    get_channel_name,
    get_channel_users,
)
from mumble_recorder.wav_writer import SegmentWriter

logger = logging.getLogger(__name__)


class Recorder:
    """Main recorder orchestration."""

    def __init__(
        self,
        config: Config,
        recording_group_id: str | None = None,
        reconnect_attempt: int = 0,
        parent_session_id: str | None = None,
    ):
        self.config = config
        self.session_id, self.session_short_id = generate_session_id()
        self.channel_slug = channel_to_slug(config.mumble_channel)

        self.recording_group_id = recording_group_id
        self.reconnect_attempt = reconnect_attempt
        self.parent_session_id = parent_session_id

        # Set after channel join in run(), not at construction time.
        # Filenames and metadata reflect actual recording start.
        self.session_start_wall_clock: datetime | None = None
        self.session_start_monotonic: float | None = None

        self.session_stop_wall_clock: datetime | None = None
        self.session_stop_monotonic: float | None = None

        # Audio queue: (monotonic_timestamp, pcm_bytes)
        self.audio_queue: queue.Queue = queue.Queue()
        self.stop_event = Event()
        self.stop_reason = "duration_reached"

        # Segment management
        self.segments_metadata: list[SegmentMetadata] = []
        self.current_segment: SegmentWriter | None = None
        self.current_segment_index: int = -1

        # Final status exposed after run()
        self.status: str | None = None
        self.metadata_path: str | None = None

        self.mumble = None
        self.channel = None
        self.runtime_status_path = Path(config.output_dir) / "recorder_runtime_status.json"

    def _signal_handler(self, signum: int, frame) -> None:
        """Lightweight signal handler: set event and reason only."""
        if signum == signal.SIGTERM:
            self.stop_reason = "signal_sigterm"
        elif signum == signal.SIGINT:
            self.stop_reason = "signal_sigint"
        self.stop_event.set()

    def _handle_mumble_disconnect(self, *args, **kwargs) -> None:
        """Called when Mumble connection is lost. Mark as disconnected and trigger stop."""
        if self.stop_reason == "duration_reached":
            self.stop_reason = "mumble_disconnected"
        self.stop_event.set()

    def _write_runtime_status(self, running: bool = True) -> None:
        """Write or update runtime status JSON file."""
        if self.session_start_monotonic is None:
            return

        try:
            elapsed_wall = datetime.now(timezone.utc).astimezone()
            elapsed_mono = time.monotonic() - self.session_start_monotonic
            audio_duration = sum(s.audio_duration_seconds for s in self.segments_metadata)
            # Include active segment's current audio duration if it's still open
            if self.current_segment is not None:
                audio_duration += self.current_segment.get_accumulated_audio_duration()

            channel_users = []
            if self.channel:
                channel_users = get_channel_users(self.channel)

            status = {
                "running": running,
                "session_id": self.session_id,
                "recording_group_id": self.recording_group_id,
                "recording_mode": self.config.recording_mode,
                "channel_name": self.config.mumble_channel,
                "channel_users": channel_users,
                "wall_clock_duration_seconds": elapsed_mono,
                "audio_duration_seconds": audio_duration,
                "updated_at_local": elapsed_wall.strftime("%Y-%m-%d %H:%M:%S"),
                "updated_at_utc": elapsed_wall.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
            }

            self.runtime_status_path.write_text(json.dumps(status, indent=2), encoding="utf-8")
        except Exception as e:
            logger.warning(f"Failed to write runtime status: {e}")

    def _log_config(self) -> None:
        logger.info("=== Recorder Configuration ===")
        logger.info(f"Session ID: {self.session_id} ({self.session_short_id})")
        logger.info(f"Channel: {self.config.mumble_channel} (slug: {self.channel_slug})")
        logger.info(f"Recording mode: {self.config.recording_mode}")
        logger.info(f"Recording duration: {self.config.recording_seconds}s")
        logger.info(f"Segment duration: {self.config.segment_duration_seconds}s")
        logger.info(f"Output directory: {self.config.output_dir}")
        logger.info("=" * 30)

    def _on_sound_received(self, user, sound_chunk) -> None:
        """Mumble callback: enqueue (monotonic_ts, pcm_bytes). Nothing else."""
        if sound_chunk and sound_chunk.pcm:
            self.audio_queue.put((time.monotonic(), bytes(sound_chunk.pcm)))

    def _writer_thread_worker(self) -> None:
        try:
            self._writer_loop()
        except Exception as e:
            logger.error(f"Writer thread error: {e}", exc_info=True)
            self.stop_reason = "writer_error"
            self.stop_event.set()

    def _writer_loop(self) -> None:
        """Process the audio queue, rotate segments, and write to disk."""
        if self.session_start_monotonic is None:
            logger.error("session_start_monotonic not set")
            self.stop_reason = "writer_error"
            return

        last_runtime_status_update = 0

        while not self.stop_event.is_set():
            elapsed_mono = time.monotonic() - self.session_start_monotonic

            if self.config.recording_seconds > 0 and elapsed_mono >= self.config.recording_seconds:
                logger.info(f"Recording duration ({self.config.recording_seconds}s) reached")
                self.stop_reason = "duration_reached"
                break

            # Rotate segment when the elapsed-time bucket changes.
            segment_index = int(elapsed_mono / self.config.segment_duration_seconds)

            if self.current_segment is None or self.current_segment_index != segment_index:
                elapsed_wall = datetime.now(timezone.utc).astimezone()
                if self.current_segment is not None:
                    self._finalize_segment(elapsed_mono, elapsed_wall)
                self._start_segment(segment_index)

            # Write runtime status approximately once per second
            now = time.monotonic()
            if now - last_runtime_status_update >= 1.0:
                self._write_runtime_status(running=True)
                last_runtime_status_update = now

            # Drain whatever is queued; block briefly so we don't busy-spin.
            try:
                while True:
                    try:
                        mono_ts, pcm = self.audio_queue.get(timeout=0.1)
                        if self.current_segment is not None:
                            self.current_segment.write_chunk(mono_ts, pcm)
                    except queue.Empty:
                        break
            except Exception as e:
                logger.error(f"Error writing audio chunk: {e}", exc_info=True)

        # Audio receive is disabled by run() before stop_event is set, so the
        # queue will not grow after this point. Drain and write what remains
        # before finalising the last segment.
        drained = 0
        while True:
            try:
                mono_ts, pcm = self.audio_queue.get_nowait()
                if self.current_segment is not None:
                    self.current_segment.write_chunk(mono_ts, pcm)
                drained += 1
            except queue.Empty:
                break
        if drained:
            logger.debug(f"Drained {drained} queued chunks after stop")

        if self.current_segment is not None:
            elapsed_mono = time.monotonic() - self.session_start_monotonic
            elapsed_wall = datetime.now(timezone.utc).astimezone()
            self._finalize_segment(elapsed_mono, elapsed_wall)

    def _start_segment(self, segment_index: int) -> None:
        """Open a new segment for writing."""
        if self.current_segment is not None:
            logger.warning("_start_segment called while a segment is still open")

        if self.session_start_monotonic is None or self.session_start_wall_clock is None:
            logger.error("session timing not initialized")
            return

        segment_offset = segment_index * self.config.segment_duration_seconds
        segment_start_mono = self.session_start_monotonic + segment_offset
        segment_start_wall = self.session_start_wall_clock + timedelta(seconds=segment_offset)

        filename = segment_filename(
            self.session_start_wall_clock,
            self.session_short_id,
            self.channel_slug,
            segment_index,
            segment_start_wall,
        )
        file_path = os.path.join(self.config.output_dir, filename)

        logger.info(f"Starting segment {segment_index}: {filename}")

        seg = SegmentWriter(
            file_path,
            self.config.recording_mode,
            segment_start_mono,
            segment_start_wall,
            self.config.segment_duration_seconds,
        )
        seg.open()

        self.current_segment = seg
        self.current_segment_index = segment_index

    def _finalize_segment(self, elapsed_mono: float, elapsed_wall: datetime) -> None:
        """Close current segment and record its metadata."""
        if self.current_segment is None:
            return

        try:
            segment_offset = self.current_segment_index * self.config.segment_duration_seconds
            segment_elapsed = elapsed_mono - segment_offset
            # Clamp: never exceed the configured segment window, never go negative.
            segment_elapsed = max(0.0, min(segment_elapsed, self.config.segment_duration_seconds))

            self.current_segment.finalize(segment_elapsed)

            meta = self.current_segment.get_metadata()
            self.segments_metadata.append(SegmentMetadata(
                segment_index=self.current_segment_index,
                segment_started_at_local=self.current_segment.segment_start_wall_clock.strftime(
                    "%Y-%m-%d %H:%M:%S"
                ),
                segment_started_at_utc=self.current_segment.segment_start_wall_clock.astimezone(
                    timezone.utc
                ).strftime("%Y-%m-%d %H:%M:%S UTC"),
                segment_stopped_at_local=elapsed_wall.strftime("%Y-%m-%d %H:%M:%S"),
                segment_stopped_at_utc=elapsed_wall.astimezone(timezone.utc).strftime(
                    "%Y-%m-%d %H:%M:%S UTC"
                ),
                wall_clock_duration_seconds=meta["wall_clock_duration"],
                audio_duration_seconds=meta["audio_duration"],
                file_name=os.path.basename(self.current_segment.file_path),
                file_path=self.current_segment.file_path,
                size_bytes=meta["file_size"],
            ))

            logger.info(
                f"Segment {self.current_segment_index} closed: "
                f"{meta['audio_duration']:.2f}s audio, {meta['file_size']} bytes"
            )
        except Exception as e:
            logger.error(f"Error finalising segment: {e}", exc_info=True)
        finally:
            self.current_segment = None
            self.current_segment_index = -1

    def run(self) -> bool:
        """Connect, record, disconnect. Returns True on success."""
        self._log_config()

        # Register signal handlers for graceful shutdown.
        signal.signal(signal.SIGTERM, self._signal_handler)
        signal.signal(signal.SIGINT, self._signal_handler)

        self.mumble = connect_to_mumble(
            self.config.mumble_host,
            self.config.mumble_port,
            self.config.mumble_username,
            self.config.mumble_password,
            self.config.mumble_connect_timeout_seconds,
        )
        if self.mumble is None:
            self.stop_reason = "mumble_connection_failed"
            self.session_stop_wall_clock = datetime.now(timezone.utc).astimezone()
            self.session_stop_monotonic = time.monotonic()
            self._create_session_metadata(is_failed=True)
            return False

        list_channels(self.mumble)
        channel = find_and_join_channel(self.mumble, self.config.mumble_channel)
        if channel is None:
            self.stop_reason = "channel_join_failed"
            self.mumble.stop()
            self.session_stop_wall_clock = datetime.now(timezone.utc).astimezone()
            self.session_stop_monotonic = time.monotonic()
            self._create_session_metadata(is_failed=True)
            return False

        self.channel = channel

        # Set session start timestamps here — after connect and channel join —
        # so filenames and metadata reflect actual recording start, not object
        # construction time (which could be 10-30s earlier during connection).
        self.session_start_wall_clock = datetime.now(timezone.utc).astimezone()
        self.session_start_monotonic = time.monotonic()

        try:
            import pymumble_py3 as pymumble
            self.mumble.callbacks.add_callback(
                pymumble.constants.PYMUMBLE_CLBK_SOUNDRECEIVED,
                self._on_sound_received,
            )
            # Try to register disconnect callback if available in pymumble_py3
            if hasattr(pymumble.constants, "PYMUMBLE_CLBK_DISCONNECTED"):
                self.mumble.callbacks.add_callback(
                    pymumble.constants.PYMUMBLE_CLBK_DISCONNECTED,
                    self._handle_mumble_disconnect,
                )
        except ImportError:
            self.mumble.stop()
            logger.error("pymumble_py3 not available")
            self.stop_reason = "writer_error"
            self.session_stop_wall_clock = datetime.now(timezone.utc).astimezone()
            self.session_stop_monotonic = time.monotonic()
            self._create_session_metadata(is_failed=True)
            return False

        writer_thread = Thread(target=self._writer_thread_worker, daemon=False)
        writer_thread.start()

        logger.info("Enabling audio receive...")
        self.mumble.set_receive_sound(True)

        if self.config.recording_seconds > 0:
            logger.info(f"Recording for {self.config.recording_seconds} seconds...")
        else:
            logger.info("Recording indefinitely until stopped...")
        try:
            while True:
                if self.config.recording_seconds > 0:
                    if time.monotonic() - self.session_start_monotonic >= self.config.recording_seconds:
                        break
                if self.stop_event.is_set():
                    break
                time.sleep(0.1)
        except KeyboardInterrupt:
            logger.info("Recording interrupted by user")
            self.stop_reason = "keyboard_interrupt"
            self.stop_event.set()

        logger.info(f"Stopping recording (reason: {self.stop_reason})...")

        # Disable audio receive FIRST so no new callbacks can enqueue after
        # stop_event is set. The writer thread can then drain the queue cleanly.
        self.mumble.set_receive_sound(False)
        if not self.stop_event.is_set():
            self.stop_event.set()
        self.session_stop_monotonic = time.monotonic()
        writer_thread.join(timeout=10)

        if writer_thread.is_alive():
            logger.warning("Writer thread did not stop within timeout")

        self.mumble.stop()

        self.session_stop_wall_clock = datetime.now(timezone.utc).astimezone()

        # Update runtime status with final state
        try:
            self._write_runtime_status(running=False)
        except Exception as e:
            logger.warning(f"Failed to write final runtime status: {e}")

        is_failed = self.stop_reason in ("writer_error", "mumble_connection_failed", "channel_join_failed")
        is_interrupted = self.stop_reason in ("signal_sigterm", "signal_sigint", "keyboard_interrupt", "mumble_disconnected")
        self._create_session_metadata(is_failed=is_failed, is_interrupted=is_interrupted)

        logger.info("Recording session completed")
        return not is_failed

    def _create_session_metadata(
        self, is_failed: bool = False, is_interrupted: bool = False
    ) -> None:
        start = self.session_start_wall_clock
        stop = self.session_stop_wall_clock

        # Calculate wall_clock_duration from monotonic elapsed time to avoid clock adjustment issues.
        if self.session_start_monotonic is not None and self.session_stop_monotonic is not None:
            monotonic_duration = self.session_stop_monotonic - self.session_start_monotonic
        else:
            monotonic_duration = (stop - start).total_seconds() if start and stop else 0.0

        audio_duration = sum(s.audio_duration_seconds for s in self.segments_metadata)

        if is_failed:
            status = "failed"
        elif is_interrupted:
            status = "interrupted"
        else:
            status = "completed"

        meta = SessionMetadata(
            session_id=self.session_id,
            session_short_id=self.session_short_id,
            recording_mode=self.config.recording_mode,
            channel_name=self.config.mumble_channel,
            session_started_at_local=start.strftime("%Y-%m-%d %H:%M:%S") if start else "",
            session_started_at_utc=start.astimezone(timezone.utc).strftime(
                "%Y-%m-%d %H:%M:%S UTC"
            ) if start else "",
            session_stopped_at_local=stop.strftime("%Y-%m-%d %H:%M:%S") if stop else "",
            session_stopped_at_utc=stop.astimezone(timezone.utc).strftime(
                "%Y-%m-%d %H:%M:%S UTC"
            ) if stop else "",
            wall_clock_duration_seconds=monotonic_duration,
            audio_duration_seconds=audio_duration,
            segment_duration_seconds=self.config.segment_duration_seconds,
            segments=self.segments_metadata,
            status=status,
            stop_reason=self.stop_reason,
            planned_duration_seconds=self.config.recording_seconds,
            recording_group_id=self.recording_group_id,
            reconnect_attempt=self.reconnect_attempt,
            parent_session_id=self.parent_session_id,
        )

        metadata_file = os.path.join(
            self.config.output_dir, f"{self.session_id}_session_metadata.json"
        )
        meta.save_to_file(metadata_file)
        self.metadata_path = metadata_file
        self.status = status

        logger.info(f"Session metadata saved: {metadata_file}")

        logger.info("=== Session Summary ===")
        logger.info(f"Session ID: {self.session_id}")
        logger.info(f"Segments: {len(self.segments_metadata)}")
        logger.info(f"Wall-clock duration: {monotonic_duration:.1f}s")
        logger.info(f"Total audio duration: {audio_duration:.1f}s")
        logger.info(f"Status: {status}")
        logger.info(f"Stop reason: {self.stop_reason}")
        logger.info(f"Mode: {self.config.recording_mode}")
        logger.info("=" * 30)

