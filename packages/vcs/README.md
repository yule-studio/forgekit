# yule-vcs

Version-control utilities extracted from the engineering-agent core (구
`agents/git`). Pure stdlib, no `yule_engineering` import (clean leaf).

- `github_url` — GitHub URL/owner-repo 파싱.
- `repo_contract` — repo write/tag 정책 contract.

옛 경로 `yule_engineering.agents.git[.github_url|.repo_contract]` 는 `yule_vcs`
를 가리키는 compat shim(sys.modules alias, identity 보존)으로 유지된다.
