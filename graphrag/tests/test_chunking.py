"""Chunking: provenance, overlap, determinism, and boundary safety."""

from __future__ import annotations

import unittest

from graphrag.chunking import chunk_document, chunk_text

def _paragraphs(count: int = 6) -> str:
    """A deterministic multi-paragraph document with real sentence structure."""
    blocks = []
    for i in range(count):
        sentences = " ".join(
            f"Sentence {j} in paragraph {i} mentions chest pain and breathing."
            for j in range(8)
        )
        blocks.append(f"Paragraph {i}. {sentences}")
    return "\n\n".join(blocks)


PARAGRAPHS = _paragraphs()


class TestChunkText(unittest.TestCase):
    def test_empty_input_yields_nothing(self):
        self.assertEqual(chunk_text(""), [])
        self.assertEqual(chunk_text("   \n\n  "), [])

    def test_rejects_impossible_overlap(self):
        with self.assertRaises(ValueError):
            chunk_text("hello", target_chars=100, overlap_chars=100)
        with self.assertRaises(ValueError):
            chunk_text("hello", target_chars=0)

    def test_offsets_are_within_the_source(self):
        text = "First paragraph here.\n\nSecond paragraph here."
        for body, start, end in chunk_text(text, target_chars=40, overlap_chars=10):
            self.assertLessEqual(start, end)
            self.assertLessEqual(end, len(text))

    def test_content_is_preserved(self):
        """No text may be dropped between chunks — every fact must survive."""
        words = " ".join(f"w{i}" for i in range(400))
        chunks = chunk_text(words, target_chars=200, overlap_chars=40)
        joined = " ".join(body for body, _, _ in chunks)
        for i in (0, 100, 399):
            self.assertIn(f"w{i}", joined)

    def test_oversized_paragraph_split_at_sentences(self):
        long_para = ". ".join(f"Sentence number {i} is here" for i in range(200)) + "."
        chunks = chunk_text(long_para, target_chars=300, overlap_chars=50)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(body) <= 400 for body, _, _ in chunks))

    def test_deterministic(self):
        a = chunk_text(PARAGRAPHS, target_chars=300, overlap_chars=40)
        b = chunk_text(PARAGRAPHS, target_chars=300, overlap_chars=40)
        self.assertEqual(a, b)


class TestProvenanceIds(unittest.TestCase):
    def test_ids_are_stable_and_sequential(self):
        chunks = chunk_document(
            "Myocardial Infarction", PARAGRAPHS, target_chars=300, overlap_chars=40
        )
        self.assertEqual(chunks[0].chunk_id, "myocardial_infarction::0")
        self.assertEqual(
            [c.index for c in chunks], list(range(len(chunks)))
        )

    def test_same_document_gives_same_ids(self):
        first = chunk_document("Aspirin", PARAGRAPHS, target_chars=300, overlap_chars=40)
        second = chunk_document("Aspirin", PARAGRAPHS, target_chars=300, overlap_chars=40)
        self.assertEqual([c.chunk_id for c in first], [c.chunk_id for c in second])

    def test_doc_id_comes_from_title(self):
        chunks = chunk_document("Chest Pain", "Some text about chest pain.")
        self.assertEqual(chunks[0].doc_id, "chest_pain")

    def test_content_hash_is_deterministic_and_text_sensitive(self):
        one = chunk_document("A", "alpha text here")[0]
        same = chunk_document("A", "alpha text here")[0]
        other = chunk_document("A", "alpha text CHANGED")[0]
        self.assertEqual(one.content_hash, same.content_hash)
        self.assertNotEqual(one.content_hash, other.content_hash)

    def test_round_trips_to_dict(self):
        chunk = chunk_document("Test", "some content")[0]
        payload = chunk.to_dict()
        self.assertEqual(payload["chunk_id"], chunk.chunk_id)
        self.assertEqual(payload["content_hash"], chunk.content_hash)


class TestOverlap(unittest.TestCase):
    def test_subsequent_chunks_carry_previous_tail(self):
        text = "\n\n".join(f"Block {i} " + "filler words here. " * 20 for i in range(8))
        chunks = chunk_text(text, target_chars=400, overlap_chars=120)
        self.assertGreater(len(chunks), 1)
        first_tail = chunks[0][0][-60:]
        self.assertIn(
            first_tail.strip()[:30], chunks[1][0], "overlap should repeat the previous tail"
        )


if __name__ == "__main__":
    unittest.main()