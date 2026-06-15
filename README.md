# mumble-recorder

Standalone headless Mumble recorder service. Records mixed channel audio to WAV.

**Current scope:** Recorder core MVP implementation. Supports two recording modes (continuous and received_audio_only) with segment rotation, metadata, and session-aware filenames.

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
| `MUMBLE_USERNAME` | `Recorder` | No | Bot display name in Mumble |
| `MUMBLE_PASSWORD` | `` (empty) | No | Server password if required |
| `MUMBLE_CHANNEL` | - | Yes | Channel name to join and record |
| `RECORDING_SECONDS` | `60` | No | Duration to record in seconds |
| `MUMBLE_CONNECT_TIMEOUT_SECONDS` | `30` | No | Max seconds to wait for connection |
| `OUTPUT_DIR` | `/recordings` | No | Output directory for recording segments (inside container) |
| `RECORDING_MODE` | `continuous` | No | Recording mode: `continuous` or `received_audio_only` |
| `SEGMENT_DURATION_SECONDS` | `1800` | No | Segment duration in seconds (default 30 minutes) |

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

Output files will be at `recordings/` (local directory).

## Recording Modes

### Continuous Mode (default)
- **Behavior**: Preserves wall-clock duration by inserting silence between received audio chunks
- **Output**: Segment WAV files with duration approximately equal to segment duration (default 1800s)
- **Use case**: Archive entire call duration, including silence
- **Example**: 30-second run with 5 seconds of speech → 30 seconds of WAV (25s silence + 5s audio)

### Received Audio Only Mode
- **Behavior**: Writes only PCM chunks received from Mumble
- **Output**: Segment WAV files with duration matching actual received audio only
- **Use case**: Compact archive of speech only, reduce file size
- **Example**: 30-second run with 5 seconds of speech → 5 seconds of WAV

Both modes produce metadata JSON files with wall-clock duration recorded.

## Validation (Local Testing)

### Setup for Testing
```bash
# Copy example config
cp .env.example .env.local

# Edit .env.local with your Mumble server details, then set short durations:
RECORDING_MODE=continuous
SEGMENT_DURATION_SECONDS=10
RECORDING_SECONDS=30
```

### Build and Run Tests
```bash
# Run unit tests
python -m pytest tests/ -v
# or with unittest:
python -m unittest discover tests/ -v
```

### Build Docker Image
```bash
docker build -t mumble-recorder:local .
```

### Continuous Mode Validation
```bash
# Set environment for testing
export $(cat .env.local | xargs)
export RECORDING_MODE=continuous
export SEGMENT_DURATION_SECONDS=10
export RECORDING_SECONDS=30

# Run in Docker
mkdir -p recordings
REC_DIR="$(pwd)/recordings"
docker run --rm \
  -e MUMBLE_HOST="$MUMBLE_HOST" \
  -e MUMBLE_PORT="$MUMBLE_PORT" \
  -e MUMBLE_USERNAME="$MUMBLE_USERNAME" \
  -e MUMBLE_PASSWORD="$MUMBLE_PASSWORD" \
  -e MUMBLE_CHANNEL="$MUMBLE_CHANNEL" \
  -e RECORDING_SECONDS=30 \
  -e RECORDING_MODE=continuous \
  -e SEGMENT_DURATION_SECONDS=10 \
  -v "$REC_DIR:/recordings" \
  mumble-recorder:local

# Verify output
ls -lh recordings/
```

### Check Duration with ffprobe
```bash
# Check WAV duration and properties
ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1:noprint_wrappers=1 recordings/*.wav

# Or with wave module
python -c "import wave; w=wave.open('recordings/*.wav'); print(f'Duration: {w.getnframes() / w.getframerate():.2f}s')"
```

### Received Audio Only Validation
```bash
# Set received_audio_only mode
export RECORDING_MODE=received_audio_only
export SEGMENT_DURATION_SECONDS=10

# Run and observe shorter WAV durations
# (only actual audio received, no silence padding)
```

### Verify Metadata
```bash
# Check session metadata
cat recordings/*_session_metadata.json | python -m json.tool

# Should show:
# - recording_mode: continuous or received_audio_only
# - wall_clock_duration_seconds: ~30 (total session time)
# - audio_duration_seconds: varies by mode
# - segment_duration_seconds: 10
# - segments: list of segment metadata with timestamps and durations
```

### Spike Diagnostic Entrypoint
The original spike is still available for manual diagnostic use:
```bash
# Run spike directly (in-memory recording, no segments/metadata)
python -m mumble_recorder.spike
```

## Verify Recording

```bash
# Check files in output directory
ls -lh recordings/

# Play a segment WAV file
ffplay recordings/*.wav
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

### Segment Filename Convention

Segment filenames follow a session-aware pattern:

```
YYYYMMDD-HHMMSS_channelname_sessionid_seg-YYYYMMDD-HHMMSS.wav
```

Example:
```
20260615-102713_test_s7f3a_seg-20260615-105713.wav
```

This pattern avoids ambiguity by embedding both the session start and segment start timestamps.

## Recorder Core Features (Current Implementation)

**Status:** ✅ MVP implementation complete

- Two recording modes: `continuous` and `received_audio_only`
- Wall-clock segment rotation (configurable duration)
- Session and segment metadata (JSON)
- Session-aware filenames with timestamps
- Streaming WAV writer (no full audio buffering)
- Silence insertion in continuous mode
- Thread-safe audio callback
- Support for short segment durations for testing

## Spike Limitations & Troubleshooting

**Audio path validation required:** If logs show "NO AUDIO CALLBACKS RECEIVED" or "NO AUDIO DATA RECEIVED", the audio path is not working. This is a blocker. Check:
- Channel name matches exactly (case-sensitive)
- Server has active speakers in that channel
- Bot has permission to join and receive audio

**Current implementation scope:**
- Mixed channel audio only (no per-user tracks).
- Single recording at a time.
- No file retention, cleanup, or resume on restart.
- No web UI yet.
- **pymumble maintenance note:** usable for MVP, but maintenance risk documented for future investments.

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
