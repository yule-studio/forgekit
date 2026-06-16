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

## projection (✅ 생성기 구현됨)

`scripts/sync_mcp_projection.py` 가 SSoT 를 provider별 연결 config 로 투영한다
(`supports_providers` 에 포함된 provider 만, **secret 값 없이 env 참조만**):

```bash
python3 scripts/sync_mcp_projection.py          # 생성/갱신
python3 scripts/sync_mcp_projection.py --check    # drift 시 exit 1 (CI/테스트)
```

| 생성물 | provider | 형식 |
| --- | --- | --- |
| `.mcp.json` | Claude Code | native project MCP (`mcpServers`, `${ENV}` 헤더) |
| `.codex-plugin/mcp.toml` | Codex | `[mcp_servers.<id>]` 스니펫(`url` + `bearer_token_env_var`) |
| `.gemini-plugin/mcp.json` | Gemini | `mcpServers`(`httpUrl` + `${ENV}` 헤더) |

- **HOME/global 미터치**: Codex/Gemini native config 는 HOME 에 있으므로 repo-tracked
  *스니펫* 을 생성한다 — 운영자가 자기 CLI config 에 include. `~/.codex`·`~/.gemini` 직접
  쓰지 않는다.
- drift/secret 회귀: [`tests/governance/test_mcp_projection.py`](../../tests/governance/test_mcp_projection.py).

## 회귀
[`tests/governance/test_mcp_registry.py`](../../tests/governance/test_mcp_registry.py).
