"""Tests for filename generation."""
import unittest
from datetime import datetime

from mumble_recorder.filenames import (
    generate_session_id,
    channel_to_slug,
    segment_filename,
)


class TestFilenames(unittest.TestCase):
    """Test filename generation."""

    def test_generate_session_id(self):
        """Test session ID generation."""
        session_id, session_short_id = generate_session_id()
        self.assertTrue(session_id.startswith("s"))
        self.assertEqual(len(session_id), 9)  # 's' + 8 hex chars
        self.assertEqual(session_short_id, session_id[:5])
        self.assertEqual(len(session_short_id), 5)

    def test_generate_session_id_uniqueness(self):
        """Test that session IDs are unique."""
        id1, _ = generate_session_id()
        id2, _ = generate_session_id()
        self.assertNotEqual(id1, id2)

    def test_channel_to_slug(self):
        """Test channel name to slug conversion."""
        test_cases = [
            ("Test", "test"),
            ("General", "general"),
            ("Random Chat", "random_chat"),
            ("Test-Channel", "test_channel"),
            ("Test_Channel", "test_channel"),
            ("Test  Channel", "test_channel"),
            ("Test Channel!!!", "test_channel"),
            ("_Leading", "leading"),
            ("Trailing_", "trailing"),
            ("", "channel"),  # Empty becomes default
        ]
        for channel_name, expected_slug in test_cases:
            with self.subTest(channel_name=channel_name):
                self.assertEqual(channel_to_slug(channel_name), expected_slug)

    def test_segment_filename(self):
        """Test segment filename generation."""
        session_start = datetime(2026, 6, 15, 10, 27, 13)
        session_short_id = "s7f3a"
        channel_slug = "test"
        segment_index = 0
        segment_start = datetime(2026, 6, 15, 10, 57, 13)

        filename = segment_filename(
            session_start,
            session_short_id,
            channel_slug,
            segment_index,
            segment_start,
        )

        expected = "20260615-102713_test_s7f3a_seg-20260615-105713.wav"
        self.assertEqual(filename, expected)

    def test_segment_filename_multiple_segments(self):
        """Test filename generation for multiple segments."""
        session_start = datetime(2026, 6, 15, 10, 0, 0)
        session_short_id = "s1234"
        channel_slug = "main"

        filenames = []
        for i in range(3):
            segment_start = datetime(2026, 6, 15, 10, i * 10, 0)
            filename = segment_filename(
                session_start,
                session_short_id,
                channel_slug,
                i,
                segment_start,
            )
            filenames.append(filename)

        # All should have same session prefix
        self.assertTrue(all(f.startswith("20260615-100000_main_s1234_seg-") for f in filenames))

        # All should end with .wav
        self.assertTrue(all(f.endswith(".wav") for f in filenames))

        # They should be different
        self.assertEqual(len(set(filenames)), 3)


if __name__ == "__main__":
    unittest.main()
