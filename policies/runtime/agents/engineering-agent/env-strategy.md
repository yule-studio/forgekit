# Engineering Agent Env & Auth Strategy (v0)

이 문서는 `engineering-agent`가 **멀티봇·멀티모델**로 운영될 때 사용할 환경변수, 인증 토큰, 설정 로더 정책을 정의한다. 회사 전체 에이전트 플랫폼의 첫 실행 부서로서, 이후 product/design/marketing/operations agent가 동일한 패턴을 그대로 따를 수 있도록 만든 레퍼런스 규약이다.

이번 정책은 **규칙과 보안 경계**만 정의한다. 실제 토큰 발급, 외부 API 호출, 토큰 회전 자동화는 후속 이슈에서 진행한다.

## 1. 네이밍 원칙

### 1.1 prefix 분류
| prefix | 책임 |
|---|---|
| `YULE_` | 부서 공통 런타임 설정 (timezone, work mode, 캐시 등). 부서가 늘어나면 `YULE_<DEPT>_` 식으로 확장. |
| `DISCORD_` | gateway bot / 채널 / 응답 모드. 단일 봇 시점의 호환을 유지. |
| `ENGINEERING_AGENT_BOT_<MEMBER>_*` | engineering-agent 부서의 멤버별 bot 토큰·persona. 멀티봇 단계에서만 사용. |
| `ANTHROPIC_` / `OPENAI_` / `GOOGLE_` / `OLLAMA_` | LLM 백엔드 인증·엔드포인트. 부서가 아니라 백엔드 풀 단위. |
| `GITHUB_` | GitHub CLI/REST 인증, 캐시, 라벨 정책. |
| `REFERENCE_` | UI/UX/마케팅 reference 외부 소스 슬롯. 이번 단계에서는 자리만 예약. |

### 1.2 회사 전체 확장 시 규칙
- 멀티 부서로 확장되어도 `YULE_`은 부서 공통, `<DEPT>_AGENT_BOT_*`는 부서 전용으로 유지한다.
- 멤버별 키는 항상 부서 prefix를 포함한다. 예: `ENGINEERING_AGENT_BOT_BACKEND_ENGINEER_TOKEN`. design-agent로 분리되면 `DESIGN_AGENT_BOT_PRODUCT_DESIGNER_TOKEN`으로 키를 옮긴다.
- LLM 백엔드 키는 부서 접두를 두지 않는다. participants pool이 부서 단위로 공유되기 때문이다.

## 2. Discord 토큰 경계

### 2.1 Phase 1 — 단일 gateway bot
- `DISCORD_BOT_TOKEN` 하나만 사용한다.
- gateway bot이 멤버 persona를 분기(임베드 author / prefix)로 표현한다.
- 채널 분리는 기존 `DISCORD_DAILY_CHANNEL_*`, `DISCORD_CHECKPOINT_CHANNEL_*`, `DISCORD_CONVERSATION_CHANNEL_*`, `DISCORD_DEBUG_CHANNEL_*`을 그대로 사용한다.

### 2.2 Phase 2 — 멤버별 bot 분리
멤버를 별도 봇으로 분리하면 다음 키를 사용한다.

```
ENGINEERING_AGENT_BOT_GATEWAY_TOKEN          # 부서 게이트웨이(외부 회신 전용)
ENGINEERING_AGENT_BOT_TECH_LEAD_TOKEN
ENGINEERING_AGENT_BOT_BACKEND_ENGINEER_TOKEN
ENGINEERING_AGENT_BOT_FRONTEND_ENGINEER_TOKEN
ENGINEERING_AGENT_BOT_PRODUCT_DESIGNER_TOKEN
ENGINEERING_AGENT_BOT_QA_ENGINEER_TOKEN
```

- 토큰을 채운 멤버만 활성화된다. 비어 있으면 gateway가 단일봇 fallback으로 그 멤버 메시지를 처리한다.
- 멤버 토큰이 한 개라도 채워지면 gateway는 외부와의 통신만 담당하고, 내부 멤버는 자기 토큰으로 발화한다 (CLAUDE.md의 "외부 대화 권한은 게이트웨이만" 규칙 유지).
- product-designer가 design-agent로 분리되면 해당 라인을 `DESIGN_AGENT_BOT_PRODUCT_DESIGNER_TOKEN`으로 옮긴다. engineering-manifest.json에서는 멤버 목록과 함께 토큰 키를 제거한다.

### 2.3 채널 정책
- 외부와의 대화는 gateway 봇만 한다. 멤버 봇이 활성화되어도 사용자에게 직접 답하지 않고 내부 채널/스레드에서만 발화한다.
- DAILY 채널은 broadcast 전용 (현 정책 유지).
- CONVERSATION/CHECKPOINT 채널만 사용자 입력을 수신한다.

## 3. LLM 인증 경계

### 3.1 키 매핑
| 백엔드 | env 키 | 비고 |
|---|---|---|
| Claude (claude CLI) | `ANTHROPIC_API_KEY` 또는 `claude` CLI 자체 인증 | CLI 인증 사용 시 env 키 없이도 동작. |
| Codex (codex CLI) | `OPENAI_API_KEY` 또는 `codex` CLI 자체 인증 | CLI 인증 우선. |
| Gemini (gemini CLI) | `GOOGLE_API_KEY` / `GEMINI_API_KEY` 또는 `gemini` CLI 자체 인증 | CLI 우선. |
| Ollama | `OLLAMA_ENDPOINT`, `OLLAMA_MODEL`, `OLLAMA_DISCORD_*`, `OLLAMA_PLANNING_ENABLED` | 인증 없음, 엔드포인트만. |
| GitHub Copilot | `gh auth` 인증 + `gh extension install github/gh-copilot` | 별도 env 키 없음. |

### 3.2 보안 경계
- LLM API 키는 `.env.local`에만 둔다. `.env`나 커밋되는 파일에는 절대 두지 않는다.
- 가능하면 CLI 자체 인증을 사용한다. CLI 인증이 가능한 백엔드는 env 키를 비우는 것을 기본으로 한다.
- 키가 노출됐다고 의심되면 즉시 회전한다. 회전 절차는 후속 토큰 관리 이슈에서 정의한다.

### 3.3 백엔드 풀과 부서의 관계
- LLM 키는 부서가 아니라 **백엔드 풀** 단위다. 새 부서가 추가되어도 동일한 키를 공유한다.
- 멤버는 LLM 키를 직접 들고 있지 않는다. 게이트웨이가 풀을 통해 백엔드를 선택한다 (참고: `runners.md`).

## 4. GitHub 인증 경계

### 4.1 키
- 로컬 실행은 `gh auth login`으로 받은 사용자 토큰을 사용한다 (`GH_TOKEN` / `GITHUB_TOKEN` 직접 주입은 권장하지 않는다).
- 캐시·정책 옵션:
  - `GITHUB_ISSUES_CACHE_SECONDS`, `GITHUB_PULL_REQUESTS_CACHE_SECONDS`
  - `YULE_GITHUB_LABEL_POLICY_FILE`, `YULE_GITHUB_LABEL_POLICY_JSON`

### 4.2 권한 경계
- 스코프: 이슈/풀 리퀘스트 read, 라벨/PR draft 작성. **자동 merge·자동 deploy·branch protection 변경은 금지**.
- 멀티 멤버봇으로 확장되어도 GitHub 토큰은 사람 사용자 1명의 토큰을 공유한다. 멤버별 GitHub 봇은 도입하지 않는다 (멤버 식별은 PR body의 persona 라벨로 표현).
- 비밀 접근(예: GitHub Actions secrets, deploy key)은 사용자 명시 승인 전에는 시도하지 않는다.

## 5. 설정 로더 기준

### 5.1 우선순위
1. 프로세스 환경변수 (이미 export된 값)
2. `.env.local` (개발자 로컬, 커밋 금지)
3. `.env` (있으면 공통 기본값, 비밀 금지)
4. 코드 안 default 값

`load_env_files`는 이미 export된 키를 덮어쓰지 않는다 (`core/env_loader.py`).

### 5.2 `.env.example` 기준
- 모든 키는 `.env.example`에 등장해야 한다. 단 멀티봇·외부 reference 슬롯은 주석 처리 상태로 둔다.
- 키마다 한 줄 주석으로 의도를 적는다. 비밀 값은 비워둔다.
- 새 키를 추가할 때는 같은 PR에서 `.env.example`, `README`, 본 정책 문서, `scripts/bootstrap`(있을 경우)을 함께 갱신한다.

### 5.3 검증 시점
- Discord bot 시작: `DiscordBotConfig.from_env()`가 필수 키를 fail-fast로 검증.
- doctor 명령: 백엔드 가용성, GitHub 인증, Discord TLS, agent 매니페스트 검증.
- 멀티봇 단계에서는 활성화된 멤버 토큰만 검증하고, 비활성 멤버는 SKIP 상태를 보고한다.

## 6. 누락/오설정 시 에러 정책

### 6.1 분류
| 키 종류 | 정책 |
|---|---|
| **Required** (예: `DISCORD_BOT_TOKEN`, `DISCORD_GUILD_ID`, `NAVER_ID`, `NAVER_APP_PASSWORD`) | fail-fast. 시작 즉시 `ValueError`로 중단하고, 메시지에 "Add it to .env.local before running ..."을 포함한다. |
| **Optional with default** (예: `DISCORD_PREPARATION_RETRY_COUNT=2`) | 기본값으로 진행. 로그 없음. |
| **Optional with feature flag** (예: `OLLAMA_PLANNING_ENABLED=false`) | 비활성. 진입 시 `info:` 한 줄로 비활성 사유를 stderr에 보고. |
| **Member bot token (Phase 2)** | 누락은 정상. 누락된 멤버는 gateway 단일봇 fallback. doctor에서 SKIP 상태로 노출. |
| **External reference slot** | 누락 시 자동 reference 수집 비활성. 사용자 제공 링크와 로컬 문서 기준으로 진행한다는 안내 메시지만 출력. |

### 6.2 메시지 규약
- 형식: `"<KEY> is required. <어떻게 해결할지 한 문장>."`
- 시작 시 누락된 키 목록을 한 번에 모아 보고하지 않고, 처음 문제가 되는 키에서 즉시 중단한다 (운영자가 한 번에 하나씩 고치게 한다).
- 잘못된 형식(예: int여야 하는 값에 문자열): `"<KEY> must be an integer value, got: <value>"` 형태를 유지한다. 기존 `discord/config.py` 패턴을 따른다.
- 비밀 값은 절대 에러 메시지에 포함하지 않는다. 키 이름과 형식 규칙만 노출.

### 6.3 fail-fast vs degrade
- 시스템 진입에 반드시 필요한 종속(Discord 토큰, Naver 인증)은 fail-fast.
- 보조 기능(외부 reference, GitHub Copilot, member bot, Ollama 보조)은 degrade. 비활성 사유를 1회만 stderr로 알린다.

## 7. 외부 reference 소스 설정 슬롯 (예약)

이번 단계에서는 키 이름만 예약하고 본문은 구현하지 않는다.

```
# Reference sources (slot reservation only — not wired in MVP)
# REFERENCE_PINTEREST_BUSINESS_ACCESS_TOKEN=
# REFERENCE_META_AD_LIBRARY_ACCESS_TOKEN=
# REFERENCE_TIKTOK_CREATIVE_CENTER_ACCESS_TOKEN=
# REFERENCE_GOOGLE_TRENDS_API_KEY=
```

규약:
- 모든 reference 키는 `REFERENCE_` 접두를 사용한다.
- 자동 수집 가능 소스 (Pinterest Trends, Meta Ad Library, TikTok Creative Center, Google Trends)는 위 슬롯을 채워서 사용한다.
- 자동 수집이 약관상 민감한 소스 (Notefolio, Behance, Mobbin, Page Flows, Awwwards 등)는 env 슬롯을 두지 않는다. 사용자 제공 링크와 수동 참고로만 사용한다.
- Really Good Emails / Pinterest Trends / Canva Design School / Wix Templates처럼 공개 페이지 기반 소스는 1차 단계에서 fetcher 없이 링크 모음만 유지한다.
- 키 누락은 항상 degrade(자동 수집 비활성)이며, fail-fast 대상이 아니다.

## 8. 후속 작업 (다음 이슈 입력)

이 문서가 정의한 규약 위에서 이어질 작업:

1. **#21 또는 후속 토큰 관리 이슈** — 멤버별 bot 토큰 활성/비활성 토글, Phase 1↔2 전환 절차, 토큰 회전 SOP.
2. **member-bot launcher** — `ENGINEERING_AGENT_BOT_*`을 읽어 멤버 봇을 병렬 기동하고, gateway와 IPC하는 진입점.
3. **doctor 확장** — 멤버 토큰별 SKIP/OK/FAIL 상태, LLM CLI 인증 상태, GitHub 권한 스코프 점검.
4. **reference fetcher** — `REFERENCE_*` 슬롯이 채워졌을 때만 동작하는 자동 수집기. Notefolio 등은 수동 링크 입력 흐름만 제공.
5. **`.env.example` 갱신** — Phase 2 멤버 토큰과 reference 슬롯을 주석 처리 상태로 추가 (이번 PR에서 함께 진행).

---

본 문서는 멀티 부서 확장(product/design/marketing/operations)의 레퍼런스다. 새 부서를 만들 때는 이 파일을 복사해 prefix만 갈아 끼우는 형태로 사용한다.
