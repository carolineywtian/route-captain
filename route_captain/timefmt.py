"""Portable date formatting: '%-I' and '%-d' (no leading zero) work on Mac and Linux but crash on
Windows, so they are filled in here before calling strftime."""
from __future__ import annotations

from datetime import datetime


def fmt(dt: datetime, pattern: str) -> str:
    """fmt(dt, '%a %b %-d, %-I:%M %p') -> 'Sat Oct 31, 9:00 AM' on every operating system."""
    hour12 = dt.hour % 12 or 12
    return dt.strftime(pattern.replace("%-I", str(hour12)).replace("%-d", str(dt.day)))
