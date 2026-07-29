"""Server-owned feature flags with production-safe defaults."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Literal

from fastapi import HTTPException

FeatureName = Literal["quant"]


@dataclass(frozen=True, slots=True)
class FeatureFlags:
    quant: bool

    def to_dict(self) -> dict[str, bool]:
        return asdict(self)


def _bool(value: str, name: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be true or false")


def resolve_feature_flags(environ: Mapping[str, str] | None = None) -> FeatureFlags:
    environ = os.environ if environ is None else environ
    production = environ.get("APP_ENV", "development").strip().lower() in {"prod", "production"}
    value = environ.get("FEATURE_QUANT")
    return FeatureFlags(quant=not production if value is None or not value.strip() else _bool(value, "FEATURE_QUANT"))


def require_feature(name: FeatureName, flags: FeatureFlags | None = None) -> None:
    flags = flags or resolve_feature_flags()
    if not getattr(flags, name):
        raise HTTPException(status_code=404, detail="Not found")


def require_quant_feature() -> None:
    require_feature("quant")
