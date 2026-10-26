from __future__ import annotations

import json
import re
from typing import Any, Dict, List

import requests


SYSTEM_PROMPT = """You are an experienced ESL curriculum designer for a Chinese learner preparing for CET-4, CET-6 and IELTS. You write natural, level-appropriate English and concise Chinese explanations. Return valid JSON only, with no markdown fences and no commentary."""


def _extract_json(text: str) -> Dict[str, Any]:
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
        text = re.sub(r"\s*```$", "", text)
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        text = text[start:end + 1]
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("LLM response is not a JSON object")
    return data


class DeepSeekClient:
    def __init__(self, api_key: str, base_url: str = "https://api.deepseek.com", model: str = "deepseek-v4-pro") -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model

    def chat_json(self, user_prompt: str, temperature: float = 0.6, max_tokens: int = 7000) -> Dict[str, Any]:
        if not self.api_key:
            raise RuntimeError("DEEPSEEK_API_KEY is not configured")
        url = self.base_url + "/chat/completions"
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},
        }
        resp = requests.post(
            url,
            headers={"Authorization": "Bearer " + self.api_key, "Content-Type": "application/json"},
            json=payload,
            timeout=240,
        )
        if resp.status_code >= 400:
            raise RuntimeError("DeepSeek HTTP %s: %s" % (resp.status_code, resp.text[:800]))
        data = resp.json()
        message = data["choices"][0].get("message", {})
        content = message.get("content", "")
        try:
            return _extract_json(content)
        except Exception as exc:
            raise RuntimeError("Invalid JSON from DeepSeek: %s; content=%r; finish=%r" % (
                exc, str(content)[:1200], data["choices"][0].get("finish_reason")
            ))


def lesson_prompt(selected: List[Dict[str, Any]], lesson_date: str) -> str:
    words = []
    for item in selected:
        words.append({
            "word": item.get("word"),
            "pos": item.get("pos"),
            "phonetic": item.get("phonetic"),
            "meaning_zh": item.get("meaning_zh"),
            "tags": item.get("tags", []),
            "role": item.get("role", "new"),
            "difficulty": item.get("difficulty", 0),
        })
    return """Create one daily English lesson for %s.

Selected words (use exactly these 15 words, in this order):
%s

Return a JSON object with this exact shape:
{
  "words": [
    {
      "word": "same as input",
      "pos": "part of speech, concise",
      "meaning_zh": "concise Chinese meaning",
      "collocation": "one common English collocation",
      "example": "natural English sentence, 8-18 words",
      "example_type": "declarative | yes_no | wh | negative | passive | relative | conditional | comparative",
      "example_zh": "Chinese translation"
    }
  ],
  "sentence_patterns": [
    {"type": "one of the sentence types above", "sentence": "English sentence using at least one selected word", "note_zh": "short Chinese note"}
  ],
  "reading": {
    "title": "English title",
    "text": "one coherent passage of 180-260 words. Use at least 10 of the selected words naturally. Bold each selected word with **word** in the text.",
    "used_words": ["selected words actually used"],
    "questions": [
      {"question": "English comprehension question", "answer": "short English answer"}
    ]
  },
  "dictation_extra": [
    {"sentence": "new English sentence of 10-20 words using at least 2 selected words, different from the word examples", "translation_zh": "Chinese translation", "words_used": ["selected words"]}
  ]
}

Requirements:
- Exactly 15 items in "words", one for each selected word.
- Exactly 5 items in "sentence_patterns".
- Exactly 3 items in "reading.questions".
- Exactly 2 items in "dictation_extra".
- Use natural, level-appropriate English. Do not make the reading sound like a word list.
- Do not put quiz answers or this instruction in the lesson.
- JSON only.
""" % (lesson_date, json.dumps(words, ensure_ascii=False, indent=2))


def repair_prompt(original: str, errors: List[str]) -> str:
    return original + "\n\nYour previous response failed validation:\n- " + "\n- ".join(errors) + "\nReturn the corrected complete JSON object only."


def translation_judge_prompt(english: str, reference_zh: str, answer: str) -> str:
    return """You are grading a Chinese learner's English-to-Chinese translation.

English sentence: %s

Reference Chinese translation: %s

Learner's translation: %s

Grade ONLY whether the meaning is conveyed. Ignore punctuation, Traditional/Simplified script, word order, and whether the Chinese sounds literary. Mark down when the learner:
- omits key information (subject, action, time, quantity, negation, condition)
- mistranslates the meaning (wrong subject/object, opposite meaning, wrong word sense)
- leaves the English untranslated or only copies it

Scoring: 90-100 = meaning fully correct (wording may differ); 70-89 = correct core meaning with a minor omission or slight inaccuracy; 40-69 = partially right but a key element is missing or wrong; 0-39 = mostly wrong or unrelated.

Return JSON only:
{"score": 0-100 integer, "verdict": "correct|partial|wrong", "comment": "one short Chinese sentence naming what is missing or wrong, or praising an accurate paraphrase", "missing": ["关键信息点未译出"]}
""" % (english, reference_zh, answer)
