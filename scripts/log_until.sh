#!/usr/bin/env bash
# Usage: scripts/log_until.sh HHMM
# Logs until HH:MM Chicago time, in chunks of up to 60 min, committing data after each chunk.
set -u
target_hhmm=$1
export TZ=America/Chicago
target=$(date -d "$(date +%F) ${target_hhmm:0:2}:${target_hhmm:2:2}" +%s)

git config user.name "github-actions[bot]"
git config user.email "41898283+github-actions[bot]@users.noreply.github.com"
commit() {
  git add data 2>/dev/null || return 0
  git diff --cached --quiet && return 0
  git commit -qm "data: $(date -u +%Y-%m-%dT%H:%MZ)"
  for i in 1 2 3; do git pull -q --rebase && git push -q && return 0; sleep 5; done
  echo "::warning::push failed"
}

while :; do
  left=$(( (target - $(date +%s)) / 60 ))
  [ "$left" -le 0 ] && break
  chunk=$(( left < 60 ? left : 60 ))
  echo "$(date +%H:%M) logging $chunk min (until ${target_hhmm:0:2}:${target_hhmm:2:2})"
  python3 noyes.py log --minutes "$chunk" > /dev/null || { echo "logger exited with $?"; sleep 30; }
  commit
done
commit
