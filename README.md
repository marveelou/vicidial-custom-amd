# vicidial-custom-amd

Custom Answering Machine Detection (AMD) engine for ViciDial, built to
replace stock Asterisk AMD() on vici-06. Stock AMD was producing too
many false positives and false negatives; this engine uses an
energy/silence timing model plus FFT beep/tone detection, tuned against
real call data.

Status (2026-08-07): Shadow mode has been running continuously against
real production traffic since 2026-08-06, with 98.1% agreement against
real call outcomes (see INSTALL.md for the full tuning story). Live
cutover (Phase 4) is in progress -- a canary attempt on a real campaign
surfaced and fixed a real production bug (see "Cutover incident" in
INSTALL.md); a fresh, closely-monitored canary re-attempt is the next
step before considering a full extension cutover.

See INSTALL.md for full install steps, the required dialplan shape,
shadow-mode setup, canary/cutover methodology, tuning guidance, and a
detailed history of what's been tried (including dead ends, so they
aren't repeated).

## Layout

agi-bin/amd_detector.py - core engine: energy/silence timing + FFT beep/tone detection
agi-bin/asterisk_agi.py - minimal AGI protocol client (stdlib only)
agi-bin/amd_agi.py - dialplan-facing AGI script (cutover replacement for AMD())
agi-bin/campaign_config.py - per-extension/campaign threshold overrides + JSON overlay support
agi-bin/campaign_overrides.json - tuning values that don't require a code deploy
agi-bin/db_logger.py - logs decisions to vicidial_custom_amd_log
agi-bin/shadow_batch_analyze.py - cron script for zero-risk shadow-mode comparison
agi-bin/rescore_processed_readonly.py - read-only re-scoring tool for measuring config/code changes
agi-bin/inspect_segments.py - diagnostic tool for one recording's speech/silence segments
agi-bin/test_synthetic.py - smoke test, no real call needed
sql/schema.sql - creates vicidial_custom_amd_log

## Safety notes

- Nothing in this repo affects live call routing until the dialplan is
  explicitly changed to call amd_agi.py in place of AMD(...) -- see
  INSTALL.md step 6.
- amd_agi.py relies on a dialplan-level MixMonitor tap for its
  recording -- it does NOT record audio itself. This is required, not
  optional (see the "Cutover incident" section in INSTALL.md for why).
- DB credentials are always read from /etc/astguiclient.conf at runtime
  -- nothing is hardcoded in this repo.
