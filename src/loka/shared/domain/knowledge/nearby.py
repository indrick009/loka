"""What « près de chez moi » means in Cameroon, in moto-taxi fares.

Nobody counts kilometres in Yaoundé or Douala: they count what the benskin
costs. A short hop is 100 F; around 250 F is the usual fare for 3.8 km (Yaoundé,
January 2025, bougna.net); a taxi is 300-400 F whatever the distance in town,
so the moto is the measure. idia's distances are straight lines between two
points, and the road is about a third longer.

- ``NEAR_KM``: about 100 F of moto — near, offered without a word.
- ``FAR_KM``: about 300 F — the most idia offers, and only once the client has
  said yes knowing where it is. Nobody takes a bus to bring one dish.
"""

from __future__ import annotations

from typing import Literal

# Straight line; ~1.5 km of road, a 100 F moto ride.
NEAR_KM = 1.2
# Straight line; ~4.5 km of road, a 300 F moto ride.
FAR_KM = 3.5

Reach = Literal["near", "far", "out"]


def reach(km: float | None) -> Reach:
    """Near, a little further (ask first), or out of reach. Unknown: ask first."""
    if km is None:
        return "far"
    if km <= NEAR_KM:
        return "near"
    return "far" if km <= FAR_KM else "out"
