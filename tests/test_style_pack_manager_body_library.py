from argparse import Namespace
import unittest

from tools.style_pack_manager import StylePackError, validate_prompt_only_body_library_review


def make_args(*, prompt_only: bool, decision: str, reviewed: int, total: int) -> Namespace:
    return Namespace(
        prompt_only_physique=prompt_only,
        aux_body_decision=decision,
        body_library_candidates_reviewed=reviewed,
        body_library_relevant_candidates_total=total,
    )


class BodyLibraryReviewTests(unittest.TestCase):
    def test_selected_library_blocks_prompt_only_before_complete_relevant_review(self) -> None:
        with self.assertRaisesRegex(StylePackError, "reviewed 2 of 5"):
            validate_prompt_only_body_library_review(
                make_args(prompt_only=True, decision="SELECTED", reviewed=2, total=5)
            )

    def test_selected_library_allows_prompt_only_after_complete_relevant_review(self) -> None:
        validate_prompt_only_body_library_review(
            make_args(prompt_only=True, decision="SELECTED", reviewed=5, total=5)
        )

    def test_visual_body_route_does_not_require_prompt_only_review_counts(self) -> None:
        validate_prompt_only_body_library_review(
            make_args(prompt_only=False, decision="SELECTED", reviewed=0, total=0)
        )
