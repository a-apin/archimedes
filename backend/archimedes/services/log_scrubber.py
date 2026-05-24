"""Log scrubber — strip PII fields before logging.

All logger calls that touch user profile data MUST route through this module
to prevent accidental PII leakage in log output.
"""

from __future__ import annotations

from typing import Any

# Fields that are considered PII and must never appear in logs.
_PII_FIELDS = frozenset({"email", "display_name", "marketing_opt_in"})


def scrub_profile(profile_dict: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of *profile_dict* with PII fields redacted.

    Redacted fields are replaced with ``"<REDACTED>"`` so log consumers
    can see that a value *was* present without seeing the value itself.
    """
    return {k: ("<REDACTED>" if k in _PII_FIELDS else v) for k, v in profile_dict.items()}
