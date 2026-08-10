# Custom AMD engine v2 — install guide (vici-06 / ViciBox 11, openSUSE Leap 15.5)

This build was written directly against your real system: your dialplan
(`extensions.conf` lines 507-539), your real `VD_amd.agi`, and the
`PJSIP`/`Kamailio0281` trunk. It replaces nothing until you explicitly
choose to at the "Cutover" step below — everything before that is purely
additive and safe to remove at any time.

## What's in this build

```
agi-bin/
  amd_detector.py               core engine: energy/silence timing + FFT beep/tone detection
  asterisk_agi.py               minimal AGI protocol client (stdlib only)
  amd_agi.py                    the dialplan-facing AGI script (cutover replacement for AMD())
  campaign_config.py            per-extension/campaign threshold overrides + JSON overlay support
  campaign_overrides.json       tuning values that don't require a code deploy (see Tuning, step 8)
  db_logger.py                  logs decisions to vicidial_custom_amd_log, reuses existing DB creds
  shadow_batch_analyze.py       cron script for zero-risk shadow-mode comparison (writes to the DB log)
  rescore_processed_readonly.py read-only re-scoring tool — measure a code/config change's real effect
                                 against already-processed recordings without touching production data
  inspect_segments.py           diagnostic tool — prints the raw speech/silence segments + decision
                                 for one recording, to debug a specific disagreement
  test_synthetic.py             smoke test — no real call needed
sql/
  schema.sql                    creates vicidial_custom_amd_log
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

Add a retention cron so `shadow_amd/processed/` (recordings already scored)
doesn't fill the tmpfs `/var/spool/asterisk/monitor` lives on — check
`df -h` first; on vici-06 this is a dedicated 6.0G tmpfs shared with other
Asterisk recording use, and average shadow recordings are only ~40KB each,
but they add up fast at real call volume:
```bash
crontab -e
# add:
24 0 * * * /usr/bin/find /var/spool/asterisk/monitor/shadow_amd/processed -maxdepth 1 -type f -mtime +2 -print | xargs -r rm -f
```

### Current shadow-mode status (as of 2026-08-06)

Shadow mode is live on 8369 and has been running against real call volume
for about a day. **Real, verified agreement with actual call outcomes is
98.1%**, after two important findings from that day of tuning — read both
before changing anything further, they'll save you from repeating a wasted
day:

1. **A ground-truth lookup bug inflated every disagreement number for most
   of the day.** `db_logger.lookup_stock_result()` originally matched a
   call's real outcome using only the epoch-second prefix of its
   `uniqueid` as a SQL `LIKE` wildcard, discarding the sequence suffix that
   actually distinguishes one call from another. On a busy dialer, two
   distinct calls placed in the same second share that prefix — confirmed
   directly on real data — so the lookup could silently grade a recording
   against a *different* call's outcome. This has been **fixed** (exact
   `uniqueid` match, no wildcard) — if you ever see accuracy numbers that
   don't make sense or swing wildly in ways that don't fit a coherent
   story, check this lookup logic before doubting the detector itself.
2. **Gap/pause-duration-based merging does not work** for telling one
   human's fragmented continuous speech apart from one real machine
   greeting's multiple short-paused phrases — tried twice (once globally,
   once scoped to post-greeting only), both times it regressed accuracy by
   trading one false-direction problem for a bigger one in the other
   direction. Don't retry this without a fundamentally different signal
   (e.g. spectral/pitch continuity across the gap, not just its duration).

The one change that *did* help, safely and measurably: tuning
`max_number_of_words` down from the stock default of 4 to **1**, via
`campaign_overrides.json` (see step 8) — this is a real, deployed,
production tuning value, not just a test. See the sweep table in step 8
for the full before/after numbers.

Use `rescore_processed_readonly.py` after any future code or config
change — it's read-only (never touches production data or the DB log) and
gives a direction/cause breakdown, not just raw accuracy, so you can see
*which way* a change moved things:
```bash
python3 /var/lib/asterisk/agi-bin/rescore_processed_readonly.py
```

## 6. Cutover (only after shadow-mode data looks good)

**⚠️ CRITICAL — read this before touching the dialplan (added 2026-08-07
after a real production incident, see "Cutover incident" section below for
the full story):** `amd_agi.py` does **NOT** record its own audio. It relies
entirely on a `MixMonitor` tap that the **dialplan** must start *before*
the AGI script runs, and it must stop that same tap itself
(`StopMixMonitor`). If you cut over using only the old two-line swap shown
in earlier versions of this doc (`AGI(amd_agi.py,${EXTEN})` replacing
`AMD(...)` with no `MixMonitor` around it), the recording will either not
exist at all, or — worse — start late and silently produce wrong decisions
on real live calls. **The MixMonitor line is not optional and not just for
shadow mode anymore.**

**Back up again first:**
```bash
cp /etc/asterisk/extensions.conf /etc/asterisk/extensions.conf.bak-cutover-$(date +%Y%m%d)
```

Replace the stock `AMD(...)` line with the custom AGI, per extension —
note the `MixMonitor`/`StopMixMonitor` lines bracketing it, this is the
**required** shape:
```
exten => 8369,n,Playback(sip-silence)
exten => 8369,n,MixMonitor(/var/spool/asterisk/monitor/custom_amd/${UNIQUEID}.wav)  ; <-- REQUIRED, not optional
exten => 8369,n,AGI(amd_agi.py,${EXTEN})                   ; <-- replaces AMD(...)
exten => 8369,n,StopMixMonitor()                            ; <-- REQUIRED (harmless no-op if amd_agi.py already stopped it)
exten => 8369,n,AGI(VD_amd.agi,${EXTEN})                   ; <-- unchanged, reads our AMDSTATUS/AMDCAUSE/AMDSTATS
exten => 8369,n,AGI(agi-VDAD_ALL_outbound.agi,NORMAL-----LB-----${CONNECTEDLINE(name)})
exten => 8369,n,Hangup()
```
Make sure `/var/spool/asterisk/monitor/custom_amd` exists and is writable
before reloading (`mkdir -p /var/spool/asterisk/monitor/custom_amd`) —
MixMonitor does not create missing directories for you.

Reload:
```bash
asterisk -rx "dialplan reload"
```

I'd strongly suggest cutting over ONE extension first (e.g. just 8369),
watching `vicidial_custom_amd_log` (now with `mode='LIVE'`) and real
campaign results for a day, before doing 8373/8375.

### Safer alternative to editing 8369 directly: canary via Routing Extension

Instead of editing the shared `8369` extension (which instantly affects
every campaign routed through it), ViciDial's **Campaign Detail** admin
screen has a per-campaign **"Routing Extension"** field. You can create a
new, separate extension (e.g. `8399`) with the exact dialplan shape above,
and point **one low-traffic or dedicated test campaign's** Routing
Extension field at it — leaving `8369` itself, and every other campaign,
completely untouched. Reverting is instant: just switch that one
campaign's field back to `8369`, no dialplan edit or reload needed.

**Two important gotchas discovered doing this (2026-08-07):**

1. **Pass the real extension's number as the AGI argument, not `${EXTEN}`.**
   `amd_agi.py` looks up tuning overrides keyed by extension string
   (`campaign_config.py`/`campaign_overrides.json` only has entries for
   `"8369"`, `"8373"`, `"8375"`). A canary extension like `8399` must call
   `AGI(amd_agi.py,8369)` — hardcoded — so it picks up 8369's tuned
   `max_number_of_words=1`, instead of silently evaluating `${EXTEN}` to
   `"8399"` and falling back to untested generic defaults.
2. **A synthetic CLI test cannot validate this end-to-end.** `asterisk -rx
   "channel originate Local/8399@default application Echo"` is useful for
   confirming the dialplan chain executes without crashing, but a bare
   `Local` channel bridged to `Echo()` generates only a tiny burst of audio
   (tens of milliseconds), regardless of which recording mechanism is
   used — it cannot prove anything about real speech timing. **Manual
   dial from the agent screen doesn't work either** — confirmed via
   `extensions.conf` log tracing that ViciDial routes manual dials through
   a completely different path (`Dial(PJSIP/...)` directly) that never
   touches the AMD/routing-extension dialplan at all, since AMD only
   exists to help the *auto-dialer* decide how to route a call — an
   agent manually dialing has already decided. **The only valid live test
   is a real call placed by the actual auto-dialer**, which means adding a
   single test lead (your own phone number) to a dedicated test campaign's
   lead list, with an agent logged in ready to take it, and letting the
   real dialer place that one call.

## Cutover incident — 2026-08-07 (read before any future cutover attempt)

A canary was attempted on the real `CoveColl` campaign using the Routing
Extension trick above, pointed at a new extension `8399` running
`amd_agi.py`. Within seconds of going live, real customer calls were
returning `MACHINE`/`MAXWORDS` almost universally. **The campaign's
Routing Extension was reverted back to `8369` immediately** (this is
exactly why the canary approach — rather than editing 8369 directly — was
used; the blast radius was contained to one campaign and reverting took
seconds, no dialplan change).

**Root cause:** at the time, `amd_agi.py` called Asterisk's `RECORD FILE`
itself, from inside the AGI script. That means audio capture only started
*after* the AGI round-trip — script launch, Python startup, module
imports, and a `campaign_id` DB lookup — had already happened. This is a
real, measurable delay that shadow mode's recordings never had (shadow
mode's `MixMonitor` starts at the *dialplan* level, before any AGI even
runs). Since the `max_number_of_words=1` tuning was calibrated entirely
against MixMonitor-captured recordings (always starting from true time
zero), analyzing a late-started recording made completely normal
conversations look artificially word-dense — confirmed directly by pulling
the actual flagged recording and inspecting its segments
(`inspect_segments.py`): it started with `SPEECH` at 0ms, no leading
silence at all, unlike every correct shadow-mode recording.

**Fix:** `amd_agi.py` no longer records anything itself. It now waits
(`time.sleep`) for the same kind of dialplan-level `MixMonitor` tap shadow
mode already used successfully, then stops it (`StopMixMonitor`) and reads
the resulting file. This is the dialplan shape now required everywhere
`amd_agi.py` is invoked — see the required shape in step 6 above. Deployed
and confirmed via `dialplan show` that the block has no duplicate lines
(an early attempt to insert the `MixMonitor`/`StopMixMonitor` lines via a
pattern-matching `awk` script was accidentally run twice and duplicated
them — **lesson: dialplan-editing scripts should regenerate a whole
marked block between comment markers, not do fragile mid-block pattern
insertion, so re-running them is always safe**).

**Validation after the fix:**
- A real auto-dialer-placed test call (single test lead in a dedicated
  test campaign, per the canary methodology above) produced a proper
  5.1-second recording starting with 2.86 seconds of genuine leading
  silence before the first speech segment — confirming the timing bug is
  gone. (The test number itself had a one-way audio issue unrelated to
  this project, so the call's actual MACHINE/MAXWORDS decision on that one
  call isn't meaningful on its own — repeated "hello?" attempts from not
  hearing a response look similar to multiple machine-greeting phrases
  under `max_number_of_words=1`.)
- Cross-checked against the existing shadow-mode dataset instead (which
  has always used this same MixMonitor-first mechanism): of 21,585 real
  shadow-mode calls where the custom engine decided `MACHINE`, only 284
  were actually real humans — a **1.32% false-positive rate**, consistent
  with the previously-measured 98.1% overall accuracy, and nowhere close
  to the near-100% failure rate seen during the incident. This is strong
  indirect evidence the fix resolves the same failure mode, using a much
  larger sample than any single test call could provide.

**Not yet done as of this writing:** a fresh, closely-monitored live
canary re-attempt with the fixed code has not yet happened. Do this before
any real cutover, and revert-on-first-sign-of-trouble exactly as before.

## 7. Rollback (any time, either phase)

```bash
cp /etc/asterisk/extensions.conf.bak-<date> /etc/asterisk/extensions.conf
asterisk -rx "dialplan reload"
```
No Asterisk restart needed, no dropped calls, back to exactly today's
behavior.

## 8. Tuning

**Prefer `campaign_overrides.json` over editing `campaign_config.py`
directly.** `campaign_config.py` already has a JSON-overlay mechanism built
in for exactly this (see `_load_json_overlay()` / `get_params()`) — it lets
you change a tuning value without touching Python source at all, so a typo
can't turn into a syntax error that breaks live call handling. It's read
fresh on every call (and by every run of `rescore_processed_readonly.py`),
so a change here takes effect immediately with no restart:
```bash
cat > /var/lib/asterisk/agi-bin/campaign_overrides.json << 'JSONEOF'
{
  "extension_overrides": {
    "8369": {"max_number_of_words": 1}
  }
}
JSONEOF
```
Remember to keep `/usr/src/vicidial-custom-amd/agi-bin/campaign_overrides.json`
in sync (copy + `git commit`) after any change here, same as any other
deployed file — it's easy to forget since it's JSON, not Python.

**Current production value for 8369: `max_number_of_words=1`** (stock
default is 4), set 2026-08-06 after sweeping the full range against real
shadow-mode data (see status note in step 5 for the ground-truth-bug
context this was measured under):

| max_number_of_words | accuracy | missed machines | false machines |
|---|---|---|---|
| 4 (stock default) | 93.0% | 679 | 33 |
| 3 | 96.9% | 221 | 69 |
| 2 | 97.9% | 75 | 121 |
| **1 (current)** | **98.1%** | **10** | **165** |
| 0 | 98.1% | 11 | 168 (no further gain — floor) |

**Direction matters and is easy to get backwards** (this cost one wasted
round-trip on 2026-08-06): *lower* `max_number_of_words` = *stricter* =
triggers `MACHINE` with fewer excess words = catches more real machines
but risks flagging more human sentences. *Higher* = *looser* = the
opposite. Before changing it, confirm via `rescore_processed_readonly.py`'s
direction breakdown which failure mode is actually dominant right now —
don't assume it's still "too many false machines," that was true earlier
in the day under the ground-truth bug and flipped once it was fixed.

Other knobs worth trying next, based on current disagreement data (16
missed real beeps as of 2026-08-06 — see step 5):
- `tone_peak_ratio` (lower = more sensitive beep detection, more false
  positives risk)
- `tone_min_duration_ms` (how long a tone must sustain before it counts)

**Do not** try gap/pause-duration-based merging of short silence segments
as a way to fix human-sentence word-miscounting — this was attempted twice
(2026-08-06) and regressed accuracy both times, for a fundamental reason
(see step 5). If tempted to revisit it, first find a way to distinguish a
human's continuous speech from a machine's scripted phrases using
something other than gap duration.

## 9. TOOLONG → MACHINE toggle (2026-08-10)

**What TOOLONG means:** when the entire `total_analysis_time` window
(5000ms) elapses as one continuous speech segment with no silence break
long enough to end it, `_timing_based_decision()` falls through to
`AMDResult("NOTSURE", "TOOLONG", ...)` — a deliberately safe default,
since a continuous 5-second block of speech could in principle be either
a human mid-sentence or a machine's unbroken greeting.

**Why this got revisited:** CoveColl's "A" status (agent manually flagged
answering machine after being connected) was running ~180/day. A 20-call
sample of "A" calls found 11/20 (55%) were `TOOLONG`-caused `NOTSURE`
decisions that the agent then caught seconds later — call length data
from ViciDial's own outbound report corroborated this (nearly all "A"
calls lasted only 4–13 seconds total, consistent with a machine playing an
unbroken greeting that both our engine and the agent independently
recognized almost immediately).

**Validation before deploying:** `toolong_validation.py` (ad-hoc, not
checked in — cross-references `custom_cause LIKE 'TOOLONG%'` LIVE
decisions against real `vicidial_log.status` via individual indexed
lookups, no JOIN) against 300 real TOOLONG-caused calls found 289/300
(96.3%) were confirmed real machines (status `A`), and only 2/300 (0.67%)
were confirmed live human conversations (`DNC`/`NI` — the only two
statuses in the sample that require an agent to have actually talked to
someone). The rest (`DROP`/`PDROP`) are ambiguous — dropped by the dialer
for capacity reasons regardless of our decision, not confirmable either
way.

**The fix:** added a new `AMDParams.toolong_as_machine` field (default
`False`) to `amd_detector.py` — when `True`, the TOOLONG branch returns
`MACHINE` instead of `NOTSURE` (cause string stays `"TOOLONG"` either way,
so it's still distinguishable in logs). Toggled per extension/campaign via
`campaign_overrides.json`, same as any other tuning value.

**Dead end, worth remembering — `campaign_id_overrides` doesn't work for
predictive-dialer campaigns:** the first two attempts scoped this to
COVECOLL via `campaign_id_overrides`, which depends on
`db_logger.lookup_campaign_id()` resolving `campaign_id` from
`vicidial_auto_calls`. Confirmed via live debug logging that **none** of
the three plausible join keys work for this call type:
  - `uniqueid` — the column exists on `vicidial_auto_calls` but stays
    `NULL` for the entire lifecycle of a COVECOLL predictive-dialer call
    (confirmed via 6 one-second snapshots, including one row that went
    `SENT` → `DISCONNECT` while still `NULL`). Not a timing race — it's
    simply never populated for this call/campaign type.
  - `callerid` (`agi_callerid`, matched against
    `vicidial_auto_calls.callerid`) — turned out to be the **campaign's
    fixed presented outbound CallerID** (identical across every call
    sampled, e.g. `18665956701`), not a per-call identifier. It can never
    match `vicidial_auto_calls.callerid`, which holds a different,
    internal per-call tracking token (e.g. `V8101012110049787017`).
  - `phone_number` — never available in `amd_agi.py`'s AGI environment at
    all for this call type; nothing to pass in.

  **Net result: `campaign_id` cannot currently be resolved for CoveColl's
  live traffic at all**, by any method tried so far. `campaign_id_overrides`
  is not unusable in general — `campaign_id` values that DO come from a
  reliable source (e.g. if a future integration passes it explicitly)
  will still work fine — but don't assume the DB-lookup path resolves it
  for predictive-dialer campaigns without testing first. If this needs
  revisiting, the honest next step is finding what channel variable (if
  any) ViciDial's own dialer sets with the real campaign_id or phone
  number before handing off to the AMD extension, rather than trying to
  reverse-engineer it from `vicidial_auto_calls` again.

**What actually worked:** since `amd_agi.py` is only ever invoked LIVE for
the CoveColl canary right now (routed via extension 8399, but the AGI
call's hardcoded tuning-lookup argument is `"8369"` — see step 6's canary
note), *every* live decision it logs already is a CoveColl call, by
construction of the canary itself. No campaign_id lookup needed. The
toggle just needed to go on the existing `extension_overrides."8369"`
entry:
```bash
python3 -c "
import json
path = '/var/lib/asterisk/agi-bin/campaign_overrides.json'
with open(path) as f: data = json.load(f)
data.setdefault('extension_overrides', {}).setdefault('8369', {})['toolong_as_machine'] = True
with open(path, 'w') as f: json.dump(data, f, indent=2)
"
```
Confirmed working live 2026-08-10 12:05–12:06 (fresh TOOLONG calls showing
`MACHINE` instead of `NOTSURE` within 2 minutes of deploy).

**Current production value for 8369:** `{"max_number_of_words": 1,
"toolong_as_machine": true}`.

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
