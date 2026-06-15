"""Tests for WAV writer."""
import os
import shutil
import tempfile
import unittest
import wave
from datetime import datetime, timezone

from mumble_recorder.wav_writer import (
    AUDIO_CHANNELS,
    AUDIO_SAMPLE_RATE,
    AUDIO_SAMPLE_WIDTH,
    SegmentWriter,
    StreamingWavWriter,
    pcm_duration_seconds,
    silence_frame_bytes,
)


def _pcm(duration_seconds: float) -> bytes:
    """Generate silent PCM of given duration."""
    return silence_frame_bytes(duration_seconds)


def _wav_duration(path: str) -> float:
    with wave.open(path, "rb") as w:
        return w.getnframes() / w.getframerate()


class TestWavWriterUtils(unittest.TestCase):

    def test_pcm_duration_seconds(self):
        bytes_per_second = AUDIO_SAMPLE_RATE * AUDIO_SAMPLE_WIDTH * AUDIO_CHANNELS
        self.assertAlmostEqual(pcm_duration_seconds(b"\x00" * bytes_per_second), 1.0, places=2)

    def test_pcm_duration_half_second(self):
        pcm = b"\x00" * ((AUDIO_SAMPLE_RATE // 2) * AUDIO_SAMPLE_WIDTH)
        self.assertAlmostEqual(pcm_duration_seconds(pcm), 0.5, places=2)

    def test_silence_frame_bytes_all_zeros(self):
        silence = silence_frame_bytes(1.0)
        expected = AUDIO_SAMPLE_RATE * AUDIO_SAMPLE_WIDTH
        self.assertEqual(len(silence), expected)
        self.assertEqual(silence, b"\x00" * expected)

    def test_silence_frame_duration_roundtrip(self):
        for dur in [0.1, 0.5, 1.0, 2.5]:
            self.assertAlmostEqual(pcm_duration_seconds(silence_frame_bytes(dur)), dur, places=2)


class TestStreamingWavWriter(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def _path(self, name):
        return os.path.join(self.tmp, name)

    def test_continuous_mode_basic(self):
        path = self._path("basic.wav")
        w = StreamingWavWriter(path, "continuous")
        w.open()
        w.write_pcm(_pcm(1.0))
        w.close()
        with wave.open(path, "rb") as wav:
            self.assertEqual(wav.getnchannels(), AUDIO_CHANNELS)
            self.assertEqual(wav.getsampwidth(), AUDIO_SAMPLE_WIDTH)
            self.assertEqual(wav.getframerate(), AUDIO_SAMPLE_RATE)

    def test_continuous_mode_silence_adds_to_total(self):
        path = self._path("silence.wav")
        w = StreamingWavWriter(path, "continuous")
        w.open()
        w.write_pcm(_pcm(1.0))
        w.write_silence(0.5)
        w.close()
        self.assertAlmostEqual(w.total_duration_seconds, 1.5, places=1)

    def test_received_audio_only_ignores_silence(self):
        path = self._path("audio_only.wav")
        w = StreamingWavWriter(path, "received_audio_only")
        w.open()
        w.write_pcm(_pcm(1.0))
        w.write_silence(0.5)  # must be ignored
        w.close()
        self.assertAlmostEqual(w.total_duration_seconds, 1.0, places=1)

    def test_empty_wav_is_valid(self):
        path = self._path("empty.wav")
        w = StreamingWavWriter(path, "continuous")
        w.open()
        w.close()
        self.assertTrue(os.path.exists(path))
        with wave.open(path, "rb") as wav:
            self.assertEqual(wav.getframerate(), AUDIO_SAMPLE_RATE)


class TestSegmentWriter(unittest.TestCase):
    """Tests for SegmentWriter gap calculation and segment finalisation."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def _seg(self, name, mode="continuous", start_mono=0.0, max_dur=10.0):
        return SegmentWriter(
            os.path.join(self.tmp, name),
            mode,
            start_mono,
            datetime.now(timezone.utc),
            max_dur,
        )

    # ------------------------------------------------------------------
    # Bug 2: gap must be measured from END of previous chunk, not its
    #         receive timestamp.
    # ------------------------------------------------------------------

    def test_gap_from_chunk_end_not_receive_time(self):
        """Continuous mode: silence inserted equals time from chunk END to next receive.

        Timeline:
          t=0.0  chunk-1 arrives, duration 0.5s  → audio occupies [0.0, 0.5)
          t=1.0  chunk-2 arrives, duration 0.5s  → gap from chunk-1 END = 1.0 - 0.5 = 0.5s

        Correct total after two chunks (before finalize):
          0.5s audio + 0.5s silence + 0.5s audio = 1.5s

        Old (buggy) code used receive timestamp as reference:
          0.5s audio + 1.0s silence + 0.5s audio = 2.0s  ← wrong
        """
        seg = self._seg("gap_test.wav")
        seg.open()
        seg.write_chunk(0.0, _pcm(0.5))
        seg.write_chunk(1.0, _pcm(0.5))
        # Check before finalize so we see only what was explicitly written.
        self.assertAlmostEqual(seg.writer.total_duration_seconds, 1.5, places=2)
        seg.finalize(1.5)

    def test_received_audio_only_no_gap_silence(self):
        """received_audio_only: no silence inserted regardless of gaps."""
        seg = self._seg("no_gap.wav", mode="received_audio_only")
        seg.open()
        seg.write_chunk(0.0, _pcm(0.5))
        seg.write_chunk(1.0, _pcm(0.5))
        # Only the two PCM chunks, no silence.
        self.assertAlmostEqual(seg.writer.total_duration_seconds, 1.0, places=2)
        seg.finalize(1.0)

    def test_first_chunk_gap_from_segment_start(self):
        """First chunk arriving 2s after segment start inserts 2s of leading silence."""
        seg = self._seg("leading_silence.wav", start_mono=0.0)
        seg.open()
        seg.write_chunk(2.0, _pcm(0.5))  # arrives 2s after segment start
        # 2s silence + 0.5s audio = 2.5s
        self.assertAlmostEqual(seg.writer.total_duration_seconds, 2.5, places=2)
        seg.finalize(2.5)

    # ------------------------------------------------------------------
    # Bug 3: finalize must pad to actual_wall_clock_elapsed, not max.
    # ------------------------------------------------------------------

    def test_finalize_pads_to_actual_elapsed_not_max(self):
        """Partial segment: finalize(5) with max=10 should produce 5s, not 10s."""
        seg = self._seg("partial.wav", max_dur=10.0)
        seg.open()
        seg.write_chunk(0.0, _pcm(2.0))  # 2s audio at segment start
        seg.finalize(5.0)  # actual elapsed = 5s (not the 10s max)

        dur = _wav_duration(os.path.join(self.tmp, "partial.wav"))
        self.assertAlmostEqual(dur, 5.0, places=1)

    def test_finalize_full_segment_pads_to_max(self):
        """Full segment: finalize(10) with max=10 pads to 10s."""
        seg = self._seg("full.wav", max_dur=10.0)
        seg.open()
        seg.write_chunk(0.0, _pcm(2.0))
        seg.finalize(10.0)

        dur = _wav_duration(os.path.join(self.tmp, "full.wav"))
        self.assertAlmostEqual(dur, 10.0, places=1)

    def test_25s_recording_produces_10_10_5_durations(self):
        """25s recording with 10s segments: three segments of ~10, ~10, ~5 seconds."""
        durations = [(10.0, 10.0), (10.0, 10.0), (5.0, 5.0)]  # (finalize_elapsed, expected_dur)

        for i, (elapsed, expected) in enumerate(durations):
            name = f"seg_{i}.wav"
            seg = self._seg(name, max_dur=10.0)
            seg.open()
            # No audio written — should be all-silence in continuous mode.
            seg.finalize(elapsed)

            dur = _wav_duration(os.path.join(self.tmp, name))
            self.assertAlmostEqual(dur, expected, places=1,
                                   msg=f"Segment {i}: expected {expected}s, got {dur:.2f}s")

    def test_no_audio_produces_silent_wav(self):
        """Continuous mode with no callbacks still produces a valid silent WAV."""
        seg = self._seg("silent.wav", max_dur=10.0)
        seg.open()
        seg.finalize(10.0)

        path = os.path.join(self.tmp, "silent.wav")
        self.assertTrue(os.path.exists(path))
        dur = _wav_duration(path)
        self.assertAlmostEqual(dur, 10.0, places=1)


if __name__ == "__main__":
    unittest.main()
