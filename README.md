# English Daily

每日英语学习归档：Hermes/Telegram 负责推送、打卡和训练，GitHub Pages 负责展示学习内容与打卡记录。

- 学习页：https://qq3045153794.github.io/english-daily/
- 仓库：https://github.com/qq3045153794/english-daily

## 工作流程

- 每天 19:40：生成当天课程、Markdown、静态 HTML 和训练蓝图。
- 每天 20:30：Telegram 推送课程链接。
- 用户回复“打卡”：立即开始 20 题拼写 + 4 句听写。
- 训练完成后：更新 SQLite、打卡日历和 GitHub Pages。

## 目录

- `docs/`：GitHub Pages 纯静态 HTML
- `lessons/`：GitHub 上的 Markdown 学习归档
- `data/word_pool.json`：从 ECDICT 提取的 CET4/CET6/IELTS 词库
- `english_coach/`：课程生成、打卡、训练和统计代码
- `scripts/ec.py`：命令行入口
- `deploy/hermes/`：Hermes Skill 和定时脚本
- `tests/`：核心逻辑测试

## 数据来源

词库由 [ECDICT](https://github.com/skywind3000/ECDICT) 提取，ECDICT 使用 MIT License。

## 常用命令

```bash
# 初始化
/home/admin/fw-venv/bin/python /home/admin/english-daily/scripts/ec.py init

# 生成今天课程（不推送）
/home/admin/fw-venv/bin/python /home/admin/english-daily/scripts/ec.py prepare

# 发送今日课程
/home/admin/fw-venv/bin/python /home/admin/english-daily/scripts/ec.py push

# 打卡并立即开始训练
/home/admin/fw-venv/bin/python /home/admin/english-daily/scripts/ec.py checkin

# 统计、错词本
/home/admin/fw-venv/bin/python /home/admin/english-daily/scripts/ec.py stats
/home/admin/fw-venv/bin/python /home/admin/english-daily/scripts/ec.py errors
```

## 私有状态

真正的学习状态保存在服务器 `/home/admin/.hermes/english/english.db`，训练答案、错词本和完整历史不会提交到公开仓库。

## 安全

- GitHub Token 保存在服务器 `~/.hermes/english/.github_token`，权限 600。
- Telegram Token 和 DeepSeek Key 来自 Hermes 的 `~/.hermes/.env`。
- GitHub Pages 是公开静态站点，只放脱敏学习内容和打卡统计。
