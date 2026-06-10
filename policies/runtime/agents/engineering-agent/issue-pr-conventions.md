# engineering-agent — Issue / PR / Obsidian / Auto-merge 컨벤션

본 문서는 engineering-agent (tech-lead / backend / frontend / qa / devops / ai / product-designer) 가 GitHub issue / PR / Obsidian 노트 / 자동 머지 를 다룰 때 따르는 **단일 컨벤션 진실** 이다. agent 가 본 컨벤션을 따르지 않으면 PR review 단계에서 reject + mistake ledger 등록 대상.

## 참고 패턴 (2026-05-12 갱신)

- **gstack** (github.com/garrytan/gstack): preamble resolver / SKILL.md.tmpl / token ceiling 가드 / CLAUDE.md persistent answers 패턴 — F14 (#124) 에서 차용.
- **harness engineering**: 모든 agent / module 은 Protocol seam + fake injection + governance regression 으로 가드.
- **token harness**: §3 commit 분리 + preamble cache (agents/preamble) + memory unifier (F10) 로 컨텍스트 재사용.

## 1. Issue 컨벤션

### 1.1 Title 포맷

```
[Type] <Series-tag> <Concise description>
```

- **Type**: `Feature` / `BugFix` / `Refactor` / `Docs` / `Setting` / `Test` / `Deploy` (라벨 emoji 와 1:1 매칭)
- **Series-tag (선택)**: `F1` ~ `Fn` (foundation series), `A-M<phase>` (milestone), `Phase <n>` 등. 시리즈 식별이 필요한 경우만 사용.
- **Concise description**: 한국어 우선, 영문 자유. 길이 80자 이내. 끝에 마침표 없음.

예:
- `[Feature] F1 PasteGuard — outbound secret redaction preflight`
- `[BugFix] coding executor protected branch guard regression`
- `[Refactor] approval worker reply routing 단순화`

### 1.2 Label 요구사항 (필수)

모든 새 issue 는 다음 중 **최소 1개** type 라벨 + 필요 시 도메인 라벨 부착:

| Type 라벨 | 의미 |
| --- | --- |
| `✨ Feature` | 신기능 |
| `🐞 BugFix` | 버그 픽스 |
| `🔨 Refactor` | 리팩토링 |
| `📃 Docs` | 문서만 |
| `⚙ Setting` | 환경/설정 |
| `✅ Test` | 테스트만 |
| `🌏 Deploy` | 배포/CI |

| 도메인 라벨 | 의미 |
| --- | --- |
| `🤖 Agent-runtime` | engineering-agent 자체 |
| `🔒 Security` | secret / outbound / 권한 |
| `📬 API` | API / GitHub App / 외부 통신 |
| `🥰 Accessibility` | 접근성 |
| `💻 CrossBrowsing` | 호환성 |
| `🎨 Html&css` | 마크업/스타일 |
| `🙋‍♂️ Question` | 질문 / 토의 |

라벨이 누락된 PR / Issue 는 mistake ledger 의 BLOCK 등급 대상.

### 1.3 Body 구조 (권장)

```markdown
## 📌 관련 이슈
- parent #<n>
- 의존 #<n>
- governance: #<n>

## 🎯 목적
...

## 📐 범위
- 신규 모듈
- 통합 지점
- 비범위

## ✅ Acceptance Criteria
1. ...

## 🔒 Hard rails
- ...

## 🔗 의존성
- 선행 / 보조

## 🧰 Harness
- 테스트 경로 / 통합 시나리오

## 🤖 Agent
- role: engineering-agent/<role>
- autonomy: L<n>_<...>

## 진입 전 결정 필요 (선택)
- [ ] ...
```

## 2. PR 컨벤션

### 2.1 Title 포맷

```
<gitmoji> [Type] <Series-tag> <Description>
```

gitmoji 와 Type 라벨은 매칭:

| gitmoji | Type |
| --- | --- |
| ✨ | Feature |
| 🐛 | BugFix |
| ♻️ | Refactor |
| 📝 | Docs |
| 🔧 | Setting |
| ✅ | Test |
| 🚀 | Deploy |

예:
- `✨ [Feature] F1 PasteGuard — outbound secret redaction preflight`
- `🔧 [Setting] .env.example multi-bot per-role 키 패턴 문서화`

### 2.2 Body 구조

issue body 와 동일하되 추가:

```markdown
## 📌 관련 이슈
- close #<n> (또는 refs #<n>)

## 무엇을 변경했는지
| commit | 목적 |
| --- | --- |
| `<hash>` | ... |

## 🤖 Agent WorkOS Audit
- audit_id: ...
- branch: ... (from <base>)
- role: ...
- autonomy_level: ...
- mode: ...
```

### 2.3 Branch 명명

- Feature: `feature/<series-or-topic>-issue-<n>` (예: `feature/paste-guard-issue-88`)
- Chore: `chore/<topic>` (예: `chore/engineering-agent-conventions`)
- BugFix: `fix/<topic>-issue-<n>`
- Refactor: `refactor/<topic>-issue-<n>`

### 2.4 Draft / Ready 정책

- **Draft** 로 open: WIP / 회귀 미완 / 운영자 결정 대기 중
- **Ready for review** 마킹: 자체 회귀 OK + governance test 통과 + acceptance criteria 충족
- tech-lead 가 spawn 한 agent 의 PR 은 기본 Draft. 자체 회귀 PASS 시 mark-ready 가능.

## 3. Commit 컨벤션 (2026-05-12 갱신 — 분리 정책)

> **Source of truth**: [`policies/reference/COMMIT_CONVENTION.md`](../../../reference/COMMIT_CONVENTION.md).
> 본 §3 은 그 SSoT 의 운영 적용 가이드일 뿐이며, 형식 충돌 시 항상 SSoT 가 우선.

### 3.1 메시지 포맷 (SSoT 사본)

**plain text section header** 를 사용한다 (`##` markdown 헤더 금지 — SSoT 와 동일):

```
<gitmoji> #<n> <Type 한국어 한 줄 요약>

변경 이유
- ...

주요 변경 사항
- ...

비고
- 회귀 결과, 후속, blocker (없으면 `- 없음`)
```

**금지**: `Co-Authored-By` trailer, 🤖 생성 trailer, `Generated with Claude` 류 메시지.

**Initial commit 특수 규칙** — 새 repo 의 첫 non-merge 커밋 (bootstrap / scaffold flow 의 repo initialization commit 포함) 은 반드시 다음 제목을 사용한다:

```
:tada: initial commit
```

- 이 규칙은 일반 gitmoji whitelist 의 예외다.
- 첫 커밋 이후에는 `:tada:` 사용 금지 (다음 커밋부터 일반 whitelist 적용).
- 첫 커밋 판별이 ambiguous 하면 enforcement layer 가 `initial_commit_detection_ambiguous` blocker 로 surface 한다.

### 3.0 적용 범위 (cross-repo)

본 컨벤션은 `yule-studio-agent` 내부에 한정되지 않는다. **봇이 GitHub write 를 수행하는 모든 target repo** (예: `yule-studio/naver-search-clone`, 향후 engineering runtime 이 coding_execute / github_work_order / draft PR 을 만드는 모든 repo) 에 동일하게 적용된다. repo-local stricter policy 가 있으면 그것이 우선하되, 기본은 본 SSoT 를 상속한다.

코드 SSoT — [`apps/engineering-agent/src/yule_engineering/agents/governance/repo_write_policy.py`](../../../../apps/engineering-agent/src/yule_engineering/agents/governance/repo_write_policy.py) 가 commit / issue title / PR title / issue anchor 4-종 hard guard 를 한 자리에 모은다. live path (GithubAppCommitter / GithubAppDraftPRCreator / GithubWriter.create_issue 등) 가 그 validator 를 호출하고 실패 시 raise.

### 3.2 커밋 분리 정책 (필수)

**PR 당 최소 3 commit** 으로 분리. 하나의 거대 커밋 금지. 분리 기준:

| 분리 단위 | 예시 |
| --- | --- |
| **data model / dataclass** 한 묶음 | `Plugin manifest dataclass + 검증` |
| **로직 / runtime** 한 묶음 | `PluginRegistry + HookChain invoke` |
| **integration / wiring** 한 묶음 | `runtime/services.py 등록 + CLI 진입점` |
| **테스트 + governance** 한 묶음 | `test_plugin_registry.py + test_extension_governance.py` |
| **docs / convention / policy** 한 묶음 | `extension-architecture.md + .env.example` |

**PR merge 방식**: `gh pr merge --merge` 또는 `--rebase` (squash 금지 — 커밋 history 보존).

`gh pr merge --squash` 는 docs-only / 1-commit chore PR 에만 허용.

### 3.3 mistake_ledger signature 추가

| signature | level | reason |
| --- | --- | --- |
| `commit.single-mega-commit` | WARNING | feature PR 인데 1 commit 만 — 분리 필요 |
| `commit.squash-merge-multi-commit-pr` | WARNING | 3+ commit PR 을 squash 머지 — history 손실 |

## 4. Obsidian 노트 컨벤션

### 4.1 파일명 (2026-05-11 갱신 — 날짜 prefix 제거)

```
<kind>-<topic-slug>[-issue-<n>].md
```

- `kind`: `task-log` / `decision` / `research` / `knowledge` / `meeting` / `work-report`
- `topic-slug`: kebab-case 5~8 단어, 핵심 키워드 위주
- `issue-<n>`: 연결 issue 가 있으면 suffix, 없으면 생략

예:
- `task-log-tech-lead-runtime-loop-issue-73.md`
- `decision-engineering-agent-authoring-policy-issue-69.md`
- `research-engineering-knowledge-surface-strengthening.md`
- `knowledge-smoke-test-devops-engineer-learning.md`

기존 `2026-MM-DD_` prefix 노트는 F8 마이그레이션 단위로 일괄 rename.

### 4.2 Frontmatter

```yaml
---
title: "<사람이 읽는 한국어 제목>"
kind: <kind>
issue: <n>
parent_issue: <n>            # 선택
session_id: <id>             # 선택
project: yule-studio-agent
created_at: 2026-05-11T00:00:00+09:00
status: in-progress | done | blocked
tags: [...]
---
```

`created_at` 이 ISO8601 KST 시각의 단일 소스 — 날짜 정보는 frontmatter 에만 둔다.

### 4.3 Auto vault push (F8 scope)

작업 완료 시점 (PR merge / status=done) 에 봇이 다음을 자동 수행:

1. `notes/vault-mirror/` 의 신규/변경 노트를 vault repo (`yule-agent-vault` 또는 동등) 에 commit
2. push (보호 브랜치 가드 적용)
3. 실패 시 mistake ledger 에 기록 + operator surface 통지

상세 wiring 은 F8 (#<TBD>) PR.

## 5. Auto-merge 정책 (F7 scope)

### 5.1 자율 머지 가능 조건 (모두 만족)

1. CI / 전체 unittest 회귀 OK (skip 외 fail 0)
2. governance regression test 통과
3. `mergeable=MERGEABLE`, `mergeStateStatus=CLEAN`
4. PR risk class = **LOW** (아래 5.2 표 참조)
5. 보호 브랜치 직접 push 없음
6. force push / `--no-verify` 사용 없음
7. PasteGuard 통과 (outbound payload 에 secret 없음)
8. acceptance criteria 자체 검증 보고 포함

### 5.2 Risk class 매트릭스

| Risk | 예시 | 머지 정책 |
| --- | --- | --- |
| LOW | docs only / .env.example / 테스트 추가만 / 단일 read-only 모듈 추가 | tech-lead agent 자율 머지 가능 |
| MEDIUM | 신규 worker / decision router 변경 / SQLite 스키마 추가 | tech-lead 검토 + 자율 머지 가능 |
| HIGH | 외부 LLM live 호출 / 외부 fetch / secret 처리 변경 / 보호 브랜치 정책 변경 | **운영자 명시 승인 필수** — 자동 머지 금지 |
| CRITICAL | live deploy / production secret rotation / 보안 가드 비활성화 | **운영자 + 보안 리뷰어 양측 승인 필수** |

### 5.3 운영자 사이클 인가

`feedback_auto_merge_authorization.md` 메모리에 명시된 사이클 (예: #81 F1~F8 그룹) 동안만 자율 머지 활성. 그 외 작업은 운영자 명시 승인 필요.

## 6. 라벨 카탈로그 갱신

신규 type / 도메인 라벨 추가 시:

1. 본 문서 §1.2 표에 추가
2. `gh label create` 로 색상 + description 설정
3. label naming: `<emoji> <Word>` (영문 또는 한글 한 단어)
4. agent prompt 에 라벨 카탈로그를 다시 inject (mistake ledger 가 빈 라벨 PR 을 BLOCK 으로 잡도록)

## 7. Mistake ledger 연동

본 컨벤션 위반은 mistake_ledger 의 `signature` 로 기록:

| signature | level | reason |
| --- | --- | --- |
| `issue.title.missing-type-bracket` | WARNING | `[Type]` 누락 |
| `issue.label.missing-type` | WARNING | type 라벨 누락 |
| `pr.title.missing-gitmoji` | ADVISORY | gitmoji 누락 |
| `commit.coauthored-by` | BLOCK | Co-Authored-By trailer 포함 |
| `commit.korean-3section-missing` | WARNING | 변경 이유/주요/비고 섹션 누락 |
| `obsidian.filename.date-prefix` | WARNING | 날짜 prefix 사용 |
| `automerge.risk-class.high-without-approval` | BLOCK | HIGH risk PR 을 운영자 승인 없이 머지 시도 |

preflight 가 위 signature 매칭 시 작업 시작 전 advisory / warning / block 반환.
