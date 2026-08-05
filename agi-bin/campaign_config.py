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

from amd_detector import AMDParams

# Keyed by dialplan extension number (as passed in ${EXTEN} to this AGI,
# identical to how VD_amd.agi receives it).
EXTENSION_OVERRIDES = {
    "8369": {  # NORMAL
    },
    "8373": {  # SURVEYCAMP
    },
    "8375": {  # SURVEYCAMPCEP
    },
}

# Optional: override by campaign_id instead of/in addition to extension,
# looked up from vicidial_auto_calls the same way VD_amd.agi does. Leave
# empty until you have campaign_ids you want to tune individually.
CAMPAIGN_ID_OVERRIDES = {
    # "MYCAMPAIGN": {"tone_peak_ratio": 0.30},
}


def get_params(extension=None, campaign_id=None) -> AMDParams:
    params = AMDParams()

    overrides = {}
    if extension and extension in EXTENSION_OVERRIDES:
        overrides.update(EXTENSION_OVERRIDES[extension])
    if campaign_id and campaign_id in CAMPAIGN_ID_OVERRIDES:
        overrides.update(CAMPAIGN_ID_OVERRIDES[campaign_id])

    for key, value in overrides.items():
        if not hasattr(params, key):
            raise ValueError(f"Unknown AMDParams field in override: {key}")
        setattr(params, key, value)

    return params
