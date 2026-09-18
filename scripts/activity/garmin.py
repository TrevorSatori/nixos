#!/usr/bin/env python3
"""
Garmin Connect → InfluxDB poller.

Writes to two buckets:
  activity   → session,source=garmin,type=<sport> ... (start+stop event pairs)
  biometrics → biometric,source=garmin,metric=<name> value=<n> <ts_ms>

OAuth tokens cached under /var/lib/garmin-to-influx/token so restarts
don't hit the login endpoint. If the cached token expires the library
re-logs in with email+password from the environment.

Explanation of the HR fetch window:
  Every tick, we fetch today + yesterday and only write points strictly
  newer than the last one we saw. That way if the poller was down at
  midnight we still catch the tail of the previous day on next start.
"""
import json
import os
import time
import urllib.request
import zipfile
import io
from datetime import date, datetime, timedelta
from pathlib import Path

from garminconnect import Garmin, GarminConnectAuthenticationError

INFLUX_URL        = os.environ["INFLUX_URL"]
INFLUX_TOKEN      = os.environ["INFLUX_TOKEN"]
INFLUX_ORG        = os.environ["INFLUX_ORG"]
ACTIVITY_BUCKET   = os.environ.get("INFLUX_ACTIVITY_BUCKET",   "activity")
BIOMETRICS_BUCKET = os.environ.get("INFLUX_BIOMETRICS_BUCKET", "biometrics")
GARMIN_EMAIL      = os.environ["GARMIN_EMAIL"]
GARMIN_PASSWORD   = os.environ["GARMIN_PASSWORD"]
POLL_INTERVAL     = int(os.environ.get("POLL_INTERVAL", "600"))
DEVICE_NAME       = os.environ.get("GARMIN_DEVICE", "Forerunner 970")
FIT_ARCHIVE_DIR   = Path(os.environ.get("GARMIN_FIT_DIR", "/data/archive/garmin"))

STATE_DIR  = Path("/var/lib/garmin-to-influx")
TOKEN_DIR  = STATE_DIR / "token"
STATE_FILE = STATE_DIR / "state.json"

ACTIVITY_TYPE_MAP = {
    "running":              "running",
    "motorcycling_v2":     "motorcycle",
    "motorcycling":        "motorcycle",
    "treadmill_running":    "treadmill",
    "indoor_running":       "treadmill",
    "walking":              "walking",
    "hiking":               "hiking",
    "cycling":              "cycling",
    "road_biking":          "cycling",
    "mountain_biking":      "cycling",
    "indoor_cycling":       "indoor_cycling",
    "strength_training":    "strength",
    "yoga":                 "yoga",
    "cardio":               "cardio",
    "swimming":             "swimming",
    "lap_swimming":         "swimming",
    "squash":               "pornography",
}


# ── influx line-protocol helpers ──────────────────────────────────────────────

def _tag(s):   return str(s).replace(",", r"\,").replace("=", r"\=").replace(" ", r"\ ")
def _field(s): return str(s).replace("\\", "\\\\").replace('"', '\\"')


def write_influx(bucket, lines):
    if not lines:
        return 0
    body = "\n".join(lines).encode("utf-8")
    url  = f"{INFLUX_URL}/api/v2/write?org={INFLUX_ORG}&bucket={bucket}&precision=ms"
    req  = urllib.request.Request(
        url, data=body, method="POST",
        headers={
            "Authorization": f"Token {INFLUX_TOKEN}",
            "Content-Type":  "text/plain; charset=utf-8",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.status


def biometric_line(metric, value, ts_ms, extra_tags=""):
    tags = f"source=garmin,metric={_tag(metric)}"
    if extra_tags:
        tags += "," + extra_tags
    return f"biometric,{tags} value={value} {int(ts_ms)}"


# ── state ─────────────────────────────────────────────────────────────────────

def load_state():
    try:
        return json.loads(STATE_FILE.read_text())
    except Exception:
        return {}


def save_state(state):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))


# ── garmin client ─────────────────────────────────────────────────────────────

def get_client():
    TOKEN_DIR.mkdir(parents=True, exist_ok=True)
    client = Garmin(email=GARMIN_EMAIL, password=GARMIN_PASSWORD)
    try:
        client.login(str(TOKEN_DIR))
    except GarminConnectAuthenticationError as e:
        print(f"[ERROR] Garmin auth failed (MFA?): {e}", flush=True)
        raise
    return client


def safe(name, fn, *args, **kwargs):
    """Wrap a Garmin API call, log outcome, swallow errors so one broken
    endpoint doesn't kill the tick."""
    try:
        r = fn(*args, **kwargs)
        n = len(r) if hasattr(r, "__len__") else "?"
        print(f"[DEBUG] {name}: {type(r).__name__} n={n}", flush=True)
        return r
    except Exception as e:
        print(f"[WARN] {name} failed: {e}", flush=True)
        return None


# ── activities ────────────────────────────────────────────────────────────────

# ── .fit archive ──────────────────────────────────────────────────────────────

def archive_fit(client, activity_id, start_str, type_key):
    """Download an activity's original .fit and store it under FIT_ARCHIVE_DIR.

    Laid out as <archive>/<YYYY>/<MM>/<YYYY-MM-DD>_<type>_<id>.fit so files
    sort chronologically and are identifiable without opening them.

    Garmin returns ORIGINAL as a zip (usually one .fit inside). Anything that
    is not a zip is written through unchanged. Returns True if a new file was
    written, False if it already existed or the download failed — never raises,
    since archiving must not break the InfluxDB write path.
    """
    try:
        day = (start_str or "")[:10]              # YYYY-MM-DD
        year, month = (day[:4], day[5:7]) if len(day) >= 10 else ("unknown", "00")
        out_dir = FIT_ARCHIVE_DIR / year / month
        stem = f"{day or 'unknown'}_{type_key}_{activity_id}"
        out_path = out_dir / f"{stem}.fit"

        if out_path.exists():
            return False

        from garminconnect import Garmin as _G
        data = client.download_activity(
            str(activity_id), dl_fmt=_G.ActivityDownloadFormat.ORIGINAL
        )
        if not data:
            print(f"[WARN] fit {activity_id}: empty download", flush=True)
            return False

        out_dir.mkdir(parents=True, exist_ok=True)

        if data[:2] == b"PK":                     # zip container
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                fits = [n for n in zf.namelist() if n.lower().endswith(".fit")]
                if not fits:
                    # Keep whatever it is rather than silently dropping it.
                    zpath = out_dir / f"{stem}.zip"
                    zpath.write_bytes(data)
                    print(f"[WARN] fit {activity_id}: no .fit in zip, kept {zpath.name}", flush=True)
                    return True
                for i, name in enumerate(fits):
                    target = out_path if i == 0 else out_dir / f"{stem}_{i}.fit"
                    target.write_bytes(zf.read(name))
        else:
            out_path.write_bytes(data)

        print(f"[INFO] archived {out_path.relative_to(FIT_ARCHIVE_DIR)}", flush=True)
        return True
    except Exception as e:
        print(f"[WARN] fit {activity_id}: {e}", flush=True)
        return False


def poll_activities(client, state):
    last_id = state.get("last_activity_id")
    since   = (date.today() - timedelta(days=30)).isoformat()
    today   = date.today().isoformat()
    activities = safe("get_activities_by_date", client.get_activities_by_date, since, today) or []

    new = []
    for a in activities:  # Garmin returns newest first
        if last_id and str(a.get("activityId")) == str(last_id):
            break
        new.append(a)

    lines = []
    for a in reversed(new):
        aid       = a.get("activityId")
        raw_type  = (a.get("activityType") or {}).get("typeKey", "unknown")
        typ       = ACTIVITY_TYPE_MAP.get(raw_type, raw_type)
        name      = a.get("activityName") or raw_type.replace("_", " ").title()
        dur_s     = int(a.get("duration") or 0)
        start_str = a.get("startTimeLocal") or a.get("startTimeGMT")
        if not start_str or dur_s <= 0:
            continue
        start_ms = int(datetime.strptime(start_str, "%Y-%m-%d %H:%M:%S").timestamp() * 1000)
        end_ms   = start_ms + dur_s * 1000
        sid      = f"garmin-{aid}"

        tags         = f"source=garmin,type={_tag(typ)},device={_tag(DEVICE_NAME)}"
        start_fields = (
            f'title="{_field(name)}",'
            f"duration_s={dur_s}i,"
            f"wall_s={dur_s}i,"
            f'session_id="{sid}"'
        )
        stop_fields = (
            f'title="{_field(name)}",'
            f"duration_s=0i,"
            f"wall_s=0i,"
            f'session_id="{sid}"'
        )
        lines.append(f"session,{tags} {start_fields} {start_ms}")
        lines.append(f"session,{tags} {stop_fields} {end_ms}")

        # Archive the raw .fit alongside the metrics. Best-effort: a failure
        # here must not stop the InfluxDB write.
        archive_fit(client, aid, start_str, raw_type)

    if new:
        state["last_activity_id"] = str(new[0]["activityId"])
    return lines


# ── biometrics ────────────────────────────────────────────────────────────────

BACKFILL_DAYS = int(os.environ.get("GARMIN_BACKFILL_DAYS", "7"))


def _recent_days():
    """Return list of dates (oldest first) covering the last BACKFILL_DAYS days,
    including today. Widens the poll window so late syncs are picked up."""
    today = date.today()
    return [today - timedelta(days=i) for i in range(BACKFILL_DAYS - 1, -1, -1)]


def poll_hr(client, state):
    # No per-timestamp dedup — Garmin can backfill older days at any time, and
    # InfluxDB overwrites points with identical (measurement, tags, timestamp).
    lines = []
    for d in _recent_days():
        hr = safe(f"get_heart_rates({d})", client.get_heart_rates, d.isoformat()) or {}
        for entry in (hr.get("heartRateValues") or []):
            if not entry or len(entry) < 2:
                continue
            ts_ms, bpm = entry[0], entry[1]
            if bpm is None or ts_ms is None:
                continue
            lines.append(biometric_line("heart_rate", f"{int(bpm)}i", ts_ms))
        resting = hr.get("restingHeartRate")
        if resting is not None:
            day_ms = int(datetime.strptime(d.isoformat(), "%Y-%m-%d").timestamp() * 1000)
            lines.append(biometric_line("resting_hr", f"{int(resting)}i", day_ms))
    return lines


def poll_stress(client, state):
    lines = []
    for d in _recent_days():
        s = safe(f"get_stress({d})", client.get_stress_data, d.isoformat()) or {}
        for entry in (s.get("stressValuesArray") or []):
            if len(entry) < 2:
                continue
            ts_ms, val = entry[0], entry[1]
            if val is None or val < 0:
                continue
            lines.append(biometric_line("stress", f"{int(val)}i", ts_ms))
    return lines


def poll_body_battery(client, state):
    lines  = []
    recent = _recent_days()
    bb     = safe("get_body_battery", client.get_body_battery, recent[0].isoformat(), recent[-1].isoformat()) or []
    for day in bb:
        for entry in (day.get("bodyBatteryValuesArray") or []):
            if not entry or len(entry) < 2:
                continue
            ts_ms, level = entry[0], entry[1]
            if level is None or ts_ms is None:
                continue
            lines.append(biometric_line("body_battery", f"{int(level)}i", ts_ms))
    return lines


def poll_steps(client, state):
    lines = []
    for d in _recent_days():
        s = safe(f"get_steps_data({d})", client.get_steps_data, d.isoformat()) or []
        for bucket in s:
            gmt   = bucket.get("startGMT")
            steps = bucket.get("steps")
            if gmt is None or steps is None:
                continue
            try:
                ts_ms = int(datetime.strptime(gmt.split(".")[0], "%Y-%m-%dT%H:%M:%S").timestamp() * 1000)
            except Exception:
                continue
            lines.append(biometric_line("steps", f"{int(steps)}i", ts_ms))
    return lines


# ── recovery metrics ──────────────────────────────────────────────────────────
# Sleep, HRV, Training Readiness, Respiration all follow Convention A:
# a point for date D is timestamped at the wake time on D (i.e. the sleep that
# just ended). Deterministic timestamp = idempotent overwrites on backfill.

def _wake_ms(sleep_data):
    """Extract wake-up time (ms UTC) from a get_sleep_data() response.
    Falls back to None if unavailable so caller can skip the day."""
    dto = (sleep_data or {}).get("dailySleepDTO") or {}
    end = dto.get("sleepEndTimestampGMT")
    if end is None:
        return None
    try:
        return int(end)
    except (TypeError, ValueError):
        return None


def poll_sleep(client, state):
    lines = []
    for d in _recent_days():
        s = safe(f"get_sleep_data({d})", client.get_sleep_data, d.isoformat()) or {}
        wake_ms = _wake_ms(s)
        if wake_ms is None:
            continue
        dto    = s.get("dailySleepDTO") or {}
        scores = dto.get("sleepScores") or {}
        overall = (scores.get("overall") or {}).get("value")

        emit = [
            ("sleep_score",      overall),
            ("sleep_duration_s", dto.get("sleepTimeSeconds")),
            ("sleep_deep_s",     dto.get("deepSleepSeconds")),
            ("sleep_rem_s",      dto.get("remSleepSeconds")),
            ("sleep_light_s",    dto.get("lightSleepSeconds")),
            ("sleep_awake_s",    dto.get("awakeSleepSeconds")),
        ]
        for metric, val in emit:
            if val is None:
                continue
            try:
                lines.append(biometric_line(metric, f"{int(val)}i", wake_ms))
            except (TypeError, ValueError):
                pass
    return lines


def poll_hrv(client, state):
    lines = []
    for d in _recent_days():
        h = safe(f"get_hrv_data({d})", client.get_hrv_data, d.isoformat()) or {}
        summary = h.get("hrvSummary") or {}
        avg     = summary.get("lastNightAvg")
        five_min_high = summary.get("lastNight5MinHigh")
        # HRV is anchored to the sleep that just ended → use its wake time.
        # Prefer the endTimestampGMT on the hrv response; fall back to a
        # sleep_data lookup for the same date if missing.
        end_gmt = None
        if isinstance(h.get("endTimestampGMT"), (int, float)):
            end_gmt = int(h["endTimestampGMT"])
        if end_gmt is None:
            s = safe(f"get_sleep_data({d}) [for hrv ts]", client.get_sleep_data, d.isoformat()) or {}
            end_gmt = _wake_ms(s)
        if end_gmt is None or avg is None:
            continue
        try:
            lines.append(biometric_line("hrv_overnight", f"{int(avg)}i", end_gmt))
        except (TypeError, ValueError):
            pass
        if five_min_high is not None:
            try:
                lines.append(biometric_line("hrv_5min_high", f"{int(five_min_high)}i", end_gmt))
            except (TypeError, ValueError):
                pass
    return lines


TRAINING_READINESS_STATUS_MAP = {
    "POOR": "Poor", "LOW": "Low", "MODERATE": "Moderate",
    "HIGH": "High", "PRIME": "Prime",
}


def poll_training_readiness(client, state):
    lines = []
    for d in _recent_days():
        tr = safe(f"get_training_readiness({d})", client.get_training_readiness, d.isoformat()) or []
        if not tr:
            continue
        # API returns a list of readings through the day; take the earliest
        # (post-wake) reading as canonical for the day.
        readings = sorted(tr, key=lambda r: r.get("timestamp") or "")
        first    = readings[0]
        score    = first.get("score")
        level    = (first.get("level") or "").upper()
        ts       = first.get("timestamp")   # ISO string e.g. "2026-09-15T13:23:11.0"
        if score is None or ts is None:
            continue
        try:
            ts_ms = int(datetime.strptime(ts.split(".")[0].rstrip("Z"), "%Y-%m-%dT%H:%M:%S").timestamp() * 1000)
        except Exception:
            continue
        # Encode status as a tag so we can pull it back as a string.
        status = TRAINING_READINESS_STATUS_MAP.get(level, level.title() or "Unknown")
        lines.append(
            f"biometric,source=garmin,metric=training_readiness,status={_tag(status)} "
            f"value={int(score)}i {ts_ms}"
        )
    return lines


def poll_respiration(client, state):
    lines = []
    for d in _recent_days():
        r = safe(f"get_respiration_data({d})", client.get_respiration_data, d.isoformat()) or {}
        avg_sleep   = r.get("avgSleepRespirationValue")
        avg_waking  = r.get("avgWakingRespirationValue")
        # Anchor to wake time of that day's sleep.
        s       = safe(f"get_sleep_data({d}) [for resp ts]", client.get_sleep_data, d.isoformat()) or {}
        wake_ms = _wake_ms(s)
        if wake_ms is None:
            continue
        for metric, val in [("respiration_sleep_avg", avg_sleep),
                            ("respiration_waking_avg", avg_waking)]:
            if val is None:
                continue
            try:
                f = float(val)
            except (TypeError, ValueError):
                continue
            # Reject NaN (f != f) and Garmin "no data" sentinels (-1, -2).
            if f != f or f < 0:
                continue
            # Store as int to match the biometric.value field type (which is
            # int64 from earlier HR/steps/stress writes). Respiration rate at
            # ~1-bpm resolution is plenty for our purposes.
            lines.append(biometric_line(metric, f"{int(round(f))}i", wake_ms))
    return lines


# ── main loop ─────────────────────────────────────────────────────────────────

def poll():
    state  = load_state()
    client = get_client()

    act_lines = poll_activities(client, state)
    if act_lines:
        write_influx(ACTIVITY_BUCKET, act_lines)
        print(f"[INFO] Activities: wrote {len(act_lines)//2}", flush=True)
    else:
        print("[INFO] Activities: no new", flush=True)

    for name, fn in [("HR", poll_hr), ("stress", poll_stress),
                     ("body_battery", poll_body_battery), ("steps", poll_steps),
                     ("sleep", poll_sleep), ("hrv", poll_hrv),
                     ("training_readiness", poll_training_readiness),
                     ("respiration", poll_respiration)]:
        try:
            lines = fn(client, state)
        except Exception as e:
            print(f"[WARN] {name} poll crashed: {e}", flush=True)
            continue
        if lines:
            write_influx(BIOMETRICS_BUCKET, lines)
            print(f"[INFO] {name}: wrote {len(lines)}", flush=True)
        else:
            print(f"[INFO] {name}: no new", flush=True)

    save_state(state)


if __name__ == "__main__":
    print(f"[INFO] Garmin→InfluxDB poller starting — every {POLL_INTERVAL}s", flush=True)
    while True:
        try:
            poll()
        except Exception as e:
            print(f"[ERROR] poll crashed: {e}", flush=True)
        time.sleep(POLL_INTERVAL)
