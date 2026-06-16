"""Tests for graceful shutdown and signal handling."""
import signal
import tempfile
import unittest
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



if __name__ == "__main__":
    unittest.main()

