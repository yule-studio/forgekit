# Context Loading Policy

## Purpose
Agents should load only the context required for their current role and task.  
(에이전트는 현재 역할과 작업에 필요한 컨텍스트만 읽어야 한다)

## Rules
- Start with the root project instructions, then load the active agent's `instruction_entry` file.  
  (루트 프로젝트 지침을 먼저 읽고, 그 다음 현재 활성 에이전트의 `instruction_entry` 파일을 읽는다)

- Load policy files listed in the active agent's `manifest.json`.  
  (현재 활성 에이전트의 `manifest.json`에 나열된 정책 파일을 읽는다)

- Do not load policies for unrelated agents unless the task explicitly requires them.  
  (작업에서 명시적으로 필요하지 않은 한 관련 없는 에이전트의 정책은 읽지 않는다)

- Prefer small, task-specific context over large always-on context.  
  (항상 많은 컨텍스트를 읽는 방식보다 작고 작업에 맞는 컨텍스트를 우선한다)

- If a referenced policy file is missing, report it and continue with the available context.  
  (참조된 정책 파일이 없으면 이를 보고하고 사용 가능한 컨텍스트로 계속 진행한다)
