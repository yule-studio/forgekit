# yule-vcs

Version-control utilities extracted from the engineering-agent core (구
`agents/git`). Pure stdlib, no `yule_engineering` import (clean leaf).

- `github_url` — GitHub URL/owner-repo 파싱.
- `repo_contract` — repo write/tag 정책 contract.

(옛 yule_engineering.agents.git compat shim 은 제거됨 — 호출부가 yule_vcs 직접 import).
