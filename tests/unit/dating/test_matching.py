"""Who is proposed to whom: the other sex only, the usual age gap first."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from loka.bounded_contexts.dating.domain.matching import age_fit, best_candidates
from loka.bounded_contexts.dating.domain.profile import Profile
from loka.bounded_contexts.dating.infrastructure.match_judge import _verdicts


def _member(name: str, age: int, sex: str, *, seeking: str | None = None) -> Profile:
    return Profile(
        profile_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        first_name=name,
        age=age,
        sex=sex,
        profession="Commerçante",
        country="Cameroun",
        city="Yaoundé",
        photo_key="p.jpg",
        description="je cherche une relation sérieuse et durable",
        looking=("PEU_IMPORTE",),
        seeking=seeking,
        created_at=datetime(2026, 11, 24, tzinfo=UTC),
    )


def test_no_same_sex_match_even_when_a_description_asked_for_one() -> None:
    her = _member("Joyce", 22, "FEMME", seeking="FEMME")
    other_woman = _member("Aïcha", 24, "FEMME")
    assert not her.could_meet(other_woman)


def test_a_woman_meets_older_men_first() -> None:
    her = _member("Joyce", 22, "FEMME")
    older, younger = _member("Ulric", 30, "HOMME"), _member("Kevin", 19, "HOMME")
    ranked = best_candidates(her, [younger, older], excluded=set())
    assert [p.first_name for p, _ in ranked] == ["Ulric", "Kevin"]


def test_the_usual_gap_beats_a_very_large_one() -> None:
    her = _member("Joyce", 22, "FEMME")
    assert age_fit(her, _member("Ulric", 30, "HOMME")) > age_fit(her, _member("Paul", 50, "HOMME"))


def test_the_judge_order_is_kept_and_a_forgotten_candidate_comes_last() -> None:
    a, b, c = _member("A", 30, "HOMME"), _member("B", 31, "HOMME"), _member("C", 32, "HOMME")
    payload = {"ranking": [{"id": 2, "score": 90, "reason": "même projet"}, {"id": 1, "score": 40}]}
    verdicts = _verdicts(payload, [a, b, c])
    assert verdicts is not None
    assert [v.profile_id for v in verdicts] == [b.profile_id, a.profile_id, c.profile_id]
    assert verdicts[0].score == 0.9


def test_an_unusable_judge_answer_is_no_answer() -> None:
    assert _verdicts({"ranking": [{"id": 9, "score": 50}]}, [_member("A", 30, "HOMME")]) is None


def test_a_profile_often_turned_down_comes_later() -> None:
    her = _member("Joyce", 22, "FEMME")
    liked, refused = _member("Ulric", 30, "HOMME"), _member("Paul", 30, "HOMME")
    ranked = best_candidates(
        her, [refused, liked], excluded=set(), refusals={refused.profile_id: (6, 6)}
    )
    assert [p.first_name for p, _ in ranked] == ["Ulric", "Paul"]


def test_one_no_on_a_new_profile_weighs_little() -> None:
    from loka.bounded_contexts.dating.domain.matching import appeal

    assert appeal(0, 0) == 1.0
    assert appeal(1, 1) > 0.85
    assert appeal(20, 20) < 0.6
