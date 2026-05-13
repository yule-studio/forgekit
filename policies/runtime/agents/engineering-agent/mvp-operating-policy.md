# Engineering Agent MVP Operating Policy

이 문서는 MVP 단계의 engineering-agent 운영 결정을 명문화한다. 결정마다 **선택 / 이유 / 반영 범위 / 회피 동작** 4가지 항목을 채운다.

## 결정 1. 작업 범위 (Action Scope)

- **선택**: Draft Pull Request 생성까지
- **이유**:
  - "분석/제안만"은 부서 정체성("실행 부서")과 어긋남. 코드를 만드는 부서가 아무 코드도 만들지 않으면 다른 부서 대비 차별점이 사라짐.
  - "자동 머지"는 사용자 승인 없는 변경을 production-bound 브랜치에 흘려보낼 위험. MVP 단계에서는 위험이 큼.
  - Draft PR 생성은 사람 리뷰 단계가 안전망. 부서가 코드를 만들면서도 사용자 통제권이 유지됨.
- **반영 범위**:
  - `manifest.json:write_policy.require_human_review_before_publish=true` (이미 적용)
  - 러너(다음 단계)는 git 브랜치 생성 + diff 작성 + draft PR 생성까지 권한 부여
  - `workflow.md`에 PR 생성 단계 명시
- **회피 동작**:
  - main/master 머지 절대 X
  - production 배포 절대 X
  - `--no-verify`, `git push --force` 같은 위험 옵션 X (사용자가 명시 요청한 경우만 예외)
  - PR 안에서 secrets 노출 X

## 결정 2. 트리거 (Trigger Mechanism)

- **선택**: Discord yes/no 확인
- **이유**:
  - 기존 `discord/checkpoint_state.py`의 `pending_confirmation` 인프라를 재활용 가능. 새 인증/승인 흐름을 만들 필요 없음.
  - "CLI 수동"만으로도 안전하지만 Discord 안에서 자연스러운 흐름이 부서 운영의 핵심 경험.
  - "자동 실행"은 통제 어려움. 우선순위 1순위 작업을 매일 자동 시작하면 사용자가 모르는 사이 코드가 만들어질 수 있음.
- **반영 범위**:
  - planning-agent가 `coding_agent_handoff` 후보를 선정 → 게이트웨이가 Discord에 "이 작업 시작할까요? `yes`/`no`" 메시지
  - `yes` 응답 시 백그라운드에서 디스패처 호출
  - `no` 응답 시 다음 후보 또는 사용자 직접 지정 대기
  - `workflow.md`에 트리거 흐름 추가
- **회피 동작**:
  - 우선순위 1순위 이슈 자동 시작 X
  - 사용자 미응답 시 30분 안에 자동 시작 X (TTL 만료되면 그냥 사라짐)
  - 한 yes 응답으로 여러 이슈 동시 시작 X

## 결정 3. 멤버 간 통신 (Inter-member Communication)

- **선택**: Discord 채널 + 내부 메시지 큐 하이브리드
- **이유**:
  - 사용자가 멤버 간 토의 흐름을 직접 보면서 개입할 수 있어야 함 (Discord 채널의 장점)
  - 모든 멤버 간 호출을 Discord에 노출하면 채팅이 너무 시끄러움 (메시지 큐의 장점)
  - 절충: **결정/산출/외부 회신**은 Discord에 노출, **내부 분배/메시지 라우팅**은 메모리/로그 기반 큐로 처리
- **반영 범위**:
  - 멤버 간 작업 분배는 게이트웨이의 메모리 큐 (`AgentMessage` dataclass, 다음 단계 구현)
  - 결정/합의/산출물은 Discord 채널에 게시 (사용자가 보는 부분)
  - tech-lead의 작업 분해 결과는 사용자에게 한 번 노출하고 yes 받은 뒤 멤버 호출 진행
- **회피 동작**:
  - 모든 내부 메시지를 Discord에 그대로 dump X (노이즈)
  - 메시지 큐에 결정 사항을 묻어두고 사용자가 못 보게 X (투명성 위반)

## 결정 4. Discord 봇 운영 방식 (Bot Identity)

- **선택**: 단계적 — 1단계는 단일 봇 페르소나 분기, 2단계는 멤버별 별도 봇 토큰
- **이유**:
  - 멤버 5명 = 별도 Discord application 5개 만드는 것은 MVP 진입 비용이 큼.
  - 단일 봇이 멘션받은 역할에 따라 페르소나 분기하는 형태로 시작하면 빠르게 검증 가능.
  - 검증 후 페르소나를 시각적으로 구분하고 싶을 때 멤버별 별도 봇으로 마이그레이션. 같은 채널 안에서 5개 봇이 각자 발화하면 토의 흐름이 명확.
- **반영 범위**:
  - **1단계 (현재 MVP 단계)**: 기존 봇 토큰 1개 그대로 사용. `engineering-agent` 게이트웨이가 Discord 채팅에서 멤버 이름을 명시(`@tech-lead 의견 부탁`)받으면 그 페르소나로 응답.
  - **2단계**: 멤버별 `DISCORD_BOT_TOKEN_<MEMBER>` 환경변수 도입, `yule discord bot --agent <name>` CLI 추가, 각 봇이 독립 프로세스로 가동.
  - 페르소나 정의는 멤버 `manifest.json:description`과 `CLAUDE.md`에 그대로 의존. 봇 ID만 다를 뿐 책임 정의는 동일.
- **회피 동작**:
  - 1단계에서 1개 봇이 5개 페르소나처럼 보이려고 멘션 위조 X
  - 2단계에서 봇 토큰을 git에 커밋 X (`.env.local`에서만 관리)
  - 멤버 봇이 게이트웨이 거치지 않고 외부 시스템(GitHub, planning-agent)에 직접 호출 X

## 결정 요약 표

| 항목 | 1단계 (지금) | 2단계 (다음 마일스톤) |
|---|---|---|
| 작업 범위 | Draft PR 생성 | 동일 |
| 트리거 | Discord yes/no | 동일 |
| 통신 방식 | 결정/산출은 Discord, 내부 분배는 메모리 큐 | 동일 |
| 봇 운영 | 단일 봇 페르소나 분기 | 멤버별 별도 봇 토큰 |

## 변경 절차

이 정책을 변경하려면:

1. 변경 이유와 영향 범위를 PR 본문에 적는다.
2. `manifest.json:write_policy`나 `workflow.md`처럼 강하게 결합된 문서가 있는지 확인하고 함께 갱신한다.
3. 기존 마일스톤 결정과 충돌하지 않는지 검토한다 (예: "자동 머지" 결정으로 변경하려면 단일 write executor 원칙도 같이 풀어야 함).
