"""Tests for mumble_client connection and timeout handling."""
import sys
import unittest
import queue
from unittest.mock import MagicMock, patch

# Mock pymumble_py3 before importing mumble_client
sys.modules['pymumble_py3'] = MagicMock()

from mumble_recorder.mumble_client import wait_until_ready, connect_to_mumble


class TestWaitUntilReady(unittest.TestCase):
    """Test wait_until_ready timeout handling."""

    def test_wait_until_ready_success(self):
        """wait_until_ready returns True on successful connection."""
        mock_mumble = MagicMock()
        mock_mumble.is_ready.return_value = True

        result = wait_until_ready(mock_mumble, timeout_seconds=5)

        self.assertTrue(result)

    def test_wait_until_ready_timeout_calls_stop(self):
        """wait_until_ready calls mumble.stop() on timeout."""
        mock_mumble = MagicMock()
        mock_mumble.is_ready.side_effect = lambda: None
        with patch('mumble_recorder.mumble_client.queue.Queue') as mock_queue_class:
            mock_queue = MagicMock()
            mock_queue.get.side_effect = queue.Empty
            mock_queue_class.return_value = mock_queue

            result = wait_until_ready(mock_mumble, timeout_seconds=1)

            self.assertFalse(result)
            mock_mumble.stop.assert_called_once()

    def test_wait_until_ready_timeout_catches_stop_exception(self):
        """wait_until_ready handles AttributeError from mumble.stop() gracefully."""
        mock_mumble = MagicMock()
        mock_mumble.is_ready.side_effect = lambda: None
        mock_mumble.stop.side_effect = AttributeError("'NoneType' object has no attribute 'close'")

        with patch('mumble_recorder.mumble_client.queue.Queue') as mock_queue_class:
            mock_queue = MagicMock()
            mock_queue.get.side_effect = queue.Empty
            mock_queue_class.return_value = mock_queue

            # Should not raise; should return False gracefully
            result = wait_until_ready(mock_mumble, timeout_seconds=1)

            self.assertFalse(result)
            mock_mumble.stop.assert_called_once()

    def test_wait_until_ready_connection_error_catches_stop_exception(self):
        """wait_until_ready handles stop exception when check_ready fails."""
        mock_mumble = MagicMock()
        # connection check raises an exception
        mock_mumble.is_ready.side_effect = RuntimeError("Connection failed")
        mock_mumble.stop.side_effect = AttributeError("'NoneType' object has no attribute 'close'")

        with patch('mumble_recorder.mumble_client.queue.Queue') as mock_queue_class:
            mock_queue = MagicMock()
            mock_queue.get.return_value = ("error", RuntimeError("Connection failed"))
            mock_queue_class.return_value = mock_queue

            result = wait_until_ready(mock_mumble, timeout_seconds=5)

            self.assertFalse(result)
            mock_mumble.stop.assert_called_once()

    def test_wait_until_ready_timeout_does_not_propagate_attribute_error(self):
        """wait_until_ready timeout does not propagate AttributeError."""
        mock_mumble = MagicMock()
        mock_mumble.stop.side_effect = AttributeError("'NoneType' object has no attribute 'close'")

        with patch('mumble_recorder.mumble_client.queue.Queue') as mock_queue_class:
            mock_queue = MagicMock()
            mock_queue.get.side_effect = queue.Empty
            mock_queue_class.return_value = mock_queue

            # Should not raise AttributeError
            try:
                result = wait_until_ready(mock_mumble, timeout_seconds=1)
                self.assertFalse(result)
            except AttributeError:
                self.fail("wait_until_ready should not propagate AttributeError from mumble.stop()")


class TestConnectToMumble(unittest.TestCase):
    """Test connect_to_mumble handling."""

    def test_connect_to_mumble_timeout_returns_none(self):
        """connect_to_mumble returns None on timeout."""
        with patch('mumble_recorder.mumble_client.pymumble.Mumble') as mock_mumble_class:
            mock_mumble = MagicMock()
            mock_mumble_class.return_value = mock_mumble
            mock_mumble.start.return_value = None
            mock_mumble.stop.side_effect = AttributeError("'NoneType' object has no attribute 'close'")

            with patch('mumble_recorder.mumble_client.wait_until_ready') as mock_wait:
                mock_wait.return_value = False

                result = connect_to_mumble(
                    host="localhost",
                    port=64738,
                    username="test",
                    password="",
                    timeout_seconds=5,
                )

                self.assertIsNone(result)

    def test_connect_to_mumble_channels_empty_catches_exception(self):
        """connect_to_mumble handles exception from stop() when channels empty."""
        with patch('mumble_recorder.mumble_client.pymumble.Mumble') as mock_mumble_class:
            mock_mumble = MagicMock()
            mock_mumble_class.return_value = mock_mumble
            mock_mumble.start.return_value = None
            mock_mumble.channels = {}  # No channels
            mock_mumble.stop.side_effect = AttributeError("'NoneType' object has no attribute 'close'")

            with patch('mumble_recorder.mumble_client.wait_until_ready') as mock_wait:
                mock_wait.return_value = True

                # Should not raise; should return None gracefully
                result = connect_to_mumble(
                    host="localhost",
                    port=64738,
                    username="test",
                    password="",
                    timeout_seconds=5,
                )

                self.assertIsNone(result)
                mock_mumble.stop.assert_called_once()

    def test_connect_to_mumble_invalid_host_returns_none(self):
        """connect_to_mumble returns None on invalid host."""
        with patch('mumble_recorder.mumble_client.pymumble.Mumble') as mock_mumble_class:
            mock_mumble_class.side_effect = Exception("Cannot resolve host")

            result = connect_to_mumble(
                host="INVALID_HOST_THAT_DOES_NOT_EXIST",
                port=64738,
                username="test",
                password="",
                timeout_seconds=5,
            )

            self.assertIsNone(result)

    def test_connect_to_mumble_successful_connection(self):
        """connect_to_mumble returns mumble client on success."""
        with patch('mumble_recorder.mumble_client.pymumble.Mumble') as mock_mumble_class:
            mock_mumble = MagicMock()
            mock_mumble_class.return_value = mock_mumble
            mock_mumble.channels = {"0": MagicMock()}  # Has channels

            with patch('mumble_recorder.mumble_client.wait_until_ready') as mock_wait:
                mock_wait.return_value = True

                result = connect_to_mumble(
                    host="localhost",
                    port=64738,
                    username="test",
                    password="",
                    timeout_seconds=5,
                )

                self.assertEqual(result, mock_mumble)
                mock_mumble.start.assert_called_once()


if __name__ == "__main__":
    unittest.main()
