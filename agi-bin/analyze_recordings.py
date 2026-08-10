#!/usr/bin/env python3
"""
Pulls today's calls with a given vicidial_log.status (default "A" -- agent
manually flagged as answering machine) for CoveColl, finds each one's
recording file (already logged in vicidial_custom_amd_log.recording_path),
and runs the same segment-level diagnostic as inspect_segments.py against
each one -- so you can see exactly why our engine made the call it did,
not just the final status/cause string.

Safe: individual indexed lookups only, no JOIN against vicidial_log.

Run this directly on vici-06:
    python3 analyze_recordings.py [STATUS] [N]
    # STATUS = vicidial_log.status to sample, default "A"
    # N = how many to sample, default 5
"""

import sys
import os

sys.path.insert(0, '/var/lib/asterisk/agi-bin')

import db_logger
from amd_detector import (
    AMDParams,
    _read_wav_mono_pcm16,
    _energy_silence_segments,
    _timing_based_decision,
    _detect_beep_tone,
)
from campaign_config import get_params


def analyze_one(path, campaign_id=None):
    params = get_params(extension="8369", campaign_id=campaign_id)

    sample_rate, raw_pcm16 = _read_wav_mono_pcm16(path)
    total_time_ms = int(len(raw_pcm16) / 2 / sample_rate * 1000)
    max_samples = int(sample_rate * (params.total_analysis_time / 1000.0))
    analysis_pcm16 = raw_pcm16[: max_samples * 2]

    print(f"file: {path}")
    print(f"sample_rate: {sample_rate}  total_time_ms: {total_time_ms}")

    tone_found, tone_start_ms = _detect_beep_tone(analysis_pcm16, sample_rate, params)
    print(f"beep tone detected: {tone_found} (start_ms={tone_start_ms})")

    segments = _energy_silence_segments(analysis_pcm16, sample_rate, params)
    print(f"{len(segments)} segments (is_speech, duration_ms):")
    for i, (is_speech, dur_ms) in enumerate(segments):
        flag = ""
        if is_speech and dur_ms < params.min_word_length:
            flag = "  <-- shorter than min_word_length, should be SKIPPED as noise"
        elif is_speech:
            flag = "  <-- counts as a word"
        print(f"  [{i:2d}] {'SPEECH ' if is_speech else 'silence'} {dur_ms:5d}ms{flag}")

    result = _timing_based_decision(segments, params, total_time_ms)
    print(f"re-analyzed decision: status={result.status} cause={result.cause} "
          f"run_time_ms={result.run_time_ms} total_time_ms={result.total_time_ms}")


def main():
    status = sys.argv[1] if len(sys.argv) > 1 else "A"
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else 5

    conn = db_logger._connect()
    cur = conn.cursor()

    cur.execute("""
        SELECT uniqueid, user FROM vicidial_log
        WHERE campaign_id='COVECOLL' AND status=%s AND call_date >= CURDATE()
        ORDER BY call_date DESC LIMIT %s
    """, (status, limit))
    rows = cur.fetchall()
    print(f"Sampling {len(rows)} '{status}'-status calls from today\n")

    for uid, user in rows:
        cur.execute(
            "SELECT recording_path, custom_status, custom_cause FROM vicidial_custom_amd_log "
            "WHERE call_uniqueid = %s",
            (uid,),
        )
        row = cur.fetchone()
        print("=" * 70)
        print(f"call {uid}  (agent={user!r})")
        if row is None:
            print("  -> not found in vicidial_custom_amd_log at all\n")
            continue

        recording_path, custom_status, custom_cause = row
        print(f"  original live decision: {custom_status} {custom_cause}")

        if not recording_path or not os.path.exists(recording_path):
            print(f"  -> recording file not found on disk: {recording_path}\n")
            continue

        try:
            analyze_one(recording_path)
        except Exception as e:
            print(f"  -> error analyzing recording: {e}")
        print()

    conn.close()


if __name__ == "__main__":
    main()
