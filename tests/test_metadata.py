"""Tests for metadata serialization and status tracking."""
import json
import os
import shutil
import tempfile
import unittest

from mumble_recorder.metadata import SessionMetadata, SegmentMetadata


class TestMetadataStatus(unittest.TestCase):
    """Test metadata status and stop_reason fields."""

    def test_session_metadata_completed_status(self):
        """Test SessionMetadata with completed status."""
        meta = SessionMetadata(
            session_id="sess-123",
            session_short_id="abc",
            recording_mode="continuous",
            channel_name="General",
            session_started_at_local="2026-01-01 10:00:00",
            session_started_at_utc="2026-01-01 10:00:00 UTC",
            session_stopped_at_local="2026-01-01 10:00:30",
            session_stopped_at_utc="2026-01-01 10:00:30 UTC",
            wall_clock_duration_seconds=30.0,
            audio_duration_seconds=28.5,
            segment_duration_seconds=10,
            segments=[],
            status="completed",
            stop_reason="duration_reached",
            planned_duration_seconds=30,
        )
        self.assertEqual(meta.status, "completed")
        self.assertEqual(meta.stop_reason, "duration_reached")

    def test_session_metadata_interrupted_status(self):
        """Test SessionMetadata with interrupted status."""
        meta = SessionMetadata(
            session_id="sess-123",
            session_short_id="abc",
            recording_mode="continuous",
            channel_name="General",
            session_started_at_local="2026-01-01 10:00:00",
            session_started_at_utc="2026-01-01 10:00:00 UTC",
            session_stopped_at_local="2026-01-01 10:00:15",
            session_stopped_at_utc="2026-01-01 10:00:15 UTC",
            wall_clock_duration_seconds=15.0,
            audio_duration_seconds=14.0,
            segment_duration_seconds=10,
            segments=[],
            status="interrupted",
            stop_reason="signal_sigterm",
            planned_duration_seconds=30,
        )
        self.assertEqual(meta.status, "interrupted")
        self.assertEqual(meta.stop_reason, "signal_sigterm")

    def test_session_metadata_failed_status(self):
        """Test SessionMetadata with failed status."""
        meta = SessionMetadata(
            session_id="sess-123",
            session_short_id="abc",
            recording_mode="continuous",
            channel_name="General",
            session_started_at_local="",
            session_started_at_utc="",
            session_stopped_at_local="2026-01-01 10:00:05",
            session_stopped_at_utc="2026-01-01 10:00:05 UTC",
            wall_clock_duration_seconds=0.0,
            audio_duration_seconds=0.0,
            segment_duration_seconds=10,
            segments=[],
            status="failed",
            stop_reason="mumble_connection_failed",
            planned_duration_seconds=30,
        )
        self.assertEqual(meta.status, "failed")
        self.assertEqual(meta.stop_reason, "mumble_connection_failed")

    def test_session_metadata_to_dict_includes_status(self):
        """Test that to_dict() includes status and stop_reason."""
        meta = SessionMetadata(
            session_id="sess-123",
            session_short_id="abc",
            recording_mode="continuous",
            channel_name="General",
            session_started_at_local="2026-01-01 10:00:00",
            session_started_at_utc="2026-01-01 10:00:00 UTC",
            session_stopped_at_local="2026-01-01 10:00:30",
            session_stopped_at_utc="2026-01-01 10:00:30 UTC",
            wall_clock_duration_seconds=30.0,
            audio_duration_seconds=28.5,
            segment_duration_seconds=10,
            segments=[],
            status="interrupted",
            stop_reason="keyboard_interrupt",
            planned_duration_seconds=30,
        )
        d = meta.to_dict()
        self.assertEqual(d["status"], "interrupted")
        self.assertEqual(d["stop_reason"], "keyboard_interrupt")
        self.assertEqual(d["planned_duration_seconds"], 30)

    def test_session_metadata_json_roundtrip(self):
        """Test that metadata can be serialized to JSON and parsed back."""
        tmp = tempfile.mkdtemp()
        try:
            meta = SessionMetadata(
                session_id="sess-123",
                session_short_id="abc",
                recording_mode="received_audio_only",
                channel_name="General",
                session_started_at_local="2026-01-01 10:00:00",
                session_started_at_utc="2026-01-01 10:00:00 UTC",
                session_stopped_at_local="2026-01-01 10:00:15",
                session_stopped_at_utc="2026-01-01 10:00:15 UTC",
                wall_clock_duration_seconds=15.0,
                audio_duration_seconds=12.3,
                segment_duration_seconds=10,
                segments=[],
                status="interrupted",
                stop_reason="signal_sigint",
                planned_duration_seconds=30,
            )

            path = os.path.join(tmp, "test_metadata.json")
            meta.save_to_file(path)

            with open(path) as f:
                data = json.load(f)

            self.assertEqual(data["session_id"], "sess-123")
            self.assertEqual(data["status"], "interrupted")
            self.assertEqual(data["stop_reason"], "signal_sigint")
            self.assertEqual(data["planned_duration_seconds"], 30)
            self.assertAlmostEqual(data["audio_duration_seconds"], 12.3, places=1)
        finally:
            shutil.rmtree(tmp)

    def test_all_stop_reasons_are_valid(self):
        """Test all documented stop_reason values can be used."""
        stop_reasons = [
            "duration_reached",
            "signal_sigterm",
            "signal_sigint",
            "keyboard_interrupt",
            "writer_error",
            "mumble_connection_failed",
            "channel_join_failed",
        ]

        for reason in stop_reasons:
            meta = SessionMetadata(
                session_id=f"sess-{reason}",
                session_short_id="x",
                recording_mode="continuous",
                channel_name="General",
                session_started_at_local="2026-01-01 10:00:00",
                session_started_at_utc="2026-01-01 10:00:00 UTC",
                session_stopped_at_local="2026-01-01 10:00:10",
                session_stopped_at_utc="2026-01-01 10:00:10 UTC",
                wall_clock_duration_seconds=10.0,
                audio_duration_seconds=9.0,
                segment_duration_seconds=10,
                segments=[],
                stop_reason=reason,
            )
            self.assertEqual(meta.stop_reason, reason)


if __name__ == "__main__":
    unittest.main()
