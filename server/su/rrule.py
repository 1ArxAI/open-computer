"""RFC 5545 RRULE subset for automation schedules, the same syntax Zo uses.
Supported: FREQ (MINUTELY, HOURLY, DAILY, WEEKLY, MONTHLY, YEARLY), INTERVAL, BYDAY, BYHOUR, BYMINUTE, BYMONTH, BYMONTHDAY, COUNT.
No DTSTART or TZID: hours are local time (SU_TZ). ponytail: no BYSETPOS/UNTIL/BYWEEKNO; add if an owner asks."""
import calendar
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from .config import TZ

FREQS = ("MINUTELY", "HOURLY", "DAILY", "WEEKLY", "MONTHLY", "YEARLY")
DAY_IDX = {"MO": 0, "TU": 1, "WE": 2, "TH": 3, "FR": 4, "SA": 5, "SU": 6}
DAY_NAME = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def parse(rrule: str) -> Dict:
    """Validate and split an RRULE string. Raises ValueError with a plain message."""
    s = (rrule or "").strip().upper().removeprefix("RRULE:")
    out: Dict = {}
    for part in filter(None, s.split(";")):
        if "=" not in part:
            raise ValueError(f"bad RRULE part '{part}'")
        k, v = part.split("=", 1)
        if k in ("DTSTART", "TZID"):
            raise ValueError(f"{k} is not allowed: hours are local time and the system adds the start")
        out[k] = v
    freq = out.get("FREQ")
    if freq not in FREQS:
        raise ValueError(f"FREQ must be one of {', '.join(FREQS)}")
    ints = lambda key: [int(x) for x in out[key].split(",")] if out.get(key) else []
    try:
        r = {"freq": freq, "interval": max(1, int(out.get("INTERVAL", 1))), "byhour": ints("BYHOUR"), "byminute": ints("BYMINUTE"),
             "bymonth": ints("BYMONTH"), "bymonthday": ints("BYMONTHDAY"), "count": int(out["COUNT"]) if out.get("COUNT") else None,
             "byday": [DAY_IDX[d.strip()[-2:]] for d in out["BYDAY"].split(",")] if out.get("BYDAY") else []}
    except (KeyError, ValueError) as e:
        raise ValueError(f"bad RRULE value: {e}")
    if any(h < 0 or h > 23 for h in r["byhour"]) or any(m < 0 or m > 59 for m in r["byminute"]):
        raise ValueError("BYHOUR must be 0-23 and BYMINUTE 0-59")
    return r


def is_once(rrule: str) -> bool:
    return parse(rrule)["count"] == 1


def _times(r: Dict) -> List[tuple]:
    hours = r["byhour"] or [9]
    mins = r["byminute"] or [0]
    return sorted((h, m) for h in hours for m in mins)


def next_after(rrule: str, after: datetime) -> Optional[datetime]:
    """First occurrence strictly after `after` (aware datetime in TZ)."""
    r = parse(rrule)
    f, n = r["freq"], r["interval"]
    if f == "MINUTELY":  # steps from the top of the hour, so INTERVAL=30 lands on :00 and :30
        base = after.replace(minute=0, second=0, microsecond=0)
        k = int((after - base).total_seconds() // 60 // n) + 1
        return base + timedelta(minutes=n * k)
    if f == "HOURLY":  # steps from midnight, so INTERVAL=6 lands on 0, 6, 12, 18
        minute = (r["byminute"] or [0])[0]
        base = after.replace(hour=0, minute=minute, second=0, microsecond=0)
        if base > after:
            base -= timedelta(days=1)
        k = int((after - base).total_seconds() // 3600 // n) + 1
        return base + timedelta(hours=n * k)
    start_day = after.date()
    for d in range(0, 800):  # ponytail: linear day scan, plenty for yearly rules
        day = start_day + timedelta(days=d)
        if f == "DAILY" and d % n:
            continue
        if f == "WEEKLY":
            if (day - start_day).days // 7 % n:
                continue
            if day.weekday() not in (r["byday"] or [start_day.weekday()]):
                continue
        elif r["byday"] and day.weekday() not in r["byday"]:
            continue
        if f == "MONTHLY":
            months = (day.year - start_day.year) * 12 + day.month - start_day.month
            if months % n or day.day not in (r["bymonthday"] or [start_day.day]):
                continue
        if f == "YEARLY":
            if (day.year - start_day.year) % n or day.month not in (r["bymonth"] or [start_day.month]) or day.day not in (r["bymonthday"] or [start_day.day]):
                continue
        if f in ("DAILY", "WEEKLY") and r["bymonth"] and day.month not in r["bymonth"]:
            continue
        for h, m in _times(r):
            cand = datetime(day.year, day.month, day.day, h, m, tzinfo=TZ)
            if cand > after:
                return cand
    return None


def describe(rrule: str) -> str:
    try:
        r = parse(rrule)
    except ValueError as e:
        return f"Invalid RRULE ({e})"
    f, n = r["freq"], r["interval"]
    ampm = lambda h, m: f"{h % 12 or 12}:{m:02d} {'am' if h < 12 else 'pm'}"
    at = ", ".join(ampm(h, m) for h, m in _times(r))
    once = " (once)" if r["count"] == 1 else ""
    if f == "MINUTELY":
        return f"Every {n} minute{'s' if n > 1 else ''}{once}"
    if f == "HOURLY":
        return (f"Every {n} hours" if n > 1 else "Hourly") + once
    if f == "DAILY":
        days = ", ".join(DAY_NAME[i] for i in r["byday"]) if r["byday"] else ("Daily" if n == 1 else f"Every {n} days")
        return f"{days} at {at}{once}"
    if f == "WEEKLY":
        days = ", ".join(DAY_NAME[i] for i in r["byday"]) or "Weekly"
        return f"{days} at {at}" + (f" every {n} weeks" if n > 1 else "") + once
    if f == "MONTHLY":
        return f"Monthly on day {', '.join(map(str, r['bymonthday'])) or '(start day)'} at {at}{once}"
    months = ", ".join(calendar.month_abbr[m] for m in r["bymonth"]) or "(start month)"
    return f"Yearly on {', '.join(map(str, r['bymonthday'])) or '(start day)'} {months} at {at}{once}"
