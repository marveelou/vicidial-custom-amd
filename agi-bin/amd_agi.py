#!/usr/bin/env python3
"""
amd_agi.py -- Asterisk-facing AGI script for the custom AMD engine.
"""

import os
import sys
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from asterisk_agi import AGI
from amd_detector import analyze_wav
from campaign_config import get_params
import db_logger

RECORDING_DIR = "/var/spool/asterisk/monitor/custom_amd"
KEEP_RECORDINGS = True


def safe_fallback(agi, reason):
    agi.set_variable("AMDSTATUS", "NOTSURE")
    agi.set_variable("AMDCAUSE", f"{reason}-0-0")
    agi.set_variable("AMDSTATS", "0-0")


def main():
    agi = AGI()

    extension = None
    if len(sys.argv) > 1 and sys.argv[1]:
        extension = sys.argv[1]
    else:
        extension = agi.env.get("agi_extension")

    channel = agi.env.get("agi_channel", "")
    uniqueid = agi.env.get("agi_uniqueid", "")
    callerid = agi.env.get("agi_callerid", "")

    try:
        os.makedirs(RECORDING_DIR, exist_ok=True)
    except OSError as e:
        agi.verbose(f"amd_agi: could not create {RECORDING_DIR}: {e}", 1)

    campaign_id = None
    try:
        # 2026-08-10: kept as best-effort enrichment, but confirmed via live
        # debug logging that none of uniqueid/callerid/phone_number reliably
        # identify a COVECOLL predictive-dialer call against
        # vicidial_auto_calls. Do NOT rely on campaign_id resolving here --
        # use extension_overrides keyed on "8369" instead (see
        # campaign_overrides.json), which already reliably scopes to this
        # canary's live traffic.
        campaign_id = db_logger.lookup_campaign_id(uniqueid, callerid=callerid)
    except Exception:
        pass

    params = get_params(extension=extension, campaign_id=campaign_id)

    safe_uniqueid = (uniqueid or f"noid-{int(time.time())}").replace("/", "_")
    recording_path = os.path.join(RECORDING_DIR, f"{safe_uniqueid}.wav")

    time.sleep(params.total_analysis_time / 1000.0)

    try:
        agi.exec_app("StopMixMonitor")
    except Exception:
        agi.verbose("amd_agi: StopMixMonitor failed:\n" + traceback.format_exc(), 1)

    if not os.path.exists(recording_path) or os.path.getsize(recording_path) < 44:
        agi.set_variable("AMDSTATUS", "NOTSURE")
        agi.set_variable("AMDCAUSE", "NOAUDIODATA-0-0")
        agi.set_variable("AMDSTATS", "0-0")
        try:
            db_logger.log_decision(
                call_uniqueid=uniqueid, campaign_id=campaign_id,
                extension=extension, mode="LIVE",
                custom_status="NOTSURE", custom_cause="NOAUDIODATA-0-0",
                custom_run_time_ms=0, custom_total_time_ms=0,
                recording_path=recording_path,
            )
        except Exception:
            pass
        return

    try:
        result = analyze_wav(recording_path, params)
    except Exception:
        agi.verbose("amd_agi: analyze_wav failed:\n" + traceback.format_exc(), 1)
        safe_fallback(agi, "ANALYZEFAIL")
        return

    agi.set_variable("AMDSTATUS", result.status)
    agi.set_variable("AMDCAUSE", result.amdcause_str())
    agi.set_variable("AMDSTATS", result.amdstats_str())

    try:
        db_logger.log_decision(
            call_uniqueid=uniqueid,
            campaign_id=campaign_id,
            extension=extension,
            mode="LIVE",
            custom_status=result.status,
            custom_cause=result.amdcause_str(),
            custom_run_time_ms=result.run_time_ms,
            custom_total_time_ms=result.total_time_ms,
            custom_detail=result.detail,
            recording_path=recording_path,
        )
    except Exception:
        pass

    if not KEEP_RECORDINGS:
        try:
            os.remove(recording_path)
        except OSError:
            pass


if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.stderr.write("amd_agi: fatal error:\n" + traceback.format_exc())
        sys.exit(0)
