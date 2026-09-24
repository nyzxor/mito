"""5-field cron plus @hourly/@daily/@weekly (ADR-0017). No third-party parser."""

from __future__ import annotations

import calendar
from dataclasses import dataclass

ALIASES = {"@hourly": "0 * * * *", "@daily": "0 0 * * *", "@weekly": "0 0 * * 0"}
_DOW = {"sun": 0, "mon": 1, "tue": 2, "wed": 3, "thu": 4, "fri": 5, "sat": 6}


class CronError(ValueError):
    pass


@dataclass(frozen=True)
class Cron:
    minute: frozenset[int]
    hour: frozenset[int]
    dom: frozenset[int]
    month: frozenset[int]
    dow: frozenset[int]

    def matches(self, y: int, m: int, d: int, hh: int, mm: int) -> bool:
        wd = (calendar.weekday(y, m, d) + 1) % 7  # cron Sun=0
        return (
            mm in self.minute
            and hh in self.hour
            and d in self.dom
            and m in self.month
            and wd in self.dow
        )


def _field(raw: str, lo: int, hi: int, names: dict[str, int] | None = None) -> frozenset[int]:
    out: set[int] = set()
    for part in raw.split(","):
        step = 1
        base = part
        if "/" in part:
            base, step_s = part.split("/", 1)
            step = int(step_s)
            if step < 1:
                raise CronError(f"bad step in {raw!r}")
        if base in ("*", ""):
            start, end = lo, hi
        elif "-" in base:
            a, b = base.split("-", 1)
            start, end = _one(a, lo, hi, names), _one(b, lo, hi, names)
        else:
            start = end = _one(base, lo, hi, names)
        if start > end:
            raise CronError(f"range {part!r} is reversed")
        out.update(range(start, end + 1, step))
    if not out:
        raise CronError(f"empty field {raw!r}")
    return frozenset(out)


def _one(token: str, lo: int, hi: int, names: dict[str, int] | None) -> int:
    key = token.strip().lower()
    n = names[key] if names and key in names else int(key)
    if n < lo or n > hi:
        raise CronError(f"{token!r} outside {lo}..{hi}")
    return n


def parse_cron(expr: str) -> Cron:
    text = ALIASES.get(expr.strip(), expr.strip())
    parts = text.split()
    if len(parts) != 5:
        raise CronError(f"cron needs 5 fields, got {expr!r}")
    return Cron(
        _field(parts[0], 0, 59),
        _field(parts[1], 0, 23),
        _field(parts[2], 1, 31),
        _field(parts[3], 1, 12),
        _field(parts[4], 0, 6, _DOW),
    )


def next_after(expr: str, ts: float) -> float:
    """Next UTC minute strictly after ts. Scans at most ~2 years of minutes."""
    cron = parse_cron(expr)
    import datetime as dt

    cur = dt.datetime.fromtimestamp(int(ts) + 60, tz=dt.UTC).replace(second=0, microsecond=0)
    for _ in range(366 * 24 * 60 * 2):
        if cron.matches(cur.year, cur.month, cur.day, cur.hour, cur.minute):
            return cur.timestamp()
        cur += dt.timedelta(minutes=1)
    raise CronError(f"no match for {expr!r}")
