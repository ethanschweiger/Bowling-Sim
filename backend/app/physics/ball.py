"""Ball model.

Mirrors what's stamped on a real bowling ball's spec sheet. `hook_potential`
folds those specs into one number the simulator uses to scale how hard the
ball changes direction once it hits dry boards.
"""

from dataclasses import dataclass
from enum import Enum


class Coverstock(str, Enum):
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


@dataclass(frozen=True)
class Ball:
    id: str
    name: str
    mass_lbs: float = 15.0
    radius_in: float = 4.29           # regulation radius, ~8.6" diameter
    rg_in: float = 2.54               # radius of gyration — lower flares earlier
    differential: float = 0.045       # RG differential — higher flares more
    surface: str = "1500-grit"        # finish on the coverstock
    coverstock: Coverstock = Coverstock.REACTIVE

    @property
    def hook_potential(self) -> float:
        """0-1ish scalar: how aggressively this ball changes direction on dry boards."""
        grip = _COVERSTOCK_GRIP[self.coverstock]
        # Higher differential = more flare = more hook. Lower RG = quicker revs.
        flare_factor = self.differential / 0.060
        rg_factor = (2.80 - self.rg_in) / (2.80 - 2.46)
        return max(0.0, min(1.5, grip * (0.6 * flare_factor + 0.4 * rg_factor)))


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
