"""The read-only ball catalog endpoint.

`GET /api/v1/balls` publishes `app.physics.ball.BALL_CATALOG` so a client
never has to hardcode which `ball_id` values a throw will accept. The
physics module stays the single source of truth: this module reads it and
maps it to response schemas, and never edits it.

Display text lives here rather than in `app.physics.ball` because it is
presentation, and the physics package deliberately knows nothing about the
API (see the root README's `## Architecture`). The per-coverstock wording
below paraphrases that module's own comments on `Coverstock`; it is not a
new claim about how any ball behaves.
"""

from fastapi import APIRouter

from app.models.schemas import BallCatalogResponse, BallResponse, BallSpecResponse
from app.physics.ball import BALL_CATALOG, Ball, Coverstock

router = APIRouter(prefix="/balls", tags=["balls"])

# One line per coverstock, covering every `Coverstock` member. A member
# with no entry here would raise on serialization rather than silently
# publishing an empty description -- see
# `test_every_coverstock_has_display_text`, which pins the coverage.
_COVERSTOCK_CHARACTER: dict[Coverstock, str] = {
    Coverstock.PLASTIC: "Near-zero hook, predictable and straight.",
    Coverstock.URETHANE: "A smooth, predictable arc with moderate hook.",
    Coverstock.REACTIVE: "Strong, sudden backend motion.",
    Coverstock.PARTICLE: "Reactive plus grit. Built for heavy oil.",
}


def describe(ball: Ball) -> str:
    """Help text for one ball: its coverstock, its finish, and what that
    combination does. Derived from the catalog entry every time, so it
    cannot drift from the ball it describes."""
    character = _COVERSTOCK_CHARACTER[ball.coverstock]
    return f"{ball.coverstock.value.capitalize()} coverstock, {ball.surface}. {character}"


def to_response(ball: Ball) -> BallResponse:
    """Map one catalog entry to its response shape, reading only.

    `coverstock` is passed as `.value` on purpose: `Coverstock` is a
    `str`/`Enum` mixin whose `str()` is `'Coverstock.REACTIVE'`, so
    handing the member itself to a client risks publishing that repr
    instead of the plain declared value.
    """
    return BallResponse(
        id=ball.id,
        name=ball.name,
        coverstock=ball.coverstock.value,
        surface=ball.surface,
        description=describe(ball),
        spec=BallSpecResponse(
            mass_lbs=ball.mass_lbs,
            radius_in=ball.radius_in,
            rg_in=ball.rg_in,
            differential=ball.differential,
            hook_potential=ball.hook_potential,
        ),
    )


@router.get("", response_model=BallCatalogResponse)
def list_balls() -> BallCatalogResponse:
    """Every selectable ball, in the catalog's declared order.

    `BALL_CATALOG` is a plain dict, so iterating it yields insertion
    order: the order `app.physics.ball` declares, which is what clients
    render. Nothing here writes to it.
    """
    return BallCatalogResponse(balls=[to_response(ball) for ball in BALL_CATALOG.values()])
