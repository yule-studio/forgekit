# COMMIT CONVENTION

이 문서는 본 레포지토리의 **공식 커밋 메시지 규칙**이며, 커밋 메시지 형식·작성 절차·검증의 **단일 source of truth** 다. 사람이 직접 커밋하거나, Engineering Agent / 보조 에이전트가 커밋 메시지를 제안할 때 모두 이 문서를 우선 기준으로 사용한다.

이전 커밋 로그(`git log`)나 `.gitmessage.txt` 의 줄 모양은 형식 판단의 근거가 아니다. 새 커밋의 형식·규칙·예외는 항상 이 문서로부터 해석한다.

현재는 1인 프로젝트이므로 복잡한 규칙보다 일관성을 우선한다.
추후 Jira 연동이 완료되면 Jira 티켓 키를 포함하는 방식으로 확장할 수 있다.

## 적용 범위 (cross-repo)

본 컨벤션은 `yule-studio-agent` 내부 커밋뿐 아니라 **봇이 GitHub write 를 수행하는 모든 target repo** (예: `yule-studio/naver-search-clone`, 향후 engineering runtime 이 coding_execute / github_work_order / draft PR 을 만드는 모든 repo) 에 동일하게 적용된다. repo-local stricter policy 가 있으면 그것을 우선 적용하되, 기본은 본 SSoT 를 상속한다.

코드 차원 SSoT — [`src/yule_orchestrator/agents/governance/repo_write_policy.py`](../../src/yule_orchestrator/agents/governance/repo_write_policy.py) 가 commit / issue title / PR title / issue anchor 4-종 hard guard 를 한 자리에 모은다. live path (GithubAppCommitter / GithubAppDraftPRCreator / GithubWriter.create_issue 등) 가 그 validator 를 호출하고 실패 시 raise.

## Initial commit 특수 규칙

새 repo 의 첫 non-merge 커밋 (bootstrap / scaffold flow 의 repo initialization commit 포함) 은 반드시 다음 제목을 사용한다:

```
:tada: initial commit
```

- 이 규칙은 일반 gitmoji whitelist 의 예외다.
- 본문 (변경 이유 / 주요 변경 사항 / 비고) 는 일반 규칙 유지.
- 첫 커밋 이후에는 `:tada:` 사용 금지.
- 첫 커밋 판별이 ambiguous 하면 enforcement layer 가 `initial_commit_detection_ambiguous` blocker 로 surface.

판별 기준 (`repo_write_policy.is_initial_commit_context`):
1. repo 의 첫 non-merge commit (HEAD 이전 commit count == 0)
2. caller (bootstrap / scaffold flow) 가 명시적으로 `initial_commit=True` 를 hint 로 전달

## 커밋 템플릿

`.gitmessage.txt` 는 편의를 위한 Git commit template 역할만 한다. 규칙의 정의·해석은 항상 이 문서를 따르고, `.gitmessage.txt` 의 빈 줄·주석은 형식의 권위가 아니다.

적용 명령어:

```bash
git config --local commit.template .gitmessage.txt
```

## 커밋 메시지 형식

```text
<gitmoji> 제목

변경 이유
- ...

주요 변경 사항
- ...

비고
- ...
```

세 섹션(`변경 이유`, `주요 변경 사항`, `비고`)은 항상 이 순서로 두며, 내용이 없을 때도 섹션 자체는 생략하지 않고 `- 없음` 한 줄로 둔다.

예시:

```text
✨ 로그인 API 추가

변경 이유
- 로그인 기능이 아직 구현되지 않았다

주요 변경 사항
- 로그인 API 엔드포인트 추가
- 인증 서비스 로직 추가

비고
- 토큰 재발급 API는 별도 작업으로 진행한다
```

비고가 비어 있을 때는 다음과 같이 `- 없음` 한 줄을 둔다.

```text
🐛 로그인 만료 토큰 처리 수정

변경 이유
- 만료 토큰이 401 대신 500을 반환하던 회귀

주요 변경 사항
- 토큰 검증 로직에 만료 분기 추가

비고
- 없음
```

## 기본 사용 Gitmoji

| Gitmoji | 의미 | 사용 기준 |
| ------- | ------ | ------ |
| ✨ | 기능 추가 | 새로운 기능, API, 로직 추가 |
| 🐛 | 버그 수정 | 오류, 예외, 잘못된 동작 수정 |
| ♻️ | 리팩토링 | 기능 변경 없이 구조 개선 |
| 📝 | 문서 수정 | 문서, README, 정책 파일 수정 |
| ✅ | 테스트 | 테스트 코드 추가 및 수정 |
| 🔧 | 설정 변경 | 설정값, 빌드, 의존성, 스크립트 수정 |

## 필요할 때만 사용하는 Gitmoji

| Gitmoji | 의미 | 사용 기준 |
| ------- | ------ | ------ |
| 🚚 | 파일 이동 | 파일 또는 디렉토리 경로 이동 |
| 🔥 | 삭제 | 미사용 코드, 파일, 설정 삭제 |
| ⚡️ | 성능 개선 | 성능 최적화, 쿼리 개선 |
| 👷 | CI/CD 변경 | GitHub Actions, 배포 파이프라인 수정 |
| 💚 | 빌드 복구 | 깨진 빌드, 테스트, CI 복구 |
| 🚑️ | 긴급 수정 | 운영 이슈 등 긴급 대응 |

## 작성 규칙

- Gitmoji는 커밋 의도에 맞게 하나만 사용한다
- 제목은 짧고 명확하게 작성한다
- 제목 끝에 마침표를 사용하지 않는다
- 하나의 커밋에는 하나의 목적만 담는다
- 본문은 한국어로 작성한다
- 본문은 `변경 이유`, `주요 변경 사항`, `비고` 순서로 작성한다
- 각 항목은 간결한 불릿(`- `)으로 작성한다
- 내용이 없는 항목도 생략하지 않고 `- 없음`으로 명시한다
- 의미가 애매하면 기본 Gitmoji를 우선 사용한다
- 커밋 메시지 초안을 제안할 때도 반드시 이 형식을 따른다
- 이전 커밋 로그(`git log`)는 형식 판단의 기준이 아니다 — 항상 이 문서를 우선 참조한다
- `.gitmessage.txt`는 편의를 위한 템플릿일 뿐이고, 규칙의 source of truth는 본 문서다

## 작성 기준

- 기능 추가가 핵심이면 ✨
- 버그 수정이 핵심이면 🐛
- 구조 개선이 핵심이면 ♻️
- 문서 수정이면 📝
- 테스트 변경이면 ✅
- 설정 변경이면 🔧
- 파일 이동은 🚚
- 삭제는 🔥
