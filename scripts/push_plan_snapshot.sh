#!/bin/bash
# ─────────────────────────────────────────────────────────────
# push_plan_snapshot.sh — yule planning snapshot → hompage repo
#
# 흐름:
#   1. yule planning daily --json 으로 오늘 plan envelope JSON 생성
#   2. hompage repo 의 public/plan-snapshot.json 으로 write
#   3. diff 가 있으면 git add + commit + push
#
# 환경변수:
#   HOMPAGE_DIR   hompage 로컬 클론 경로 (default: $HOME/local-dev/hompage)
#   HOMPAGE_REMOTE 원격 이름 (default: origin)
#   HOMPAGE_BRANCH 푸시할 브랜치 (default: main)
#   YULE_BIN      yule CLI 경로 (default: uv run yule)
#   SKIP_PUSH     "1" 이면 git push 생략 (테스트용)
#
# cron / systemd timer 에서 호출하기 좋게 idempotent + non-zero exit
# 만 의미 있는 실패로 반환.
# ─────────────────────────────────────────────────────────────

set -euo pipefail

HOMPAGE_DIR="${HOMPAGE_DIR:-$HOME/local-dev/hompage}"
HOMPAGE_REMOTE="${HOMPAGE_REMOTE:-origin}"
HOMPAGE_BRANCH="${HOMPAGE_BRANCH:-main}"
YULE_BIN="${YULE_BIN:-uv run yule}"

SNAPSHOT_PATH="$HOMPAGE_DIR/public/plan-snapshot.json"
TMP_OUTPUT="$(mktemp -t yule-plan-XXXXXX.json)"
trap 'rm -f "$TMP_OUTPUT"' EXIT

log() { printf '[push-plan-snapshot %s] %s\n' "$(date -Iseconds)" "$*"; }

if [[ ! -d "$HOMPAGE_DIR/.git" ]]; then
  log "ERROR: hompage 클론 디렉터리가 아님 — HOMPAGE_DIR=$HOMPAGE_DIR"
  exit 1
fi
if [[ ! -d "$HOMPAGE_DIR/public" ]]; then
  log "ERROR: $HOMPAGE_DIR/public/ 가 없음 — hompage 가 맞는지 확인"
  exit 1
fi

# 1. 최신 main 으로 sync (rebase 충돌 시 abort — 사람 개입 필요)
log "sync hompage main"
git -C "$HOMPAGE_DIR" fetch --quiet "$HOMPAGE_REMOTE" "$HOMPAGE_BRANCH"
git -C "$HOMPAGE_DIR" checkout --quiet "$HOMPAGE_BRANCH"
git -C "$HOMPAGE_DIR" pull --quiet --rebase "$HOMPAGE_REMOTE" "$HOMPAGE_BRANCH" || {
  log "ERROR: git pull --rebase 실패 — 충돌이 있으면 사람이 정리해야 함"
  exit 2
}

# 2. snapshot 생성 (envelope.to_dict() 그대로)
log "generate snapshot via yule planning daily"
if ! $YULE_BIN planning daily --json > "$TMP_OUTPUT" 2>/tmp/yule-plan-snapshot.err; then
  log "ERROR: yule planning daily 실패. stderr:"
  cat /tmp/yule-plan-snapshot.err >&2
  exit 3
fi

# generated_at 추가 (UI 가 신선도 표시할 때 사용). jq 가 있으면 사용,
# 없으면 root 객체에 그냥 append (간단 sed).
if command -v jq >/dev/null 2>&1; then
  jq --arg ts "$(date -Iseconds)" '. + {generated_at: $ts}' "$TMP_OUTPUT" \
    > "$TMP_OUTPUT.with_ts" && mv "$TMP_OUTPUT.with_ts" "$TMP_OUTPUT"
else
  log "WARN: jq 없음 — generated_at 미주입. 권장: brew install jq / apt install jq"
fi

# 3. 변경 있는지 비교 후 write
if [[ -f "$SNAPSHOT_PATH" ]] && diff -q "$TMP_OUTPUT" "$SNAPSHOT_PATH" >/dev/null 2>&1; then
  log "snapshot unchanged, skip git operations"
  exit 0
fi

cp "$TMP_OUTPUT" "$SNAPSHOT_PATH"
log "wrote $SNAPSHOT_PATH"

# 4. git commit + push
cd "$HOMPAGE_DIR"
git add public/plan-snapshot.json

if git diff --cached --quiet; then
  log "no staged changes after copy (race condition?) — skip"
  exit 0
fi

PLAN_DATE="$(jq -r .daily_plan.plan_date "$SNAPSHOT_PATH" 2>/dev/null \
  || jq -r .plan_date "$SNAPSHOT_PATH" 2>/dev/null \
  || date +%Y-%m-%d)"

git commit -m "🔧 plan-snapshot — ${PLAN_DATE} ($(date +%H:%M))" >/dev/null

if [[ "${SKIP_PUSH:-0}" == "1" ]]; then
  log "SKIP_PUSH=1, commit 만 하고 종료"
  exit 0
fi

git push --quiet "$HOMPAGE_REMOTE" "$HOMPAGE_BRANCH"
log "pushed plan-snapshot for $PLAN_DATE to $HOMPAGE_REMOTE/$HOMPAGE_BRANCH"
