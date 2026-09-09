"""Project-owned pattern definitions shared by profiling and quality."""

from __future__ import annotations

import re
from typing import Mapping


PATTERN_REGEXES: Mapping[str, re.Pattern[str]] = {
    "EMAIL_LIKE": re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+"),
    "PHONE_LIKE": re.compile(r"\+?[0-9][0-9()\-\s]{6,}"),
    "UUID_LIKE": re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}"),
    "INTEGER_STRING": re.compile(r"[+-]?\d+"),
    "DECIMAL_STRING": re.compile(r"[+-]?\d+\.\d+"),
    "DATE_STRING": re.compile(r"\d{4}-\d{2}-\d{2}(?:[T ].*)?"),
    "URL_LIKE": re.compile(r"https?://[^\s]+", re.IGNORECASE),
}


def matches_pattern(pattern_type: str, value: str) -> bool:
    """Return whether a string matches the complete project-owned pattern."""

    regex = PATTERN_REGEXES.get(pattern_type)
    return bool(regex and regex.fullmatch(value))
