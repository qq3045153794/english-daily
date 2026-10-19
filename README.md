# English Daily

每日英语学习归档：Hermes/Telegram 负责推送、打卡和训练，GitHub Pages 负责展示学习内容与打卡记录。

## 目录

- `docs/`：GitHub Pages 静态站点
- `data/word_pool.json`：从 ECDICT 提取的 CET4/CET6/IELTS 词库
- `english_coach/`：课程生成、打卡、训练和统计代码
- `scripts/ec.py`：命令行入口

## 数据来源

词库由 [ECDICT](https://github.com/skywind3000/ECDICT) 提取，ECDICT 使用 MIT License。

## 本地初始化

```bash
python3 scripts/ec.py init
python3 scripts/ec.py build-pool --download
python3 scripts/ec.py import-words
python3 scripts/ec.py prepare
```

## 服务器命令

```bash
/home/admin/fw-venv/bin/python /home/admin/english-daily/scripts/ec.py prepare
/home/admin/fw-venv/bin/python /home/admin/english-daily/scripts/ec.py push
```
