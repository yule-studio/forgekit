# Claude Mem Unifier (`claude-mem`)

## 도입 이유
F10 (#150) 의 5-소스 (mistake ledger / decision 노트 / research /
task-log / Discord 합의) long-term memory 를 통합해, 같은 주제가 다음
session 에서 다시 들어왔을 때 운영자가 매번 컨텍스트를 다시 붙여 줄 필요
없게 한다. F14 (#124) preamble cache 와 함께 토큰 천장 안에서 작동.

## 동작
- kind: `learning`
- hooks_provided: `PREFLIGHT` (관련 기억 surface)
- hooks_consumed: `COMPLETION` (새 기억 후보 추출)
- risk_class: `MEDIUM` — 잘못된 기억 surface 는 결정 흐림
- autonomy_level: `advisory`

## 환경변수
- `YULE_LONG_TERM_MEMORY_ENABLED` — `1` 시 동작
- `YULE_MEMORY_FRESHNESS_DAYS` — 기억 신선도 윈도우 (default 90 일)
- `YULE_MEMORY_MAX_SHARDS_PER_QUERY` — preamble 주입 최대 shard 수

## 운영 가이드
- 끄는 방법: ENABLED=0 시 preamble 에 memory 섹션이 비어 나감.
- 검증: 새 기억 surface 가 잘못이면 vault 의 해당 decision/task-log 파일을
  직접 수정 후 fresh sync. claude-mem 은 그 파일을 권위 source 로 사용.
- 보안: PII / secret 은 PasteGuard 통과 후만 ledger 에 들어감.

## 관련 정책 / 이슈
- F10 (#150) memory unifier 도입
- F14 (#124) memory auto-wire into preamble
- `policies/runtime/agents/engineering-agent/memory-policy.md`
