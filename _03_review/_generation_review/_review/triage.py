"""Versioned, explainable technical flags; never writes a human rating."""
import math

VERSION = "gbr-technical-triage-1"
LIMITATION = "Signal checks flag possible technical problems. They do not assess composition, realism or artistic merit; intentional music can trigger them."


def number(value):
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else None


def technical_triage(metrics, events=()):
    required = {key: number(metrics.get(key)) for key in ("duration", "rms_dbfs", "clipped_fraction")}
    if any(value is None for value in required.values()) or required["duration"] <= 0 or not 0 <= required["clipped_fraction"] <= 1:
        return {"version": VERSION, "status": "not_assessed", "flags": [], "human_rating": None,
                "limitation": LIMITATION, "reason": "Required finite signal measurements are unavailable"}
    flags = []
    def flag(code, evidence, *, start=None, end=None):
        flags.append({"code": code, "evidence": evidence, "start": start, "end": end})
    if required["rms_dbfs"] <= -75:
        flag("near_silent_recording", {"rms_dbfs": required["rms_dbfs"], "threshold_dbfs": -75})
    if required["clipped_fraction"] >= .001:
        flag("frequent_full_scale_samples", {"fraction": required["clipped_fraction"], "threshold": .001,
                                              "meaning": "Full-scale sample occupancy; not proof of audible clipping"})
    true_peak = number(metrics.get("true_peak_dbtp_4x"))
    if true_peak is not None and true_peak > .5:
        flag("estimated_intersample_overs", {"estimate_dbtp": true_peak, "threshold_dbtp": .5, "certified_meter": False})
    correlation = number(metrics.get("stereo_correlation"))
    if correlation is not None and correlation < -.45:
        flag("possible_mono_cancellation", {"mean_correlation": correlation, "threshold": -.45})
    balance = number(metrics.get("channel_balance_db"))
    if balance is not None and abs(balance) >= 15 and required["rms_dbfs"] > -45:
        flag("channel_imbalance", {"mean_balance_db": balance, "threshold_db": 15})
    for event in events:
        start, end = number(event.get("start")), number(event.get("end"))
        if event.get("type") == "possible_dropout" and start is not None and end is not None and start >= 1 and end <= required["duration"] - 1 and end - start >= 1:
            flag("interior_quiet_gap", {"meaning": "Quiet region detected; may be an intentional rest"}, start=start, end=end)
    return {"version": VERSION, "status": "flagged" if flags else "no_strong_flags", "flags": flags,
            "human_rating": None, "limitation": LIMITATION}
