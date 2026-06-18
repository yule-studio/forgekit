---
title: handoff — bkurs
kind: handoff
status: proposed
created_at: 2026-06-17
tags: [forgekit, handoff]
related: []
agent_author: tech-lead
agent_role: Tech Lead
handoff_from: gateway
handoff_to: engineers
phase: tech-lead
source_flow: pm-intake-handoff
cssclasses: [fk-techlead]
agent_color: "#00d8f0"
---

> [!fk-techlead] Tech Lead · phase: tech-lead · gateway → engineers

## 핵심 요약
- 요청: bkurs-fe와 bkurs-be를 완성해줘. 디자인, 간격, 운영도 부족한 것 같아.
- goal: bkurs-fe와 bkurs-be를 완성해줘. 디자인, 간격, 운영도 부족한 것 같아.
- 역할 분배: 3개 · blocked: 1개

## 역할 split
- [DevOps] 배포/인프라 rollout (권한 필요) **(BLOCKED — operator/runbook 필요)**
- [QA] acceptance criteria 기반 회귀/스모크 테스트 작성
- [Tech Lead] 범위/리스크/순서 조율 + role handoff 승인

## handoff trace (누가 무엇을 언제)
- Product (PM) (intake): operator → gateway — raw ask → implied features 보강 + 결정/기본값 정리
- Engineering Gateway (gateway): product-agent → tech-lead — ProductIntentPacket 검증 후 tech-lead 로 전달
- Tech Lead (tech-lead): gateway → engineers — 3 role tasks (1 blocked)
