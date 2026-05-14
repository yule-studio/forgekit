# Vault Documentation Versioning (v1.0.0)

| 문서 버전 | 작성일 | 작성자 | 주요 변경 사항 |
| --- | --- | --- | --- |
| v1.0.0 | 2026-05-15 | engineering-agent/tech-lead | 최초 — semver 기반 문서 버전 bump 규칙 (#151) |

Obsidian vault (`yule-agent-vault/obsidian-vault/`) 안 모든 노트가
frontmatter 다음에 **문서 버전 표** 를 둔다. 본 정책은 그 표의
**버전 bump 규칙** 을 정의한다.

`policies/runtime/vault/naming-convention.md` (v.2.0.0) 의 *companion* —
naming-convention 이 *파일명* 의 규칙이라면 본 정책은 *내용 버전* 의 규칙.

## 0. 적용 범위

- 모든 신규 / 수정 노트 (research / decision / task-log / report / reference /
  meeting / knowledge / pattern).
- vault 에 노트를 쓰는 모든 agent (engineering-agent / planning-agent /
  product-agent / 등) 가 동일 규칙.
- 본 정책 위반 = 문서 버전 표가 변경 규모와 mismatch → reader 신뢰 ↓.

## 1. 표준 형식

모든 노트의 frontmatter 다음:

```markdown
# <한 줄 제목>

| 문서 버전 | 작성일 | 작성자 | 주요 변경 사항 |
| --- | --- | --- | --- |
| v2.1.0 | 2026-05-15 | engineering-agent/tech-lead | social-login 흡수 |
| v2.0.0 | 2026-05-14 | engineering-agent/tech-lead | 폴더 split (8 sub-folder) |
| v1.0.0 | 2026-05-14 | engineering-agent/tech-lead | 최초 |
```

### 1.1 표기 규칙

- `v` 소문자 prefix (점 X — `v.1.0.0` 는 deprecated).
- Major.Minor.Patch — 마침표 구분.
- 최신 버전이 위 (descending — reverse chronological).
- 옛 버전 row **영구 보존** (history) — 삭제 X.
- 같은 날 여러 bump OK — 버전만 다르면 OK.
- 작성자: `<부서>/<역할>` 형식 (예: `engineering-agent/tech-lead`).
- "주요 변경 사항" 컬럼: 한 줄, 구체적 (vague "update" / "fix" X).

## 2. Bump 규칙 — semver 적용

### 2.1 MAJOR (X.0.0)

폴더 / 구조 / navigation 변경 — reader 의 mental model 이 바뀜.

**예시**:

- 단일 파일 → 폴더 split (예: `signup.md` → `signup/` 폴더 13 파일).
- 다른 도메인 흡수 (예: `signup` 이 `login` / `email-verify` / `password-reset` 흡수).
- Phase 재구성 (Hub 의 chapter 순서 / 의미 변경).
- 노트 위치 이동 (folder A → folder B).
- 인접 노트 / hub 의 cross-link 가 다수 깨지는 변경.

### 2.2 MINOR (X.Y.0)

새 노트 / 새 흐름 / 새 섹션 추가 — 의미 확장 (기존 navigation 유지).

**예시**:

- 새 implementation 노트 추가 (예: `social-login-impl.md` 신규).
- design-decisions 에 새 결정 (예: `idempotency-policy.md` 추가).
- security 에 새 layer (예: `audit-logging.md`).
- 함정 카테고리 추가 (예: `domain-pitfalls.md`).

### 2.3 PATCH (X.Y.Z)

수정 / 보강 / 함정 추가 — 의미 변화 X.

**예시**:

- 함정 5개 → 10개 보강.
- link / typo 정정.
- ASCII → mermaid 다이어그램 전환.
- "왜" 깊이 추가 (기존 섹션 안 내용 보강).
- 코드 snippet 의 누락 import 추가.

## 3. 판별 질문

새 변경이 어느 bump 인지 결정 시 순서대로 답:

1. **reader 의 navigation 이 바뀌나?** (폴더 / 위치 / Hub 의 chapter)
   - Yes → MAJOR
   - No → 다음
2. **새 의미 / 새 흐름 / 새 노트가 추가됐나?**
   - Yes → MINOR
   - No → 다음
3. **기존 내용 보강 / 수정만?**
   - Yes → PATCH

## 4. 옛 형식 (deprecated)

```
v.1.0.0  ❌ — 점 prefix 사용 안 함 (옛 형식, 마이그레이션 점진적 OK)
v1.0     ❌ — Patch 자리 누락
1.0.0    ❌ — v prefix 누락
"v1"     ❌ — Minor/Patch 누락
```

옛 노트의 `v.1.0.0` → 다음 bump 시 `v2.0.0` (점 제거하면서 자연 정정).
강제 일괄 마이그레이션 X — 점진적.

## 5. 봇의 의무

vault 에 노트를 쓰는 모든 agent:

| 의무 | 무엇 |
| --- | --- |
| 새 노트 작성 시 | v1.0.0 시작 + "최초" 명시 |
| 노트 수정 시 | §3 판별 질문 → Major/Minor/Patch bump |
| 옛 버전 row | 영구 보존 (descending 정렬 유지) |
| "주요 변경 사항" | 한 줄, 구체적 (vague "update" 금지) |
| 작성자 | `<부서>/<역할>` 형식 |

### 5.1 봇 작성 flow

```
1. 노트 작성 / 수정 시작
2. 변경 규모 판별 (§3 판별 질문)
3. 적절한 bump 결정
4. 문서 버전 표 의 최상단에 새 row 추가
   - 기존 row 절대 삭제 X
   - 새 row: v{new} | {YYYY-MM-DD} | {부서/역할} | {한 줄 변경 사항}
5. 본문 변경
6. PR 의 commit message 에도 bump 명시 (예: "v4.2.0 social-login 흡수")
```

## 6. 적용 예 — signup.md 의 history 재해석

**옛 표 (mismatch)**:
```
v.3.0.0 — auth 통합
v.2.0.0 — 폴더 split (12 detail)
v.1.0.0 — 단일 파일
```

→ 모두 Major. 매번 너무 큼.

**새 정책 적용 (올바른 해석)**:
```
v4.2.0 — social-login 흡수 (Minor — 새 흐름)
v4.0.1 — mermaid 전환 (Patch — 의미 변화 X)
v4.0.0 — Option A 폴더 8개 split (Major — 구조 변경)
v3.0.0 — auth 통합 (Major — 도메인 흡수)
v2.0.0 — 폴더 split (Major — 구조 변경)
v1.0.0 — 최초 단일 파일
```

→ Major / Minor / Patch 가 변경 규모와 일치.

## 7. 다른 정책과의 관계

| 정책 | 책임 | 본 정책과 관계 |
| --- | --- | --- |
| `policies/runtime/vault/naming-convention.md` | vault **파일명** 규칙 | companion — 파일명 vs 내용 버전 |
| `policies/runtime/agents/engineering-agent/obsidian-governance.md` | engineering-agent 의 Obsidian 작성 거버넌스 | 본 정책이 §2 (Naming) 뒤에 추가될 수도 (옛 정책의 인용 보강) |
| `policies/runtime/agents/engineering-agent/issue-pr-conventions.md` | Issue / PR 컨벤션 | PR 의 commit message 에 bump 명시 권장 |
| `policies/runtime/vault/manifest.md` (있다면) | vault 인덱스 manifest | 본 정책 신규 — 추후 추가 가능 |

## 8. 함정 모음

### 함정 1 — 매번 Major 올림
"big bump" 환상. typo 도 Major 면 의미 부풀림.
→ §3 판별 질문 적용.

### 함정 2 — 옛 버전 row 삭제
history 손실 — 변경 추적 X.
→ 영구 보존, descending 정렬.

### 함정 3 — 변경 사항 vague
"update" / "fix" 만. reader 가 무엇 변경됐는지 모름.
→ 한 줄 구체 ("social-login 흡수").

### 함정 4 — `v.1.0.0` 점 prefix
deprecated 형식. 새 bump 시 자연 정정.

### 함정 5 — 같은 PR 의 여러 logical 변경을 하나의 row 로
"social-login 흡수 + mermaid 전환" → 다른 의미 압축 X.
→ 각 logical 변경마다 row.

### 함정 6 — 의미 변화 없는데 Minor 올림
typo / link 정정 = Patch.
→ 의미 추가 없으면 Patch.

### 함정 7 — Patch 가 폴더 변경 포함
구조 변경 = Major.

### 함정 8 — 새 노트 생성 시 v0.1.0
"미완성" 의미로 v0.x.x 사용 → 본 정책은 시작은 v1.0.0.
→ "draft" 표시는 frontmatter `status: draft` 로.

## 9. 검증 (옵션 — 추후)

회귀 테스트로 vault mirror 의 모든 노트의 문서 버전 표 형식 검증:

```python
# tests/engineering/test_doc_versioning_governance.py (옵션)
def test_all_notes_have_version_table():
    # frontmatter 다음 첫 표가 "문서 버전 | 작성일 | 작성자 | 주요 변경 사항" 헤더
    ...
```

본 정책 v1.0.0 시점엔 테스트 없음 — 정책 명시만. 향후 governance test 추가.

## 관련 문서

- `policies/runtime/vault/naming-convention.md` — vault 파일명 규칙 (companion)
- `policies/runtime/agents/engineering-agent/obsidian-governance.md` — Obsidian 작성 거버넌스
- vault 미러: `yule-agent-vault/obsidian-vault/40-patterns/documentation-versioning.md`
- semver: https://semver.org
- Issue #151 — 본 정책의 origin
