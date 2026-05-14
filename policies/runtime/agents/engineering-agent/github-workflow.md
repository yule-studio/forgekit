# GitHub Workflow — engineering-agent 부서 공통 (Issue #69)

> **소유:** 본 정책은 `engineering-agent` 부서 전체에 적용된다 (7 역할 + gateway).
> **목적:** issue / PR / label / progress comment / 커밋 분할 / GitHub Apps 사용 / push 의 부서 공통 규칙을 단일 문서로 박는다.
> **출처:** Issue #69 D-69-10 ~ D-69-14. 결정 노트: `notes/vault-mirror/.../decisions/2026-05-08_issue-69-decision-engineering-agent-authoring-policy.md`.

본 정책은 [`obsidian-governance.md`](obsidian-governance.md) (Obsidian) 와 [`write-ownership.md`](write-ownership.md) (3-mode author) 와 함께 [`governance.md`](governance.md) umbrella 가 묶는 3 정책 중 **GitHub 표면** 을 책임진다.

## 1. GitHub Issue body 규칙

issue body 는 **`.github/ISSUE_TEMPLATE/-feature--issue-template.md`** 4 섹션 엄격 준수.

```markdown
## 어떤 기능인가요?
> ...

## 작업 상세 내용
- [ ] ...

## 참고할만한 자료(선택)
- ...
```

추가 규칙:

- sub-issue 면 `## 어떤 기능인가요?` 섹션 첫 단락에 **`Parent: #<n>`** 명시.
- 입력 issue 가 있으면 `## 어떤 기능인가요?` 또는 `## 참고할만한 자료(선택)` 섹션에 입력 issue 번호 명시 (예: "완료된 #25 / #48 / #59").
- author = 결정 트리 ([`write-ownership.md`](write-ownership.md) §5) — 부서 intake 면 gateway, 다역할 통합 결정이면 tech-lead, 단일 role 작업이면 role-owned.

## 2. GitHub PR body 규칙

PR body 는 **`.github/PULL_REQUEST_TEMPLATE`** 4 섹션 엄격 준수 + 그 뒤 **`## 🤖 Agent WorkOS Audit`** 자동 append.

```markdown
## 📌 관련 이슈
- close #<n>
- parent #<parent>
- derived from #<선행 issue 1>
- derived from #<선행 issue 2>

## ✨ 과제 내용
- 작업 목적
- 왜 필요한지
- 무엇을 변경했는지
- 무엇을 아직 하지 않았는지

## :camera_with_flash: 스크린샷(선택)
- (없음 또는 N/A)

## 📚 레퍼런스 (또는 새로 알게 된 내용) 혹은 궁금한 사항들
- 참고 레퍼런스
- 통합에 사용한 선행 이슈/PR/Obsidian 노트
- 새로 알게 된 점
- 후속 과제

## 🤖 Agent WorkOS Audit

- audit_id: `<id>`
- branch: `<branch>` (from `<base>`)
- repo: `<owner>/<repo>`
- role: `<author role>`           # write-ownership.md §5 결정
- autonomy_level: `<L0~L4>`       # autonomy_policy 의 string value
- actor: `<bot identity>`
- mode: `<role-owned | tech-lead-mediated | gateway-mediated>`
- issue: <URL>
```

추가 규칙:

- PR title = `<gitmoji> <핵심 작업 요약> (#<issue>)`. gitmoji 는 [`policies/reference/COMMIT_CONVENTION.md`](../../../reference/COMMIT_CONVENTION.md) 의 기본 / 필요 시 표 그대로.
- PR body 가 issue template 형태로 섞이지 않게 한다 — `## 어떤 기능인가요?` 등 issue 헤딩은 PR 에 사용 금지.
- 변경 파일 설명은 **PR body §✨ 과제 내용** 과 **issue progress comment** 양쪽에 남긴다.
- repository PR template 자동 fill 은 G6 LiveGithubAppClient 의 `repository_pr_template.compose_pr_body` 가 책임 (`a19b718` 결과).

## 3. GitHub Label policy

부착 = repo 의 **실재 label 만**. 자동 생성 금지.

| 실재 label (부착 가능) | 부착 trigger |
| --- | --- |
| `✨ Feature` | 신규 기능 / 정책 / 규칙 신설 |
| `📃 Docs` | 문서 / 정책 / 노트 수정 |
| `✅ Test` | 회귀 / acceptance / smoke test 추가 |
| `🔨 Refactor` | 정책 정리 / 구조 조정 / 분기 분해 |
| `🐞 BugFix` | 회귀 / regression fix |
| `⚙ Setting` | 환경 / 설치 / 부트스트랩 변경 |
| `🌏 Deploy` | runtime / 배포 정책 변경 |
| `📬 API` | API 계약 / 외부 통신 변경 |
| `🥰 Accessibility` | a11y |
| `💻 CrossBrowsing` | browser 호환성 |
| `🎨 Html&css` | markup / styling |
| `🙋‍♂️ Question` | 정보 요청 |

| 추천 label (repo 미생성) | 부착 시점 | 어떻게 surface 하는가 |
| --- | --- | --- |
| `🎯 Core` | 코어 비즈니스 로직 / 부서 공통 운영 규칙 | issue comment 의 "추천 라벨" 섹션 |
| `🏗 Infrastructure` | DB / 배포 / CI / GitHub Apps / Obsidian 통합 인프라 | 같음 |
| `📦 Domain` | 도메인 모델 / 엔티티 정의 | 같음 |
| `🗄 Schema` | DB 스키마 / 마이그레이션 | 같음 |
| `🔐 Auth` | 인증 / 인가 흐름 | 같음 |

추가 규칙:

- 부착 라벨마다 **이유 1 줄** 을 issue comment 에 명시.
- 추천 label 은 별도 `### 추천 라벨 (repo 미생성)` 섹션으로.
- 부착 / 추천 정책의 source-of-truth 는 [`policies/runtime/agents/planning-agent/github-label-policy.md`](../../planning-agent/github-label-policy.md).

## 4. Progress comment 형식

issue 진행 코멘트는 **5 섹션 모두 필수**.

```markdown
## 📈 Progress — <라운드 라벨>

### 1. 이번 라운드 목표
- ...

### 2. 변경 파일

| path | 변경 종류 | 변경 사유 |
| --- | --- | --- |
| `policies/.../*.md` | added | ... |

### 3. 테스트 / 검증 결과
- `python3 -m unittest discover -s tests -t .` → N / N 통과
- ...

### 4. Obsidian 노트 경로
- `notes/vault-mirror/.../research/...md`
- `notes/vault-mirror/.../decisions/...md`
- `notes/vault-mirror/.../task-logs/...md`

### 5. 다음 액션
- ...
```

추가 규칙:

- 라벨 부착 / 추천 / 변경 사유는 본 progress 의 §2 / §3 / §5 에 분산.
- gateway-mediated kickoff / final closure 는 별도 형식 허용 (본 progress 형식 의무 아님).

## 5. 커밋 분할 + GitHub Apps + push

### 5.1 커밋 / PR 분할 — semantic CRUD-like slices (P0-G refine)

PR / 커밋 분할의 *목적* 은 literal CRUD 4 분할 강제가 아니라 **리뷰어가 5분 안에 목적을 설명 가능 + 롤백이 독립 가능 + 테스트 포인트가 한 덩어리** 인 단위로 자르는 것이다.

기본 원칙 (한 PR / 한 commit 모두 적용):

1. **한 PR = 한 책임.** 한 PR 안에 의도가 다른 변경이 섞이지 않는다.
2. **5분 룰.** 리뷰어가 PR title + body §✨ 만 보고 5 분 안에 목적을 설명 가능해야 한다. 안 되면 분할.
3. **롤백 독립.** revert 했을 때 다른 의도 변경까지 같이 되돌아가면 안 된다.
4. **테스트 포인트가 한 덩어리.** 한 PR 의 변경은 단일 acceptance / regression test 묶음으로 보호 가능해야 한다.

**Semantic slice 분류 — C / R / U / D + 예외 4 종.**

| 분류 | 의미 | 대표 예시 |
| --- | --- | --- |
| **C** (Create / Construct) | 새 구조·새 기능·새 정책 신설 | 신규 모듈 / 신규 정책 파일 / 신규 dataclass / 신규 worker. |
| **R** (Read / Reveal) | 조회 / 진단 / 가시화 / 계측 | 신규 status command / 신규 metric / observability surface / 새 audit log shape. |
| **U** (Update / Upgrade) | 기존 동작·기존 정책 업데이트 | 정책 §X.Y 의 규칙 갱신 / 기존 endpoint contract 강화 / 기존 surface 의 표현 정정. |
| **D** (Delete / Decommission) | 제거 / 정리 / deprecated / cleanup | 더 이상 쓰이지 않는 helper 제거 / dead-flag drop / 정책 supersede. |

**예외 4 종.** 다음은 본 정책의 5분 룰 / 한 PR = 한 책임 을 *지키되 분류 강제 X*:

- **hotfix.** 인시던트 / live 회귀의 즉시 차단. 분류 표기 무관. PR title 에 `hotfix:` prefix 명시.
- **docs-only.** 정책 / docs 만 land. 분류 강제 안 함. 단, 같은 PR 에 코드 변경이 함께 들어가면 분할.
- **test-only.** 회귀 test 만 추가. 코드 동작 변경 없음. 분류 강제 안 함.
- **tiny config fix.** ≤ 10 줄, 단일 env / config 정정. 분류 강제 안 함. PR body §✨ 에 "tiny config" 한 줄.

**한 PR 안의 commit 분할.**

- 한 PR 안에 **최소 3 commit, 권장 5 commit** 의 논리 단위 분할.
- 모든 변경을 1 commit 으로 뭉치는 것 금지 (예외 4 종은 예외).
- 분할 기준 (정책 PR 의 예시):
  1. C — 선행 산출물 분석 / Obsidian 노트 / audit doc
  2. C — 첫 정책 layer (예: Obsidian / wikilink / naming)
  3. C / U — 두 번째 정책 layer (write ownership / authoring)
  4. C / U — 세 번째 정책 layer (GitHub workflow / docs)
  5. C — 정책 회귀 test
- 코드 PR 의 예시:
  1. C — 신규 모듈 (pure unit)
  2. U — 기존 caller 의 wire-in
  3. C — 회귀 test
- 커밋 메시지 = [`policies/reference/COMMIT_CONVENTION.md`](../../../reference/COMMIT_CONVENTION.md) 의 한국어 3 섹션 (`변경 이유` / `주요 변경 사항` / `비고`) 엄격 준수. 비어 있는 섹션은 `- 없음` 한 줄.

**PR 사이즈 가이드.**

- 정책 PR: doc 만이면 5~8 commit, code 동반이면 7~10.
- 코드 PR: 단일 책임이면 3~6 commit. 그 이상 필요하면 **PR 분할 검토**.
- 단일 PR 의 diff 가 **> 800 줄 (test 제외)** 이면 분할 권장 — 리뷰어 5분 룰 위반.

### 5.2 GitHub Apps 우선 사용

- issue 생성 / label 적용 / kickoff comment / progress comment / draft PR 생성 = **G6 LiveGithubAppClient + gh CLI** 우선 사용.
- 사용자 명시 승인이 있으면 G6 의 git data API (blob / tree / commit / branch ref / draft PR) 를 라이브 경로로 사용 가능 (`#25` PR #68 가 첫 사례).

### 5.3 push 정책

- 기본 — push 는 **현재 작업 브랜치만**.
- protected branch (`main` / `master` / `dev` / `prod` / `release`) **직접 push 금지** — 영구.
- force push **영구 금지**.
- auto merge / production deploy **영구 금지**.
- 사용자 standing rule "push 금지" 가 활성화돼 있으면 작업 시작 시점에 명시 해제 받기 — 본 issue 는 명시 해제 받음 (issue body / kickoff comment 명시).

## 6. 검증

`tests/engineering/test_engineering_agent_governance_doc.py` 가 본 정책의 핵심 항목을 lint-style 로 검증:

- §1 issue 4 섹션 헤딩 존재
- §2 PR 4 섹션 + Audit 헤딩 존재
- §3 실재 label 표 / 추천 label 표 모두 존재
- §4 progress 5 섹션 모두 본문에 명시
- §5 커밋 분할 / push 정책 키워드 존재 + §5.1 의 semantic CRUD-like slice 표 존재

## 7. 변경 이력

| 일자 | 변경 |
| --- | --- |
| 2026-05-08 | 초안 (Issue #69 — D-69-10 ~ D-69-14 결정 반영. PR template fix `a19b718` 결과 활용.) |
| 2026-05-14 | P0-G 1차 (Issue #139 / parent #138) — §5.1 을 semantic CRUD-like slices 로 refine. C/R/U/D + 예외 4 종 (hotfix / docs-only / test-only / tiny config). 5 분 룰 / 롤백 독립 / 테스트 포인트 한 덩어리 원칙 명시. PR 사이즈 가이드 (800 줄) 추가. |
