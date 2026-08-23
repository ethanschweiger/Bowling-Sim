"""Ball model.

Mirrors what's stamped on a real bowling ball's spec sheet. `hook_potential`
folds coverstock, surface, RG, and differential into one number the
simulator uses to scale how hard the ball changes direction once it hits
dry boards.

Two properties are deliberately unused in the trajectory calculation this
milestone, both documented rather than silently dropped:

- `mass_lbs` doesn't affect deceleration. Under simple Coulomb friction,
  `a = mu * g` — mass cancels out of `F = ma`, so a heavier ball doesn't
  coast farther down the lane on that basis. Mass matters for momentum
  transfer at pin impact, which isn't modeled yet (pin carry is v2+).
- `radius_in` is unused because every ball in the catalog shares the
  regulation diameter, so it can't yet differentiate simulated behavior.
  It stays on the model for when custom or undersized balls matter.
"""

from dataclasses import dataclass
from enum import StrEnum


class Coverstock(StrEnum):
    PLASTIC = "plastic"       # spares balls — near-zero hook
    URETHANE = "urethane"     # smooth, predictable arc
    REACTIVE = "reactive"     # strong, sudden backend motion
    PARTICLE = "particle"     # reactive + grit, for heavy oil


# Roughly how much grip each coverstock adds, relative to plastic.
_COVERSTOCK_GRIP: dict[Coverstock, float] = {
    Coverstock.PLASTIC: 0.05,
    Coverstock.URETHANE: 0.45,
    Coverstock.REACTIVE: 1.0,
    Coverstock.PARTICLE: 1.2,
}


def _surface_grip_factor(surface: str) -> float:
    """Rougher (lower-grit) surfaces bite into oil harder and hook more;
    a polished surface is the smoothest finish a coverstock can carry.

    Grit numbers below come straight off a box's spec sheet (e.g.
    "500-grit", "2000-grit"); anything without a parseable number falls
    back to a neutral factor of 1.0.
    """
    if surface == "polished":
        return 0.6

    digits = "".join(ch for ch in surface if ch.isdigit())
    if not digits:
        return 1.0

    grit = int(digits)
    # 500 grit (roughest) -> ~1.3x grip, 3000 grit (finest) -> ~0.6x grip.
    factor = 1.3 - (grit - 500) / (3000 - 500) * (1.3 - 0.6)
    return max(0.5, min(1.3, factor))


@dataclass(frozen=True)
class Ball:
    id: str
    name: str
    mass_lbs: float = 15.0
    # regulation radius, ~8.6" diameter — unused this milestone, see module docstring
    radius_in: float = 4.29
    rg_in: float = 2.54               # radius of gyration — lower flares earlier
    differential: float = 0.045       # RG differential — higher flares more
    surface: str = "1500-grit"        # finish on the coverstock
    coverstock: Coverstock = Coverstock.REACTIVE

    @property
    def hook_potential(self) -> float:
        """Scalar: how aggressively this ball changes direction on dry boards."""
        grip = _COVERSTOCK_GRIP[self.coverstock] * _surface_grip_factor(self.surface)
        # Higher differential = more flare = more hook. Lower RG = quicker revs.
        flare_factor = self.differential / 0.060
        rg_factor = (2.80 - self.rg_in) / (2.80 - 2.46)
        return max(0.0, min(1.8, grip * (0.6 * flare_factor + 0.4 * rg_factor)))


# A small starter catalog — enough to make ball choice matter in v1.
BALL_CATALOG: dict[str, Ball] = {
    "house_ball": Ball(
        id="house_ball",
        name="House Ball",
        rg_in=2.75,
        differential=0.020,
        surface="polished",
        coverstock=Coverstock.PLASTIC,
    ),
    "urethane_smooth": Ball(
        id="urethane_smooth",
        name="Smooth Urethane",
        rg_in=2.60,
        differential=0.035,
        surface="1000-grit",
        coverstock=Coverstock.URETHANE,
    ),
    "reactive_pearl": Ball(
        id="reactive_pearl",
        name="Reactive Pearl",
        rg_in=2.52,
        differential=0.052,
        surface="2000-grit",
        coverstock=Coverstock.REACTIVE,
    ),
    "particle_beast": Ball(
        id="particle_beast",
        name="Particle Beast",
        rg_in=2.48,
        differential=0.058,
        surface="500-grit",
        coverstock=Coverstock.PARTICLE,
    ),
}
