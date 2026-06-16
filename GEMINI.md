# Yule Studio Agent

This file provides Gemini CLI context for this repository.  
(이 파일은 Gemini CLI가 이 레포지토리의 작업 맥락을 이해하기 위한 컨텍스트 파일이다)

**Entry order (any main provider is the same):** `AGENTS.md` → root `CLAUDE.md` → context docs.
This file is a THIN projection — it adds only Gemini's default role. **All shared rules
(safety / approval / coding / git / sync) live in `CLAUDE.md` — not duplicated here.**  
(공통 규칙은 `CLAUDE.md` 한 곳에만 있다 — 여기 복제하지 않는다)

@./CLAUDE.md

## Gemini Role (projection only)

- Default **advisor** unless a task explicitly assigns executor.  
  (명시적 executor 부여가 없으면 기본 advisor)
- Prefer analysis, requirement review, long-context review, planning support.  
  (분석 / 요구사항 검토 / 긴 맥락 검토 / 계획 보조 우선)
- Everything else (file edits, destructive commands, secrets, approval gates, engineering-agent
  policy) follows the shared rules in `CLAUDE.md` + the context docs routed by `AGENTS.md`.

## Custom commands (generated projection)

- `.gemini/commands/*.toml` 은 레지스트리 skill 의 **Gemini projection 생성물**이다
  (Claude Code `.claude/skills/`, Codex `.agents/skills/` 와 같은 역할).
  손으로 편집하지 않는다 — SSoT(`skills/<id>.md` + `agents/grants/slash-command-grants.json`)
  를 고치고 `python3 scripts/sync_harness_skills.py` 를 재실행한다.
- 어떤 skill 이 Gemini 로 투영되는지는 grant 의 `harness` 목록에 `"gemini"` 포함 여부로 정해진다.
- Gemini 의 적합 영역(research / large-context / cheap analysis / draft)은
  [`docs/provider-capability-matrix.md`](docs/provider-capability-matrix.md) 참조.
  plugin/hook/skill/MCP/backend 개념 분리는 [`docs/plugin-taxonomy.md`](docs/plugin-taxonomy.md).
