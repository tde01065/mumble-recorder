"""Tests for web server."""
import json
import tempfile
import unittest
import zipfile
from io import BytesIO
from pathlib import Path
from subprocess import TimeoutExpired
from unittest.mock import patch, MagicMock

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

    def test_session_zip_returns_200(self):
        """GET /api/recordings/<session_id>/download.zip returns 200 with zip content."""
        session = {
            "session_id": "zip-test",
            "session_short_id": "zt",
            "channel_name": "test",
            "recording_mode": "continuous",
            "session_started_at_local": "2026-01-01 10:00:00",
            "session_stopped_at_local": "2026-01-01 10:01:00",
            "wall_clock_duration_seconds": 60,
            "audio_duration_seconds": 60,
            "segments": [
                {"segment_index": 0, "file_name": "segment1.wav", "size_bytes": 100},
            ],
        }

        meta_path = Path(self.temp_dir.name) / "zip_session_metadata.json"
        with open(meta_path, "w") as f:
            json.dump(session, f)

        seg_path = Path(self.temp_dir.name) / "segment1.wav"
        seg_path.write_bytes(b"WAV_DATA_HERE")

        response = self.client.get("/api/recordings/zip-test/download.zip")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content_type, "application/zip")

    def test_session_zip_contains_metadata_and_segments(self):
        """Session ZIP contains metadata JSON and segment WAV files."""
        session = {
            "session_id": "content-test",
            "channel_name": "test",
            "recording_mode": "continuous",
            "session_started_at_local": "2026-01-01 10:00:00",
            "session_stopped_at_local": "2026-01-01 10:01:00",
            "wall_clock_duration_seconds": 60,
            "audio_duration_seconds": 60,
            "segments": [
                {"segment_index": 0, "file_name": "seg0.wav", "size_bytes": 100},
                {"segment_index": 1, "file_name": "seg1.wav", "size_bytes": 100},
            ],
        }

        meta_path = Path(self.temp_dir.name) / "content_session_metadata.json"
        with open(meta_path, "w") as f:
            json.dump(session, f)

        (Path(self.temp_dir.name) / "seg0.wav").write_bytes(b"SEG0")
        (Path(self.temp_dir.name) / "seg1.wav").write_bytes(b"SEG1")

        response = self.client.get("/api/recordings/content-test/download.zip")
        self.assertEqual(response.status_code, 200)

        zip_data = BytesIO(response.data)
        with zipfile.ZipFile(zip_data) as zf:
            names = zf.namelist()
            self.assertIn("metadata/content_session_metadata.json", names)
            self.assertIn("segments/seg0.wav", names)
            self.assertIn("segments/seg1.wav", names)

            # Verify content
            meta_content = zf.read("metadata/content_session_metadata.json")
            meta_json = json.loads(meta_content)
            self.assertEqual(meta_json["session_id"], "content-test")

            seg_content = zf.read("segments/seg0.wav")
            self.assertEqual(seg_content, b"SEG0")

    def test_session_zip_skips_missing_segments(self):
        """Session ZIP skips missing segment files without failing."""
        session = {
            "session_id": "missing-seg-test",
            "channel_name": "test",
            "recording_mode": "continuous",
            "session_started_at_local": "2026-01-01 10:00:00",
            "session_stopped_at_local": "2026-01-01 10:01:00",
            "wall_clock_duration_seconds": 60,
            "audio_duration_seconds": 60,
            "segments": [
                {"segment_index": 0, "file_name": "exists.wav", "size_bytes": 100},
                {"segment_index": 1, "file_name": "missing.wav", "size_bytes": 100},
            ],
        }

        meta_path = Path(self.temp_dir.name) / "missing_seg_session_metadata.json"
        with open(meta_path, "w") as f:
            json.dump(session, f)

        (Path(self.temp_dir.name) / "exists.wav").write_bytes(b"EXISTS")

        response = self.client.get("/api/recordings/missing-seg-test/download.zip")
        self.assertEqual(response.status_code, 200)

        zip_data = BytesIO(response.data)
        with zipfile.ZipFile(zip_data) as zf:
            names = zf.namelist()
            self.assertIn("segments/exists.wav", names)
            self.assertNotIn("segments/missing.wav", names)

    def test_session_zip_returns_404_for_missing_session(self):
        """GET /api/recordings/<session_id>/download.zip returns 404 if missing."""
        response = self.client.get("/api/recordings/nonexistent/download.zip")
        self.assertEqual(response.status_code, 404)
        data = json.loads(response.data)
        self.assertEqual(data["error"], "not found")

    def test_session_zip_rejects_unsafe_filenames(self):
        """Session ZIP rejects unsafe segment filenames (path traversal, etc)."""
        session = {
            "session_id": "unsafe-test",
            "channel_name": "test",
            "recording_mode": "continuous",
            "session_started_at_local": "2026-01-01 10:00:00",
            "session_stopped_at_local": "2026-01-01 10:01:00",
            "wall_clock_duration_seconds": 60,
            "audio_duration_seconds": 60,
            "segments": [
                {"segment_index": 0, "file_name": "../etc/passwd", "size_bytes": 100},
                {"segment_index": 1, "file_name": "good.wav", "size_bytes": 100},
            ],
        }

        meta_path = Path(self.temp_dir.name) / "unsafe_session_metadata.json"
        with open(meta_path, "w") as f:
            json.dump(session, f)

        (Path(self.temp_dir.name) / "good.wav").write_bytes(b"GOOD")

        response = self.client.get("/api/recordings/unsafe-test/download.zip")
        self.assertEqual(response.status_code, 200)

        zip_data = BytesIO(response.data)
        with zipfile.ZipFile(zip_data) as zf:
            names = zf.namelist()
            self.assertNotIn("../etc/passwd", names)
            self.assertIn("segments/good.wav", names)

    def test_group_zip_returns_200(self):
        """GET /api/groups/<group_id>/download.zip returns 200."""
        session1 = {
            "session_id": "group-session-1",
            "recording_group_id": "group-123",
            "channel_name": "test",
            "recording_mode": "continuous",
            "session_started_at_local": "2026-01-01 10:00:00",
            "session_stopped_at_local": "2026-01-01 10:01:00",
            "wall_clock_duration_seconds": 60,
            "audio_duration_seconds": 60,
            "segments": [
                {"segment_index": 0, "file_name": "g1_seg.wav", "size_bytes": 100},
            ],
        }

        meta_path = Path(self.temp_dir.name) / "group_session1_session_metadata.json"
        with open(meta_path, "w") as f:
            json.dump(session1, f)

        (Path(self.temp_dir.name) / "g1_seg.wav").write_bytes(b"G1SEG")

        response = self.client.get("/api/groups/group-123/download.zip")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content_type, "application/zip")

    def test_group_zip_includes_all_sessions_in_group(self):
        """Group ZIP includes metadata and segments for all sessions in group."""
        session1 = {
            "session_id": "g2-session-1",
            "recording_group_id": "group-456",
            "channel_name": "test",
            "recording_mode": "continuous",
            "session_started_at_local": "2026-01-01 10:00:00",
            "session_stopped_at_local": "2026-01-01 10:01:00",
            "wall_clock_duration_seconds": 60,
            "audio_duration_seconds": 60,
            "segments": [{"segment_index": 0, "file_name": "g2s1.wav", "size_bytes": 100}],
        }
        session2 = {
            "session_id": "g2-session-2",
            "recording_group_id": "group-456",
            "channel_name": "test",
            "recording_mode": "continuous",
            "session_started_at_local": "2026-01-02 10:00:00",
            "session_stopped_at_local": "2026-01-02 10:01:00",
            "wall_clock_duration_seconds": 60,
            "audio_duration_seconds": 60,
            "segments": [{"segment_index": 0, "file_name": "g2s2.wav", "size_bytes": 100}],
        }

        meta_path1 = Path(self.temp_dir.name) / "g2s1_session_metadata.json"
        with open(meta_path1, "w") as f:
            json.dump(session1, f)

        meta_path2 = Path(self.temp_dir.name) / "g2s2_session_metadata.json"
        with open(meta_path2, "w") as f:
            json.dump(session2, f)

        (Path(self.temp_dir.name) / "g2s1.wav").write_bytes(b"G2S1")
        (Path(self.temp_dir.name) / "g2s2.wav").write_bytes(b"G2S2")

        response = self.client.get("/api/groups/group-456/download.zip")
        self.assertEqual(response.status_code, 200)

        zip_data = BytesIO(response.data)
        with zipfile.ZipFile(zip_data) as zf:
            names = zf.namelist()
            self.assertIn("g2-session-1/metadata/g2s1_session_metadata.json", names)
            self.assertIn("g2-session-1/segments/g2s1.wav", names)
            self.assertIn("g2-session-2/metadata/g2s2_session_metadata.json", names)
            self.assertIn("g2-session-2/segments/g2s2.wav", names)

    def test_group_zip_excludes_other_groups(self):
        """Group ZIP does not include sessions from other groups."""
        session1 = {
            "session_id": "session-a",
            "recording_group_id": "group-a",
            "channel_name": "test",
            "recording_mode": "continuous",
            "session_started_at_local": "2026-01-01 10:00:00",
            "session_stopped_at_local": "2026-01-01 10:01:00",
            "wall_clock_duration_seconds": 60,
            "audio_duration_seconds": 60,
            "segments": [{"segment_index": 0, "file_name": "a.wav", "size_bytes": 100}],
        }
        session2 = {
            "session_id": "session-b",
            "recording_group_id": "group-b",
            "channel_name": "test",
            "recording_mode": "continuous",
            "session_started_at_local": "2026-01-02 10:00:00",
            "session_stopped_at_local": "2026-01-02 10:01:00",
            "wall_clock_duration_seconds": 60,
            "audio_duration_seconds": 60,
            "segments": [{"segment_index": 0, "file_name": "b.wav", "size_bytes": 100}],
        }

        meta_path1 = Path(self.temp_dir.name) / "s_a_session_metadata.json"
        with open(meta_path1, "w") as f:
            json.dump(session1, f)

        meta_path2 = Path(self.temp_dir.name) / "s_b_session_metadata.json"
        with open(meta_path2, "w") as f:
            json.dump(session2, f)

        (Path(self.temp_dir.name) / "a.wav").write_bytes(b"A")
        (Path(self.temp_dir.name) / "b.wav").write_bytes(b"B")

        response = self.client.get("/api/groups/group-a/download.zip")
        self.assertEqual(response.status_code, 200)

        zip_data = BytesIO(response.data)
        with zipfile.ZipFile(zip_data) as zf:
            names = zf.namelist()
            self.assertIn("session-a/metadata/s_a_session_metadata.json", names)
            self.assertIn("session-a/segments/a.wav", names)
            # session-b should not be in the zip
            self.assertNotIn("session-b", str(names))

    def test_group_zip_returns_404_for_missing_group(self):
        """GET /api/groups/<group_id>/download.zip returns 404 if missing."""
        response = self.client.get("/api/groups/nonexistent-group/download.zip")
        self.assertEqual(response.status_code, 404)
        data = json.loads(response.data)
        self.assertEqual(data["error"], "not found")

    def test_group_zip_skips_malformed_metadata(self):
        """Group ZIP skips malformed session metadata without crashing."""
        valid_session = {
            "session_id": "valid-in-group",
            "recording_group_id": "mixed-group",
            "channel_name": "test",
            "recording_mode": "continuous",
            "session_started_at_local": "2026-01-01 10:00:00",
            "session_stopped_at_local": "2026-01-01 10:01:00",
            "wall_clock_duration_seconds": 60,
            "audio_duration_seconds": 60,
            "segments": [{"segment_index": 0, "file_name": "valid.wav", "size_bytes": 100}],
        }

        meta_path = Path(self.temp_dir.name) / "valid_group_session_metadata.json"
        with open(meta_path, "w") as f:
            json.dump(valid_session, f)

        # Create a malformed metadata file with same group_id
        malformed_path = Path(self.temp_dir.name) / "malformed_group_session_metadata.json"
        malformed_path.write_text('{"recording_group_id": "mixed-group", "invalid": json}')

        (Path(self.temp_dir.name) / "valid.wav").write_bytes(b"VALID")

        response = self.client.get("/api/groups/mixed-group/download.zip")
        self.assertEqual(response.status_code, 200)

        zip_data = BytesIO(response.data)
        with zipfile.ZipFile(zip_data) as zf:
            names = zf.namelist()
            self.assertIn("valid-in-group/metadata/valid_group_session_metadata.json", names)
            self.assertIn("valid-in-group/segments/valid.wav", names)

    def test_group_zip_skips_missing_segments(self):
        """Group ZIP skips missing segment files without failing."""
        session = {
            "session_id": "group-missing-seg",
            "recording_group_id": "group-missing",
            "channel_name": "test",
            "recording_mode": "continuous",
            "session_started_at_local": "2026-01-01 10:00:00",
            "session_stopped_at_local": "2026-01-01 10:01:00",
            "wall_clock_duration_seconds": 60,
            "audio_duration_seconds": 60,
            "segments": [
                {"segment_index": 0, "file_name": "found.wav", "size_bytes": 100},
                {"segment_index": 1, "file_name": "notfound.wav", "size_bytes": 100},
            ],
        }

        meta_path = Path(self.temp_dir.name) / "grp_missing_session_metadata.json"
        with open(meta_path, "w") as f:
            json.dump(session, f)

        (Path(self.temp_dir.name) / "found.wav").write_bytes(b"FOUND")

        response = self.client.get("/api/groups/group-missing/download.zip")
        self.assertEqual(response.status_code, 200)

        zip_data = BytesIO(response.data)
        with zipfile.ZipFile(zip_data) as zf:
            names = zf.namelist()
            self.assertIn("group-missing-seg/segments/found.wav", names)
            self.assertNotIn("notfound.wav", str(names))


class TestRecorderControl(unittest.TestCase):
    """Tests for recorder control API and UI."""

    def setUp(self):
        """Set up test app and temp directory."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.app = create_app(self.temp_dir.name)
        self.client = self.app.test_client()

    def tearDown(self):
        """Clean up temp directory."""
        self.temp_dir.cleanup()

    def test_recorder_status_stopped(self):
        """GET /api/recorder/status returns stopped status."""
        response = self.client.get("/api/recorder/status")
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)

        self.assertFalse(data["running"])
        self.assertIsNone(data["pid"])
        self.assertIsNone(data["started_at"])
        self.assertIsNone(data["mode"])
        self.assertIsNone(data["returncode"])

    @patch("mumble_recorder.web.subprocess.Popen")
    def test_recorder_status_detects_natural_exit(self, mock_popen):
        """Status updates returncode and last_exit_at when process exits naturally."""
        mock_process = MagicMock()
        mock_process.pid = 1234
        mock_process.poll.return_value = None
        mock_process.stdout.readline.side_effect = [b"", b""]
        mock_popen.return_value = mock_process

        response = self.client.post(
            "/api/recorder/start",
            data={"recording_mode": "continuous"},
        )
        self.assertEqual(response.status_code, 200)

        mock_process.poll.return_value = 1

        response = self.client.get("/api/recorder/status")
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)

        self.assertFalse(data["running"])
        self.assertEqual(data["returncode"], 1)
        self.assertIsNotNone(data["last_exit_at"])

    @patch("mumble_recorder.web.subprocess.Popen")
    def test_recorder_start_continuous(self, mock_popen):
        """POST /api/recorder/start with continuous mode starts process."""
        mock_process = MagicMock()
        mock_process.pid = 1234
        mock_process.poll.return_value = None
        mock_process.stdout.readline.side_effect = [b"", b""]
        mock_popen.return_value = mock_process

        response = self.client.post(
            "/api/recorder/start",
            data={"recording_mode": "continuous"},
        )
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)

        self.assertTrue(data["running"])
        self.assertEqual(data["pid"], 1234)
        self.assertEqual(data["mode"], "continuous")
        self.assertIsNotNone(data["started_at"])

        mock_popen.assert_called_once()
        call_args = mock_popen.call_args
        self.assertIn("mumble_recorder", call_args[0][0])

    @patch("mumble_recorder.web.subprocess.Popen")
    def test_recorder_start_received_audio_only(self, mock_popen):
        """POST /api/recorder/start with received_audio_only mode."""
        mock_process = MagicMock()
        mock_process.pid = 1234
        mock_process.poll.return_value = None
        mock_process.stdout.readline.side_effect = [b"", b""]
        mock_popen.return_value = mock_process

        response = self.client.post(
            "/api/recorder/start",
            data={"recording_mode": "received_audio_only"},
        )
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)

        self.assertTrue(data["running"])
        self.assertEqual(data["mode"], "received_audio_only")

        call_kwargs = mock_popen.call_args[1]
        self.assertEqual(call_kwargs["env"]["RECORDING_MODE"], "received_audio_only")

    @patch("mumble_recorder.web.subprocess.Popen")
    def test_recorder_start_invalid_mode_returns_400(self, mock_popen):
        """POST /api/recorder/start with invalid mode returns 400."""
        response = self.client.post(
            "/api/recorder/start",
            data={"recording_mode": "invalid_mode"},
        )
        self.assertEqual(response.status_code, 400)
        data = json.loads(response.data)
        self.assertIn("Invalid recording mode", data["error"])

    @patch("mumble_recorder.web.subprocess.Popen")
    def test_recorder_start_already_running_returns_409(self, mock_popen):
        """POST /api/recorder/start when already running returns 409."""
        mock_process = MagicMock()
        mock_process.pid = 1234
        mock_process.poll.return_value = None
        mock_process.stdout.readline.side_effect = [b"", b""]
        mock_popen.return_value = mock_process

        response1 = self.client.post(
            "/api/recorder/start",
            data={"recording_mode": "continuous"},
        )
        self.assertEqual(response1.status_code, 200)

        response2 = self.client.post(
            "/api/recorder/start",
            data={"recording_mode": "continuous"},
        )
        self.assertEqual(response2.status_code, 409)
        data = json.loads(response2.data)
        self.assertIn("already running", data["error"])

    @patch("mumble_recorder.web.subprocess.Popen")
    def test_recorder_stop_when_running(self, mock_popen):
        """POST /api/recorder/stop terminates running process."""
        mock_process = MagicMock()
        mock_process.pid = 1234
        mock_process.returncode = None
        mock_process.stdout.readline.side_effect = [b"", b""]
        mock_popen.return_value = mock_process

        # poll() returns None initially (running), then 0 after terminate
        mock_process.poll.side_effect = [None, None, 0, 0, 0]
        mock_process.wait.return_value = None

        response1 = self.client.post(
            "/api/recorder/start",
            data={"recording_mode": "continuous"},
        )
        self.assertEqual(response1.status_code, 200)

        mock_process.returncode = 0

        response2 = self.client.post("/api/recorder/stop")
        self.assertEqual(response2.status_code, 200)
        data = json.loads(response2.data)

        self.assertFalse(data["running"])
        self.assertEqual(data["returncode"], 0)
        self.assertIsNotNone(data["last_exit_at"])

        mock_process.terminate.assert_called_once()

    @patch("mumble_recorder.web.subprocess.Popen")
    def test_recorder_stop_when_not_running_returns_409(self, mock_popen):
        """POST /api/recorder/stop when not running returns 409."""
        response = self.client.post("/api/recorder/stop")
        self.assertEqual(response.status_code, 409)
        data = json.loads(response.data)
        self.assertIn("not running", data["error"])

    @patch("mumble_recorder.web.subprocess.Popen")
    def test_recorder_stop_sends_terminate_signal(self, mock_popen):
        """POST /api/recorder/stop calls terminate() on process."""
        mock_process = MagicMock()
        mock_process.pid = 1234
        mock_process.returncode = None
        mock_process.stdout.readline.side_effect = [b"", b""]
        mock_popen.return_value = mock_process

        mock_process.poll.side_effect = [None, None, 0, 0, 0]
        mock_process.wait.return_value = None

        self.client.post(
            "/api/recorder/start",
            data={"recording_mode": "continuous"},
        )

        mock_process.returncode = 0

        self.client.post("/api/recorder/stop")

        mock_process.terminate.assert_called_once()

    @patch("mumble_recorder.web.subprocess.Popen")
    def test_recorder_stop_kills_if_timeout(self, mock_popen):
        """POST /api/recorder/stop kills process if terminate times out."""
        mock_process = MagicMock()
        mock_process.pid = 1234
        mock_process.returncode = None
        mock_process.stdout.readline.side_effect = [b"", b""]
        mock_popen.return_value = mock_process

        mock_process.poll.side_effect = [None, None, 0, 0, 0]
        mock_process.wait.side_effect = [TimeoutExpired("cmd", 10), None]
        mock_process.returncode = -9

        self.client.post(
            "/api/recorder/start",
            data={"recording_mode": "continuous"},
        )

        self.client.post("/api/recorder/stop")

        mock_process.terminate.assert_called_once()
        mock_process.kill.assert_called_once()

    def test_ui_contains_recorder_control_panel(self):
        """GET / HTML contains recorder control panel."""
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        html = response.data.decode()

        self.assertIn("Recorder Control", html)
        self.assertIn("recording_mode", html)
        self.assertIn("Start Recording", html)
        self.assertIn("When talking", html)
        self.assertIn("Continuous", html)

    def test_ui_has_recorder_status_display(self):
        """GET / HTML includes recorder status elements."""
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        html = response.data.decode()

        self.assertIn("recorder-status", html)
        self.assertIn("recorder-details", html)
        self.assertIn("loadRecorderStatus", html)

    def test_ui_has_stop_button(self):
        """GET / HTML includes stop button (hidden by default)."""
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        html = response.data.decode()

        self.assertIn("Stop Recording", html)
        self.assertIn("stopRecorder", html)

    def test_recorder_start_with_json_content_type(self):
        """POST /api/recorder/start accepts JSON body."""
        with patch("mumble_recorder.web.subprocess.Popen") as mock_popen:
            mock_process = MagicMock()
            mock_process.pid = 1234
            mock_process.poll.return_value = None
            mock_process.stdout.readline.side_effect = [b"", b""]
            mock_popen.return_value = mock_process

            response = self.client.post(
                "/api/recorder/start",
                json={"recording_mode": "received_audio_only"},
            )
            self.assertEqual(response.status_code, 200)
            data = json.loads(response.data)
            self.assertTrue(data["running"])
            self.assertEqual(data["mode"], "received_audio_only")

    @patch("mumble_recorder.web.subprocess.Popen")
    def test_recorder_status_includes_error_on_start_failure(self, mock_popen):
        """Recorder status includes last_start_error when start fails."""
        mock_popen.side_effect = RuntimeError("Connection refused")

        response = self.client.post(
            "/api/recorder/start",
            data={"recording_mode": "continuous"},
        )
        self.assertEqual(response.status_code, 409)

        response = self.client.get("/api/recorder/status")
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)

        self.assertFalse(data["running"])
        self.assertIn("Connection refused", data["last_start_error"])


if __name__ == "__main__":
    unittest.main()
