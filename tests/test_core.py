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
        # 用户要求：拼写题不给首字母提示，只给字母数
        self.assertNotIn("首字母", item["prompt"])
        self.assertIn("7 个字母", item["prompt"])

    def test_quiz_clue_keeps_common_pos(self):
        from english_coach.words import quiz_clue
        # might 的冷门名词义不能盖掉情态动词义
        pos, zh = quiz_clue("n. 力量, 权力\\naux. 可能, 也许", "")
        self.assertIn("可能", zh)
        self.assertTrue(zh.index("可能") < zh.index("力量"))
        # less / least 必须能靠"最"区分开
        _, less = quiz_clue("n. 较少, 较小\\na. 少的, 小的\\nadv. 较少, 较小, 较差", "")
        _, least = quiz_clue("n. 最少, 最小, 最小限度\\na. 最少的, 最小的\\nadv. 最小, 最少", "")
        self.assertNotEqual(less, least)
        self.assertIn("最少", least)
        # 领域标签和噪声丢弃
        _, power = quiz_clue("n. 力, 体力\\nvt. 使...有力量\\n[计] 乘幂; DOS外部命令:能控制许多电池电源计算机上的电源管理特性", "")
        self.assertNotIn("DOS", power)
        self.assertNotIn("[", power)

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

    def test_stats_counts_today_when_checked(self):
        today = date.today()
        self.db.set_setting("start_date", (today - timedelta(days=1)).isoformat())
        id1 = self.db.upsert_lesson((today - timedelta(days=1)).isoformat(), "a", "a")
        id2 = self.db.upsert_lesson(today.isoformat(), "b", "b")
        self.db.checkin(id1)
        self.db.checkin(id2)
        s = calculate_stats(self.db, today)
        self.assertEqual(s["total_checkins"], 2)
        self.assertEqual(s["expected_days"], 2)
        self.assertEqual(s["completion_rate"], 100.0)


if __name__ == "__main__":
    unittest.main()
