"""Faces as vectors: one person behind two numbers, and each member's taste.

A reader turns a profile photo into a vector (see ``FaceReader``); two photos of
the same person lie close together. Decision of 26 Nov 2026: faces count in
the matching. A member's taste is learnt from their answers — the faces they
said yes to against those they passed on — and scores the next faces. The
vectors never leave the server and are never shown.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from typing import Protocol

Vector = tuple[float, ...]

# Cosine similarity above which two photos are taken for the same person.
# OpenCV gives 0.363 for SFace; a little higher, a near-double is not enough.
SAME_PERSON = 0.42
# Answers needed before a taste is trusted: two yeses can be chance.
MIN_ANSWERS = 3


class FaceReader(Protocol):
    async def vector(self, object_key: str) -> Vector | None:
        """The face in the photo; ``None`` with no single clear face, or on failure."""


def similarity(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine similarity, 0 when either is missing."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


def same_person(a: Sequence[float], b: Sequence[float]) -> bool:
    return similarity(a, b) >= SAME_PERSON


def _mean(vectors: list[Vector]) -> list[float]:
    return [sum(column) / len(vectors) for column in zip(*vectors, strict=True)]


def taste(answers: Iterable[tuple[Vector, bool]]) -> Vector | None:
    """The direction of the faces said yes to, away from those passed on.

    ``None`` before ``MIN_ANSWERS`` answers with at least one yes: nothing is
    known yet, and every face counts the same.
    """
    liked = [face for face, yes in answers if yes and face]
    passed = [face for face, yes in answers if not yes and face]
    if not liked or len(liked) + len(passed) < MIN_ANSWERS:
        return None
    toward = _mean(liked)
    if passed:
        away = _mean(passed)
        toward = [t - a for t, a in zip(toward, away, strict=True)]
    norm = math.sqrt(sum(x * x for x in toward))
    return tuple(x / norm for x in toward) if norm else None


def appeal_to(taste_of: Vector | None, face: Sequence[float]) -> float:
    """How much a face is the member's type, from -1 to 1; 0 when unknown."""
    if taste_of is None or not face:
        return 0.0
    return similarity(taste_of, face)
