#!/usr/bin/env bash
set -uo pipefail
LOG=/home/admin/.hermes/english/prepare.log
mkdir -p /home/admin/.hermes/english
out=$(/home/admin/fw-venv/bin/python /home/admin/english-daily/scripts/ec.py prepare 2>&1)
rc=$?
printf '%s\n' "$out" >> "$LOG"
if [ "$rc" -ne 0 ]; then
  printf '英语课程准备失败：%s\n' "$out"
fi
exit 0
