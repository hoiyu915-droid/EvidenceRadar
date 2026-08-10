from __future__ import annotations

import unittest

from tools.featured_selection import (
    FeaturedSelectionError,
    featured_policy_note,
    parse_featured_policy_note,
    select_featured_work_ids_v2,
)


def item(work_id: str, category: str, score: int, triage: str = "PRIORITY") -> dict[str, object]:
    return {
        "work_id": work_id,
        "category": category,
        "routing_score": score,
        "triage_status": triage,
        "event_status": "QUALIFYING",
        "event_class": "NEW_PUBLICATION",
    }


class FeaturedSelectionTests(unittest.TestCase):
    def test_owner_style_category_limits_and_global_hard_cap(self) -> None:
        candidates = [
            *[item(f"c{i}", "clinical_medicine", 100-i) for i in range(10)],
            *[item(f"l{i}", "llm_research", 100-i) for i in range(15)],
            *[item(f"h{i}", "human_ai", 100-i) for i in range(8)],
        ]
        policy = {
            "ranking_pool_max_per_category": 30,
            "per_category": {
                "clinical_medicine": {"target": 4, "hard_max": 6},
                "llm_research": {"target": 6, "hard_max": 10},
                "human_ai": {"target": 4, "hard_max": 6},
            },
            "target_total": 14,
            "hard_max_total": 18,
        }
        selected = select_featured_work_ids_v2(
            candidates,
            target_per_category=5,
            hard_max_per_category=8,
            policy=policy,
        )
        self.assertLessEqual(len(selected), 18)
        self.assertGreaterEqual(len(selected), 14)
        self.assertLessEqual(sum(value.startswith("c") for value in selected), 6)
        self.assertLessEqual(sum(value.startswith("l") for value in selected), 10)
        self.assertLessEqual(sum(value.startswith("h") for value in selected), 6)

    def test_ranking_pool_never_changes_complete_input(self) -> None:
        candidates = [item(f"x{i:02d}", "clinical_medicine", 100-i) for i in range(50)]
        original_ids = [str(value["work_id"]) for value in candidates]
        selected = select_featured_work_ids_v2(
            candidates,
            target_per_category=5,
            hard_max_per_category=8,
            policy={"ranking_pool_max_per_category": 30},
        )
        self.assertEqual(len(candidates), 50)
        self.assertEqual([str(value["work_id"]) for value in candidates], original_ids)
        self.assertEqual(len(selected), 8)
        self.assertTrue(selected <= set(original_ids[:30]))

    def test_policy_note_round_trip_is_deterministic(self) -> None:
        policy = {
            "ranking_pool_max_per_category": 30,
            "per_category": {"llm_research": {"target": 6, "hard_max": 10}},
            "target_total": 20,
            "hard_max_total": 32,
        }
        note = featured_policy_note(policy)
        self.assertEqual(parse_featured_policy_note([note]), policy)
        self.assertEqual(featured_policy_note(policy), note)

    def test_invalid_global_target_fails_closed(self) -> None:
        with self.assertRaises(FeaturedSelectionError):
            select_featured_work_ids_v2(
                [item("x", "clinical_medicine", 1)],
                policy={"target_total": 33, "hard_max_total": 32},
            )


if __name__ == "__main__":
    unittest.main()
