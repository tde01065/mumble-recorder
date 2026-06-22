"""Tests for web server."""
import json
import tempfile
import unittest
from pathlib import Path

from mumble_recorder.web import create_app


class TestWebApp(unittest.TestCase):
    """Web server tests."""

    def setUp(self):
        """Set up test app and temp directory."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.app = create_app(self.temp_dir.name)
        self.client = self.app.test_client()

    def tearDown(self):
        """Clean up temp directory."""
        self.temp_dir.cleanup()

    def test_health(self):
        """GET /api/health returns ok."""
        response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)
        self.assertEqual(data["status"], "ok")

    def test_recordings_empty(self):
        """GET /api/recordings returns object when no sessions."""
        response = self.client.get("/api/recordings")
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)
        self.assertEqual(data["items"], [])
        self.assertEqual(data["page"], 1)
        self.assertEqual(data["total_items"], 0)

    def test_recordings_sorted_newest_first(self):
        """GET /api/recordings returns sessions sorted newest first."""
        # Create two metadata files with different timestamps
        session1 = {
            "session_id": "session1",
            "session_short_id": "s1",
            "channel_name": "test",
            "recording_mode": "continuous",
            "session_started_at_local": "2026-01-01 10:00:00",
            "session_stopped_at_local": "2026-01-01 10:01:00",
            "wall_clock_duration_seconds": 60,
            "audio_duration_seconds": 60,
            "segments": [],
        }
        session2 = {
            "session_id": "session2",
            "session_short_id": "s2",
            "channel_name": "test",
            "recording_mode": "continuous",
            "session_started_at_local": "2026-01-02 10:00:00",
            "session_stopped_at_local": "2026-01-02 10:01:00",
            "wall_clock_duration_seconds": 60,
            "audio_duration_seconds": 60,
            "segments": [],
        }

        # Write files (oldest first so newest has later mtime)
        path1 = Path(self.temp_dir.name) / "session1_session_metadata.json"
        with open(path1, "w") as f:
            json.dump(session1, f)

        path2 = Path(self.temp_dir.name) / "session2_session_metadata.json"
        with open(path2, "w") as f:
            json.dump(session2, f)

        response = self.client.get("/api/recordings")
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)
        self.assertEqual(len(data["items"]), 2)
        # Newest first
        self.assertEqual(data["items"][0]["session_id"], "session2")
        self.assertEqual(data["items"][1]["session_id"], "session1")

    def test_recordings_includes_all_fields(self):
        """GET /api/recordings includes required fields."""
        session = {
            "session_id": "test-id",
            "session_short_id": "t1",
            "recording_group_id": "group-1",
            "reconnect_attempt": 1,
            "parent_session_id": "parent-id",
            "channel_name": "test-channel",
            "recording_mode": "continuous",
            "session_started_at_local": "2026-01-01 10:00:00",
            "session_stopped_at_local": "2026-01-01 10:01:00",
            "wall_clock_duration_seconds": 60,
            "audio_duration_seconds": 50,
            "status": "completed",
            "stop_reason": "duration_reached",
            "planned_duration_seconds": 60,
            "segments": [],
        }

        path = Path(self.temp_dir.name) / "test_session_metadata.json"
        with open(path, "w") as f:
            json.dump(session, f)

        response = self.client.get("/api/recordings")
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)
        self.assertEqual(len(data["items"]), 1)

        item = data["items"][0]
        self.assertEqual(item["session_id"], "test-id")
        self.assertEqual(item["session_short_id"], "t1")
        self.assertEqual(item["recording_group_id"], "group-1")
        self.assertEqual(item["reconnect_attempt"], 1)
        self.assertEqual(item["parent_session_id"], "parent-id")
        self.assertEqual(item["channel_name"], "test-channel")
        self.assertEqual(item["recording_mode"], "continuous")
        self.assertEqual(item["wall_clock_duration_seconds"], 60)
        self.assertEqual(item["audio_duration_seconds"], 50)
        self.assertEqual(item["status"], "completed")
        self.assertEqual(item["stop_reason"], "duration_reached")
        self.assertEqual(item["segment_count"], 0)
        self.assertIn("metadata_filename", item)

    def test_recording_detail(self):
        """GET /api/recordings/<session_id> returns full metadata (not list projection)."""
        session = {
            "session_id": "detail-test",
            "session_short_id": "d1",
            "channel_name": "test",
            "recording_mode": "continuous",
            "session_started_at_local": "2026-01-01 10:00:00",
            "session_stopped_at_local": "2026-01-01 10:01:00",
            "wall_clock_duration_seconds": 60,
            "audio_duration_seconds": 60,
            "segments": [
                {
                    "segment_index": 0,
                    "file_name": "test.wav",
                    "size_bytes": 1000,
                }
            ],
            "custom_full_metadata_field": "only-detail",
        }

        path = Path(self.temp_dir.name) / "detail_session_metadata.json"
        with open(path, "w") as f:
            json.dump(session, f)

        response = self.client.get("/api/recordings/detail-test")
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)
        self.assertEqual(data["session_id"], "detail-test")
        self.assertEqual(len(data["segments"]), 1)
        # Verify full metadata is returned, not list projection
        self.assertEqual(data["custom_full_metadata_field"], "only-detail")
        self.assertIn("metadata_filename", data)

    def test_recording_detail_not_found(self):
        """GET /api/recordings/<session_id> returns 404 if missing."""
        response = self.client.get("/api/recordings/nonexistent")
        self.assertEqual(response.status_code, 404)

    def test_file_download_wav(self):
        """GET /api/files/<filename> downloads wav file."""
        # Create a test wav file
        wav_path = Path(self.temp_dir.name) / "test.wav"
        wav_path.write_bytes(b"RIFF\x00\x00\x00\x00WAVEfmt ")

        response = self.client.get("/api/files/test.wav")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, b"RIFF\x00\x00\x00\x00WAVEfmt ")

    def test_file_download_metadata(self):
        """GET /api/files/<filename> downloads metadata json."""
        meta_path = Path(self.temp_dir.name) / "test_session_metadata.json"
        meta_path.write_text('{"test": "data"}')

        response = self.client.get("/api/files/test_session_metadata.json")
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)
        self.assertEqual(data["test"], "data")

    def test_file_download_path_traversal_rejected(self):
        """GET /api/files rejects filenames with .. or path separators."""
        # ".." in filename is rejected by the handler
        response = self.client.get("/api/files/test..wav")
        self.assertEqual(response.status_code, 400)

        # "/" and "\\" in filenames cause Flask routing to not match the route,
        # resulting in 404, which is also secure (files not accessible via subdirs)

    def test_file_download_disallowed_type(self):
        """GET /api/files/<filename> rejects non-wav/metadata files."""
        response = self.client.get("/api/files/test.txt")
        self.assertEqual(response.status_code, 403)

    def test_file_download_not_found(self):
        """GET /api/files/<filename> returns 404 if missing."""
        response = self.client.get("/api/files/nonexistent.wav")
        self.assertEqual(response.status_code, 404)

    def test_malformed_metadata_does_not_crash(self):
        """GET /api/recordings handles malformed metadata gracefully."""
        # Write a valid session first
        valid_session = {
            "session_id": "valid",
            "channel_name": "test",
            "recording_mode": "continuous",
            "session_started_at_local": "2026-01-01 10:00:00",
            "session_stopped_at_local": "2026-01-01 10:01:00",
            "wall_clock_duration_seconds": 60,
            "audio_duration_seconds": 60,
            "segments": [],
        }

        valid_path = Path(self.temp_dir.name) / "valid_session_metadata.json"
        with open(valid_path, "w") as f:
            json.dump(valid_session, f)

        # Write a malformed one
        malformed_path = Path(self.temp_dir.name) / "malformed_session_metadata.json"
        malformed_path.write_text("{invalid json}")

        response = self.client.get("/api/recordings")
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)
        self.assertEqual(len(data["items"]), 2)

        # Find the malformed one
        malformed = [s for s in data["items"] if s["status"] == "error"]
        self.assertEqual(len(malformed), 1)
        self.assertIn("malformed", malformed[0]["stop_reason"])

    def test_api_recordings_response_shape(self):
        """GET /api/recordings returns object with pagination info."""
        session = {
            "session_id": "test1",
            "channel_name": "test",
            "recording_mode": "continuous",
            "session_started_at_local": "2026-01-01 10:00:00",
            "session_stopped_at_local": "2026-01-01 10:01:00",
            "wall_clock_duration_seconds": 60,
            "audio_duration_seconds": 60,
            "segments": [],
        }

        path = Path(self.temp_dir.name) / "test_session_metadata.json"
        with open(path, "w") as f:
            json.dump(session, f)

        response = self.client.get("/api/recordings")
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)

        self.assertIn("items", data)
        self.assertIn("page", data)
        self.assertIn("page_size", data)
        self.assertIn("total_items", data)
        self.assertIn("total_pages", data)
        self.assertIn("filters", data)

        self.assertEqual(data["page"], 1)
        self.assertEqual(data["page_size"], 25)
        self.assertEqual(data["total_items"], 1)
        self.assertEqual(data["total_pages"], 1)
        self.assertEqual(len(data["items"]), 1)

    def test_api_pagination_page_size(self):
        """GET /api/recordings?page_size=2 paginates correctly."""
        for i in range(5):
            session = {
                "session_id": f"session{i}",
                "channel_name": "test",
                "recording_mode": "continuous",
                "session_started_at_local": f"2026-01-{i+1:02d} 10:00:00",
                "session_stopped_at_local": f"2026-01-{i+1:02d} 10:01:00",
                "wall_clock_duration_seconds": 60,
                "audio_duration_seconds": 60,
                "segments": [],
            }
            path = Path(self.temp_dir.name) / f"session{i}_session_metadata.json"
            with open(path, "w") as f:
                json.dump(session, f)

        response = self.client.get("/api/recordings?page_size=2")
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)

        self.assertEqual(data["page_size"], 2)
        self.assertEqual(data["total_items"], 5)
        self.assertEqual(data["total_pages"], 3)
        self.assertEqual(len(data["items"]), 2)

    def test_api_pagination_pages(self):
        """GET /api/recordings?page=2&page_size=2 returns second page."""
        for i in range(5):
            session = {
                "session_id": f"session{i}",
                "channel_name": "test",
                "recording_mode": "continuous",
                "session_started_at_local": f"2026-01-{i+1:02d} 10:00:00",
                "session_stopped_at_local": f"2026-01-{i+1:02d} 10:01:00",
                "wall_clock_duration_seconds": 60,
                "audio_duration_seconds": 60,
                "segments": [],
            }
            path = Path(self.temp_dir.name) / f"session{i}_session_metadata.json"
            with open(path, "w") as f:
                json.dump(session, f)

        response = self.client.get("/api/recordings?page=2&page_size=2")
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)

        self.assertEqual(data["page"], 2)
        self.assertEqual(len(data["items"]), 2)

    def test_api_page_size_capped_at_100(self):
        """GET /api/recordings?page_size=150 caps at 100."""
        response = self.client.get("/api/recordings?page_size=150")
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)
        self.assertEqual(data["page_size"], 100)

    def test_api_page_size_too_small_capped_to_1(self):
        """GET /api/recordings?page_size=0 normalizes to 1."""
        response = self.client.get("/api/recordings?page_size=0")
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)
        self.assertEqual(data["page_size"], 1)

    def test_api_page_too_small_normalized_to_1(self):
        """GET /api/recordings?page=0 normalizes to 1."""
        response = self.client.get("/api/recordings?page=0")
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)
        self.assertEqual(data["page"], 1)

    def test_api_status_filter(self):
        """GET /api/recordings?status=completed filters by status."""
        session1 = {
            "session_id": "session1",
            "status": "completed",
            "channel_name": "test",
            "recording_mode": "continuous",
            "session_started_at_local": "2026-01-01 10:00:00",
            "session_stopped_at_local": "2026-01-01 10:01:00",
            "wall_clock_duration_seconds": 60,
            "audio_duration_seconds": 60,
            "segments": [],
        }
        session2 = {
            "session_id": "session2",
            "status": "interrupted",
            "channel_name": "test",
            "recording_mode": "continuous",
            "session_started_at_local": "2026-01-02 10:00:00",
            "session_stopped_at_local": "2026-01-02 10:01:00",
            "wall_clock_duration_seconds": 60,
            "audio_duration_seconds": 60,
            "segments": [],
        }

        path1 = Path(self.temp_dir.name) / "session1_session_metadata.json"
        with open(path1, "w") as f:
            json.dump(session1, f)

        path2 = Path(self.temp_dir.name) / "session2_session_metadata.json"
        with open(path2, "w") as f:
            json.dump(session2, f)

        response = self.client.get("/api/recordings?status=completed")
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)

        self.assertEqual(data["total_items"], 1)
        self.assertEqual(data["items"][0]["session_id"], "session1")
        self.assertEqual(data["filters"]["status"], "completed")

    def test_api_group_id_filter(self):
        """GET /api/recordings?group_id=grp filters by group."""
        session1 = {
            "session_id": "session1",
            "recording_group_id": "grp-1",
            "channel_name": "test",
            "recording_mode": "continuous",
            "session_started_at_local": "2026-01-01 10:00:00",
            "session_stopped_at_local": "2026-01-01 10:01:00",
            "wall_clock_duration_seconds": 60,
            "audio_duration_seconds": 60,
            "segments": [],
        }
        session2 = {
            "session_id": "session2",
            "recording_group_id": "grp-2",
            "channel_name": "test",
            "recording_mode": "continuous",
            "session_started_at_local": "2026-01-02 10:00:00",
            "session_stopped_at_local": "2026-01-02 10:01:00",
            "wall_clock_duration_seconds": 60,
            "audio_duration_seconds": 60,
            "segments": [],
        }

        path1 = Path(self.temp_dir.name) / "session1_session_metadata.json"
        with open(path1, "w") as f:
            json.dump(session1, f)

        path2 = Path(self.temp_dir.name) / "session2_session_metadata.json"
        with open(path2, "w") as f:
            json.dump(session2, f)

        response = self.client.get("/api/recordings?group_id=grp-1")
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)

        self.assertEqual(data["total_items"], 1)
        self.assertEqual(data["items"][0]["session_id"], "session1")
        self.assertEqual(data["filters"]["group_id"], "grp-1")

    def test_api_from_date_filter_date_only(self):
        """GET /api/recordings?from=YYYY-MM-DD filters by date."""
        session1 = {
            "session_id": "session1",
            "channel_name": "test",
            "recording_mode": "continuous",
            "session_started_at_local": "2026-01-01 10:00:00",
            "session_stopped_at_local": "2026-01-01 10:01:00",
            "wall_clock_duration_seconds": 60,
            "audio_duration_seconds": 60,
            "segments": [],
        }
        session2 = {
            "session_id": "session2",
            "channel_name": "test",
            "recording_mode": "continuous",
            "session_started_at_local": "2026-01-05 10:00:00",
            "session_stopped_at_local": "2026-01-05 10:01:00",
            "wall_clock_duration_seconds": 60,
            "audio_duration_seconds": 60,
            "segments": [],
        }

        path1 = Path(self.temp_dir.name) / "session1_session_metadata.json"
        with open(path1, "w") as f:
            json.dump(session1, f)

        path2 = Path(self.temp_dir.name) / "session2_session_metadata.json"
        with open(path2, "w") as f:
            json.dump(session2, f)

        response = self.client.get("/api/recordings?from=2026-01-03")
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)

        self.assertEqual(data["total_items"], 1)
        self.assertEqual(data["items"][0]["session_id"], "session2")

    def test_api_to_date_filter_date_only(self):
        """GET /api/recordings?to=YYYY-MM-DD filters by date (end of day)."""
        session1 = {
            "session_id": "session1",
            "channel_name": "test",
            "recording_mode": "continuous",
            "session_started_at_local": "2026-01-01 10:00:00",
            "session_stopped_at_local": "2026-01-01 10:01:00",
            "wall_clock_duration_seconds": 60,
            "audio_duration_seconds": 60,
            "segments": [],
        }
        session2 = {
            "session_id": "session2",
            "channel_name": "test",
            "recording_mode": "continuous",
            "session_started_at_local": "2026-01-05 10:00:00",
            "session_stopped_at_local": "2026-01-05 10:01:00",
            "wall_clock_duration_seconds": 60,
            "audio_duration_seconds": 60,
            "segments": [],
        }

        path1 = Path(self.temp_dir.name) / "session1_session_metadata.json"
        with open(path1, "w") as f:
            json.dump(session1, f)

        path2 = Path(self.temp_dir.name) / "session2_session_metadata.json"
        with open(path2, "w") as f:
            json.dump(session2, f)

        response = self.client.get("/api/recordings?to=2026-01-03")
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)

        self.assertEqual(data["total_items"], 1)
        self.assertEqual(data["items"][0]["session_id"], "session1")

    def test_api_date_filter_with_time(self):
        """GET /api/recordings?from=YYYY-MM-DDTHH:MM accepts time format."""
        session1 = {
            "session_id": "session1",
            "channel_name": "test",
            "recording_mode": "continuous",
            "session_started_at_local": "2026-01-01 08:00:00",
            "session_stopped_at_local": "2026-01-01 09:00:00",
            "wall_clock_duration_seconds": 60,
            "audio_duration_seconds": 60,
            "segments": [],
        }
        session2 = {
            "session_id": "session2",
            "channel_name": "test",
            "recording_mode": "continuous",
            "session_started_at_local": "2026-01-01 10:00:00",
            "session_stopped_at_local": "2026-01-01 11:00:00",
            "wall_clock_duration_seconds": 60,
            "audio_duration_seconds": 60,
            "segments": [],
        }

        path1 = Path(self.temp_dir.name) / "session1_session_metadata.json"
        with open(path1, "w") as f:
            json.dump(session1, f)

        path2 = Path(self.temp_dir.name) / "session2_session_metadata.json"
        with open(path2, "w") as f:
            json.dump(session2, f)

        response = self.client.get("/api/recordings?from=2026-01-01T09:00")
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)

        self.assertEqual(data["total_items"], 1)
        self.assertEqual(data["items"][0]["session_id"], "session2")

    def test_api_invalid_date_returns_400(self):
        """GET /api/recordings?from=invalid returns 400."""
        response = self.client.get("/api/recordings?from=not-a-date")
        self.assertEqual(response.status_code, 400)
        data = json.loads(response.data)
        self.assertIn("error", data)

    def test_api_invalid_page_returns_400(self):
        """GET /api/recordings?page=abc returns 400."""
        response = self.client.get("/api/recordings?page=abc")
        self.assertEqual(response.status_code, 400)
        data = json.loads(response.data)
        self.assertIn("error", data)

    def test_ui_returns_200(self):
        """GET / returns 200 with HTML."""
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Mumble Recordings", response.data)

    def test_ui_contains_filter_form(self):
        """GET / HTML contains filter form fields."""
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        html = response.data.decode()

        self.assertIn('name="status"', html)
        self.assertIn('name="from"', html)
        self.assertIn('name="to"', html)
        self.assertIn('name="group_id"', html)
        self.assertIn('name="page_size"', html)

    def test_ui_preserves_filters_in_form(self):
        """GET /?status=completed preserves filter values in form."""
        session = {
            "session_id": "test1",
            "status": "completed",
            "channel_name": "test",
            "recording_mode": "continuous",
            "session_started_at_local": "2026-01-01 10:00:00",
            "session_stopped_at_local": "2026-01-01 10:01:00",
            "wall_clock_duration_seconds": 60,
            "audio_duration_seconds": 60,
            "segments": [],
        }
        path = Path(self.temp_dir.name) / "test_session_metadata.json"
        with open(path, "w") as f:
            json.dump(session, f)

        response = self.client.get("/?status=completed&from=2026-01-01&group_id=grp1")
        self.assertEqual(response.status_code, 200)
        html = response.data.decode()

        self.assertIn('selected', html)

    def test_ui_shows_pagination_links(self):
        """GET / with paginated results shows prev/next links."""
        for i in range(30):
            session = {
                "session_id": f"session{i}",
                "channel_name": "test",
                "recording_mode": "continuous",
                "session_started_at_local": f"2026-01-{(i % 28) + 1:02d} 10:00:00",
                "session_stopped_at_local": f"2026-01-{(i % 28) + 1:02d} 10:01:00",
                "wall_clock_duration_seconds": 60,
                "audio_duration_seconds": 60,
                "segments": [],
            }
            path = Path(self.temp_dir.name) / f"session{i}_session_metadata.json"
            with open(path, "w") as f:
                json.dump(session, f)

        response = self.client.get("/?page_size=10")
        self.assertEqual(response.status_code, 200)
        html = response.data.decode()

        self.assertIn("Next", html)


if __name__ == "__main__":
    unittest.main()
