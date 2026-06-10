# Engineering Agent Runner Policy (v0)

이 문서는 engineering-agent 부서가 LLM 백엔드를 호출하는 공통 런너 추상화의 정책 기준선이다. 본 정책은 **부서 단위 participants 풀**(claude / codex / gemini / ollama / github-copilot)에 적용되며, 멤버(tech-lead / backend-engineer / frontend-engineer / product-designer / qa-engineer)는 이 풀을 공유한다.

## Scope
- in scope: 런너 인터페이스 계약, 가용성 검사, dry-run, 확장 포인트(reference / ranking / performance) 사용 규약.
- out of scope: 멤버 디스패치 로직, Discord 통합, 자동 reference 수집기 구현체, 멀티봇 토큰 운용.

## Contract
런너는 `apps/engineering-agent/src/yule_engineering/agents/runners/base.py`의 다음 계약을 구현한다.

- `AgentRunner.is_available()` — 백엔드 호출 가능 여부를 빠르게 반환한다. doctor 명령과 레지스트리 빌드 시점에서 호출된다.
- `AgentRunner.submit(request)` — 실제 백엔드 호출. 실패는 예외 대신 `AgentResponse.status`로 표현한다 (`OK` / `ERROR` / `UNAVAILABLE`).
- `AgentRunner.dry_run(request)` — 백엔드를 건드리지 않고 결정적 응답을 반환한다. 테스트와 운영자 dry-run 경로에서 사용한다.
- `AgentRunner.run(request, dry_run=False)` — 위 메서드를 감싸 reference 자동 수집과 performance 기록을 적용하는 공식 진입점.

`AgentRequest`는 prompt, role, task_id, repository, write_allowed, references, context, metadata만 가진다. Discord/플래닝/GitHub 스키마를 그대로 흘리지 않는다.

## Capabilities
런너는 자기 능력을 `RunnerCapability`로 선언한다. 디스패처는 이 값으로 1차 필터링한 뒤 `role-weights-v0.md`의 가중치를 적용한다.

| 백엔드 | capabilities |
|---|---|
| claude | execute, advise, review, patch_propose |
| codex | advise, review, patch_propose |
| gemini | advise, long_context |
| ollama | advise, local_private |
| github-copilot | github_native, patch_propose |

`execute`(쓰기 권한)는 풀 전체에서 한 번에 한 런너만 갖는다. `manifest.json`의 `write_policy.max_write_executors_per_run=1` 규칙과 일치해야 한다.

## Extension Points
런너 본문은 `RunnerHooks`를 통해 선택적으로 다음을 받는다.

- `ReferenceCollector` — request.references가 비어 있을 때 `run()`이 1회 호출한다. UI/UX/마케팅 작업의 최소 3건(이상 5건) reference 정책을 자동화하는 슬롯.
- `RankingSignal` — 디스패처가 런너 선택 단계에서 사용한다. 런너 본문에서는 호출하지 않는다.
- `PerformanceTracker` — 모든 `run()` 결과를 받는다. 실패도 기록해 성공률/지연/비용 대시보드의 입력으로 사용한다.

세 훅 모두 런너 본문은 알지 못해도 동작해야 한다. 미주입 시 기본 동작은 reference 보강 없음, 성능 기록 없음, 랭킹 영향 없음이다.

## Dry-run 전략
실제 백엔드 호출 전에 다음 경로에서 dry-run을 사용한다.

- 신규 wrapper 추가 시: `runner.run(request, dry_run=True)`로 hook 호출 순서, metrics 누적, 응답 스키마를 검증한다.
- 운영자 점검 시: `yule doctor`(예정)에서 각 런너의 `is_available()`만 실행해 가벼운 헬스체크를 수행한다.
- 디스패처 회귀 검증 시: 풀 전체를 `factories=` 인자로 fake runner로 교체해 분배 결정만 검증한다.

dry-run 응답은 `RunnerStatus.DRY_RUN`을 반환하며 backend가 컨택트되지 않았음을 detail에 명시한다.

## 최소 테스트 기준
- 레지스트리 로딩이 `engineering-agent/manifest.json`의 5종 id를 모두 인식한다.
- 매핑 누락 id는 warnings로만 노출되고 풀 빌드를 막지 않는다.
- dry-run 경로에서 `submit`이 호출되지 않는다.
- `PerformanceTracker.record`가 매 실행마다 한 번 호출된다.
- request.references가 비어 있으면 `ReferenceCollector.collect`가 호출되고, 채워져 있으면 호출되지 않는다.

테스트는 `tests/test_agent_runners.py`에서 유지한다.

## 후속 작업
- 각 wrapper의 실제 백엔드 호출 본문(claude/codex/gemini CLI 인자, ollama generate API, gh copilot extension 호출).
- 디스패처: role × capability × ranking signal × write_policy를 결합해 단일 executor와 다수 advisor를 결정한다.
- ReferenceCollector 구현체와 reference-pack.md의 매핑.
- PerformanceTracker SQLite 스키마와 대시보드.
