#!/usr/bin/env python3
"""
inspect_segments.py

Diagnostic tool: prints the raw speech/silence segment list amd_detector.py
computes for a given WAV file, plus the final decision. Use this to see
WHY a specific call was disagreed on -- in particular, to check whether
"noise blip" segments inside a MACHINE (MAXWORDS) result are actually
short (<120ms, what min_word_length assumes) or are longer than expected
(meaning min_word_length is set too low to filter them, and the
2026-08-06 fix's `continue` never fires on real audio).

Usage:
    python3 inspect_segments.py <path-to-wav>
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from amd_detector import (
    AMDParams,
    _read_wav_mono_pcm16,
    _energy_silence_segments,
    _timing_based_decision,
    _detect_beep_tone,
)
from campaign_config import get_params


def main():
    if len(sys.argv) < 2:
        print("usage: inspect_segments.py <path-to-wav>", file=sys.stderr)
        sys.exit(2)

    path = sys.argv[1]
    params = get_params(extension="8369", campaign_id=None)

    sample_rate, raw_pcm16 = _read_wav_mono_pcm16(path)
    total_time_ms = int(len(raw_pcm16) / 2 / sample_rate * 1000)
    max_samples = int(sample_rate * (params.total_analysis_time / 1000.0))
    analysis_pcm16 = raw_pcm16[: max_samples * 2]

    print(f"file: {path}")
    print(f"sample_rate: {sample_rate}  total_time_ms: {total_time_ms}")
    print(f"params: initial_silence={params.initial_silence} greeting={params.greeting} "
          f"after_greeting_silence={params.after_greeting_silence} "
          f"min_word_length={params.min_word_length} max_number_of_words={params.max_number_of_words} "
          f"max_word_length={params.max_word_length} silence_threshold={params.silence_threshold}")
    print()

    tone_found, tone_start_ms = _detect_beep_tone(analysis_pcm16, sample_rate, params)
    print(f"beep tone detected: {tone_found} (start_ms={tone_start_ms})")
    print()

    segments = _energy_silence_segments(analysis_pcm16, sample_rate, params)
    print(f"{len(segments)} segments (is_speech, duration_ms):")
    for i, (is_speech, dur_ms) in enumerate(segments):
        flag = ""
        if is_speech and dur_ms < params.min_word_length:
            flag = "  <-- shorter than min_word_length, should be SKIPPED as noise"
        elif is_speech:
            flag = "  <-- counts as a word"
        print(f"  [{i:2d}] {'SPEECH ' if is_speech else 'silence'} {dur_ms:5d}ms{flag}")

    print()
    result = _timing_based_decision(segments, params, total_time_ms)
    print(f"decision: status={result.status} cause={result.cause} "
          f"run_time_ms={result.run_time_ms} total_time_ms={result.total_time_ms}")


if __name__ == "__main__":
    main()
