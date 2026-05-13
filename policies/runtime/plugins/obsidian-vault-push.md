# Obsidian Vault Auto-push (`obsidian-vault-push`)

## 도입 이유
runtime 이 vault (`/Users/<u>/local-dev/yule-agent-vault/obsidian-vault`)
안에 decision / research / task-log 노트를 작성한 뒤, 외부 git 저장소로
자동 push 하여 운영자 디바이스 간 동기화를 보장. 수동 commit/push 누락
방지.

## 동작
- kind: `delivery`
- hooks_provided: `OUTBOUND_VAULT`, `COMPLETION`
- hooks_consumed: 없음
- risk_class: `MEDIUM` — vault git 충돌 / 잘못된 push
- autonomy_level: `supervised`

## 환경변수
- `YULE_VAULT_AUTOPUSH_ENABLED` — `1` 시 활성 (default OFF — 운영자가 직접
  검토 후 push 를 선호하는 환경 고려)
- `YULE_VAULT_REPO_ROOT` — vault git 루트 경로
- `YULE_VAULT_BRANCH` — push 대상 브랜치 (default `main`)

## 운영 가이드
- 끄는 방법: ENABLED=0 시 runtime 은 vault 파일만 write 하고 commit/push 안 함
  (사용자가 직접 처리).
- 충돌 시: auto-push 가 push 실패 보고 → 운영자 수동 resolve. force push 금지.
- secret/PII: PasteGuard 통과한 페이로드만 vault 에 들어가므로 push payload 도
  같은 검증을 통과한 상태.

## 관련 정책 / 이슈
- F8 Obsidian export 컨벤션
- `policies/runtime/agents/engineering-agent/memory-policy.md`
