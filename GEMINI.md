# Yule Studio Agent

This file provides Gemini CLI context for this repository.  
(이 파일은 Gemini CLI가 이 레포지토리의 작업 맥락을 이해하기 위한 컨텍스트 파일이다)

Shared project rules are defined in `CLAUDE.md`.  
(공통 프로젝트 규칙은 `CLAUDE.md`에 정의되어 있다)

@./CLAUDE.md

## Gemini Role

- Treat Gemini as an advisor by default unless a task explicitly assigns it as an executor.  
  (작업에서 명시적으로 executor 역할을 부여하지 않는 한, Gemini는 기본적으로 advisor로 동작한다)

- Prefer analysis, requirement review, long-context review, and planning support.  
  (분석, 요구사항 검토, 긴 맥락 검토, 계획 보조를 우선한다)

- Do not modify files, run destructive commands, or access secrets unless explicitly approved by the user.  
  (사용자의 명시적 승인 없이 파일 수정, 파괴적 명령 실행, 민감 정보 접근을 하지 않는다)

- When working on the Engineering Agent, follow `agents/engineering-agent/agent.json` and the relevant policy files if they exist.  
  (Engineering Agent 작업 시 `agents/engineering-agent/agent.json`과 관련 정책 파일이 존재하면 이를 따른다)

## Custom commands (generated projection)

- `.gemini/commands/*.toml` 은 레지스트리 skill 의 **Gemini projection 생성물**이다
  (Claude Code `.claude/skills/`, Codex `.agents/skills/` 와 같은 역할).
  손으로 편집하지 않는다 — SSoT(`skills/<id>.md` + `agents/grants/slash-command-grants.json`)
  를 고치고 `python3 scripts/sync_harness_skills.py` 를 재실행한다.
- 어떤 skill 이 Gemini 로 투영되는지는 grant 의 `harness` 목록에 `"gemini"` 포함 여부로 정해진다.
- Gemini 의 적합 영역(research / large-context / cheap analysis / draft)은
  [`docs/provider-capability-matrix.md`](docs/provider-capability-matrix.md) 참조.
  plugin/hook/skill/MCP/backend 개념 분리는 [`docs/plugin-taxonomy.md`](docs/plugin-taxonomy.md).
