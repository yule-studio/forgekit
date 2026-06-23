---
title: "도입 검토: GeekNews 발견: 경량 TUI 상태 대시보드 라이브러리"
kind: adoption-review
status: draft
created_at: 2026-06-23
tags: [forgekit, discovery, adoption-review, tool_candidate, collect-first]
related: []
agent_author: user-researcher
agent_role: User Researcher
handoff_from: discovery
handoff_to: pm
phase: discovery
source_flow: discovery-adoption
cssclasses: [fk-user-research]
agent_color: "#c084fc"
---

> [!fk-user-research] User Researcher · phase: discovery · discovery → pm

## 핵심 요약
- 후보: GeekNews 발견: 경량 TUI 상태 대시보드 라이브러리 (분류 tool_candidate, score 4.0)
- disposition: **collect-first** — 근거는 충분치 않음 — 3축 검토 전까지 evidence 만 누적(즉시 활성화 안 함)

## 도입 효율 검토 (8축)
1. current pain — 콘솔에서 런타임 상태를 한눈에 보기 어려움 — 경량 도구 필요
2. expected benefit — 기존보다 단순/저비용 TUI
3. overlap — 기존 capability와 겹침 관측 안 됨 (도입 시 별도 검증 필요)
4. operational cost — 설치·attach·버전 유지 비용 (toolchain 영향)
5. maintenance risk — 외부 의존·ToS·유지보수 리스크 (vendor lock-in 점검)
6. provider/runtime fit — provider-neutral 로 평가 (특정 vendor 가정 금지) — runtime/harness fit 은 specialist 검토
7. governance/security — 표준 승인 게이트 적용 (실행은 게이트 통과 후)
8. adopt-now vs collect-first vs hold — collect-first

## 3축 검토 (PM / tech-lead / specialist)
- 요청 대상: product-manager, tech-lead, platform-runtime-engineer
- 질문: 이 후보를 adopt-now / collect-first / hold 중 무엇으로 둘지 — pain·benefit·overlap·cost·risk·fit·governance 기준으로 검토 요청
- 상태: 검토 대기 (collect-first — 즉시 활성화 안 함)

## 적용 맥락
- collect-first 면 evidence 만 누적, adopt-now 결정 시에만 armory intake(promote_candidate)로 연결.
- adopted(검증된 catalog spec) ≠ equipped(register_promoted) — 장착은 별도 명시 단계.

## 참고
- candidate_id: 콘솔에서-런타임-상태를-한눈에-보기-어려움-—-경량-도구 · 출처: geeknews
