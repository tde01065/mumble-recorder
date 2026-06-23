"""Web UI and API server for recordings."""
import io
import json
import logging
import os
import sys
import zipfile
from datetime import datetime
from pathlib import Path

from flask import Flask, render_template_string, request, send_file, jsonify, url_for

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def parse_int_query_param(value: str | None, default: int, min_val: int | None = None, max_val: int | None = None) -> tuple[int, str | None]:
    """Parse integer query parameter with optional bounds.

    Returns (parsed_value, error_message). If error_message is not None, value is invalid.
    """
    if value is None:
        return default, None

    try:
        parsed = int(value)
    except (ValueError, TypeError):
        return None, f"Invalid integer: {value}"

    if min_val is not None and parsed < min_val:
        parsed = min_val
    if max_val is not None and parsed > max_val:
        parsed = max_val

    return parsed, None


def parse_date_filter(value: str | None) -> tuple[datetime | None, str | None]:
    """Parse date/datetime filter.

    Accepts: YYYY-MM-DD, YYYY-MM-DDTHH:MM, YYYY-MM-DD HH:MM
    Returns (datetime, error_message). If error_message is not None, value is invalid.
    """
    if value is None:
        return None, None

    value = value.strip()

    formats = [
        "%Y-%m-%d",
        "%Y-%m-%dT%H:%M",
        "%Y-%m-%d %H:%M",
    ]

    for fmt in formats:
        try:
            return datetime.strptime(value, fmt), None
        except ValueError:
            continue

    return None, f"Invalid date format: {value}. Expected YYYY-MM-DD, YYYY-MM-DDTHH:MM, or YYYY-MM-DD HH:MM"


def filter_and_paginate_sessions(
    sessions: list[dict],
    page: int = 1,
    page_size: int = 25,
    status: str | None = None,
    from_date: datetime | None = None,
    to_date: datetime | None = None,
    group_id: str | None = None,
) -> dict:
    """Filter and paginate sessions.

    Returns dict with items, page, page_size, total_items, total_pages, filters.
    """
    filtered = sessions[:]

    if status:
        filtered = [s for s in filtered if s.get("status") == status]

    if group_id:
        filtered = [s for s in filtered if s.get("recording_group_id") == group_id]

    if from_date or to_date:
        def matches_date_range(session):
            started = session.get("session_started_at_local")
            if not started:
                return True

            try:
                session_dt = datetime.fromisoformat(started.replace(" ", "T"))
            except (ValueError, AttributeError):
                return True

            if from_date and session_dt < from_date:
                return False
            if to_date and session_dt > to_date:
                return False

            return True

        filtered = [s for s in filtered if matches_date_range(s)]

    total_items = len(filtered)
    total_pages = (total_items + page_size - 1) // page_size if page_size > 0 else 1

    start_idx = (page - 1) * page_size
    end_idx = start_idx + page_size
    items = filtered[start_idx:end_idx]

    return {
        "items": items,
        "page": page,
        "page_size": page_size,
        "total_items": total_items,
        "total_pages": total_pages,
        "filters": {
            "status": status,
            "from": from_date.isoformat() if from_date else None,
            "to": to_date.isoformat() if to_date else None,
            "group_id": group_id,
        }
    }


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


def is_allowed_recording_file(filename: str) -> bool:
    """Check if filename is allowed for download (wav or metadata json)."""
    return filename.endswith(".wav") or filename.endswith("_session_metadata.json")


def safe_output_file(output_path: Path, filename: str) -> Path | None:
    """Resolve filename safely, ensuring it stays under output_path.

    Returns None if the file is outside output_path or invalid.
    """
    if ".." in filename or "/" in filename or "\\" in filename:
        return None

    if not is_allowed_recording_file(filename):
        return None

    file_path = output_path / filename

    try:
        file_path.resolve().relative_to(output_path.resolve())
        return file_path
    except ValueError:
        return None


def load_full_metadata_for_session(output_dir: str, session_id: str) -> dict | None:
    """Load full metadata for a specific session.

    Returns None if not found or malformed.
    """
    output_path = Path(output_dir)

    if not output_path.exists():
        return None

    try:
        metadata_files = output_path.glob("*_session_metadata.json")
    except OSError:
        return None

    for metadata_file in metadata_files:
        try:
            with open(metadata_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            if data.get("session_id") == session_id:
                return data
        except (json.JSONDecodeError, IOError):
            continue

    return None


def find_metadata_for_group(output_dir: str, recording_group_id: str) -> list[tuple[dict, str]]:
    """Find all metadata for sessions in a group.

    Returns list of (session_metadata, metadata_filename) tuples.
    Malformed files are skipped with warning.
    """
    sessions = []
    output_path = Path(output_dir)

    if not output_path.exists():
        return sessions

    try:
        metadata_files = output_path.glob("*_session_metadata.json")
    except OSError:
        return sessions

    for metadata_file in metadata_files:
        try:
            with open(metadata_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            if data.get("recording_group_id") == recording_group_id:
                sessions.append((data, metadata_file.name))
        except (json.JSONDecodeError, IOError) as e:
            logger.warning(f"Skipping malformed metadata in group ZIP: {metadata_file.name}: {e}")
            continue

    return sessions


def build_zip_for_session(output_dir: str, session_id: str) -> io.BytesIO | None:
    """Build in-memory ZIP for a single session.

    Returns BytesIO object or None if session not found.
    Skips missing segment files with warning.
    """
    metadata = load_full_metadata_for_session(output_dir, session_id)
    if metadata is None:
        return None

    output_path = Path(output_dir)
    zip_buffer = io.BytesIO()

    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        # Add metadata file
        try:
            metadata_file = None
            for mf in output_path.glob("*_session_metadata.json"):
                with open(mf, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if data.get("session_id") == session_id:
                    metadata_file = mf
                    break

            if metadata_file:
                zf.write(metadata_file, arcname=f"metadata/{metadata_file.name}")
        except (json.JSONDecodeError, IOError, OSError) as e:
            logger.warning(f"Failed to add metadata to session ZIP: {e}")

        # Add segment files
        for segment in metadata.get("segments", []):
            filename = segment.get("file_name")
            if not filename:
                continue

            file_path = safe_output_file(output_path, filename)
            if file_path is None:
                logger.warning(f"Skipping unsafe segment filename in session ZIP: {filename}")
                continue

            if not file_path.exists():
                logger.warning(f"Skipping missing segment file in session ZIP: {filename}")
                continue

            try:
                zf.write(file_path, arcname=f"segments/{filename}")
            except (IOError, OSError) as e:
                logger.warning(f"Failed to add segment {filename} to session ZIP: {e}")

    zip_buffer.seek(0)
    return zip_buffer


def build_zip_for_group(output_dir: str, recording_group_id: str) -> io.BytesIO | None:
    """Build in-memory ZIP for all sessions in a group.

    Returns BytesIO object or None if group not found.
    Skips missing segment files and malformed sessions with warnings.
    """
    sessions = find_metadata_for_group(output_dir, recording_group_id)
    if not sessions:
        return None

    output_path = Path(output_dir)
    zip_buffer = io.BytesIO()

    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for metadata, metadata_filename in sessions:
            session_id = metadata.get("session_id")
            if not session_id:
                logger.warning("Skipping session without session_id in group ZIP")
                continue

            # Add metadata file
            try:
                file_path = safe_output_file(output_path, metadata_filename)
                if file_path and file_path.exists():
                    zf.write(file_path, arcname=f"{session_id}/metadata/{metadata_filename}")
            except (IOError, OSError) as e:
                logger.warning(f"Failed to add metadata for session {session_id} to group ZIP: {e}")

            # Add segment files
            for segment in metadata.get("segments", []):
                filename = segment.get("file_name")
                if not filename:
                    continue

                file_path = safe_output_file(output_path, filename)
                if file_path is None:
                    logger.warning(f"Skipping unsafe segment filename in group ZIP: {filename}")
                    continue

                if not file_path.exists():
                    logger.warning(f"Skipping missing segment file in group ZIP: {filename}")
                    continue

                try:
                    zf.write(file_path, arcname=f"{session_id}/segments/{filename}")
                except (IOError, OSError) as e:
                    logger.warning(f"Failed to add segment {filename} to group ZIP: {e}")

    zip_buffer.seek(0)
    return zip_buffer


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

        page, err = parse_int_query_param(request.args.get("page"), 1, 1)
        if err:
            return jsonify({"error": err}), 400

        page_size, err = parse_int_query_param(request.args.get("page_size"), 25, 1, 100)
        if err:
            return jsonify({"error": err}), 400

        status = request.args.get("status")
        group_id = request.args.get("group_id")

        from_date, err = parse_date_filter(request.args.get("from"))
        if err:
            return jsonify({"error": err}), 400

        to_date, err = parse_date_filter(request.args.get("to"))
        if err:
            return jsonify({"error": err}), 400

        if from_date is None:
            from_date = None
        if to_date is None:
            to_date = None
        else:
            from datetime import time
            to_date = to_date.replace(hour=23, minute=59, second=59)

        result = filter_and_paginate_sessions(
            sessions,
            page=page,
            page_size=page_size,
            status=status,
            from_date=from_date,
            to_date=to_date,
            group_id=group_id,
        )

        return jsonify(result)

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

    @app.route("/api/recordings/<session_id>/download.zip", methods=["GET"])
    def download_session_zip(session_id: str):
        """Download ZIP file for a single session."""
        zip_buffer = build_zip_for_session(output_dir, session_id)
        if zip_buffer is None:
            return jsonify({"error": "not found"}), 404

        if zip_buffer.getbuffer().nbytes == 0:
            return jsonify({"error": "no files to download"}), 404

        return send_file(
            zip_buffer,
            mimetype="application/zip",
            as_attachment=True,
            download_name=f"recording-{session_id}.zip",
        )

    @app.route("/api/groups/<recording_group_id>/download.zip", methods=["GET"])
    def download_group_zip(recording_group_id: str):
        """Download ZIP file for all sessions in a group."""
        zip_buffer = build_zip_for_group(output_dir, recording_group_id)
        if zip_buffer is None:
            return jsonify({"error": "not found"}), 404

        if zip_buffer.getbuffer().nbytes == 0:
            return jsonify({"error": "no files to download"}), 404

        # Use short ID in filename
        short_id = recording_group_id[:8]
        return send_file(
            zip_buffer,
            mimetype="application/zip",
            as_attachment=True,
            download_name=f"recording-group-{short_id}.zip",
        )

    @app.route("/", methods=["GET"])
    def index():
        sessions = load_recordings(output_dir)

        page, _ = parse_int_query_param(request.args.get("page"), 1, 1)
        page_size, _ = parse_int_query_param(request.args.get("page_size"), 25, 1, 100)
        status = request.args.get("status")
        from_date_str = request.args.get("from")
        to_date_str = request.args.get("to")
        group_id = request.args.get("group_id")

        from_date, _ = parse_date_filter(from_date_str)
        to_date, _ = parse_date_filter(to_date_str)

        if to_date is not None:
            to_date = to_date.replace(hour=23, minute=59, second=59)

        result = filter_and_paginate_sessions(
            sessions,
            page=page,
            page_size=page_size,
            status=status,
            from_date=from_date,
            to_date=to_date,
            group_id=group_id,
        )

        sessions_page = result["items"]
        total_items = result["total_items"]
        total_pages = result["total_pages"]

        for s in sessions_page:
            if s.get("recording_group_id"):
                s["recording_group_id_short"] = s["recording_group_id"][:8]

        prev_page = page - 1 if page > 1 else None
        next_page = page + 1 if page < total_pages else None

        def build_query_string(override_page=None):
            params = {}
            if override_page is not None:
                params["page"] = override_page
            else:
                params["page"] = page
            params["page_size"] = page_size
            if status:
                params["status"] = status
            if from_date_str:
                params["from"] = from_date_str
            if to_date_str:
                params["to"] = to_date_str
            if group_id:
                params["group_id"] = group_id
            return "&".join(f"{k}={v}" for k, v in params.items())

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
        .filter-form { background-color: #f9f9f9; padding: 15px; border-radius: 4px; margin-bottom: 20px; }
        .filter-row { margin-bottom: 10px; display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
        .filter-group { display: flex; gap: 5px; align-items: center; }
        label { font-weight: bold; }
        input, select { padding: 5px; }
        button { padding: 6px 12px; background-color: #0066cc; color: white; border: none; border-radius: 3px; cursor: pointer; }
        button:hover { background-color: #0052a3; }
        .clear-link { color: #0066cc; text-decoration: underline; cursor: pointer; margin-left: 10px; }
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
        .pagination { margin-top: 15px; text-align: center; }
        .pagination a { margin: 0 5px; }
        .results-info { margin: 10px 0; color: #666; }
    </style>
</head>
<body>
    <h1>Mumble Recordings</h1>

    <div class="filter-form">
        <form method="get" action="/">
            <div class="filter-row">
                <div class="filter-group">
                    <label for="status">Status:</label>
                    <select name="status" id="status">
                        <option value="">All</option>
                        <option value="completed"{% if status == 'completed' %} selected{% endif %}>Completed</option>
                        <option value="interrupted"{% if status == 'interrupted' %} selected{% endif %}>Interrupted</option>
                        <option value="failed"{% if status == 'failed' %} selected{% endif %}>Failed</option>
                        <option value="error"{% if status == 'error' %} selected{% endif %}>Error</option>
                    </select>
                </div>
                <div class="filter-group">
                    <label for="from">From:</label>
                    <input type="text" name="from" id="from" placeholder="YYYY-MM-DD" value="{{ from_date_str or '' }}">
                </div>
                <div class="filter-group">
                    <label for="to">To:</label>
                    <input type="text" name="to" id="to" placeholder="YYYY-MM-DD" value="{{ to_date_str or '' }}">
                </div>
                <div class="filter-group">
                    <label for="group_id">Group ID:</label>
                    <input type="text" name="group_id" id="group_id" placeholder="group id" value="{{ group_id or '' }}">
                </div>
                <div class="filter-group">
                    <label for="page_size">Per page:</label>
                    <select name="page_size" id="page_size">
                        <option value="10"{% if page_size == 10 %} selected{% endif %}>10</option>
                        <option value="25"{% if page_size == 25 %} selected{% endif %}>25</option>
                        <option value="50"{% if page_size == 50 %} selected{% endif %}>50</option>
                        <option value="100"{% if page_size == 100 %} selected{% endif %}>100</option>
                    </select>
                </div>
            </div>
            <div class="filter-row">
                <button type="submit">Filter</button>
                <a href="/" class="clear-link">Clear</a>
            </div>
        </form>
    </div>

    {% if total_items > 0 %}
        <div class="results-info">
            Showing {{ (page - 1) * page_size + 1 }} to {{ min(page * page_size, total_items) }} of {{ total_items }} recording(s)
        </div>

        {% if total_pages > 1 %}
            <div class="pagination">
                {% if prev_page %}
                    <a href="/?{{ build_query_string(prev_page) }}">&laquo; Previous</a>
                {% else %}
                    <span style="color: #ccc;">&laquo; Previous</span>
                {% endif %}

                <span>Page {{ page }} of {{ total_pages }}</span>

                {% if next_page %}
                    <a href="/?{{ build_query_string(next_page) }}">Next &raquo;</a>
                {% else %}
                    <span style="color: #ccc;">Next &raquo;</span>
                {% endif %}
            </div>
        {% endif %}

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
            {% for session in sessions_page %}
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
                        <a href="/api/recordings/{{ session.session_id }}/download.zip">session.zip</a>
                        {% if session.recording_group_id %}
                            <br><a href="/api/groups/{{ session.recording_group_id }}/download.zip">group.zip</a>
                        {% endif %}
                        <br><a href="/api/files/{{ session.metadata_filename }}">metadata</a>
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

        {% if total_pages > 1 %}
            <div class="pagination">
                {% if prev_page %}
                    <a href="/?{{ build_query_string(prev_page) }}">&laquo; Previous</a>
                {% else %}
                    <span style="color: #ccc;">&laquo; Previous</span>
                {% endif %}

                <span>Page {{ page }} of {{ total_pages }}</span>

                {% if next_page %}
                    <a href="/?{{ build_query_string(next_page) }}">Next &raquo;</a>
                {% else %}
                    <span style="color: #ccc;">Next &raquo;</span>
                {% endif %}
            </div>
        {% endif %}
    {% else %}
        <p>No recordings found.</p>
    {% endif %}
</body>
</html>
        """
        return render_template_string(
            html,
            total_items=total_items,
            total_pages=total_pages,
            page=page,
            page_size=page_size,
            sessions_page=sessions_page,
            status=status,
            from_date_str=from_date_str,
            to_date_str=to_date_str,
            group_id=group_id,
            prev_page=prev_page,
            next_page=next_page,
            build_query_string=build_query_string,
            min=min,
        )

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
