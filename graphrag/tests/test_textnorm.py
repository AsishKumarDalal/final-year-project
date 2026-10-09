"""Validation gate and normalisation.

The fixtures are lifted from ``docs/rag_docs/solution.md`` — the real "Tesla
problem" and the three live failures it records. A regression here silently
poisons every future traversal, so the over-merge guards are tested explicitly
rather than assumed.
"""

from __future__ import annotations

import unittest

from graphrag.textnorm import (
    display_name,
    is_vague,
    normalize_entity,
    normalize_entity_type,
    normalize_relation,
    slugify,
    split_compound,
    validate_entity_name,
    validate_triple,
)


class TestNormalizeEntity(unittest.TestCase):
    def test_corporate_suffix_variants_share_one_key(self):
        """solution.md §3 — level 1 of the ladder, the first 50% free."""
        key = normalize_entity("Tesla, Inc.")
        self.assertEqual(key, "tesla")
        self.assertEqual(normalize_entity("Tesla Inc"), key)
        self.assertEqual(normalize_entity("tesla, INC."), key)
        self.assertEqual(normalize_entity("BP plc"), normalize_entity("BP"))

    def test_meaningful_suffix_is_not_stripped(self):
        """Over-merging is worse than under-merging. 'Motors' is a real word."""
        self.assertEqual(normalize_entity("Tesla Motors"), "tesla motors")
        self.assertEqual(normalize_entity("Tesla Energy"), "tesla energy")
        self.assertEqual(normalize_entity("General Motors"), "general motors")

    def test_distinct_entities_stay_distinct(self):
        self.assertNotEqual(normalize_entity("Tesla"), normalize_entity("Elon Musk"))

    def test_repeated_suffixes_stripped(self):
        self.assertEqual(normalize_entity("Tesla Inc Ltd"), "tesla")

    def test_empty_after_normalisation(self):
        self.assertEqual(normalize_entity("the"), "")


class TestNormalizeRelation(unittest.TestCase):
    def test_free_text_becomes_upper_snake(self):
        self.assertEqual(normalize_relation("is CEO of"), "CEO_OF")
        self.assertEqual(normalize_relation("located in"), "LOCATED_IN")
        self.assertEqual(normalize_relation("PRESENTS_WITH"), "PRESENTS_WITH")

    def test_punctuation_collapsed(self):
        self.assertEqual(normalize_relation("treats-with"), "TREATS_WITH")


class TestDisplayAndSlug(unittest.TestCase):
    def test_display_keeps_full_form(self):
        self.assertEqual(display_name("  Tesla, Inc. "), "Tesla, Inc.")

    def test_slugify(self):
        self.assertEqual(slugify("Myocardial Infarction"), "myocardial_infarction")
        self.assertEqual(slugify("Tesla, Inc."), "tesla_inc")


class TestValidateTriple(unittest.TestCase):
    def test_accepts_a_good_triple(self):
        self.assertIsNone(validate_triple("Elon Musk", "CEO_OF", "Tesla, Inc."))

    def test_rejects_missing_fields(self):
        self.assertEqual(validate_triple("", "CEO_OF", "Tesla"), "missing_field")
        self.assertEqual(validate_triple("Elon Musk", "", "Tesla"), "missing_field")

    def test_rejects_self_loop(self):
        self.assertEqual(
            validate_triple("Tesla", "RELATED_TO", "Tesla"), "self_loop"
        )

    def test_rejects_vague_bucket_names(self):
        """graph_making.md §6 — 'other automakers' is never an entity."""
        self.assertEqual(
            validate_triple("Tesla", "COMPETES_WITH", "other automakers"),
            "vague_entity",
        )

    def test_rejects_funding_round_junk(self):
        long_name = " ".join(f"word{i}" for i in range(8))
        self.assertEqual(
            validate_triple(long_name, "RAISED", "Tesla"), "name_too_many_tokens"
        )

    def test_rejects_overlong_fields(self):
        self.assertEqual(
            validate_triple("x" * 101, "RAISED", "Tesla"), "entity_too_long"
        )
        self.assertEqual(
            validate_triple("Tesla", "R" * 61, "SpaceX"), "relation_too_long"
        )

    def test_relationship_verbs_are_dropped(self):
        """One relation must not split into three nodes because the LLM wrote
        'is the CEO of', 'was the CEO of' and 'the CEO of'."""
        variants = {
            normalize_relation("is the CEO of"),
            normalize_relation("was the CEO of"),
            normalize_relation("the CEO of"),
            normalize_relation("CEO_OF"),
        }
        self.assertEqual(variants, {"CEO_OF"})


class TestValidateEntityName(unittest.TestCase):
    def test_vague_and_overlong_rejected(self):
        self.assertEqual(validate_entity_name("various countries"), "vague_entity")
        self.assertEqual(validate_entity_name(""), "missing_field")
        self.assertIsNone(validate_entity_name("myocardial infarction"))


class TestSplitCompound(unittest.TestCase):
    def test_conjunction_is_split(self):
        self.assertEqual(split_compound("Tesla and SpaceX"), ["Tesla", "SpaceX"])

    def test_single_name_untouched(self):
        self.assertEqual(split_compound("Elon Musk"), ["Elon Musk"])

    def test_inner_word_not_split(self):
        """'Anderson' contains 'and' but is one word."""
        self.assertEqual(split_compound("Anderson"), ["Anderson"])


class TestEntityType(unittest.TestCase):
    def test_known_type_passes_through(self):
        self.assertEqual(normalize_entity_type("drug"), "drug")
        self.assertEqual(normalize_entity_type("Body_System"), "body_system")

    def test_unknown_type_falls_back_rather_than_dropping(self):
        """Losing a node breaks traversal; a wrong type is fixable by backfill."""
        self.assertEqual(normalize_entity_type("PERSON"), "condition")


class TestVagueDetection(unittest.TestCase):
    def test_bucket_names(self):
        for name in ("other automakers", "various countries", "many patients"):
            self.assertTrue(is_vague(name), name)

    def test_real_entities_are_not_vague(self):
        for name in ("myocardial infarction", "chest pain", "aspirin"):
            self.assertFalse(is_vague(name), name)


if __name__ == "__main__":
    unittest.main()