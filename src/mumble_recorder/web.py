"""Web UI and API server for recordings."""
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

from flask import Flask, render_template_string, request, send_file, jsonify

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def load_recordings(output_dir: str) -> list[dict]:
    """Load and parse session metadata files from output directory.

    Returns a list of sessions sorted newest first. Malformed files are
    logged but do not crash the API.
    """
    sessions = []
    output_path = Path(output_dir)

    if not output_path.exists():
        return sessions

    try:
        metadata_files = sorted(
            output_path.glob("*_session_metadata.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
    except OSError as e:
        logger.warning(f"Failed to list recordings directory: {e}")
        return sessions

    for metadata_file in metadata_files:
        try:
            with open(metadata_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            session = {
                "session_id": data.get("session_id"),
                "session_short_id": data.get("session_short_id"),
                "recording_group_id": data.get("recording_group_id"),
                "reconnect_attempt": data.get("reconnect_attempt"),
                "parent_session_id": data.get("parent_session_id"),
                "channel_name": data.get("channel_name"),
                "recording_mode": data.get("recording_mode"),
                "session_started_at_local": data.get("session_started_at_local"),
                "session_stopped_at_local": data.get("session_stopped_at_local"),
                "wall_clock_duration_seconds": data.get("wall_clock_duration_seconds"),
                "audio_duration_seconds": data.get("audio_duration_seconds"),
                "status": data.get("status", "completed"),
                "stop_reason": data.get("stop_reason"),
                "planned_duration_seconds": data.get("planned_duration_seconds"),
                "segment_count": len(data.get("segments", [])),
                "metadata_filename": metadata_file.name,
                "segments": data.get("segments", []),
            }
            sessions.append(session)
        except (json.JSONDecodeError, IOError, KeyError) as e:
            logger.warning(f"Failed to parse {metadata_file.name}: {e}")
            sessions.append({
                "session_id": None,
                "session_short_id": None,
                "recording_group_id": None,
                "reconnect_attempt": None,
                "parent_session_id": None,
                "channel_name": None,
                "recording_mode": None,
                "session_started_at_local": None,
                "session_stopped_at_local": None,
                "wall_clock_duration_seconds": None,
                "audio_duration_seconds": None,
                "status": "error",
                "stop_reason": f"malformed_metadata: {type(e).__name__}",
                "planned_duration_seconds": None,
                "segment_count": 0,
                "metadata_filename": metadata_file.name,
                "segments": [],
            })

    return sessions


def create_app(output_dir: str | None = None) -> Flask:
    """Create and configure Flask app."""
    app = Flask(__name__)

    if output_dir is None:
        output_dir = os.getenv("OUTPUT_DIR", "/recordings")

    output_path = Path(output_dir)

    @app.route("/api/health", methods=["GET"])
    def health():
        return jsonify({"status": "ok"})

    @app.route("/api/recordings", methods=["GET"])
    def get_recordings():
        sessions = load_recordings(output_dir)
        return jsonify(sessions)

    @app.route("/api/recordings/<session_id>", methods=["GET"])
    def get_recording(session_id: str):
        """Return full metadata for a session."""
        output_path = Path(output_dir)

        if not output_path.exists():
            return jsonify({"error": "not found"}), 404

        try:
            metadata_files = output_path.glob("*_session_metadata.json")
        except OSError as e:
            logger.warning(f"Failed to list recordings directory: {e}")
            return jsonify({"error": "not found"}), 404

        for metadata_file in metadata_files:
            try:
                with open(metadata_file, "r", encoding="utf-8") as f:
                    data = json.load(f)

                if data.get("session_id") == session_id:
                    data["metadata_filename"] = metadata_file.name
                    return jsonify(data)
            except (json.JSONDecodeError, IOError, KeyError) as e:
                logger.warning(f"Failed to parse {metadata_file.name}: {e}")
                continue

        return jsonify({"error": "not found"}), 404

    @app.route("/api/files/<filename>", methods=["GET"])
    def get_file(filename: str):
        # Prevent path traversal
        if ".." in filename or "/" in filename or "\\" in filename:
            return jsonify({"error": "invalid path"}), 400

        # Only allow wav and session metadata files
        if not (filename.endswith(".wav") or filename.endswith("_session_metadata.json")):
            return jsonify({"error": "file type not allowed"}), 403

        file_path = output_path / filename

        # Ensure the resolved path is still under output_dir
        try:
            file_path.resolve().relative_to(output_path.resolve())
        except ValueError:
            return jsonify({"error": "invalid path"}), 400

        if not file_path.exists():
            return jsonify({"error": "not found"}), 404

        return send_file(
            file_path,
            as_attachment=True,
            download_name=filename,
        )

    @app.route("/", methods=["GET"])
    def index():
        sessions = load_recordings(output_dir)

        # Shorten recording group ID for display
        for s in sessions:
            if s.get("recording_group_id"):
                s["recording_group_id_short"] = s["recording_group_id"][:8]

        html = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Mumble Recordings</title>
    <style>
        body { font-family: sans-serif; margin: 20px; }
        h1 { color: #333; }
        table { width: 100%; border-collapse: collapse; margin-top: 20px; }
        th, td { padding: 8px; text-align: left; border-bottom: 1px solid #ddd; }
        th { background-color: #f5f5f5; font-weight: bold; }
        tr:hover { background-color: #f9f9f9; }
        .status-ok { color: green; }
        .status-error { color: red; }
        .status-interrupted { color: orange; }
        a { color: #0066cc; }
        .small { font-size: 0.9em; color: #666; }
        .timestamp { font-family: monospace; font-size: 0.9em; }
    </style>
</head>
<body>
    <h1>Mumble Recordings</h1>
    {% if sessions %}
        <p>{{ sessions|length }} session(s) found</p>
        <table>
            <tr>
                <th>Start Time (Local)</th>
                <th>Channel</th>
                <th>Status</th>
                <th>Duration</th>
                <th>Segments</th>
                <th>Reconnect</th>
                <th>Group</th>
                <th>Actions</th>
            </tr>
            {% for session in sessions %}
                <tr>
                    <td class="timestamp">{{ session.session_started_at_local or "—" }}</td>
                    <td>{{ session.channel_name or "—" }}</td>
                    <td class="{% if session.status == 'completed' %}status-ok{% elif session.status == 'interrupted' %}status-interrupted{% else %}status-error{% endif %}">
                        {{ session.status }}
                        {% if session.stop_reason %}
                            <div class="small">{{ session.stop_reason }}</div>
                        {% endif %}
                    </td>
                    <td>
                        {% if session.wall_clock_duration_seconds %}
                            {{ "%.0f"|format(session.wall_clock_duration_seconds) }}s
                        {% else %}
                            —
                        {% endif %}
                    </td>
                    <td>{{ session.segment_count }}</td>
                    <td>
                        {% if session.reconnect_attempt %}
                            #{{ session.reconnect_attempt }}
                        {% else %}
                            —
                        {% endif %}
                    </td>
                    <td class="small">{{ session.recording_group_id_short or "—" }}</td>
                    <td>
                        <a href="/api/files/{{ session.metadata_filename }}">metadata</a>
                        {% for segment in session.segments[:3] %}
                            {% if loop.first %}<br>{% endif %}
                            <a href="/api/files/{{ segment.file_name }}">wav{{ loop.index }}</a>
                        {% endfor %}
                        {% if session.segments|length > 3 %}
                            <br><span class="small">+{{ session.segments|length - 3 }} more</span>
                        {% endif %}
                    </td>
                </tr>
            {% endfor %}
        </table>
    {% else %}
        <p>No recordings found in {{ output_dir }}</p>
    {% endif %}
</body>
</html>
        """
        return render_template_string(html, sessions=sessions, output_dir=output_dir)

    return app


def main():
    """Run the web server."""
    output_dir = os.getenv("OUTPUT_DIR", "/recordings")
    host = "0.0.0.0"
    port = int(os.getenv("WEB_PORT", "5000"))

    app = create_app(output_dir)
    logger.info(f"Starting web server on {host}:{port}")
    logger.info(f"Recording directory: {output_dir}")
    app.run(host=host, port=port, debug=False)


if __name__ == "__main__":
    main()
