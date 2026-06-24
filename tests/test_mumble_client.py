"""Tests for mumble_client connection and timeout handling."""
import sys
import unittest
import queue
from unittest.mock import MagicMock, patch

# Mock pymumble_py3 before importing mumble_client
sys.modules['pymumble_py3'] = MagicMock()

from mumble_recorder.mumble_client import wait_until_ready, connect_to_mumble, get_channel_users


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


class TestGetChannelUsers(unittest.TestCase):
    """Test get_channel_users channel user extraction."""

    def test_get_channel_users_returns_list_of_names(self):
        """get_channel_users returns list of user names from channel."""
        mock_channel = MagicMock()
        # Create user mocks that have only a name attribute (not get_property or get methods)
        mock_user1 = MagicMock(spec=['name'])
        mock_user1.name = "Alice"
        mock_user2 = MagicMock(spec=['name'])
        mock_user2.name = "Bob"
        # get_users() returns a dict, not a list
        mock_channel.get_users.return_value = {"user1": mock_user1, "user2": mock_user2}

        result = get_channel_users(mock_channel)

        # Result should contain both names (order may vary)
        self.assertEqual(set(result), {"Alice", "Bob"})

    def test_get_channel_users_returns_empty_list_on_failure(self):
        """get_channel_users returns empty list on lookup failure."""
        mock_channel = MagicMock()
        mock_channel.get_users.side_effect = Exception("Channel lookup failed")

        result = get_channel_users(mock_channel)

        self.assertEqual(result, [])

    def test_get_channel_users_handles_none_channel(self):
        """get_channel_users returns empty list when channel is None."""
        result = get_channel_users(None)

        self.assertEqual(result, [])

    def test_get_channel_users_handles_empty_dict(self):
        """get_channel_users returns empty list when get_users returns empty dict."""
        mock_channel = MagicMock()
        mock_channel.get_users.return_value = {}

        result = get_channel_users(mock_channel)

        self.assertEqual(result, [])

    def test_get_channel_users_handles_attribute_error(self):
        """get_channel_users returns empty list on AttributeError."""
        mock_channel = MagicMock()
        mock_channel.get_users.side_effect = AttributeError("'NoneType' has no attribute 'name'")

        result = get_channel_users(mock_channel)

        self.assertEqual(result, [])

    def test_get_channel_users_skips_none_users(self):
        """get_channel_users skips None values in user dict."""
        mock_channel = MagicMock()
        mock_user1 = MagicMock(spec=['name'])
        mock_user1.name = "Alice"
        mock_channel.get_users.return_value = {"user1": mock_user1, "user2": None}

        result = get_channel_users(mock_channel)

        self.assertEqual(result, ["Alice"])


if __name__ == "__main__":
    unittest.main()
