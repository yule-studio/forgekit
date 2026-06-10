# Engineering Agent Discord Conversation Layer (v0)

이 문서는 engineering-agent가 Discord `#업무-접수` 채널에서 자연어를 **유도리 있게 받아들이는** 대화 레이어의 정책 기준선이다. 코드 진실 소스는 `apps/engineering-agent/src/yule_engineering/discord/engineering_conversation.py`. 본 단계는 순수 함수와 응답 envelope만 정의하며 bot.py 배선은 후속 마일스톤에서 진행한다.

## 1. planning conversation과의 차이

| 항목 | planning conversation | engineering conversation |
|---|---|---|
| 코드 위치 | `discord/conversation.py` | `discord/engineering_conversation.py` |
| 목적 | snapshot 기반의 결정적 답변 (브리핑/우선순위/체크포인트) | 자유 발화로 들어온 작업을 **정리·확정**해 다음 단계 (workflow.intake)로 잇기 |
| 데이터 의존 | `DailyPlanSnapshot` 필수 | 없음 — prompt 텍스트만 입력 |
| LLM 호출 | Ollama로 자연어 답변 보강 | 없음 (분류·되묻기·제안만) |
| 부수효과 | SQLite cache (pending confirmation), 체크포인트 응답 기록 | 없음 (read-only) |
| 응답 시점 | 한 번 답하고 종결 | "이대로 진행" 같은 follow-up까지 따라가는 다단계 |
| 외부 의존 | snapshot loader, ollama client | `dispatcher.TaskType` enum만 |

두 모듈은 **서로 호출하지 않는다.** 같은 채널(`#업무-접수`)에서 충돌하지 않도록 bot 라우팅이 채널·스코프 기준으로 둘을 분리한다 (이번 마일스톤에서는 라우팅 코드 변경 없음).

## 2. 의도 분류 (5종)

탐지 우선순위 — 위에서부터 검사해 첫 매치를 채택한다.

| 의도 ID | 트리거 (우선) | 대응 |
|---|---|---|
| `confirm_intake` | "이대로 진행", "그럼 이걸로", "확정", "ok"/"오케이" 등 | `ready_to_intake=True` 반환. 이전 turn의 prompt를 `intake_prompt`로 보존. |
| `general_engineering_help` | "engineering-agent", "도움말", "어떻게 써" 등 | 자기소개 + 사용 가이드. |
| `needs_clarification` | 매우 짧음(≤3자), 단어 1개, 또는 "도와줘" 류 모호 표현 | 어느 화면/API/흐름인지 되묻는다. `needs_clarification=True`. |
| `split_task_proposal` | "그리고/또/and"로 연결된 2개 이상의 substantial(≥2 words) 갈래 | 갈래를 1, 2, ... 로 나눠 제안. `proposed_splits` 채워 반환. |
| `task_intake_candidate` (default) | 위 어디에도 안 잡히는 일반 요청 | "이대로 진행해도 될까요?" 형태로 정리해 묻는다. |

분류는 키워드 기반으로 시작하며, 후속 마일스톤에서 LLM 분류기로 **점진적으로 대체**할 수 있도록 모든 진입점은 `EngineeringConversationResponse` envelope만 반환한다.

## 3. 응답 envelope

```python
@dataclass(frozen=True)
class EngineeringConversationResponse:
    content: str                          # Discord에 그대로 보낼 텍스트
    intent_id: str                        # 위 5종 중 하나
    ready_to_intake: bool = False         # bot.py가 workflow.intake 호출해야 하는지
    needs_clarification: bool = False     # 사용자에게 되묻는 중인지
    proposed_splits: tuple[str, ...] = () # 갈래 제안 시 채워짐
    suggested_task_type: Optional[str]    # dispatcher TaskType.value 힌트
    write_likely: bool = False            # 추후 intake에서 write_requested로 전달할지 힌트
    intake_prompt: Optional[str]          # confirm 시 직전 turn의 prompt
    mention_user_id: Optional[int]
```

규약
- 모든 값은 **한 turn**의 결과만 담는다. 이전 turn은 호출자(bot.py)가 채널 단위 상태로 저장해 다음 호출 때 `last_proposed_prompt`로 넘긴다.
- `confirm_intake` 응답을 받은 호출자는 envelope의 `intake_prompt` + `suggested_task_type` + `write_likely`를 그대로 `workflow.intake`에 전달한다.
- `proposed_splits`가 있으면 호출자는 사용자에게 선택을 받거나 `이대로 진행`을 기다린 뒤 한 세션으로 묶을 수 있다.

## 4. 확정 표현 화이트리스트

### 4.1 standalone 토큰 (전체 메시지가 이 토큰만)
`ok`, `okay`, `오케이`, `오케`, `오키`, `yes`, `yep`, `go`, `고`, `ㄱㄱ`, `확정`, `진행`, `등록`

### 4.2 포함 표현
`이대로 진행`, `이대로 등록`, `이걸로 등록`, `이걸로 진행`, `그럼 이걸로`, `그럼 등록`, `그럼 진행`, `좋아 진행`, `좋습니다 진행`, `오케이 진행`, `ok 진행`, `그렇게 등록`, `그렇게 진행`, `진행해줘`, `진행해 주세요`, `등록해줘`, `등록해 주세요`, `yes 등록`, `yes 진행`, `go 진행`, `확정`, `확정해`

확정 표현은 항상 **다른 의도보다 먼저** 검사한다. 그렇지 않으면 사용자가 follow-up으로 보낸 짧은 확정이 새 intake로 오인될 수 있다.

## 5. 갈래 분할 규칙

`split_task_branches`는 다음 패턴을 분리자로 사용한다.
- `그리고` / `, 그리고`
- `또`
- ` and ` (영문, 양옆 공백 필수)

분리 후 각 fragment가 **2 words 이상**일 때만 분할로 인정한다. "음 그리고 좋아" 같은 잡담은 분리되지 않는다. 한 갈래만 남으면 빈 튜플을 반환해 호출자가 원래 메시지로 fall back한다.

## 6. write_likely 휴리스틱

원칙: 검토/분석 신호가 있으면 `False`(검토 신호가 우선), 없고 명시 쓰기 신호가 있으면 `True`.

쓰기 신호: `구현`, `만들`, `추가`, `수정`, `고쳐`/`고치`, `리팩`/`refactor`, `implement`, `build`, `create`, `fix`, `패치`/`patch`, `PR`, `pull request`, `draft`, `짜야`, `짜줘`, `짜자`, `작성`, `쓸게`, `써줘`.

검토 신호 (write_likely 차단): `어떻게 생각`, `분석`, `리뷰`/`review`, `검토`, `조사`.

`write_likely`는 어디까지나 **첫 인상 힌트**다. 실제 write 게이트는 dispatcher와 workflow가 user_approved 플래그로 강제하므로, 이 휴리스틱이 어긋나도 안전 사고로 이어지지 않는다.

## 7. task_type 힌트 매핑

`_TASK_TYPE_KEYWORDS`는 dispatcher의 분류 우선순위와 동일한 순서를 따른다 (구체 → 일반):

`visual-polish` → `onboarding-flow` → `email-campaign` → `landing-page` → `qa-test` → `platform-infra` → `frontend-feature` → `backend-feature`

매치 없으면 `None`. 호출자는 `None`일 때 dispatcher의 자체 분류기에 prompt를 그대로 넘긴다.

## 8. bot.py 배선 가이드 (후속 마일스톤)

이 모듈을 실제 채널에 붙일 때 호출자가 지켜야 할 흐름:

1. `#업무-접수` 메시지를 받으면 `build_engineering_conversation_response(text, last_proposed_prompt=stash_get(channel))`을 호출.
2. envelope.content를 채널에 그대로 게시.
3. envelope의 신호를 다음과 같이 처리:
   - `ready_to_intake=True` → `WorkflowOrchestrator.intake(prompt=envelope.intake_prompt, task_type=envelope.suggested_task_type, write_requested=envelope.write_likely, channel_id=..., user_id=...)` 호출.
   - `needs_clarification=True` → 다음 turn까지 대기. stash 갱신 안 함.
   - `proposed_splits` non-empty → 사용자가 선택할 때까지 대기. stash에 prompt 보관.
   - `task_intake_candidate` 기본 → stash에 prompt 보관 후 다음 turn에서 confirm 받기.
4. stash는 채널·사용자 단위로 30분 TTL이 적당. SQLite cache `engineering-conversation-stash` namespace 추천 (다른 Claude 작업 영역).

이 모듈 자체는 stash를 관리하지 않는다 — pure function 원칙 유지.

## 9. 변경 절차

- 의도 5종을 늘리거나 줄이려면 **본 문서**를 먼저 갱신하고 코드 enum/상수와 정합 테스트(`tests/test_engineering_conversation.py`)도 함께 손본다.
- 확정 표현 화이트리스트 추가는 보수적으로. 일반 동사("좋다", "응", "어")는 제외한다 — 단독으로 쓰이면 의도 모호하므로 needs_clarification 쪽이 더 안전하다.
- task_type 힌트 매핑은 dispatcher의 `_KEYWORD_RULES`와 어긋나면 안 된다. dispatcher.md §2의 우선순위와 동일하게 유지한다.

## 10. 후속 작업

- bot.py에서 채널/스코프 라우팅으로 planning conversation과 분리.
- stash 저장 (SQLite cache namespace).
- LLM 보강 분류기 (Ollama)로 키워드 fallback 보강.
- 멤버 봇 자유 대화 — 멤버별 페르소나(예: backend-engineer 톤)는 별도 모듈에서.
