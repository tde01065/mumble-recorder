"""Streaming WAV writer with support for continuous and received_audio_only modes."""
import os
import wave
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

AUDIO_SAMPLE_RATE = 48000
AUDIO_CHANNELS = 1
AUDIO_SAMPLE_WIDTH = 2  # 16-bit


def pcm_duration_seconds(pcm_bytes: bytes) -> float:
    """Calculate audio duration in seconds from PCM byte length."""
    num_samples = len(pcm_bytes) // AUDIO_SAMPLE_WIDTH
    return num_samples / AUDIO_SAMPLE_RATE


def silence_frame_bytes(duration_seconds: float) -> bytes:
    """Generate silence PCM data for given duration."""
    num_samples = int(duration_seconds * AUDIO_SAMPLE_RATE)
    return b"\x00" * (num_samples * AUDIO_SAMPLE_WIDTH)


class StreamingWavWriter:
    """Streaming WAV writer. Writes incrementally; never buffers full recording."""

    def __init__(self, file_path: str, recording_mode: str):
        self.file_path = file_path
        self.recording_mode = recording_mode
        self.wav_file = None
        self.total_duration_seconds = 0.0

    def open(self) -> None:
        os.makedirs(os.path.dirname(self.file_path), exist_ok=True)
        self.wav_file = wave.open(self.file_path, "wb")
        self.wav_file.setnchannels(AUDIO_CHANNELS)
        self.wav_file.setsampwidth(AUDIO_SAMPLE_WIDTH)
        self.wav_file.setframerate(AUDIO_SAMPLE_RATE)

    def write_pcm(self, pcm_bytes: bytes) -> None:
        if self.wav_file is None:
            raise RuntimeError("WAV file not opened")
        if not pcm_bytes:
            return
        self.wav_file.writeframes(pcm_bytes)
        self.total_duration_seconds += pcm_duration_seconds(pcm_bytes)

    def write_silence(self, duration_seconds: float) -> None:
        """Write silence (continuous mode only; ignored in received_audio_only)."""
        if self.recording_mode != "continuous":
            return
        if duration_seconds <= 0:
            return
        self.write_pcm(silence_frame_bytes(duration_seconds))

    def close(self) -> None:
        if self.wav_file is not None:
            self.wav_file.close()
            self.wav_file = None
            file_size = os.path.getsize(self.file_path)
            logger.info(
                f"Closed WAV: {self.file_path} "
                f"({file_size} bytes, {self.total_duration_seconds:.2f}s)"
            )

    def get_file_size(self) -> int:
        return os.path.getsize(self.file_path) if os.path.exists(self.file_path) else 0


class SegmentWriter:
    """Writes a single segment to disk, streaming audio as it arrives."""

    def __init__(
        self,
        file_path: str,
        recording_mode: str,
        segment_start_monotonic: float,
        segment_start_wall_clock: datetime,
        segment_max_duration: float,
    ):
        self.file_path = file_path
        self.recording_mode = recording_mode
        self.segment_start_wall_clock = segment_start_wall_clock
        self.segment_max_duration = segment_max_duration

        self.writer = StreamingWavWriter(file_path, recording_mode)
        self.segment_wall_clock_elapsed = 0.0

        # Tracks the monotonic end-time of the last written audio chunk.
        # Initialised to segment start so the first chunk's gap is measured
        # from the segment boundary, not from t=0 of the process.
        self.last_audio_end_monotonic = segment_start_monotonic

    def open(self) -> None:
        self.writer.open()

    def write_chunk(self, monotonic_timestamp: float, pcm_bytes: bytes) -> None:
        """Write one audio chunk. In continuous mode inserts silence for gaps.

        Gap is measured from the END of the previous chunk (receive_time +
        chunk_duration), not from its receive timestamp. This avoids inflating
        the silence by the duration of the previous chunk itself.
        """
        if not pcm_bytes:
            return

        chunk_duration = pcm_duration_seconds(pcm_bytes)
        gap_seconds = max(0.0, monotonic_timestamp - self.last_audio_end_monotonic)

        if self.recording_mode == "continuous" and gap_seconds > 0:
            self.writer.write_silence(gap_seconds)

        self.writer.write_pcm(pcm_bytes)

        # Advance timeline to end of this chunk.
        self.last_audio_end_monotonic = monotonic_timestamp + chunk_duration

    def finalize(self, actual_wall_clock_elapsed: float) -> None:
        """Close the segment, padding to actual_wall_clock_elapsed in continuous mode.

        Uses actual_wall_clock_elapsed (not segment_max_duration) so that the
        last partial segment is padded to its real duration, not the full
        segment window. E.g. for a 25s recording with 10s segments the last
        segment receives elapsed=5 and is padded to 5s, not 10s.
        """
        self.segment_wall_clock_elapsed = actual_wall_clock_elapsed

        if self.recording_mode == "continuous":
            remaining = max(0.0, actual_wall_clock_elapsed - self.writer.total_duration_seconds)
            if remaining > 0:
                self.writer.write_silence(remaining)

        self.writer.close()

    def get_accumulated_audio_duration(self) -> float:
        """Get current accumulated audio duration for this segment (before finalization)."""
        return self.writer.total_duration_seconds

    def get_metadata(self) -> dict:
        return {
            "wall_clock_duration": self.segment_wall_clock_elapsed,
            "audio_duration": self.writer.total_duration_seconds,
            "file_size": self.writer.get_file_size(),
        }
