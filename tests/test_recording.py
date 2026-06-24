"""Tests for graceful shutdown and signal handling."""
import signal
import tempfile
import unittest
import time
import json
from pathlib import Path
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone

from mumble_recorder.config import Config
from mumble_recorder.recording import Recorder


def _mock_config(output_dir="/tmp"):
    """Create a properly configured mock Config."""
    config = MagicMock(spec=Config)
    config.mumble_channel = "General"
    config.mumble_host = "localhost"
    config.mumble_port = 64738
    config.mumble_username = "test"
    config.mumble_password = ""
    config.mumble_connect_timeout_seconds = 5
    config.recording_seconds = 30
    config.segment_duration_seconds = 10
    config.recording_mode = "continuous"
    config.output_dir = output_dir
    config.reconnect_enabled = True
    config.reconnect_max_attempts = 10
    config.reconnect_delay_seconds = 5
    return config


class TestRecorderSignalHandling(unittest.TestCase):
    """Test signal handler and shutdown state."""

    def test_signal_handler_sigterm_sets_reason(self):
        """Test SIGTERM handler sets stop_reason."""
        config = _mock_config()
        recorder = Recorder(config)
        self.assertEqual(recorder.stop_reason, "duration_reached")

        recorder._signal_handler(signal.SIGTERM, None)
        self.assertEqual(recorder.stop_reason, "signal_sigterm")
        self.assertTrue(recorder.stop_event.is_set())

    def test_signal_handler_sigint_sets_reason(self):
        """Test SIGINT handler sets stop_reason."""
        config = _mock_config()
        recorder = Recorder(config)
        self.assertEqual(recorder.stop_reason, "duration_reached")

        recorder._signal_handler(signal.SIGINT, None)
        self.assertEqual(recorder.stop_reason, "signal_sigint")
        self.assertTrue(recorder.stop_event.is_set())

    def test_initial_stop_reason_is_duration_reached(self):
        """Test initial stop_reason is duration_reached."""
        config = _mock_config()
        recorder = Recorder(config)
        self.assertEqual(recorder.stop_reason, "duration_reached")
        self.assertFalse(recorder.stop_event.is_set())

    def test_writer_error_sets_stop_reason(self):
        """Test writer thread error sets stop_reason to writer_error."""
        config = _mock_config()
        recorder = Recorder(config)
        recorder._writer_thread_worker()
        self.assertEqual(recorder.stop_reason, "writer_error")

    def test_connection_failure_sets_reason(self):
        """Test Mumble connection failure sets stop_reason."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = _mock_config(output_dir=tmpdir)
            recorder = Recorder(config)

            with patch("mumble_recorder.recording.connect_to_mumble") as mock_connect:
                mock_connect.return_value = None
                result = recorder.run()

            self.assertFalse(result)
            self.assertEqual(recorder.stop_reason, "mumble_connection_failed")

    def test_channel_join_failure_sets_reason(self):
        """Test channel join failure sets stop_reason."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = _mock_config(output_dir=tmpdir)
            config.mumble_channel = "NonExistent"
            recorder = Recorder(config)

            mock_mumble = MagicMock()
            mock_mumble.channels = {}

            with patch("mumble_recorder.recording.connect_to_mumble") as mock_connect:
                with patch("mumble_recorder.recording.find_and_join_channel") as mock_join:
                    mock_connect.return_value = mock_mumble
                    mock_join.return_value = None
                    result = recorder.run()

            self.assertFalse(result)
            self.assertEqual(recorder.stop_reason, "channel_join_failed")

    def test_session_metadata_includes_status_field(self):
        """Test that session metadata receives status field."""
        config = _mock_config()
        recorder = Recorder(config)
        recorder.session_start_wall_clock = datetime.now(timezone.utc).astimezone()
        recorder.session_stop_wall_clock = datetime.now(timezone.utc).astimezone()
        recorder.session_start_monotonic = 100.0
        recorder.session_stop_monotonic = 115.0
        recorder.stop_reason = "signal_sigterm"

        with patch("mumble_recorder.recording.SessionMetadata") as MockMeta:
            recorder._create_session_metadata(is_failed=False, is_interrupted=True)

            MockMeta.assert_called_once()
            call_kwargs = MockMeta.call_args.kwargs
            self.assertEqual(call_kwargs["status"], "interrupted")
            self.assertEqual(call_kwargs["stop_reason"], "signal_sigterm")

    def test_failed_session_metadata_has_failed_status(self):
        """Test that failed session gets failed status."""
        config = _mock_config()
        recorder = Recorder(config)
        recorder.session_start_wall_clock = datetime.now(timezone.utc).astimezone()
        recorder.session_stop_wall_clock = datetime.now(timezone.utc).astimezone()
        recorder.session_start_monotonic = 100.0
        recorder.session_stop_monotonic = 105.0
        recorder.stop_reason = "writer_error"

        with patch("mumble_recorder.recording.SessionMetadata") as MockMeta:
            recorder._create_session_metadata(is_failed=True, is_interrupted=False)

            MockMeta.assert_called_once()
            call_kwargs = MockMeta.call_args.kwargs
            self.assertEqual(call_kwargs["status"], "failed")
            self.assertEqual(call_kwargs["stop_reason"], "writer_error")

    def test_completed_session_metadata_has_completed_status(self):
        """Test that normal session gets completed status."""
        config = _mock_config()
        recorder = Recorder(config)
        recorder.session_start_wall_clock = datetime.now(timezone.utc).astimezone()
        recorder.session_stop_wall_clock = datetime.now(timezone.utc).astimezone()
        recorder.session_start_monotonic = 100.0
        recorder.session_stop_monotonic = 130.0
        recorder.stop_reason = "duration_reached"

        with patch("mumble_recorder.recording.SessionMetadata") as MockMeta:
            recorder._create_session_metadata(is_failed=False, is_interrupted=False)

            MockMeta.assert_called_once()
            call_kwargs = MockMeta.call_args.kwargs
            self.assertEqual(call_kwargs["status"], "completed")
            self.assertEqual(call_kwargs["stop_reason"], "duration_reached")

    def test_mumble_disconnected_session_metadata_has_interrupted_status(self):
        """Test that mumble_disconnected produces interrupted status."""
        config = _mock_config()
        recorder = Recorder(config)
        recorder.session_start_wall_clock = datetime.now(timezone.utc).astimezone()
        recorder.session_stop_wall_clock = datetime.now(timezone.utc).astimezone()
        recorder.session_start_monotonic = 100.0
        recorder.session_stop_monotonic = 115.0
        recorder.stop_reason = "mumble_disconnected"

        with patch("mumble_recorder.recording.SessionMetadata") as MockMeta:
            recorder._create_session_metadata(is_failed=False, is_interrupted=True)

            MockMeta.assert_called_once()
            call_kwargs = MockMeta.call_args.kwargs
            self.assertEqual(call_kwargs["status"], "interrupted")
            self.assertEqual(call_kwargs["stop_reason"], "mumble_disconnected")


class TestRecorderStatusExposure(unittest.TestCase):
    """Test that recorder exposes status, stop_reason, and metadata_path."""

    def test_recorder_exposes_status_after_metadata_creation(self):
        """Test that recorder.status is set after _create_session_metadata."""
        config = _mock_config()
        recorder = Recorder(config)
        self.assertIsNone(recorder.status)

        recorder.session_start_wall_clock = datetime.now(timezone.utc).astimezone()
        recorder.session_stop_wall_clock = datetime.now(timezone.utc).astimezone()
        recorder.session_start_monotonic = 100.0
        recorder.session_stop_monotonic = 130.0

        with tempfile.TemporaryDirectory() as tmpdir:
            recorder.config.output_dir = tmpdir
            recorder._create_session_metadata(is_failed=False, is_interrupted=False)

            self.assertEqual(recorder.status, "completed")
            self.assertIsNotNone(recorder.metadata_path)

    def test_recorder_exposes_metadata_path(self):
        """Test that recorder.metadata_path is set correctly."""
        config = _mock_config()
        recorder = Recorder(config)
        self.assertIsNone(recorder.metadata_path)

        recorder.session_start_wall_clock = datetime.now(timezone.utc).astimezone()
        recorder.session_stop_wall_clock = datetime.now(timezone.utc).astimezone()
        recorder.session_start_monotonic = 100.0
        recorder.session_stop_monotonic = 130.0

        with tempfile.TemporaryDirectory() as tmpdir:
            recorder.config.output_dir = tmpdir
            recorder._create_session_metadata(is_failed=False, is_interrupted=False)

            self.assertIsNotNone(recorder.metadata_path)
            self.assertTrue(recorder.metadata_path.endswith("_session_metadata.json"))

    def test_recorder_stop_reason_preserved(self):
        """Test that recorder.stop_reason is always available."""
        config = _mock_config()
        recorder = Recorder(config)
        self.assertEqual(recorder.stop_reason, "duration_reached")

        recorder._signal_handler(signal.SIGTERM, None)
        self.assertEqual(recorder.stop_reason, "signal_sigterm")

    def test_mumble_disconnected_sets_stop_reason(self):
        """Test _handle_mumble_disconnect sets stop_reason."""
        config = _mock_config()
        recorder = Recorder(config)
        self.assertEqual(recorder.stop_reason, "duration_reached")
        self.assertFalse(recorder.stop_event.is_set())

        recorder._handle_mumble_disconnect()

        self.assertEqual(recorder.stop_reason, "mumble_disconnected")
        self.assertTrue(recorder.stop_event.is_set())

    def test_mumble_disconnected_accepts_callback_arguments(self):
        """Test _handle_mumble_disconnect accepts callback arguments."""
        config = _mock_config()
        recorder = Recorder(config)

        recorder._handle_mumble_disconnect(object(), reason="test")

        self.assertEqual(recorder.stop_reason, "mumble_disconnected")
        self.assertTrue(recorder.stop_event.is_set())

    def test_mumble_disconnected_does_not_override_other_reasons(self):
        """Test that _handle_mumble_disconnect doesn't override explicit stop reasons."""
        config = _mock_config()
        recorder = Recorder(config)
        recorder.stop_reason = "signal_sigterm"

        recorder._handle_mumble_disconnect()

        # Should not override signal_sigterm
        self.assertEqual(recorder.stop_reason, "signal_sigterm")
        self.assertTrue(recorder.stop_event.is_set())


class TestCLIExitLogic(unittest.TestCase):
    """Test CLI exit code and message logic."""

    def test_completed_status_exit_zero_info_message(self):
        """Test completed status returns exit 0 and info log."""
        from mumble_recorder.recording_cli import get_cli_exit_info

        exit_code, message, is_error = get_cli_exit_info("completed", True)
        self.assertEqual(exit_code, 0)
        self.assertEqual(message, "Recording completed successfully")
        self.assertFalse(is_error)

    def test_interrupted_status_exit_zero_info_message(self):
        """Test interrupted status returns exit 0 and info log."""
        from mumble_recorder.recording_cli import get_cli_exit_info

        exit_code, message, is_error = get_cli_exit_info("interrupted", False)
        self.assertEqual(exit_code, 0)
        self.assertEqual(message, "Recording interrupted and finalized cleanly")
        self.assertFalse(is_error)

    def test_failed_status_exit_one_error_message(self):
        """Test failed status returns exit 1 and error log."""
        from mumble_recorder.recording_cli import get_cli_exit_info

        exit_code, message, is_error = get_cli_exit_info("failed", False)
        self.assertEqual(exit_code, 1)
        self.assertEqual(message, "Recording failed")
        self.assertTrue(is_error)

    def test_none_status_success_true_exits_zero(self):
        """Test None status with success=True acts as completed."""
        from mumble_recorder.recording_cli import get_cli_exit_info

        exit_code, message, is_error = get_cli_exit_info(None, True)
        self.assertEqual(exit_code, 0)
        self.assertEqual(message, "Recording completed successfully")
        self.assertFalse(is_error)

    def test_none_status_success_false_exits_one(self):
        """Test None status with success=False acts as failed."""
        from mumble_recorder.recording_cli import get_cli_exit_info

        exit_code, message, is_error = get_cli_exit_info(None, False)
        self.assertEqual(exit_code, 1)
        self.assertEqual(message, "Recording failed")
        self.assertTrue(is_error)

    def test_status_overrides_success_boolean(self):
        """Test that explicit status takes precedence over success."""
        from mumble_recorder.recording_cli import get_cli_exit_info

        # Even if success=True, if status is failed, should exit 1
        exit_code, message, is_error = get_cli_exit_info("failed", True)
        self.assertEqual(exit_code, 1)
        self.assertTrue(is_error)

        # Even if success=False, if status is completed, should exit 0
        exit_code, message, is_error = get_cli_exit_info("completed", False)
        self.assertEqual(exit_code, 0)
        self.assertFalse(is_error)



class TestReconnectConfig(unittest.TestCase):
    """Test reconnect configuration defaults."""

    def test_reconnect_enabled_default_true(self):
        """Test reconnect_enabled defaults to True."""
        from mumble_recorder.config import Config
        self.assertTrue(Config.__dataclass_fields__["reconnect_enabled"].default)

    def test_reconnect_max_attempts_default_10(self):
        """Test RECONNECT_MAX_ATTEMPTS defaults to 10."""
        from mumble_recorder.config import Config
        config = MagicMock(spec=Config)
        self.assertEqual(Config.__dataclass_fields__["reconnect_max_attempts"].default, 10)

    def test_reconnect_delay_seconds_default_5(self):
        """Test RECONNECT_DELAY_SECONDS defaults to 5."""
        from mumble_recorder.config import Config
        self.assertEqual(Config.__dataclass_fields__["reconnect_delay_seconds"].default, 5)


class TestMetadataLinkageFields(unittest.TestCase):
    """Test reconnect metadata linkage fields."""

    def test_recorder_accepts_recording_group_id(self):
        """Test Recorder accepts recording_group_id parameter."""
        config = _mock_config()
        group_id = "test-group-123"
        recorder = Recorder(config, recording_group_id=group_id)
        self.assertEqual(recorder.recording_group_id, group_id)

    def test_recorder_accepts_reconnect_attempt(self):
        """Test Recorder accepts reconnect_attempt parameter."""
        config = _mock_config()
        recorder = Recorder(config, reconnect_attempt=2)
        self.assertEqual(recorder.reconnect_attempt, 2)

    def test_recorder_accepts_parent_session_id(self):
        """Test Recorder accepts parent_session_id parameter."""
        config = _mock_config()
        parent_id = "parent-session-abc"
        recorder = Recorder(config, parent_session_id=parent_id)
        self.assertEqual(recorder.parent_session_id, parent_id)

    def test_initial_session_has_zero_reconnect_attempt(self):
        """Test initial session has reconnect_attempt=0."""
        config = _mock_config()
        recorder = Recorder(config)
        self.assertEqual(recorder.reconnect_attempt, 0)

    def test_initial_session_has_no_parent_session_id(self):
        """Test initial session has parent_session_id=None."""
        config = _mock_config()
        recorder = Recorder(config)
        self.assertIsNone(recorder.parent_session_id)

    def test_metadata_includes_linkage_fields(self):
        """Test session metadata includes recording_group_id, reconnect_attempt, parent_session_id."""
        config = _mock_config()
        group_id = "test-group-456"
        parent_id = "parent-session-def"
        recorder = Recorder(config, recording_group_id=group_id, reconnect_attempt=1, parent_session_id=parent_id)
        recorder.session_start_wall_clock = datetime.now(timezone.utc).astimezone()
        recorder.session_stop_wall_clock = datetime.now(timezone.utc).astimezone()
        recorder.session_start_monotonic = 100.0
        recorder.session_stop_monotonic = 115.0

        with tempfile.TemporaryDirectory() as tmpdir:
            recorder.config.output_dir = tmpdir
            recorder._create_session_metadata()

            self.assertIsNotNone(recorder.metadata_path)
            with open(recorder.metadata_path, "r") as f:
                import json
                meta_dict = json.load(f)
                self.assertEqual(meta_dict["recording_group_id"], group_id)
                self.assertEqual(meta_dict["reconnect_attempt"], 1)
                self.assertEqual(meta_dict["parent_session_id"], parent_id)


class TestRemainingDurationCalculation(unittest.TestCase):
    """Test remaining duration calculation for reconnect."""

    def test_remaining_duration_from_cli_start(self):
        """Test remaining duration is calculated from CLI start."""
        from mumble_recorder.recording_cli import calculate_remaining_duration
        cli_start = time.monotonic()
        time.sleep(0.1)
        remaining = calculate_remaining_duration(cli_start, 60)
        self.assertLess(remaining, 60)
        self.assertGreater(remaining, 58)

    def test_remaining_duration_clamped_to_zero(self):
        """Test remaining duration is never negative."""
        from mumble_recorder.recording_cli import calculate_remaining_duration
        cli_start = time.monotonic()
        time.sleep(0.2)
        remaining = calculate_remaining_duration(cli_start, 0)
        self.assertEqual(remaining, 0)


class TestReconnectDecision(unittest.TestCase):
    """Test reconnect decision logic."""

    def test_reconnect_true_for_interrupted_mumble_disconnected(self):
        """Test should_reconnect returns True for interrupted/mumble_disconnected."""
        from mumble_recorder.recording_cli import should_reconnect
        config = _mock_config()
        recorder = Recorder(config)
        recorder.status = "interrupted"
        recorder.stop_reason = "mumble_disconnected"
        result = should_reconnect(recorder, 0, 10, 60)
        self.assertTrue(result)

    def test_reconnect_false_for_completed(self):
        """Test should_reconnect returns False for completed."""
        from mumble_recorder.recording_cli import should_reconnect
        config = _mock_config()
        recorder = Recorder(config)
        recorder.status = "completed"
        recorder.stop_reason = "duration_reached"
        result = should_reconnect(recorder, 0, 10, 60)
        self.assertFalse(result)

    def test_reconnect_false_for_failed(self):
        """Test should_reconnect returns False for failed."""
        from mumble_recorder.recording_cli import should_reconnect
        config = _mock_config()
        recorder = Recorder(config)
        recorder.status = "failed"
        recorder.stop_reason = "mumble_connection_failed"
        result = should_reconnect(recorder, 0, 10, 60)
        self.assertFalse(result)

    def test_reconnect_false_when_reconnect_disabled(self):
        """Test should_reconnect returns False when reconnect_enabled=False."""
        from mumble_recorder.recording_cli import should_reconnect
        config = _mock_config()
        config.reconnect_enabled = False
        recorder = Recorder(config)
        recorder.status = "interrupted"
        recorder.stop_reason = "mumble_disconnected"
        result = should_reconnect(recorder, 0, 10, 60)
        self.assertFalse(result)

    def test_reconnect_false_when_attempts_exhausted(self):
        """Test should_reconnect returns False when max attempts reached."""
        from mumble_recorder.recording_cli import should_reconnect
        config = _mock_config()
        config.reconnect_max_attempts = 3
        recorder = Recorder(config)
        recorder.status = "interrupted"
        recorder.stop_reason = "mumble_disconnected"
        result = should_reconnect(recorder, 3, 10, 60)
        self.assertFalse(result)

    def test_reconnect_false_when_no_time_remaining(self):
        """Test should_reconnect returns False when no time remaining."""
        from mumble_recorder.recording_cli import should_reconnect
        config = _mock_config()
        recorder = Recorder(config)
        recorder.status = "interrupted"
        recorder.stop_reason = "mumble_disconnected"
        result = should_reconnect(recorder, 0, 60, 60)
        self.assertFalse(result)

    def test_reconnect_false_for_signal_sigterm(self):
        """Test should_reconnect returns False for signal_sigterm."""
        from mumble_recorder.recording_cli import should_reconnect
        config = _mock_config()
        recorder = Recorder(config)
        recorder.status = "interrupted"
        recorder.stop_reason = "signal_sigterm"
        result = should_reconnect(recorder, 0, 10, 60)
        self.assertFalse(result)

    def test_reconnect_false_for_keyboard_interrupt(self):
        """Test should_reconnect returns False for keyboard_interrupt."""
        from mumble_recorder.recording_cli import should_reconnect
        config = _mock_config()
        recorder = Recorder(config)
        recorder.status = "interrupted"
        recorder.stop_reason = "keyboard_interrupt"
        result = should_reconnect(recorder, 0, 10, 60)
        self.assertFalse(result)


class TestUnlimitedDuration(unittest.TestCase):
    """Test unlimited duration support (RECORDING_SECONDS=0)."""

    def test_unlimited_duration_config_allows_zero(self):
        """Test that RECORDING_SECONDS=0 is valid and means unlimited."""
        config = _mock_config()
        config.recording_seconds = 0
        self.assertEqual(config.recording_seconds, 0)

    def test_writer_loop_skips_duration_check_when_zero(self):
        """Test writer loop doesn't stop due to duration when recording_seconds=0."""
        config = _mock_config()
        config.recording_seconds = 0
        recorder = Recorder(config)
        recorder.session_start_monotonic = time.monotonic()
        recorder.stop_event.set()

        recorder._writer_loop()
        self.assertEqual(recorder.stop_reason, "duration_reached")

    def test_main_loop_respects_unlimited_duration(self):
        """Test main recording loop doesn't check duration when recording_seconds=0."""
        config = _mock_config()
        config.recording_seconds = 0
        recorder = Recorder(config)

        start = time.monotonic()
        recorder.session_start_monotonic = start

        elapsed = time.monotonic() - recorder.session_start_monotonic
        if config.recording_seconds > 0:
            should_stop = elapsed >= config.recording_seconds
        else:
            should_stop = False

        self.assertFalse(should_stop)


class TestRuntimeStatusLive(unittest.TestCase):
    """Test runtime status calculation for live talk time."""

    def test_runtime_status_includes_active_segment_audio_duration(self):
        """Test that runtime status includes current active segment's audio duration."""
        from mumble_recorder.wav_writer import SegmentWriter
        config = _mock_config()
        with tempfile.TemporaryDirectory() as tmpdir:
            config.output_dir = tmpdir
            recorder = Recorder(config)
            recorder.session_start_monotonic = time.monotonic()
            recorder.session_start_wall_clock = datetime.now(timezone.utc).astimezone()

            # Create a mock active segment with some audio
            mock_segment = MagicMock(spec=SegmentWriter)
            mock_segment.get_accumulated_audio_duration.return_value = 5.5
            recorder.current_segment = mock_segment

            # Add finalized segments
            from mumble_recorder.metadata import SegmentMetadata
            recorder.segments_metadata.append(SegmentMetadata(
                segment_index=0,
                segment_started_at_local="2026-01-01 12:00:00",
                segment_started_at_utc="2026-01-01 12:00:00 UTC",
                segment_stopped_at_local="2026-01-01 12:00:10",
                segment_stopped_at_utc="2026-01-01 12:00:10 UTC",
                wall_clock_duration_seconds=10.0,
                audio_duration_seconds=3.0,
                file_name="test.wav",
                file_path="/tmp/test.wav",
                size_bytes=1000,
            ))

            recorder._write_runtime_status(running=True)

            runtime_file = Path(tmpdir) / "recorder_runtime_status.json"
            self.assertTrue(runtime_file.exists())

            with open(runtime_file, "r") as f:
                status = json.load(f)

            # Audio duration should include finalized (3.0) + active (5.5) = 8.5
            self.assertEqual(status["audio_duration_seconds"], 8.5)
            self.assertTrue(status["running"])

    def test_final_runtime_status_remains_in_file(self):
        """Test that final runtime status file persists with running=false."""
        config = _mock_config()
        with tempfile.TemporaryDirectory() as tmpdir:
            config.output_dir = tmpdir
            recorder = Recorder(config)
            recorder.session_start_monotonic = time.monotonic()
            recorder.session_start_wall_clock = datetime.now(timezone.utc).astimezone()

            # Write initial runtime status
            recorder._write_runtime_status(running=True)
            runtime_file = Path(tmpdir) / "recorder_runtime_status.json"
            self.assertTrue(runtime_file.exists())

            # Write final runtime status
            recorder._write_runtime_status(running=False)

            # File should still exist after being marked as not running
            self.assertTrue(runtime_file.exists())

            with open(runtime_file, "r") as f:
                status = json.load(f)

            self.assertFalse(status["running"])
            self.assertIn("session_id", status)
            self.assertIn("audio_duration_seconds", status)


if __name__ == "__main__":
    unittest.main()
