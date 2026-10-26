---
name: english-coach
description: Daily English lessons, check-ins, and Telegram quizzes.
version: 0.1.0
author: Xie Tao, Hermes Agent
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [English, Learning, Telegram, Spaced Repetition]
    related_skills: []
---

# English Coach

Use this skill for the daily English lesson, Telegram check-in, spelling quiz, sentence dictation, English→Chinese translation, pronunciation, and learning statistics.

## Canonical command

```bash
/home/admin/fw-venv/bin/python /home/admin/english-daily/scripts/ec.py <command>
```

The SQLite database and lesson JSON are managed only by this CLI. Never use `sqlite3`, Python snippets, or file edits to change learning state.

## Commands

- `今日英语`, `今日课程`, `打开课程` → `show`
- `打卡` → `checkin`
- `只打卡` → `checkin --no-quiz`
- `开始训练` → `start`
- `提示` → `hint`
- `跳过` → `skip`
- `暂停` → `pause`
- `继续` → `resume`
- `统计`, `打卡统计` → `stats`
- `错词本` → `errors`
- `发音 WORD` → `speak WORD`
- `训练状态` → `quiz-status`

Do not call both `checkin` and `start` for the same “打卡” message. `checkin` already starts training and returns the first question.

## Answer routing

1. If the message is one of the commands above, run that command.
2. Otherwise run `quiz-status` first. If it says a quiz is active or paused, treat the whole user message as an answer and run:

```bash
/home/admin/fw-venv/bin/python /home/admin/english-daily/scripts/ec.py answer <user message>
```

3. If there is no active quiz, answer as a normal conversation.

The CLI speaks dictation audio directly through Telegram. Its stdout is the exact text to send back to the user. Return stdout verbatim, without adding answers, hints, or extra questions.

## Rules

- Never reveal the expected spelling, dictation sentence or reference translation before the user answers.
- If the CLI prints `ERROR:`, tell the user that error plainly; do not invent a lesson or quiz.
- If a lesson is missing, say it has not been generated and do not start a quiz.
- Keep one quiz session. Do not create a second session when the user sends “打卡” twice.
- The user can pause and resume a quiz. Do not discard progress.
- Do not modify the GitHub repository, Pages settings, cron jobs, or database directly unless the user explicitly asks for maintenance.
