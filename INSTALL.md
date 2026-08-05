# Custom AMD engine v2 — install guide (vici-06 / ViciBox 11, openSUSE Leap 15.5)

This build was written directly against your real system: your dialplan
(`extensions.conf` lines 507-539), your real `VD_amd.agi`, and the
`PJSIP`/`Kamailio0281` trunk. It replaces nothing until you explicitly
choose to at the "Cutover" step below — everything before that is purely
additive and safe to remove at any time.

## What's in this build

```
agi-bin/
  amd_detector.py         core engine: energy/silence timing + FFT beep/tone detection
  asterisk_agi.py         minimal AGI protocol client (stdlib only)
  amd_agi.py              the dialplan-facing AGI script (cutover replacement for AMD())
  campaign_config.py      per-extension/campaign threshold overrides
  db_logger.py            logs decisions to vicidial_custom_amd_log, reuses existing DB creds
  shadow_batch_analyze.py cron script for zero-risk shadow-mode comparison
  test_synthetic.py       smoke test — no real call needed
sql/
  schema.sql              creates vicidial_custom_amd_log
```

Verified in the sandbox this was built in: all files byte-compile clean,
and `test_synthetic.py` correctly classifies a synthetic short-greeting-
then-pause sample as HUMAN and a synthetic long-greeting-then-beep sample
as MACHINE, and does NOT false-positive on a sustained multi-harmonic tone
(a stand-in for a held human vowel) — it falls back to timing-based
`LONGGREETING` instead, exactly the intended behavior. Not yet run against
a real phone call recording — that's step 1 below.

## 1. Dependencies

Your system (confirmed earlier): openSUSE Leap 15.5 / ViciBox 11, Python
3.6.15 default. This build only needs the stdlib (`wave`, `audioop`) plus
`numpy` and `pymysql` — no torch, no GPU, nothing heavy, unlike the other
Whisper-based approach we looked at. Both are available directly in your
existing repos:

```bash
zypper install python3-numpy python3-PyMySQL
```

Confirm:
```bash
python3 -c "import numpy, pymysql; print('ok')"
```

## 2. Copy files onto vici-06

Same directory `VD_amd.agi` already lives in:

```bash
cp agi-bin/*.py /var/lib/asterisk/agi-bin/
chmod +x /var/lib/asterisk/agi-bin/amd_agi.py /var/lib/asterisk/agi-bin/shadow_batch_analyze.py
chown asterisk:asterisk /var/lib/asterisk/agi-bin/amd_detector.py \
    /var/lib/asterisk/agi-bin/asterisk_agi.py /var/lib/asterisk/agi-bin/amd_agi.py \
    /var/lib/asterisk/agi-bin/campaign_config.py /var/lib/asterisk/agi-bin/db_logger.py \
    /var/lib/asterisk/agi-bin/shadow_batch_analyze.py
```
(adjust the owning user/group if your `VD_amd.agi` is owned by something
other than `asterisk` — check with `ls -la /var/lib/asterisk/agi-bin/VD_amd.agi` first.)

## 3. Create the logging table

This reuses your existing ViciDial database credentials (read from
`/etc/astguiclient.conf` automatically at runtime — nothing hardcoded).
Run the schema once, with whatever DB access method you normally use:

```bash
mysql -u <VARDB_user> -p <VARDB_database> < sql/schema.sql
```
(get `VARDB_user`/`VARDB_database` values from `/etc/astguiclient.conf` if
you don't already have them handy.)

## 4. Sanity-check on this box before touching the dialplan at all

```bash
cd /var/lib/asterisk/agi-bin
python3 test_synthetic.py
```
Should print `PASS: synthetic smoke test succeeded`. If it doesn't, stop
here and send me the output — don't proceed to the dialplan changes below
until this passes on vici-06 itself (Python version, numpy install, file
permissions can all differ from the sandbox this was built in).

## 5. Shadow mode — zero risk to live calls

Create the shadow recording directory:
```bash
mkdir -p /var/spool/asterisk/monitor/shadow_amd
chown asterisk:asterisk /var/spool/asterisk/monitor/shadow_amd
```

**Back up the dialplan file first:**
```bash
cp /etc/asterisk/extensions.conf /etc/asterisk/extensions.conf.bak-$(date +%Y%m%d)
```

Add ONE line, right after the existing `Playback(sip-silence)` line and
BEFORE the existing `AMD(...)` line, for each of 8369/8373/8375. This is
the only dialplan change in shadow mode — nothing else moves:

```
exten => 8369,n,Playback(sip-silence)
exten => 8369,n,MixMonitor(/var/spool/asterisk/monitor/shadow_amd/${UNIQUEID}.wav)
exten => 8369,n,AMD(2000,2000,1000,5000,120,50,4,256)     ; <-- unchanged, still drives real routing
exten => 8369,n,AGI(VD_amd.agi,${EXTEN})                   ; <-- unchanged
exten => 8369,n,AGI(agi-VDAD_ALL_outbound.agi,NORMAL-----LB-----${CONNECTEDLINE(name)})
exten => 8369,n,Hangup()
```
Repeat the same single `MixMonitor(...)` insertion for the 8373 and 8375
blocks (lines 521-526 and 534-539 in your current file).

`MixMonitor` is a passive tap — it does not block or consume the channel's
audio, so stock `AMD()`/`VD_amd.agi`/`VDAD_ALL_outbound.agi` keep behaving
exactly as they do today. Real call routing is 100% unaffected.

Reload just the dialplan (no Asterisk restart, no dropped calls):
```bash
asterisk -rx "dialplan reload"
```

Set up the comparison cron job:
```bash
crontab -e
# add:
*/5 * * * * /usr/bin/python3 /var/lib/asterisk/agi-bin/shadow_batch_analyze.py >> /var/log/asterisk/shadow_amd.log 2>&1
```

Let this run for a few days across real call volume, then check agreement:
```sql
SELECT
  SUM(agreement=1) AS agree,
  SUM(agreement=0) AS disagree,
  SUM(agreement IS NULL) AS unresolved,
  COUNT(*) AS total
FROM vicidial_custom_amd_log
WHERE mode='SHADOW';
```
And to see specific disagreements (the interesting ones — these are where
stock AMD and the custom engine differ, worth listening to the recording
for):
```sql
SELECT call_uniqueid, custom_status, custom_cause, stock_status, recording_path
FROM vicidial_custom_amd_log
WHERE mode='SHADOW' AND agreement=0
ORDER BY created_at DESC LIMIT 50;
```

## 6. Cutover (only after shadow-mode data looks good)

**Back up again first:**
```bash
cp /etc/asterisk/extensions.conf /etc/asterisk/extensions.conf.bak-cutover-$(date +%Y%m%d)
```

Replace the stock `AMD(...)` line with the custom AGI, per extension:
```
exten => 8369,n,Playback(sip-silence)
exten => 8369,n,AGI(amd_agi.py,${EXTEN})                   ; <-- replaces AMD(...)
exten => 8369,n,AGI(VD_amd.agi,${EXTEN})                   ; <-- unchanged, reads our AMDSTATUS/AMDCAUSE/AMDSTATS
exten => 8369,n,AGI(agi-VDAD_ALL_outbound.agi,NORMAL-----LB-----${CONNECTEDLINE(name)})
exten => 8369,n,Hangup()
```
You can drop the `MixMonitor(...)` shadow-mode line at this point (or keep
it for ongoing QA — your choice; it's still harmless either way).

Reload:
```bash
asterisk -rx "dialplan reload"
```

I'd strongly suggest cutting over ONE extension first (e.g. just 8369),
watching `vicidial_custom_amd_log` (now with `mode='LIVE'`) and real
campaign results for a day, before doing 8373/8375.

## 7. Rollback (any time, either phase)

```bash
cp /etc/asterisk/extensions.conf.bak-<date> /etc/asterisk/extensions.conf
asterisk -rx "dialplan reload"
```
No Asterisk restart needed, no dropped calls, back to exactly today's
behavior.

## 8. Tuning

Edit `campaign_config.py`'s `EXTENSION_OVERRIDES` (keyed by `"8369"`,
`"8373"`, `"8375"`) to adjust any `AMDParams` field per extension — most
useful ones to try first, based on the disagreement data:
- `tone_peak_ratio` (lower = more sensitive beep detection, more false
  positives risk)
- `tone_min_duration_ms` (how long a tone must sustain before it counts)
- `max_number_of_words` / `greeting` (same knobs stock AMD exposes)

## Known limitation

If `amd_agi.py` fails catastrophically before it can even initialize the
AGI connection (not a normal analysis failure — those are already caught
and default to `AMDSTATUS=NOTSURE`, the safe fallback), `${AMDSTATUS}`
never gets set at all, and `VD_amd.agi`'s check
(`$AMDSTATUS =~ /PERSON|HUMAN|NOTSURE|HANGUP/`) won't match an empty
value, which routes the call down its "machine" branch. This is an edge
case (an interpreter/import failure, not a runtime bug in the detection
logic) but it's why step 4 (running `test_synthetic.py` directly on
vici-06) matters before touching the dialplan at all — it confirms Python,
numpy, and file permissions are all correctly in place on the real box.
