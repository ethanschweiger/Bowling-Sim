"""The read-only oil pattern catalog endpoint.

`GET /api/v1/oil-patterns` publishes the same `SUPPORTED_OIL_PATTERNS`
registry `POST /api/v1/games` validates `oil_pattern` against, so a client
never has to hardcode which pattern ids exist. Anything listed here is
creatable; anything absent is a 422 on create.

Each pattern's numbers come from the `OilPatternSpec` its own registered
builder produces, rather than from a second copy kept in step by hand.
That costs one lane build per request and buys the guarantee that the
published spec is the spec a game would actually be created with.

Display text lives here rather than in `app.physics.lane` because it is
presentation, and the physics package deliberately knows nothing about the
API (see the root README's `## Architecture`).
"""

from fastapi import APIRouter

from app.games.service import SUPPORTED_OIL_PATTERNS
from app.models.schemas import (
    OilPatternCatalogResponse,
    OilPatternResponse,
    OilPatternSpecResponse,
)
from app.physics.lane import OilPatternSpec

router = APIRouter(prefix="/oil-patterns", tags=["oil-patterns"])

# One entry per supported pattern id. A new registry entry without one
# here fails loudly rather than publishing an empty description -- see
# `test_every_supported_pattern_has_display_text`.
_PATTERN_DESCRIPTION: dict[str, str] = {
    "house": (
        "Forgiving, with the oil concentrated in the middle of the lane: "
        "misses inside hold, and misses outside hook back. The pattern this "
        "simulator models by default."
    ),
}


def to_response(pattern_id: str, spec: OilPatternSpec) -> OilPatternResponse:
    """Map one registered pattern to its response shape, reading only."""
    return OilPatternResponse(
        id=pattern_id,
        name=spec.name,
        description=_PATTERN_DESCRIPTION[pattern_id],
        spec=OilPatternSpecResponse(
            length_ft=spec.length_ft,
            taper_ft=spec.taper_ft,
            center_boards=spec.center_boards,
            total_boards=spec.total_boards,
            pattern_ratio=spec.pattern_ratio,
            total_volume_ml=spec.total_volume_ml,
        ),
    )


@router.get("", response_model=OilPatternCatalogResponse)
def list_oil_patterns() -> OilPatternCatalogResponse:
    """Every selectable oil pattern, in the registry's declared order.

    `SUPPORTED_OIL_PATTERNS` is a plain dict, so iterating it yields
    insertion order. Each builder is called to read the spec it produces;
    the resulting `LaneCondition` is discarded, and neither the registry
    nor the spec is written to.
    """
    return OilPatternCatalogResponse(
        patterns=[
            to_response(pattern_id, build_condition().spec)
            for pattern_id, build_condition in SUPPORTED_OIL_PATTERNS.items()
        ]
    )
