#!/usr/bin/env python3
"""
campaign_config.py

Per-extension / per-campaign threshold overrides for the custom AMD engine.

Confirmed from the live vici-06 dialplan (extensions.conf):
    8369 -> NORMAL campaign type       (agi-VDAD_ALL_outbound.agi,NORMAL-----LB-...)
    8373 -> SURVEYCAMP campaign type   (agi-VDAD_ALL_outbound.agi,SURVEYCAMP-----LB-...)
    8375 -> SURVEYCAMPCEP campaign type(agi-VDAD_ALL_outbound.agi,SURVEYCAMPCEP-----LB-...)

All three currently run stock AMD with identical parameters
(2000,2000,1000,5000,120,50,4,256). There is no requirement that the custom
engine's tuning match that, or that all three extensions use the same
tuning -- this file is where you'd split them apart once shadow-mode data
shows they behave differently (e.g. survey campaigns may have shorter/
scripted greetings than a general outbound campaign).

Edit EXTENSION_OVERRIDES below to tune per extension. Anything not
overridden falls back to AMDParams' built-in defaults.
"""

import json
import os

from amd_detector import AMDParams

# Keyed by dialplan extension number (as passed in ${EXTEN} to this AGI,
# identical to how VD_amd.agi receives it).
#
# Defaults below deliberately MIRROR the confirmed live stock AMD() call on
# all three extensions -- AMD(2000,2000,1000,5000,120,50,4,256) -- so that,
# out of the box, the ONLY behavioral difference vs. today's stock AMD is
# the new FFT beep/tone detector layered on top. That makes any shadow-mode
# disagreement attributable to the tone detector specifically, rather than
# to incidental timing differences -- the fair, apples-to-apples baseline
# for deciding whether the new logic is actually an improvement before you
# start tuning it away from stock.
_STOCK_BASELINE = {
    "initial_silence": 2000,
    "greeting": 2000,
    "after_greeting_silence": 1000,
    "total_analysis_time": 5000,
    "min_word_length": 120,
    "between_words_silence": 50,
    "max_number_of_words": 4,
    "silence_threshold": 256,
}

EXTENSION_OVERRIDES = {
    "8369": dict(_STOCK_BASELINE),  # NORMAL
    "8373": dict(_STOCK_BASELINE),  # SURVEYCAMP
    "8375": dict(_STOCK_BASELINE),  # SURVEYCAMPCEP
}

# Optional: override by campaign_id instead of/in addition to extension,
# looked up from vicidial_auto_calls the same way VD_amd.agi does. Leave
# empty until you have campaign_ids you want to tune individually.
CAMPAIGN_ID_OVERRIDES = {
    # "MYCAMPAIGN": {"tone_peak_ratio": 0.30},
}

# --- optional JSON overlay, for the web dashboard's Settings page ---
# If campaign_overrides.json exists next to this file, it takes priority
# over the hardcoded dicts above (per-field, per-extension/campaign_id).
# This lets the dashboard persist tuning changes without ever touching
# Python source -- editing source from a web form is a bad idea (a typo
# becomes a syntax error that breaks amd_agi.py on every call), so all
# dashboard-driven tuning goes through this JSON file instead.
_OVERRIDES_JSON_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "campaign_overrides.json")


def _load_json_overlay():
    if not os.path.exists(_OVERRIDES_JSON_PATH):
        return {}, {}
    try:
        with open(_OVERRIDES_JSON_PATH) as f:
            data = json.load(f)
        return data.get("extension_overrides", {}), data.get("campaign_id_overrides", {})
    except Exception:
        # Never let a malformed settings file break call handling -- fall
        # back to the hardcoded defaults above.
        return {}, {}


def get_params(extension=None, campaign_id=None) -> AMDParams:
    params = AMDParams()

    json_ext_overrides, json_campaign_overrides = _load_json_overlay()

    overrides = {}
    if extension and extension in EXTENSION_OVERRIDES:
        overrides.update(EXTENSION_OVERRIDES[extension])
    if extension and extension in json_ext_overrides:
        overrides.update(json_ext_overrides[extension])
    if campaign_id and campaign_id in CAMPAIGN_ID_OVERRIDES:
        overrides.update(CAMPAIGN_ID_OVERRIDES[campaign_id])
    if campaign_id and campaign_id in json_campaign_overrides:
        overrides.update(json_campaign_overrides[campaign_id])

    for key, value in overrides.items():
        if not hasattr(params, key):
            raise ValueError(f"Unknown AMDParams field in override: {key}")
        setattr(params, key, value)

    return params
