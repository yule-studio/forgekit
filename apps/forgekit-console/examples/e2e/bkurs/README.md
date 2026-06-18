# BKURS end-to-end evidence bundle (forgekit WT1–WT6)

요청: **"bkurs-fe와 bkurs-be를 완성해줘. 디자인, 간격, 운영도 부족한 것 같아."**

이 번들은 forgekit 운영 흐름이 실제로 닫히는 것을 보여주는 evidence 다. 재생성:
`python -m unittest tests.forgekit.test_bkurs_e2e` (검증) — 산출물은 아래.

| 파일 | 단계 | 무엇 |
| --- | --- | --- |
| `1-handoff.json` | WT2 | PM intake → packet → gateway → tech-lead split. 역할 분배 + 운영 신호 → 배포/인프라 **BLOCKED**. trace(누가→누구). |
| `2-runtime-loop.json` | WT3 | bounded always-on 루프 — 관측→분류→패킷→handoff→대기. privileged 영역 → runbook + 대기. **execute phase 없음**. |
| `3-runbook-deploy.md` | WT3 | 배포 권한 없음 → Terraform/ops/approval runbook (가짜 실행 대신). |
| `4-operator-inbox.json` | WT4 | operator 알림 — `ACCESS_REQUIRED`, action-oriented(무엇/왜/지금/옵션). desktop(opt-in)+inbox 동일 사건. |
| `5-vault-note.md` | WT5 | authored vault 노트 — `agent_author`/`agent_role`/`phase`/`handoff_*`/`cssclasses`. 누가 어느 단계에서 썼는지. |

## 아직 사람 결정이 필요한 지점 (정직)
- 배포/인프라/IAM apply → operator 승인 + runbook 수행 (forgekit 자율 범위 밖).
- 구체 제품 결정(공개 정책/노출 순서 등) → PM 결정 질문(있으면 packet 에).

## 다음 자동 / 수동 단계
- 자동: 역할 ready task(FE/BE/QA)는 엔지니어 runner 연결 시 진행(후속 WT).
- 수동: BLOCKED 영역은 위 runbook 으로 operator 가 직접 수행 후 forgekit 에 결과 통보.
