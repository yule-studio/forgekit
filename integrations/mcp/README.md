# MCP server registry — vendor-neutral SSoT

> MCP(Model Context Protocol) 서버는 **backend 가 연결하는 외부 도구 채널**이다 —
> plugin/hook/skill 과 다른 개념([`docs/plugin-taxonomy.md`](../../docs/plugin-taxonomy.md) §6).
> 이 폴더의 `<id>.json` 이 vendor-neutral SSoT 이고, 코드 로더/검증은
> [`agents/harness/mcp_registry.py`](../../apps/engineering-agent/src/yule_engineering/agents/harness/mcp_registry.py).

## 스키마

```json
{
  "id": "figma",                       // kebab/snake 식별자
  "name": "Figma MCP",
  "description": "...",
  "transport": "http",                 // http | stdio
  "url": "https://mcp.figma.com/mcp",  // http 일 때 필수
  "command": null,                     // stdio 일 때 필수
  "auth": { "type": "bearer", "env": "FIGMA_OAUTH_TOKEN" },  // env 는 KEY 이름 — 값 금지
  "tools": ["use_figma", "..."],       // 선언 도구(선택)
  "supports_providers": ["claude", "codex", "gemini"],  // MCP 가능 harness 만
  "autonomy_level": "supervised",
  "risk_class": "MEDIUM"
}
```

## 하드레일

1. `transport` ∈ {`http`,`stdio`}. http → `url` 필수, stdio → `command` 필수.
2. **secret 값 금지** — `auth.env` 는 자격을 담은 env **키 이름**만(예: `FIGMA_OAUTH_TOKEN`).
   토큰 값/URL-식 문자열이면 검증 실패.
3. `supports_providers` ⊆ {`claude`,`codex`,`gemini`} — **Ollama 제외**. Ollama 는
   MCP host 가 아니라 local inference backend 이기 때문([matrix §4](../../docs/provider-capability-matrix.md)).

## projection (후속)

provider별 연결 파일 생성은 후속:
- Claude: `.mcp.json` / `.claude` MCP 설정
- Codex: `~/.codex/config.toml` 의 `[mcp_servers.<id>]`
- Gemini: Gemini MCP 설정

생성기는 `scripts/sync_harness_skills.py` 와 같은 SSoT→projection 패턴을 따른다(미구현).

## 회귀
[`tests/governance/test_mcp_registry.py`](../../tests/governance/test_mcp_registry.py).
