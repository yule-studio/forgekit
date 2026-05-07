# Coding Version Control Policy

## Purpose
This policy defines how Engineering Agent should handle branches, commits, pull requests, and repository naming rules.
(이 정책은 Engineering Agent가 브랜치, 커밋, Pull Request, 저장소 네이밍 규칙을 어떻게 다뤄야 하는지 정의한다)

## Rules
- Follow the repository branch strategy documented in `policies/reference/BRANCH_STRATEGY.md` when that document exists.
  (`policies/reference/BRANCH_STRATEGY.md`가 존재하면 해당 브랜치 전략을 따른다)

- Follow the repository commit convention documented in `policies/reference/COMMIT_CONVENTION.md` when that document exists.
  (`policies/reference/COMMIT_CONVENTION.md`가 존재하면 해당 커밋 규칙을 따른다)

- Follow the repository naming convention documented in `policies/reference/NAMING_CONVENTION.md` when that document exists.
  (`policies/reference/NAMING_CONVENTION.md`가 존재하면 해당 네이밍 규칙을 따른다)

- Prefer branch-based work. Do not commit directly to protected branches such as `main` or `dev`.
  (브랜치 기반 작업을 우선하고 `main`, `dev` 같은 보호 브랜치에 직접 커밋하지 않는다)

- Use one clear purpose per commit.
  (하나의 커밋에는 하나의 명확한 목적만 담는다)

- Use Gitmoji-based commit messages when the repository defines that convention.
  (레포지토리가 해당 규칙을 정의하면 Gitmoji 기반 커밋 메시지를 사용한다)

- If the branch strategy requires a Jira ticket key and no ticket key is available, pause before creating a new branch and ask the user for the intended key.
  (브랜치 전략이 Jira 티켓 키를 요구하는데 티켓 키가 없으면 새 브랜치 생성 전에 멈추고 사용자에게 키를 확인한다)

- Treat large rename or move operations as separate commits when practical.
  (가능하면 대규모 리네임이나 이동 작업은 별도 커밋으로 분리한다)

## Commit message authoring rules

- 커밋 메시지를 제안하거나 작성할 때 최근 커밋 형식(`git log`)을 따라 추론하지 않는다. 직전 커밋이 규칙을 위반했을 수 있으므로 이전 메시지의 모양은 형식의 권위가 아니다.
- `policies/reference/COMMIT_CONVENTION.md` 를 우선 기준으로 사용한다. 형식·섹션 순서·필수 항목·예외는 항상 그 문서로부터 해석한다.
- 레포 루트의 `.gitmessage.txt` 가 있으면 Git commit template 으로 사용할 수 있다. 다만 규칙 해석은 `COMMIT_CONVENTION.md` 를 따르고, `.gitmessage.txt` 의 빈 줄·주석은 형식의 권위가 아니다.
