#!/usr/bin/env python3
"""
rescore_processed_readonly.py

Read-only re-scoring tool for measuring the real-world effect of a detector
code change (e.g. the 2026-08-06 min_word_length fix) WITHOUT touching
production data. It re-runs analyze_wav() with the current amd_detector.py
against files already sitting in shadow_amd/processed/, looks up each
call's real stock-AMD outcome the same way shadow_batch_analyze.py does,
and prints a match/disagree/accuracy summary -- but never calls
db_logger.log_decision() and never moves/deletes any file.

This is what you'd run right after deploying a detector fix, to see the
before/after accuracy delta on the same real-call population, before
drawing any conclusion about whether the fix actually helped.

Usage (on vici-06, same directory as the other agi-bin scripts):
    python3 rescore_processed_readonly.py
    python3 rescore_processed_readonly.py /var/spool/asterisk/monitor/shadow_amd/processed
    python3 rescore_processed_readonly.py <dir> --limit 600   # cap file count
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from amd_detector import analyze_wav
from campaign_config import get_params
import db_logger

DEFAULT_DIR = "/var/spool/asterisk/monitor/shadow_amd/processed"
EXTENSION = "8369"  # see shadow_batch_analyze.py for why this is hardcoded


def extract_uniqueid(filename):
    base = os.path.basename(filename)
    return base.rsplit(".", 1)[0]


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    rec_dir = args[0] if args else DEFAULT_DIR

    limit = None
    if "--limit" in sys.argv:
        idx = sys.argv.index("--limit")
        limit = int(sys.argv[idx + 1])

    params = get_params(extension=EXTENSION, campaign_id=None)

    wav_files = sorted(f for f in os.listdir(rec_dir) if f.lower().endswith(".wav"))
    if limit:
        wav_files = wav_files[:limit]

    if not wav_files:
        print(f"No .wav files found in {rec_dir}", file=sys.stderr)
        sys.exit(1)

    match = 0
    disagree = 0
    unknown = 0
    disagreements = []

    for fname in wav_files:
        path = os.path.join(rec_dir, fname)
        uniqueid = extract_uniqueid(fname)

        stock_status, stock_code = db_logger.lookup_stock_result(call_uniqueid=uniqueid)

        try:
            result = analyze_wav(path, params)
        except Exception as e:
            print(f"{fname}: analyze failed: {e}")
            continue

        if not stock_status:
            unknown += 1
            continue

        if stock_status == result.status:
            match += 1
        else:
            disagree += 1
            line = f"{fname}: custom={result.status} ({result.cause})  stock={stock_status}"
            disagreements.append(line)

    total_compared = match + disagree
    print(f"=== rescore_processed_readonly: {rec_dir} ===")
    print(f"files scanned: {len(wav_files)}")
    print(f"match={match} disagree={disagree} unknown={unknown}")
    if total_compared:
        print(f"accuracy: {match/total_compared*100:.1f}%  (over {total_compared} calls with a known stock outcome)")

    if disagreements:
        print()
        print(f"=== {len(disagreements)} disagreement(s) ===")
        for line in disagreements:
            print(line)


if __name__ == "__main__":
    main()
