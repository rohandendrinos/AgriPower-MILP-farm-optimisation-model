"""Roof tilt: the template's Flat / Low / High dropdown as a pitch in degrees.

The optimisation model has no such scheme (config.py uses one roof_tilt for every
roof orientation, 20 degrees as shipped). This mapping is the app's own translation
of the farmer's answer into that single figure. Illustrative pitches, not measured
angles: a nominally flat commercial roof still needs a shallow tilt for drainage and
output, and Low and High bracket common corrugated-roof pitches. The model's own
default, 20 degrees, is the Low bucket.
"""

ROOF_TILT_DEGREES_BY_BUCKET = {
    "Flat": 10.0,
    "Low": 20.0,
    "High": 35.0,
}


def tilt_degrees_for(bucket: str) -> float:
    return ROOF_TILT_DEGREES_BY_BUCKET[bucket]
