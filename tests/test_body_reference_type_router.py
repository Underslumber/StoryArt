import unittest

from tools.body_reference_type_router import load_registry, resolve_types


class BodyReferenceTypeRouterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.registry = load_registry()

    def resolve(self, text: str) -> dict:
        return resolve_types(text, self.registry)

    def test_natural_russian_alias(self) -> None:
        result = self.resolve("Для тела возьми натуру")
        self.assertEqual(result["selected_types"], ["NATURAL"])
        self.assertEqual(result["permanent_body_geometry_authority"], "NATURAL")

    def test_sketch_russian_alias(self) -> None:
        result = self.resolve("Позу возьми из наброска")
        self.assertEqual(result["selected_types"], ["SKETCH"])
        self.assertEqual(result["focus_order"][0]["authority"], "SOFT_POSE_ONLY")

    def test_olchas_case_insensitive(self) -> None:
        result = self.resolve("Сосредоточься на теле OLCHAS")
        self.assertEqual(result["selected_types"], ["OLCHAS"])
        self.assertIn("ART_BODY_SHAPE_SOFT", result["focus_order"][0]["allowed_roles"])

    def test_multiple_mentions_preserve_user_order(self) -> None:
        result = self.resolve("OlchaS для формы, набросок для позы, натура для анатомии")
        self.assertEqual(result["selected_types"], ["OLCHAS", "SKETCH", "NATURAL"])
        self.assertEqual(result["permanent_body_geometry_authority"], "NATURAL")

    def test_olchas_never_becomes_body_build_target(self) -> None:
        result = self.resolve("используй olchas")
        self.assertIn("BODY_BUILD_TARGET", result["focus_order"][0]["forbidden_roles"])

    def test_unmentioned_type_is_not_guessed(self) -> None:
        result = self.resolve("сделай фигуру выразительнее")
        self.assertEqual(result["status"], "UNRESOLVED")
        self.assertTrue(result["requires_user_choice"])

    def test_alias_does_not_match_inside_another_word(self) -> None:
        result = self.resolve("контуризация фона")
        self.assertEqual(result["selected_types"], [])

    def test_exact_three_type_registry(self) -> None:
        self.assertEqual(set(self.registry["types"]), {"NATURAL", "SKETCH", "OLCHAS"})


if __name__ == "__main__":
    unittest.main()
