import json
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

from english_coach.config import normalize_answer, normalize_sentence
from english_coach.db import Database
from english_coach.quiz import evaluate_dictation, evaluate_spelling, levenshtein
from english_coach.stats import calculate_stats
from english_coach.words import spelling_item


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.db = Database(self.home / "test.db")
        self.db.init()
        pool = []
        for i, word in enumerate(["abandon", "benefit", "culture", "develop", "effort"]):
            pool.append({
                "word": word,
                "phonetic": "x",
                "pos": "n.",
                "meaning_zh": "释义%d" % i,
                "definition_en": "definition",
                "tags": ["cet4"],
                "difficulty": i,
                "exam_level": 1,
            })
        p = self.home / "pool.json"
        p.write_text(json.dumps(pool), encoding="utf-8")
        self.db.import_words(p)

    def tearDown(self):
        self.tmp.cleanup()

    def test_spelling(self):
        self.assertEqual(evaluate_spelling("abandon", "abandon"), (True, 1.0, "exact"))
        self.assertEqual(evaluate_spelling("abandon", "abandn")[0], False)
        self.assertEqual(evaluate_spelling("abandon", "abandn")[1], 0.5)
        self.assertEqual(evaluate_spelling("cat", "cut")[2], "wrong")

    def test_dictation(self):
        ok, score, exp, got = evaluate_dictation("I want to learn English.", "i want to learn english")
        self.assertTrue(ok)
        self.assertEqual(score, 1.0)
        self.assertEqual(exp, ["i", "want", "to", "learn", "english"])

    def test_normalize(self):
        self.assertEqual(normalize_answer("  Abandon! "), "abandon")
        self.assertEqual(normalize_sentence("I  want, to learn."), "i want to learn")

    def test_levenshtein(self):
        self.assertEqual(levenshtein("kitten", "sitting"), 3)

    def test_spelling_item_prompt(self):
        item = spelling_item({"id": 1, "word": "abandon", "pos": "v.", "meaning_zh": "放弃"}, 1, 20)
        self.assertIn("第 1/20 题", item["prompt"])
        self.assertEqual(item["expected"], "abandon")

    def test_parse_answers(self):
        from english_coach.quiz import _parse_answers
        self.assertEqual(_parse_answers("lose court allow", 3), ["lose", "court", "allow"])
        self.assertEqual(_parse_answers("lose\ncourt\nallow", 3), ["lose", "court", "allow"])
        self.assertEqual(_parse_answers("lose, court、allow", 3), ["lose", "court", "allow"])
        # 数量对不上时空格不拆，保留原有分隔符行为
        self.assertEqual(_parse_answers("look up", 3), ["look up"])

    def test_stats(self):
        today = date.today()
        self.db.set_setting("start_date", (today - timedelta(days=2)).isoformat())
        # Add two lessons, check in one.
        id1 = self.db.upsert_lesson((today - timedelta(days=2)).isoformat(), "a", "a")
        id2 = self.db.upsert_lesson((today - timedelta(days=1)).isoformat(), "b", "b")
        self.db.checkin(id1)
        s = calculate_stats(self.db, today)
        self.assertEqual(s["total_checkins"], 1)
        self.assertEqual(s["expected_days"], 2)
        self.assertEqual(s["missed_days"], 1)
        self.assertEqual(s["streak"], 0)


if __name__ == "__main__":
    unittest.main()
