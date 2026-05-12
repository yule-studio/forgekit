# RepoMap (`repo-map`)

## 도입 이유
작업 시작 전 (preflight) 레포의 디렉터리/모듈 지도를 한 번 만들어
role-take 가 "어디서 시작할지" 를 빠르게 판단하도록 한다. 매번 git ls-files
+ 디렉터리 탐색을 LLM 에게 시키는 대신 캐시된 지도를 1 회 주입.

## 동작
- kind: `exploration`
- hooks_provided: `PREFLIGHT`
- hooks_consumed: 없음
- risk_class: `LOW` — 읽기 전용
- autonomy_level: `advisory`

## 환경변수
없음. 캐시 위치는 hookify 와 동일한 DB 영역 또는 메모리.

## 운영 가이드
- 끄는 방법: 끌 수 있음. 대신 LLM 컨텍스트에 디렉터리 트리 비용이
  매 작업 추가됨 (token 증가).
- 갱신: 큰 리팩터 후 캐시 invalidate. 자동 mtime 기반 (F14 preamble
  cache 와 같은 패턴).

## 관련 정책 / 이슈
- F14 (#124) preamble cache + skill codegen 과 함께 동작
- `policies/runtime/agents/engineering-agent/context-compression.md`
