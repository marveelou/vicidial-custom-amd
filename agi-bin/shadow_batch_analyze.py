#!/usr/bin/env python3
"""
shadow_batch_analyze.py

The SAFE, zero-risk-to-live-calls way to evaluate this engine before
cutover. Run this from cron every few minutes.

How shadow mode works:
  1. In the dialplan, BEFORE the existing `AMD(...)` line, add a
     MixMonitor() call that records the whole call in the background.
     MixMonitor is a passive tap -- it does not consume or block the
     channel's audio, so stock AMD()/VD_amd.agi/VDAD_ALL_outbound.agi
     keep running exactly as they do today. Nothing about real call
     routing changes.
  2. This script periodically scans the shadow recording directory,
     runs the SAME detector logic amd_agi.py would use, and looks up
     what the REAL (stock-AMD-driven) call outcome was from
     vicidial_log.status.
  3. Both are logged side-by-side into vicidial_custom_amd_log with
     mode='SHADOW' so you can measure agreement/disagreement before
     ever letting the custom engine drive real routing.

Suggested dialplan addition (see INSTALL.md for the exact lines):
    exten => 8369,n,MixMonitor(${UNIQUEID}.wav,,/var/lib/asterisk/agi-bin/shadow_notify.sh)
  or simply:
    exten => 8369,n,MixMonitor(${UNIQUEID}.wav)
  pointed at RECORDING_DIR below (set via Asterisk's MIXMON_DIR or an
  absolute path argument -- see INSTALL.md).

Suggested cron entry:
    */5 * * * * /usr/bin/python3 /var/lib/asterisk/agi-bin/shadow_batch_analyze.py >> /var/log/asterisk/shadow_amd.log 2>&1
"""

import os
import sys
import shutil
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from amd_detector import analyze_wav
from campaign_config import get_params
import db_logger

RECORDING_DIR = "/var/spool/asterisk/monitor/shadow_amd"
PROCESSED_DIR = os.path.join(RECORDING_DIR, "processed")
MIN_AGE_SECONDS = 15  # skip files still possibly being written by Asterisk


def extract_uniqueid(filename):
    base = os.path.basename(filename)
    base = base.rsplit(".", 1)[0]
    return base


def process_one(path):
    uniqueid = extract_uniqueid(path)

    stock_status, stock_code = db_logger.lookup_stock_result(call_uniqueid=uniqueid)

    campaign_id = None
    try:
        # best-effort: vicidial_log carries campaign_id too, but we don't
        # have a dedicated lookup for it here -- extension-based defaults
        # are used unless you extend lookup_stock_result to also return it.
        pass
    except Exception:
        pass

    # All confirmed live campaigns currently route through extension 8369
    # (checked directly against production vicidial_campaigns.campaign_vdad_exten --
    # 19/19 active campaigns use 8369; 8373/8375 are wired in the dialplan
    # but carry no real traffic). Hardcoding this is what makes shadow-mode
    # comparisons fair: without it, get_params() silently falls back to
    # AMDParams' generic defaults (greeting=1500, max_number_of_words=3,
    # initial_silence=2500) instead of the confirmed stock baseline
    # (2000,2000,1000,5000,120,50,4,256) -- this mismatch was the dominant
    # cause of the inflated MAXWORDS/LONGGREETING disagreement rate seen
    # in the first shadow-mode batch on 2026-08-06. Revisit this if
    # 8373/8375 ever carry real traffic.
    extension = "8369"

    params = get_params(extension=extension, campaign_id=campaign_id)

    try:
        result = analyze_wav(path, params)
    except Exception:
        print(f"shadow_batch_analyze: analyze failed for {path}:")
        traceback.print_exc()
        return False

    db_logger.log_decision(
        call_uniqueid=uniqueid,
        campaign_id=campaign_id,
        extension=extension,
        mode="SHADOW",
        custom_status=result.status,
        custom_cause=result.amdcause_str(),
        custom_run_time_ms=result.run_time_ms,
        custom_total_time_ms=result.total_time_ms,
        custom_detail=result.detail,
        stock_status=stock_status,
        stock_cause=stock_code,
        recording_path=path,
    )

    agree = "?" if not stock_status else ("MATCH" if stock_status == result.status else "DISAGREE")
    print(f"{uniqueid}: custom={result.status} ({result.cause})  stock={stock_status or '?'}  [{agree}]")
    return True


def main():
    os.makedirs(RECORDING_DIR, exist_ok=True)
    os.makedirs(PROCESSED_DIR, exist_ok=True)

    now = time.time()
    count = 0

    for entry in os.listdir(RECORDING_DIR):
        full_path = os.path.join(RECORDING_DIR, entry)
        if not os.path.isfile(full_path):
            continue
        if not entry.lower().endswith(".wav"):
            continue
        try:
            age = now - os.path.getmtime(full_path)
        except OSError:
            continue
        if age < MIN_AGE_SECONDS:
            continue  # might still be open by Asterisk

        ok = process_one(full_path)
        count += 1

        dest = os.path.join(PROCESSED_DIR, entry)
        try:
            shutil.move(full_path, dest)
        except OSError as e:
            print(f"shadow_batch_analyze: could not move {full_path}: {e}")

    print(f"shadow_batch_analyze: processed {count} recording(s)")


if __name__ == "__main__":
    main()
