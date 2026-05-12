# Engineering Agent

Engineering Agent 는 `#업무-접수` 채널에서 자유 대화로 작업을 정리하고, 확정되면 workflow session 과 작업 thread 를 만든다. CLI 로도 같은 workflow 를 직접 조작할 수 있다.

운영 정책의 본문은 `policies/runtime/agents/engineering-agent/lifecycle-mvp.md` 에 있다. 이 문서는 사용자 진입용 요약이다.

## CLI

```bash
yule engineer intake --prompt "Obsidian 기반 에이전트 지식 저장 구조 설계" --write
yule engineer approve --session <session_id>
yule engineer progress --session <session_id> --note "운영-리서치에 1차 자료 정리"
yule engineer complete --session <session_id> --summary "설계안 정리 완료"
yule engineer reject --session <session_id> --reason "요구사항 재정의 필요"
yule engineer show --session <session_id>
```

- `intake` 는 dispatcher 계획, 참여 후보, 실행 후보, reference 제안을 포함한 접수 메시지를 생성.
- `--write` 를 붙인 세션은 승인 전까지 쓰기 작업이 차단.
- `complete --references-used refs.json` 을 쓰면 완료 보고에 실제 반영한 reference 를 함께 남길 수 있다.

## Discord 라우팅

- Discord 자유 대화에서 `새로 등록하지 말고 기존 스레드에서 이어가` 처럼 말하면 열린 thread 를 찾아 이어 붙이고, 새 세션은 만들지 않는다.
- gateway 는 매 요청마다 `decide_routing()` 으로 현재 열려 있는 workflow session 과 prompt 를 비교해 4 가지 action 중 하나로 라우팅한다 — `join_existing_work`, `create_new_work`, `ask_for_clarification`, `append_context_only`. `기존 맥락 참고` 같은 표현이 단독으로 쓰이면 자동 join 을 강제하지 않고 similarity 로만 판정.
- 사용자가 명시적으로 `이어가/새로 등록하지 말고` 라고 적었지만 매칭되는 open thread 가 없으면 새 세션을 만들지 않고 어느 작업에 합류할지 다시 묻는다.
- gateway 가 작업 thread 를 만들면 thread id 가 `WorkflowSession.thread_id` 로 영속화. 작업 thread 는 진행 메모와 결과 회신 공간이며, research forum 의 멤버 봇 발화는 별도의 open-call 프로토콜로 시작.

## Research 자동 수집

- `auto_collect_or_request_more_input()` 이 `score_research_sufficiency()` 로 역할별 coverage(tech-lead·ai-engineer·backend·frontend·design·qa·devops) 를 평가하고, 부족하면 `ENGINEERING_RESEARCH_MAX_PROVIDER_CALLS` 예산 안에서 부족한 역할 위주로 추가 query.
- 같은 URL/title 이면 dedupe, 새 자료가 늘지 않는 round 가 연속 4 번이면 안전 종료.
- 결과 `CollectionOutcome.sufficiency` 에는 부족한 역할 / source_type 이 그대로 남아 운영자가 어디가 비어 있는지 확인 가능.
- reference budget tier (small/medium/large/deep_research) 와 multi-provider 운영은 [research-budget.md](research-budget.md) 참고.

## Coding Agent Authorization MVP

Tech Lead 가 사용자 업무 요청을 받아 *누가* 코드 수정 권한을 가져야 하는지 결정하고, 사용자가 명시적으로 승인한 뒤에만 executor role 에게 안전한 prompt 가 전달되도록 만든 흐름이다. 실제 파일 수정 / 자동 merge / 자동 push / 자동 deploy 는 이 MVP 범위가 아니며, executor prompt 생성까지만 진행한다.

### Discord 흐름

1. 업무 접수 후 같은 thread 에서 `코딩 권한 제안` (또는 `수정 권한 제안` / `구현 권한 제안`) 이라고 답하면 Tech Lead 가 분석한 권한 미리보기가 표시된다.
2. 미리보기에는 executor role / reviewer / participants / write scope / forbidden scope / safety rules / 추천 사유가 포함된다.
3. 동의하면 `수정 승인` / `이대로 구현 진행` / `구현 시작` 중 하나로 답한다.
4. 승인이 도착하면 `coding_job=ready` 상태로 `session.extra["coding_job"]` 에 저장되고 executor 가 실행할 prompt 가 함께 만들어진다.

### Research-only 모드 (Phase 2 stab)

prompt 에 `코드 수정 없이` / `자료 수집이 목표` / `리서치만` / `조사해줘` / `정리까지만` 같은 신호가 있으면 게이트웨이가 `**[engineering-agent] 조사 단계 — 코드 수정은 하지 않습니다**` 헤더 + "조사 중심 역할:" 만 노출. coding executor 표시 없음. 사용자가 별도로 `수정 권한 제안` 을 요청해야 implementation 으로 전환된다. 자세한 정책: `policies/runtime/agents/engineering-agent/live-regression.md` §1.

### Executor role 추천 룰 (deterministic)

| 작업 키워드 예시 | 추천 executor |
|---|---|
| Spring Security / 인증 / API / DB / schema / transaction | `backend-engineer` |
| React / UI / CSS / 컴포넌트 / 화면 / 접근성 | `frontend-engineer` |
| RAG / LLM / prompt / memory / agent runtime / evaluation | `ai-engineer` |
| Docker / CI / GitHub Actions / 배포 / supervisor / monitoring | `devops-engineer` |
| 회귀 / acceptance / smoke test / fixture | `qa-engineer` |
| UX copy / 운영 UX / 디자인 토큰 / 사용자 흐름 문서 | `product-designer` |
| 도메인 키워드 매칭 실패 / 모호 / 빈 요청 | `tech-lead` (clarification fallback) |

`agents/engineering-agent/<role>/manifest.json` 의 `default_executor_priority.high|medium|low` 키워드 뱅크가 점수 합산(high=+3, medium=+1.5, low=−1) 으로 단일 executor 를 결정. 동점 시 `backend → ai → devops → frontend → qa → product-designer` 순서로 deterministic.

### 상태 진단

상태 질문 응답과 `yule supervisor run --once` 출력에 다음 라인이 추가된다.

```
- coding_job: pending-approval (executor=`frontend-engineer`) — 사용자 `수정 승인` 대기
- coding_job: ready (executor=`backend-engineer`, write_scope=src/<service>/api/**, src/<service>/auth/** 외)
```

### MVP 범위 밖

- 실제 파일 수정 실행 / 자동 merge / 자동 push / 자동 deploy
- 다중 role 동시 write
- secret 접근 / git reset 류 destructive 명령
- GitHub PR 자동 생성 / merge 자동화
- 완전 자율 장기 실행

## Obsidian 로컬 동기화

ResearchPack 을 개인 Obsidian vault 에 Markdown 파일로 저장하려면 `OBSIDIAN_VAULT_PATH` 에 vault 절대경로를 설정한다. **실제 절대경로는 git 에 커밋되는 `.env.example` 이 아니라 로컬 전용 `.env.local` 에 둔다** — `.gitignore` 가 `.env*` 는 제외하고 `.env.example` 만 화이트리스트로 추적하기 때문.

```bash
# .env.local 예시
OBSIDIAN_VAULT_PATH=/Users/<MY_USER>/local-dev/yule-agent-vault/obsidian-vault
# (선택) 기본 export 레이아웃과 기본 project — 둘 다 비워 두면 아래 기본값이 적용
OBSIDIAN_EXPORT_LAYOUT=yule-agent-vault
OBSIDIAN_DEFAULT_PROJECT=yule-studio-agent
```

```bash
yule obsidian sync --session <session_id>                    # 실제 쓰기 (overwrite 금지가 기본)
yule obsidian sync --session <session_id> --dry-run          # 경로/내용만 검증
yule obsidian sync --session <session_id> --overwrite
yule obsidian sync --session <session_id> --kind reference
yule obsidian sync --session <session_id> --project other-project
yule obsidian sync --session <session_id> --layout legacy-agent
```

기본 export 경로는 yule-agent-vault 정책을 따른다 — `10-projects/<project>/<kind>/YYYY-MM-DD_<kind>-<slug>.md`. project 결정 우선순위: **CLI `--project` → `session.extra["project"]` / `["project_name"]` → `OBSIDIAN_DEFAULT_PROJECT` env → 기본값 `yule-studio-agent`**. 알 수 없는 kind 는 `00-inbox/unsorted/` 로 라우팅.

| kind | yule-agent-vault 경로 | 비고 |
| --- | --- | --- |
| `research` (기본) | `10-projects/<project>/research/` | ResearchPack 1차 자료 노트 |
| `decision` | `10-projects/<project>/decisions/` | TechLeadSynthesis 가 있을 때 자동 선택 |
| `reference` | `10-projects/<project>/references/` | 디자인 / UX 레퍼런스 |
| `task-log` | `10-projects/<project>/task-logs/` | 작업 진행 로그 |
| `meeting` / `meeting-notes` | `10-projects/<project>/meeting-notes/` | 회의록 |
| 알 수 없음 | `00-inbox/unsorted/` | 미상 / 애매한 kind 는 inbox 로 |

기존 vault 가 아직 `Agents/Engineering/...` 트리에 머물러 있다면 `OBSIDIAN_EXPORT_LAYOUT=legacy-agent` (또는 `--layout legacy-agent`) 로 한시적으로 옛 경로 사용. legacy 모드에서는 frontmatter 에 `project:` 키가 들어가지 않아 byte 출력이 마이그레이션 이전과 그대로 유지.

같은 날짜·같은 slug 로 sync 가 반복되면 같은 폴더 안에서 `..._2.md`, `..._3.md` 식으로 자동 suffix. `--overwrite` 를 명시하면 suffix 없이 원래 파일을 그대로 교체. 자세한 contract 와 안전 정책은 `policies/runtime/agents/engineering-agent/obsidian-memory.md` 참고. `yule doctor` 는 `obsidian vault` 체크를 자동 수행.

긴 원문 prompt 가 들어와도 title / filename 은 30~50자 수준의 짧은 요약으로 자동 정리. 원문 전체는 frontmatter `original_prompt` 키와 본문 `## 원문 요청` 섹션에 보존, basename 은 100 자 이하로 강제.

vault 를 git 으로 관리한다면 `--git-commit` 옵션으로 sync 직후 자동 commit. **대상은 코드 저장소가 아니라 Obsidian vault repo**. 기본 동작은 **opt-in**, **이번 sync 가 만든 그 note 파일 하나만 stage/commit**, **push 는 절대 하지 않음**. vault repo 에 이미 staged 변경이 있거나 vault 가 git repo 가 아니면 fail-loud.

```bash
yule obsidian sync --session <session_id> --git-commit
yule obsidian sync --session <session_id> --git-commit --git-message "obsidian sync: hero 회의"
yule obsidian sync --session <session_id> --git-commit --dry-run
```

게이트웨이가 deliberation 을 끝내면 `TechLeadSynthesis`(합의안 / 해야 할 일 / 더 조사할 것 / 사용자 결정 필요 / 승인 여부) 도 session 에 함께 저장. sync 는 이 값을 복원해 기본 레이아웃 기준 `10-projects/<project>/decisions/` 아래에 5 개 섹션을 갖춘 결정 노트로 떨어뜨린다. synthesis 키가 없는 오래된 session 은 안전하게 fallback 해 `research/` 폴더의 자료 노트로만 떨어진다.

## 라이브 회귀 시나리오

회귀 검증 절차 (k8s research-only / RAG memory / status diagnostic / Obsidian write 4 종): `policies/runtime/agents/engineering-agent/live-regression.md`.
