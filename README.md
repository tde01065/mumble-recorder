# mumble-recorder

Standalone headless Mumble recorder service. Records mixed channel audio to WAV.

**Current scope:** Spike proof-of-concept. Tests basic connectivity, channel joining, and audio capture.

## Requirements

- Docker
- Access to a Mumble (Murmur) server
- Channel name and optional password

## Quick Start

1. Copy the environment template:
   ```bash
   cp .env.example .env.local
   ```

2. Edit `.env.local` with your Mumble server details:
   ```bash
   MUMBLE_HOST=your.mumble.server
   MUMBLE_PORT=64738
   MUMBLE_USERNAME=RecorderBot
   MUMBLE_PASSWORD=yourpassword  # if required
   MUMBLE_CHANNEL=ChannelName
   RECORDING_SECONDS=30
   ```

3. Ensure `.env.local` is in `.gitignore` and never committed (it contains secrets).

## Environment Variables

| Variable | Default | Required | Notes |
|----------|---------|----------|-------|
| `MUMBLE_HOST` | - | Yes | Mumble server hostname or IP |
| `MUMBLE_PORT` | `64738` | No | Mumble server port |
| `MUMBLE_USERNAME` | `RecorderBot` | No | Bot display name in Mumble |
| `MUMBLE_PASSWORD` | `` (empty) | No | Server password if required |
| `MUMBLE_CHANNEL` | - | Yes | Channel name to join and record |
| `RECORDING_SECONDS` | `30` | No | Duration to record in seconds |
| `MUMBLE_CONNECT_TIMEOUT_SECONDS` | `30` | No | Max seconds to wait for connection |
| `OUTPUT_FILE` | `/recordings/spike-recording.wav` | No | Output WAV file path (inside container) |

## Build

```bash
docker build -t mumble-recorder:spike .
```

## Run (Docker with env file)

```bash
mkdir -p recordings

# Linux/macOS:
docker run --rm \
  --env-file .env.local \
  -v "$(pwd)/recordings:/recordings" \
  mumble-recorder:spike

# Git Bash on Windows with WSL/Linux Docker daemon:
REC_DIR="$(pwd | sed 's#^/c/#/mnt/c/#')/recordings"
MSYS_NO_PATHCONV=1 docker run --rm \
  --env-file .env.local \
  --mount type=bind,source="$REC_DIR",target=/recordings \
  mumble-recorder:spike
```

Output file will be at `recordings/spike-recording.wav` (local directory).

## Run (Docker Compose)

1. Copy and edit the example config:
   ```bash
   cp .env.example .env.local
   # Edit .env.local with your server details
   ```

2. Start recording:
   ```bash
   docker-compose -f docker-compose.example.yml up
   ```

3. Output appears in `./recordings/`

## Verify Recording

```bash
# Check file exists and has content
ls -lh recordings/spike-recording.wav

# Play the file (with ffplay, sox, or media player)
ffplay recordings/spike-recording.wav
```

## Spike Proof-of-Concept Results

**Status:** ✅ Technically proven

- Docker image builds successfully.
- Container connects to private Mumble server.
- Bot authenticates and lists channels.
- Bot joins channel successfully.
- Audio callbacks received and processed.
- WAV file written successfully.

**Test result:** 30.2s wall-clock recording produced a 15.80s WAV with 790 audio callbacks and 1,516,800 PCM bytes.

**Format verified:** `ffprobe` confirms PCM s16le (16-bit signed little-endian), mono, 48 kHz.

### Important Design Finding

The spike implements **received-audio-only** recording. It writes only PCM chunks received from Mumble callbacks; it does not preserve wall-clock silence. This is acceptable for the spike and represents a design choice for the MVP.

## MVP Recording Modes (Planned)

The MVP will support two recording modes:

- **`continuous`** (default): Preserves wall-clock duration by inserting silence between received audio chunks. Segment rotation is based on wall-clock time.
- **`received_audio_only`**: Writes only PCM chunks received from Mumble, similar to the spike. Suitable for archival of active speech only.

In `continuous` mode, each segment exposes its exact wall-clock start time.

### Segment Filename Convention (Planned)

Segment filenames follow a session-aware pattern:

```
YYYYMMDD-HHMMSS_channelname_sessionid_seg-YYYYMMDD-HHMMSS.wav
```

Example:
```
20260615-102713_test_s7f3a_seg-20260615-105713.wav
```

This pattern avoids ambiguity in multi-session or archival contexts by embedding both the session start and segment start timestamps.

## Spike Limitations & Troubleshooting

**Audio path validation required:** If logs show "NO AUDIO CALLBACKS RECEIVED" or "NO AUDIO DATA RECEIVED", the audio path is not working. This is a blocker for the spike. Check:
- Channel name matches exactly (case-sensitive)
- Server has active speakers in that channel
- Bot has permission to join and receive audio

**Current spike limitations:**
- Mixed channel audio only (no per-user tracks).
- Single recording at a time.
- Receives audio only; does not preserve wall-clock silence.
- No file segmentation, retention, or resume on restart.
- No web UI yet.
- No metadata storage yet.
- **pymumble maintenance note:** usable for spike, but maintenance risk documented for MVP investment decisions.

## Logs

The spike logs all major steps:
- Connection status
- Available channels
- Selected channel and ID
- Audio callback count and byte total
- Final WAV file size
- Warnings if no PCM payload was recorded

## Output

- **Success**: WAV file exists, contains PCM audio, is playable
- **No audio**: Logs indicate if no audio callbacks were received or if no PCM payload was recorded
- **File format**: WAV, 48 kHz, 16-bit signed, mono
