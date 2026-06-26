# Tool Evaluation — CLI Agent 평가 기준

> ForgeKit 이 CLI agent(Claude Code / Codex CLI / Gemini CLI / Aider / OpenCode 등)를
> executor 로 채택할지 판단하는 기준. 실제 평가는 `labs/cli-agents/<agent>/` 에서 수행하고
> 결론은 `nexus/evaluations/` 에 축적한다. 도구 선택은 [hephaistos.md](hephaistos.md) 의
> tool-selector 가 이 데이터를 참조한다.

## 1. 평가 축

| 축 | 질문 |
| --- | --- |
| **non-interactive 실행** | 사람 입력 없이 1-shot/배치로 돌릴 수 있는가? |
| **workspace path 지정** | 작업 디렉터리(repo/worktree)를 명시 지정할 수 있는가? |
| **prompt file 지원** | 프롬프트를 파일/stdin 으로 줄 수 있는가? |
| **stdout/stderr/exit code** | 출력·에러·종료코드를 안정적으로 파싱할 수 있는가? |
| **diff 추출** | 변경 결과를 diff/patch 로 뽑을 수 있는가? |
| **shell 실행** | 도구가 셸 명령을 실행하는가(권한 경계 필요)? |
| **permission/sandbox/approval** | 승인 모델·샌드박스·권한 게이트가 있는가? |
| **fit_for_forgekit** | 위 종합 → ForgeKit executor 로서 적합도 |

## 2. 적합도 판정

- **adopt-now** — non-interactive + workspace 지정 + 안정적 exit/diff. 어댑터 설계 진행.
- **collect-first** — 일부 축 미충족/불확실. labs 에서 근거 더 모은 뒤 재평가.
- **hold** — sandbox/approval 부재로 안전 경계 위험, 또는 ForgeKit 흐름과 불일치.

## 3. 원칙

- **평가 없는 도구 선택 금지** — 모든 채택은 `labs` 평가 + `nexus/evaluations` 근거를 거친다.
- **adopted ≠ equipped** — 적합 판정이 곧 설치/활성화가 아니다(별도 단계).
- 평가는 재현 가능한 명령(`labs/cli-agents/_template/commands.md`)과 함께 기록한다.
