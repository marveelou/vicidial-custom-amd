#!/usr/bin/env python3
"""
amd_agi.py

The Asterisk-facing AGI script for the custom AMD engine. This is what
replaces the stock `AMD(...)` line in extensions.conf during cutover:

    Before (line 509):
        exten => 8369,n,AMD(2000,2000,1000,5000,120,50,4,256)

    After:
        exten => 8369,n,AGI(amd_agi.py,${EXTEN})

It does NOT need to be paired with any other dialplan change --
VD_amd.agi (line 510) only reads ${AMDSTATUS}/${AMDCAUSE}/${AMDSTATS},
confirmed from the real script on vici-06, and doesn't care how they were
set. Everything downstream of line 509 keeps working exactly as it does
today.

What it does:
  1. Reads the AGI environment (channel, uniqueid, callerid, extension).
  2. Looks up per-extension (and optionally per-campaign) tuning from
     campaign_config.py.
  3. Records the live channel audio to a WAV file via AGI RECORD FILE,
     capped at total_analysis_time and with Asterisk's own silence
     detection as an early-exit, same shape as stock AMD's own blocking
     analysis window.
  4. Runs amd_detector.analyze_wav() against that recording (energy/
     silence timing model + FFT beep/tone detection).
  5. Sets AMDSTATUS / AMDCAUSE / AMDSTATS via SET VARIABLE.
  6. Logs the decision to vicidial_custom_amd_log (best-effort, never
     fatal to the call if the DB write fails).

Exits silently on any unexpected error with AMDSTATUS=NOTSURE rather than
letting an exception kill the AGI mid-call -- VD_amd.agi's default branch
for anything other than PERSON|HUMAN|NOTSURE|HANGUP is "treat as machine
and hang up", so on our own internal failure we deliberately set NOTSURE
(routed as human/uncertain -> falls through to the agent, the safer
failure mode for a live campaign) rather than risk misrouting a real
human caller to the machine-handling path because of a bug in our code.
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
KEEP_RECORDINGS = True  # set False once you've validated and want to save disk


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
        safe_fallback(agi, "NORECORDDIR")
        return

    campaign_id = None
    try:
        campaign_id = db_logger.lookup_campaign_id(callerid)
    except Exception:
        pass  # optional enrichment only

    params = get_params(extension=extension, campaign_id=campaign_id)

    safe_uniqueid = (uniqueid or f"noid-{int(time.time())}").replace("/", "_")
    recording_basename = os.path.join(RECORDING_DIR, f"amd-{safe_uniqueid}")
    recording_path = recording_basename + ".wav"

    try:
        silence_secs = max(1, round(params.after_greeting_silence / 1000.0))
        agi.record_file(
            recording_basename,
            fmt="wav",
            escape_digits="",
            timeout_ms=params.total_analysis_time,
            silence_secs=silence_secs,
        )
    except Exception:
        agi.verbose("amd_agi: RECORD FILE failed:\n" + traceback.format_exc(), 1)
        safe_fallback(agi, "RECORDFAIL")
        return

    if not os.path.exists(recording_path) or os.path.getsize(recording_path) < 44:
        # no usable audio captured (e.g. immediate hangup) -- stock AMD's
        # equivalent case is NOAUDIODATA
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
        pass  # logging is best-effort; never let it affect call handling

    if not KEEP_RECORDINGS:
        try:
            os.remove(recording_path)
        except OSError:
            pass


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # absolute last-resort guard: never let an uncaught exception here
        # leave the channel in an undefined AMD state.
        sys.stderr.write("amd_agi: fatal error:\n" + traceback.format_exc())
        sys.exit(0)
