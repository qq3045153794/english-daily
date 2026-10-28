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

    def test_normalize_chinese(self):
        from english_coach.config import normalize_chinese
        self.assertEqual(normalize_chinese("他决定推迟会议，直到下周。"), "他决定推迟会议直到下周")
        self.assertEqual(normalize_chinese("  参考译文：OK!  "), "参考译文ok")

    def test_translation_grading(self):
        from english_coach.quiz import evaluate_translation
        ref = "他决定把会议推迟到下周。"

        # 完全一致
        ok, score, mode, _ = evaluate_translation("He put off the meeting.", ref, ref)
        self.assertTrue(ok)
        self.assertEqual((score, mode), (1.0, "exact"))

        # LLM 判分：意思到位但措辞不同
        ok, score, mode, comment = evaluate_translation(
            "He put off the meeting.", ref, "他把会议推迟到了下个星期。",
            judge=lambda en, zh, ans: {"score": 92, "comment": "译文准确"},
        )
        self.assertTrue(ok)
        self.assertEqual((score, mode, comment), (1.0, "ok", "译文准确"))

        # LLM 判分：漏了关键信息
        ok, score, mode, _ = evaluate_translation(
            "He put off the meeting.", ref, "他取消了会议。",
            judge=lambda en, zh, ans: {"score": 55, "comment": "把推迟译成了取消"},
        )
        self.assertFalse(ok)
        self.assertEqual((score, mode), (0.5, "partial"))

        # LLM 挂了要退回相似度，不能把正确译文判错
        ok, score, mode, _ = evaluate_translation(
            "He put off the meeting.", ref, ref,
            judge=lambda en, zh, ans: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        self.assertTrue(ok)

        # 空答案
        ok, score, mode, _ = evaluate_translation("He put off the meeting.", ref, "   ")
        self.assertEqual((ok, score, mode), (False, 0.0, "empty"))

    def test_blueprint_tail_is_one_dictation_and_three_translations(self):
        from english_coach.lesson import build_blueprint
        cards = []
        for i in range(5):
            cards.append({
                "word": "word%d" % i,
                "word_id": i + 1,
                "pos": "n.",
                "meaning_zh": "释义%d" % i,
                "example": "Example sentence number %d about word%d." % (i, i),
                "example_type": "declarative" if i % 2 else "wh",
                "example_zh": "第%d个例句的中文翻译。" % i,
            })
        data = {
            "dictation_extra": [
                {"sentence": "Extra sentence one uses several words.", "translation_zh": "补充句一的中文翻译。"},
                {"sentence": "Extra sentence two uses several words.", "translation_zh": "补充句二的中文翻译。"},
            ]
        }
        bp = build_blueprint(self.db, cards, data, spelling_count=5, dictation_count=1, translation_count=3)
        self.assertEqual(len(bp["dictation"]), 1)
        self.assertEqual(len(bp["translation"]), 3)
        # 听写题不重复出现在翻译题里
        dict_sentences = {x["expected"].lower() for x in bp["dictation"]}
        for item in bp["translation"]:
            self.assertEqual(item["kind"], "translation")
            self.assertNotIn(item["english"].lower(), dict_sentences)
            self.assertIn(item["english"], item["prompt"])
            self.assertIn("翻译成中文", item["prompt"])
            self.assertTrue(item["expected"])
        # 译文必须来自课文，而不是空
        zh_pool = {c["example_zh"] for c in cards} | {"补充句一的中文翻译。", "补充句二的中文翻译。"}
        for item in bp["translation"]:
            self.assertIn(item["expected"], zh_pool)

    def test_quiz_review_only_picks_due_words(self):
        from english_coach.words import select_quiz_words
        with self.db.connect() as conn:
            words = [dict(r) for r in conn.execute("SELECT id, word FROM words ORDER BY id")]
        lesson = [{"id": words[0]["id"], "word": words[0]["word"], "pos": "n.", "meaning_zh": "释义"}]
        # 第 3、4 个词：错 3 次但没到期；第 5 个词：错 1 次，已逾期
        with self.db.connect() as conn:
            conn.execute(
                "INSERT INTO word_state(word_id,status,wrong_count,correct_count,streak,ease,interval_days,next_review_at) VALUES(?,?,?,?,?,?,?,?)",
                (words[2]["id"], "learning", 3, 0, 0, 2.3, 1, (date.today() + timedelta(days=10)).isoformat()),
            )
            conn.execute(
                "INSERT INTO word_state(word_id,status,wrong_count,correct_count,streak,ease,interval_days,next_review_at) VALUES(?,?,?,?,?,?,?,?)",
                (words[3]["id"], "learning", 3, 0, 0, 2.3, 1, (date.today() + timedelta(days=10)).isoformat()),
            )
            conn.execute(
                "INSERT INTO word_state(word_id,status,wrong_count,correct_count,streak,ease,interval_days,next_review_at) VALUES(?,?,?,?,?,?,?,?)",
                (words[4]["id"], "learning", 1, 0, 0, 2.3, 1, (date.today() - timedelta(days=1)).isoformat()),
            )
        picked = [x["word"] for x in select_quiz_words(self.db, lesson, count=5)]
        self.assertIn(words[0]["word"], picked)
        self.assertIn(words[4]["word"], picked)      # 到期错词要复习
        self.assertNotIn(words[2]["word"], picked)   # 没到期不许提前抽
        self.assertNotIn(words[3]["word"], picked)
        self.assertEqual(len(picked), 2)             # 不拿未到期的词凑满 20

    def test_translation_item_is_delivered_alone(self):
        from english_coach.quiz import _batch_text, _pending_batch
        items = [
            {"kind": "spelling", "word_id": 1, "prompt": "拼写题", "expected": "abandon", "hint": "", "source": "new"},
            {"kind": "dictation", "word_id": None, "prompt": "听写题", "expected": "I am here.", "hint": "", "source": "new"},
            {"kind": "translation", "word_id": None, "prompt": "第 1/3 题：请把下面的英文翻译成中文。\n\nI am here.", "expected": "我在这里。", "english": "I am here.", "hint": "", "source": "new"},
            {"kind": "translation", "word_id": None, "prompt": "第 2/3 题：请把下面的英文翻译成中文。\n\nYou are there.", "expected": "你在那里。", "english": "You are there.", "hint": "", "source": "new"},
        ]
        session_id = self.db.create_session(1, items)
        session = self.db.active_session()
        self.assertEqual(int(session["id"]), session_id)
        batch = _pending_batch(self.db, session, 5)
        self.assertEqual(len(batch), 1)
        # 逐题推进到第一道翻译
        self.db.advance_session(session_id, 0.0, steps=2)
        session = self.db.active_session()
        batch = _pending_batch(self.db, session, 5)
        self.assertEqual(len(batch), 1)
        self.assertEqual(batch[0]["kind"], "translation")
        self.assertEqual(batch[0]["english"], "I am here.")
        text = _batch_text(SimpleNamespace(), batch, SimpleNamespace())
        self.assertIn("I am here.", text)
        self.assertNotIn("请依次拼写", text)
        # 推进后下一道翻译同样是单题
        self.db.advance_session(session_id, 0.0, steps=1)
        batch = _pending_batch(self.db, self.db.active_session(), 5)
        self.assertEqual(batch[0]["english"], "You are there.")

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
