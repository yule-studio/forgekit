# Engineering Agent Inter-member Message Protocol (v0)

이 문서는 engineering-agent 부서 안의 멤버들 — 그리고 향후 cto-agent / design-agent / marketing-agent 부서 — 가 주고받는 **표준 메시지 계약**을 정의한다. 코드 진실 소스는 `apps/engineering-agent/src/yule_engineering/agents/message.py`.

## 1. 목적과 범위
- 부서 내부 왕복 흐름(tech-lead → 멤버 → tech-lead → gateway)을 한 데이터 모델로 통일한다.
- 다른 부서가 그대로 재사용할 수 있도록 부서 비종속(`from_role`/`to_role`은 free-form 문자열) 형태로 정의한다.
- 본 단계는 **계약과 빌더만** 정의한다. 영속화·운반(채널)은 다음 멤버 봇 IPC 마일스톤에서 다룬다.

## 2. 라우팅 주소

`role_address(agent_id, role) -> "<agent>/<role>"` 형식을 표준으로 사용한다.

예시
- `engineering-agent/tech-lead`
- `engineering-agent/backend-engineer`
- `design-agent/product-designer` (분리 시)
- 게이트웨이 회신은 특수값 `"gateway"`로 표시한다.

## 3. 필수 필드

```
AgentMessage
├── 라우팅
│   ├── from_role: str
│   └── to_role: str
├── 작업 컨텍스트
│   ├── task_type: str  # TaskType enum value 권장
│   ├── topic: str       # 짧은 제목
│   ├── content: str     # 본문
│   ├── requested_action: RequestedAction
│   └── priority: Priority  # 기본 P2
├── 스레딩 메타
│   ├── message_id: str (auto, 12 hex)
│   ├── parent_message_id: Optional[str]
│   ├── thread_id: Optional[str]
│   ├── run_id: Optional[str]   # workflow session id 권장
│   └── created_at: datetime
└── 레퍼런스 팩 (UI/UX/마케팅/콘텐츠 작업 시)
    ├── context_refs: tuple[ContextRef, ...]   # PR/issue/file 포인터
    ├── reference_links: tuple[str, ...]        # URL (사용자 1순위)
    ├── reference_notes: tuple[Mapping, ...]    # {title, source, takeaway, avoid}
    ├── visual_direction: Optional[str]         # 시각 톤 메모
    ├── copy_tone: Optional[str]                # 카피 톤 메모
    └── competitive_examples: tuple[Mapping, ...] # {name, url, why}
```

## 4. RequestedAction

요청용 (tech-lead → 멤버):
- `analyze` — 검토하고 의견 제시
- `advise` — 추천안 제시
- `implement` — 코드/콘텐츠 생산
- `review` — 기존 산출물 리뷰
- `test` — 테스트/검증 케이스 생산
- `design` — 디자인 산출물 생산
- `investigate` — 디버깅/조사
- `handoff` — 다른 부서·역할에 인계
- `acknowledge` — 단순 수신 확인 (close_thread가 사용)
- `peer_review` — same-role council 안 reviewer seat 요청. owner / challenger
  draft 를 받아 `PeerReviewNote` 작성. council 의 외부 결정만 노출되도록
  내부 fan-out 표현 (자세한 council 모델은 [council-runtime](../../../../docs/engineering-role-council-runtime.md) §3 참조).
- `council_synthesis` — cross-role synthesis 요청. **모든 RoleCouncilResult
  의 `consensus_status` 가 `agreed` 또는 `agreed_with_conditions` 일 때만**
  발행 가능. 그 외에는 builder 가 거부.
- `tech_lead_signoff` — tech-lead 가 `ApprovalPacket` 의 technical signoff
  를 부여 / 조건부 / 거절 / escalate 한다. operator approval 과는 분리된다.

회신용 (멤버 → tech-lead):
- `in_progress` — 진행 중 상태 보고
- `completed` — 완료 (terminal)
- `needs_clarification` — 정보 부족
- `blocked` — 외부 의존으로 막힘
- `rejected` — 범위 외/거절 (terminal)

스키마 자체는 방향을 강제하지 않는다. 빌더 헬퍼(`new_request`/`reply_to`/`close_thread`)가 잘못된 짝을 거부한다.

## 5. Priority

- `P0` — 긴급/장애. SLA 즉시.
- `P1` — 높음. 같은 작업일 안에 회신.
- `P2` — 일반(기본).
- `P3` — 낮음. 가용 시간에.

## 6. 왕복 빌더

```python
from yule_engineering.agents import (
    new_request, reply_to, close_thread,
    RequestedAction, Priority, ContextRef, role_address,
)

req = new_request(
    from_role=role_address("engineering-agent", "tech-lead"),
    to_role=role_address("engineering-agent", "backend-engineer"),
    task_type="backend-feature",
    topic="users API에 email 인증 필드 추가",
    content="users 테이블에 email_verified column을 추가하고 ...",
    requested_action=RequestedAction.IMPLEMENT,
    priority=Priority.P1,
    run_id="ws-abc123",
    context_refs=[ContextRef(kind="issue", value="#142")],
    reference_links=["https://example.com/auth-flow"],
)

reply = reply_to(
    req,
    content="구현 완료. PR #143 열었음.",
    requested_action=RequestedAction.COMPLETED,
    context_refs=[ContextRef(kind="pr", value="#143")],
)

closure = close_thread(
    reply,
    summary="email 인증 추가, PR #143 머지 대기",
    references_used=[{"title": "Auth0", "rationale": "이중 토큰 패턴 차용"}],
)
```

규약
- `reply_to`는 from/to를 자동으로 swap하고 `parent_message_id`를 부모로 설정한다.
- `task_type`/`topic`/`thread_id`/`run_id`는 부모에서 상속해 체인을 그룹화한다.
- `close_thread`는 terminal 회신(`COMPLETED`/`REJECTED`)에서만 호출 가능하며, 결과를 `extra.round_trip_outcome`에 기록한다.

## 7. 통합 포인트 (다음 마일스톤)

- **dispatcher**: `DispatchPlan.assignments`를 입력 삼아 tech-lead가 각 멤버에게 `new_request`를 발행한다. role 기반 라우팅이 `role_address(agent_id, role)`로 일치한다.
- **workflow**: `WorkflowSession.session_id`를 `run_id`에 그대로 사용한다. 멤버 회신은 `WorkflowSession.progress_notes`로 누적, 최종 `completed` 회신은 `format_completion_message`의 입력이 된다.
- **Discord**: `thread_id`를 Discord thread snowflake로 매핑한다. `with_thread_id`로 후속 할당.
- **멤버 봇 IPC**: in-process queue (Phase 1) → socket (Phase 2). 메시지 자체는 동일.

## 8. 다른 부서 재사용

본 스키마는 부서 비종속이다. cto-agent / design-agent / marketing-agent가 도입될 때:
- `from_role` / `to_role` prefix만 자기 부서 id로 바꾼다.
- RequestedAction enum이 부족하면 본 정책에 추가한 뒤 코드 enum도 함께 갱신한다 (하나의 진실 소스).
- 레퍼런스 팩 필드(`visual_direction` / `copy_tone` / `competitive_examples`)는 마케팅·디자인 부서가 그대로 사용. 백엔드/QA는 비워둔다.

## 9. 변경 절차

1. RequestedAction 또는 Priority 추가/삭제는 본 문서를 먼저 수정.
2. 코드(`message.py`)와 정합 테스트 (`tests/test_agent_message.py::ActionPartitionTestCase`)를 함께 갱신.
3. 다른 부서가 이미 사용 중이면 마이그레이션 노트를 PR 본문에 첨부.
4. 영속화/운반 채널이 추가되면 직렬화 스키마(JSON shape)는 본 문서의 §3 트리를 그대로 따른다 (필드 이름 보존).

## 10. 후속 작업

- 영속화 (SQLite 테이블 또는 JSON cache namespace `engineering-agent-messages`).
- in-process queue 기반 멤버 봇 IPC 디스패처.
- Discord thread bridge (게이트웨이가 thread를 만들고 `thread_id`를 메시지에 주입).
- close_thread → workflow.complete() 자동 연결.
