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


if __name__ == "__main__":
    unittest.main()
