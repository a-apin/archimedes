"""Public feature discovery; server environment remains source of truth."""

from fastapi import APIRouter

from archimedes.feature_flags import resolve_feature_flags

features_router = APIRouter(prefix="/api/features", tags=["features"])


@features_router.get("")
def get_features() -> dict[str, bool]:
    return resolve_feature_flags().to_dict()
