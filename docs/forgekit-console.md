# Forgekit console — 운영자 콘솔 (Claude Code-스타일)

> `forgekit` 는 이 레포(사용자-facing 이름 **forgekit**)의 운영자 콘솔이다. 터미널에서
> `forgekit` 한 줄이면 전체화면 TUI 콘솔이 열린다. UI 는 **Claude Code CLI 처럼 chat-first**:
> 상단 작은 실-이미지 아바타 intro → 조용한 issue line → transcript(본문) → **항상 보이는
> 하단 고정 composer(채팅 입력)** → hint 한 줄. `/help` 는 modal 이 아니라 **transcript 안에**
> 렌더된다.

## 1. forgekit 와 yule 의 관계

| | 역할 | 진입점 |
| --- | --- | --- |
| `yule` | 기존 engineering CLI (서브커맨드: runtime/harness/engineer/…) — **그대로 유지** | `yule_engineering.cli:main` |
| `forgekit` | 운영자 콘솔 TUI (yule runtime/harness/doctor surface 를 **읽어서** 표시) | `forgekit_console.app.main:main` |

- 브랜드/실행 커맨드만 `forgekit` 로 분리했다. **내부 Python 패키지명 전면 rename 은 하지 않았다**
  — `yule_engineering` 등은 그대로다. 1차는 *브랜드 전환 + 콘솔 프레임 + future seam* 이 핵심.
- `forgekit` 콘솔은 비즈니스 로직을 갖지 않는다. 기존 surface(`runtime status` / `harness status` /
  `doctor`)를 재사용해 *읽기 전용* 으로 표시한다.

## 2. 설치 / 실행

```bash
# 콘솔은 textual 이 필요 — console extra 로 설치
#   (pillow = 아바타 베이크, textual-image = 실 인라인 이미지 렌더)
pip install -e '.[console]'

forgekit            # = forgekit console (전체화면 콘솔 열기)
forgekit console
forgekit --version
forgekit console --repo-root /path/to/repo   # status 기준 경로 지정
```

- `pip install -e .`(코어, textual 미포함) 만 해도 `forgekit` 명령은 등록된다. 단 textual 이 없으면
  콘솔 실행 시 **친절한 설치 안내(exit 3)** 를 출력한다(트레이스백 아님).
- 기본 repo root 해석 우선순위: `--repo-root` > `YULE_REPO_ROOT` > 현재 디렉터리.

### 아바타 — 실 이미지가 1순위 (real-image FIRST)

- **기본 표시 경로는 진짜 인라인 이미지다 — ASCII/half-block 텍스트가 아니다.** 원본
  헤드폰 portrait(`assets/avatar/profile_hermes_source.jpg`, 고해상 master)를 얼굴 중심으로
  crop → 작은 square PNG(`assets/avatar/forgekit-avatar.png`, ≈96px, Claude 아이콘 스케일)
  로 **사전-베이크**(`python -m forgekit_console.assets.avatar.bake`, Pillow 필요)해 커밋한다.
  콘솔은 큰 master 를 직접 렌더하지 않고 이 작은 PNG 만 렌더한다.
- **이미지-capable 터미널** (Kitty graphics / iTerm2 inline / Sixel) 이면 `textual-image`
  패키지로 그 PNG 를 **진짜 인라인 이미지**로 그린다 (1순위, primary).
- **그렇지 않은 터미널** 이면 작고 선명한 **텍스트/심볼 마크** 로 fallback 한다 (2순위,
  secondary). fallback 은 절대 큰 픽셀 덩어리(half-block 래스터)가 아니라 두 줄짜리
  깔끔한 브랜드 마크다.
- capability 판정과 renderer 선택은 **순수·주입가능** (env / `force` 인자) — `tui/image_renderer.py`
  의 `detect_image_capability` / `select_renderer`. 그래서 실제 터미널 없이 테스트된다.
  `FORGEKIT_AVATAR=image|text` 로 강제할 수 있다.
- 현실 주의: headless / 이미지-비지원 터미널에서는 실 이미지 렌더를 눈으로 확인할 수 없다.
  코드/테스트는 **추상화 + capability 검출 + fallback 로직 + 레이아웃 구조** 를 보장하고,
  우선순위(real-image FIRST, fallback SECOND)를 명시한다.

## 3. 화면 구성 (Claude Code chat-first 위→아래 흐름)

Claude Code 터미널 UI 처럼 **intro → issue line → transcript(본문) → 고정 composer → hint**
의 세로 흐름이다. 핵심은 **하단 composer(채팅 입력)가 항상 보인다**는 점 — `/help` 가
열려도 약해지거나 사라지지 않는다.

```
🜲  forgekit v0.1.0                     ← intro: 작은 실-이미지 아바타(좌) + 브랜드/버전/provider/profile/repo(우)
    operator console
    provider —   profile operator
    /repo/path
ready · /status                        ← issue line (텍스트 1줄, 조용)
─ transcript(본문, 1fr): welcome → 명령 결과 → /help 문서가 위→아래로 한 흐름에 쌓임 ─
   …                                      (스크롤되는 chat-first 영역)
/help · / palette · Tab 완성 · ^C quit  ← 1줄 hint
─────────────────────────────────────  ← (composer 상단 경계선)
  palette …            (slash 입력 시 입력 바로 위 inline)
● operator  > 명령 입력 …               ← 고정 composer(mode pill + input) — dock: bottom, 항상 보임
```

- **intro(IntroHeader)**: 작은 **실-이미지** 아바타(좌, image-capable 시) + `forgekit` 이름/버전,
  provider/profile, repo(우). 비지원 터미널이면 아바타 자리에 작은 텍스트 마크.
- **issue line(1줄)**: 텍스트 중심 한 줄 — 기본 `ready · /status`, 이슈 있으면 `N issues: … · /doctor`. 긴 operator 상태 행은 첫 화면에서 약화(자세히는 `/status`).
- **transcript(1fr, 본문)**: 하나의 큰 chat-first 읽기 영역. 명령 echo·결과·`/help` 문서가
  모두 여기에 위→아래로 쌓인다.
- **composer(고정, 하단)**: `dock: bottom` 으로 **항상 보이는** 채팅 입력 — inline palette(slash
  입력 시 입력 바로 위) + mode pill(`● operator` / `● palette` / `● <agent>`) + 입력창. help 가
  열려 있는 동안에도 그대로 보이고 입력 가능하다.

## 3b. 키 바인딩 / 상호작용

| 키 | 동작 |
| --- | --- |
| `/` | command palette(입력 아래 inline) 오픈·필터 |
| `Tab` / `Shift+Tab` | palette 자동완성·순환 (`/he` + Tab → `/help`) · help 열림 시 **탭 전환** |
| `↑` / `↓` | palette 후보 순환 |
| `Enter` | 정확한 명령 실행, 후보 선택 중이면 선택 명령 실행 |
| `Esc` | help 닫기 → palette 닫기 → agent 모드 해제 (순서) |
| `F1` | help 토글 |
| `^L` / `^R` / `^C` | 로그 지우기 / issue line 새로고침 / 종료 |

- **slash palette**: 평소 숨김, `/` 입력 시 (고정 composer 안의) 입력 바로 위 얇은 inline 메뉴로 필터·하이라이트.
- **`/help`**: 모달/사이드패널/아코디언 아님 → **transcript 안에** 전체 폭 help 블록으로 렌더된다(Claude Code 가 transcript 에 help 를 연 모습). 탭 `Help · General · Commands · Agents`, **기본 General**, 한 번에 활성 탭만 표시(Tab 으로 전환). **composer 는 그 동안에도 그대로 보인다.** Esc 로 닫는다.

## 4. slash 명령

| 명령 | 동작 |
| --- | --- |
| `/help` | 본문에 inline help 문서(탭, 기본 General) |
| `/agents` | 에이전트 레지스트리(로그에 inline 출력) |
| `/status` | operator 요약(provider/eval/self-improve/token) — 자세한 상태는 여기서 |
| `/runtime` · `/doctor` | runtime status · 환경 진단 요약 |
| `/pm-agent` | **Product intake gate** — 요구 보강·결정 질문·spec packet handoff(엔지니어링 앞단). [`product-intake-gate.md`](product-intake-gate.md) |
| `/planning-agent`·`/backend-agent`·`/security-agent`·`/ops-observer` | 에이전트 모드 진입 **stub** |

`/agents` 는 platform-runtime-engineer / knowledge-engineer / ops-observer 를 포함한 28 역할의
책임·커밋권한·vault lane·색을 보여준다. 역할 호출 계약은 [`agent-invocation-contract.md`](agent-invocation-contract.md),
공통 vault 색/메타데이터 정책은 [`obsidian-agent-color-policy.md`](obsidian-agent-color-policy.md).
| `/layout` | (예약) 현재는 help 로 안내 |
| `/clear` | 로그 지우기 |
| `/quit` · **`/exit`** | 종료 (`/exit` 는 `/quit` alias) |

일반 텍스트는 아직 **live submit 미연결** — 안내만 표시한다.

## 5. 아키텍처 (느슨한 결합)

```
apps/forgekit-console/src/forgekit_console/
  models.py            순수 dataclass 계약 + KIND_*/MODE_* (stdlib)
  commands/
    registry.py        agent/command 레지스트리 (data-driven 확장 seam)
    parser.py          slash 파서 + palette 매처 (순수)
    palette.py         palette/autocomplete 상태머신 — open/refilter/cycle/complete (순수)
    router.py          ParsedInput → CommandResult (순수, 로더 주입)
  data/status_loader.py  기존 surface 재사용 + 순수 shaper
  tui/render.py        문자열 렌더(welcome/intro-meta/issue-line/hint/mode-pill/help-in-transcript/palette) — 순수
  tui/image_renderer.py 아바타 렌더 추상화 — capability 검출(순수) + real-image/text 두 renderer
  tui/avatar_panel.py  아바타 위젯(선택된 renderer 의 renderable 을 mount, textual)
  tui/header.py        IntroHeader 위젯 — 아바타(좌) + 브랜드/버전/provider/profile/repo(우)
  tui/transcript.py    Transcript 위젯 — chat-first 본문(결과 + help-in-transcript 누적)
  tui/composer.py      Composer 위젯 — 고정 하단 채팅 입력(palette + mode pill + input)
  tui/keymap.py        키 바인딩 + 힌트 (순수 데이터)
  tui/styles.py        Screen CSS 상수(app.py 분리, 위젯별 CSS 는 각 위젯에)
  tui/palette.py       inline command palette 위젯 (textual, composer 안)
  tui/app.py           Textual App — Claude-Code chat-first compose + 상태(mode/palette/help-tab)
  app/main.py          `forgekit` 엔트리 (textual 부재 시 graceful degrade)
  assets/avatar/profile_hermes_source.jpg  고해상 master(crop 원본)
  assets/avatar/forgekit-avatar.png        작은 베이크 PNG(실-이미지 1순위 표시 에셋)
  assets/avatar/bake.py                    master → 작은 PNG 베이크 build-time 도구(Pillow)
```

- **순수 코어(models/commands/data/tui.render·image_renderer 의 검출·선택·keymap·styles)는 textual 없이 import·테스트 가능.**
  TUI 위젯/앱은 textual 설치 시 pilot 스모크로 검증(`tests/forgekit/test_tui_smoke.py`),
  아바타 capability/선택/fallback 은 `tests/forgekit/test_image_renderer.py`.
- **상태 분리**: input/palette/help/mode 가 섞이지 않는다 — palette 는 순수 상태머신, help-tab/mode 는 app 보유, 렌더는 순수 render 함수.
- status 는 기존 yule 헬퍼를 **best-effort 재사용**(실패 시 alert 로 degrade, 콘솔은 안 죽음).

## 6. 이번 범위 / 범위 밖

**범위(Claude Code clone 디자인):** chat-first 세로 흐름(작은 **실-이미지** 아바타 intro →
issue line → transcript 본문 → **항상 보이는 고정 하단 composer** → hint), 아바타 real-image
FIRST + capability 검출 + text fallback SECOND, `/help` 를 **transcript 안에** 렌더(모달 아님),
`/exit`(=`/quit` alias), 상단 정보 밀도 완화(operator 상세는 `/status`), 테스트/문서.
brain/setup/provider 는 미포함.

**범위 밖(후속):** brain/setup/provider 코어, 실제 live chat loop, Agent Town, macOS 알림,
Discord push, approval inbox 조작, multi-provider session persistence.

## 7. 관련
- [`runtime-operator-surfaces.md`](runtime-operator-surfaces.md) (재사용하는 surface) ·
  [`operations.md`](operations.md) · [`monorepo-structure.md`](monorepo-structure.md)
