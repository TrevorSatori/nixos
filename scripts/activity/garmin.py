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
                     ("body_battery", poll_body_battery), ("steps", poll_steps)]:
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
