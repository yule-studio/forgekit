# Forgekit console — 운영자 콘솔 (1차 프레임)

> `forgekit` 는 이 레포(사용자-facing 이름 **forgekit**)의 운영자 콘솔이다. 터미널에서
> `forgekit` 한 줄이면 전체화면 TUI 콘솔이 열린다. 이번 단계는 **완성형 채팅 앱이 아니라
> 1차 콘솔 프레임 + slash palette + status pane + 입력창** 이다.

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
# 콘솔은 textual 이 필요 — console extra 로 설치 (pillow 포함; 이미지 렌더용·선택)
pip install -e '.[console]'

forgekit            # = forgekit console (전체화면 콘솔 열기)
forgekit console
forgekit --version
forgekit console --repo-root /path/to/repo   # status 기준 경로 지정
```

- `pip install -e .`(코어, textual 미포함) 만 해도 `forgekit` 명령은 등록된다. 단 textual 이 없으면
  콘솔 실행 시 **친절한 설치 안내(exit 3)** 를 출력한다(트레이스백 아님).
- 기본 repo root 해석 우선순위: `--repo-root` > `YULE_REPO_ROOT` > 현재 디렉터리.
- **아바타는 작은 텍스트 워드마크가 기본** (3차에서 변경). 픽셀화되는 래스터 사진을 기본
  표시하지 않는다. 사전-베이크된 이미지 에셋/Pillow 는 *opt-in* 경로로만 남는다.

## 3. 화면 구성 (content-first, 위→아래 흐름)

3차에서 좌/중/우 3-pane cockpit 을 **단일 컬럼 content-first 흐름**으로 재설계했다. 입력은
상단에 있고, 명령 결과·help·palette 가 그 아래로 펼쳐진다.

```
forge kit ▸ operator console · <repo> · view focus      ← 1줄 header(작은 워드마크)
● provider runtime live 3/5 · self-improvement waiting 1 ← 1줄 operator status pill
● operator  > 명령 입력 …                                ← 얇은 입력 줄(mode pill + input)
  palette 3 · Tab 완성 …          (slash 입력 시 입력 바로 아래 inline)
  forgekit help · Help · General …(/help 시 입력 아래 inline 문서로 펼쳐짐)
─ main log (welcome → 명령 결과가 위→아래로 쌓임) ──────  [dashboard 모드면 우측 rail]
/ palette  Tab 완성  F1 help  /layout 보기  ^L  ^C        ← 1줄 contextual hint
```

- **header(1줄)**: 작은 forgekit 워드마크 + repo + 현재 view(focus/dashboard).
- **status pill(1줄)**: operator 요약 한 줄(health dot + provider/self-improve…) — 2차 정보로 내림.
- **input(1줄)**: 절제된 mode pill(`● operator` / `● palette` / `● <agent>`) + 얇은 입력창.
- **inline surfaces**: palette/help 는 입력 바로 아래에서 **문서처럼 inline 으로 펼쳐짐**(팝업/모달 아님).
- **main log**: welcome → slash 결과가 위→아래로 쌓이는 reading flow.
- **operator 정보 3단계**: 상시 pill → `/status` inline → `dashboard` 모드 우측 rail.

## 3b. 키 바인딩 / 상호작용

| 키 | 동작 |
| --- | --- |
| `/` | command palette(입력 아래 inline) 오픈·필터 |
| `Tab` / `Shift+Tab` | 자동완성·다음/이전 후보 (`/he` + Tab → `/help`) |
| `↑` / `↓` | palette 후보 순환 · (help 열림 시) help 스크롤 |
| `Enter` | 정확한 명령이면 실행, 후보 선택 중이면 선택 명령 실행 |
| `Esc` | help 닫기 → palette 닫기 → agent 모드 해제 (순서) |
| `F1` | help inline surface 토글 |
| `/layout` | focus ↔ dashboard 보기 전환 |
| `^L` / `^R` / `^C` | 로그 지우기 / 상태 새로고침 / 종료 |

- **slash palette**: 평소 숨김, `/` 입력 시 입력 바로 아래 **얇은 inline 메뉴**로 필터·하이라이트(상자형 팝업 아님).
- **`/help`**: 모달 제거 → 입력 아래 **inline help 문서**(Help/General/Commands/Agents 섹션, 스크롤). Esc 로 닫고 흐름 복귀.
- **아바타**: 기본 = 작은 crisp 워드마크. fallback 순서 = 워드마크 → (opt-in)베이크/Pillow → 텍스트. **래스터가 깨질 바엔 표시하지 않는다.**

## 4. slash 명령

| 명령 | 동작 |
| --- | --- |
| `/help` | inline help 문서 토글 |
| `/agents` | 에이전트 레지스트리(로그에 inline 출력) |
| `/status`, `/harness` | operator 대시보드 요약(provider/eval/self-improve/token + next) |
| `/runtime` | `runtime status` 요약 |
| `/doctor` | 환경 진단 요약 |
| `/layout` | focus ↔ dashboard 보기 전환 |
| `/pm-agent`·`/planning-agent`·`/backend-agent`·`/security-agent`·`/ops-observer` | 에이전트 모드 진입 **stub** (+추천 액션; ops-observer 는 alerts 동반) |
| `/clear` | 로그 지우기 |
| `/quit` | 종료 |

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
  tui/render.py        문자열 렌더(banner/status-pill/hint/mode-pill/help-inline/palette) — 순수
  tui/avatar.py        mini_brandmark(기본) + 베이크/Pillow/텍스트 fallback tier
  tui/keymap.py        키 바인딩 + 힌트 (순수 데이터)
  tui/styles.py        Screen CSS 상수(app.py 분리)
  tui/palette.py       inline command palette 위젯 (textual)
  tui/help_view.py     inline help 문서 위젯 (textual, 모달 아님)
  tui/app.py           Textual App — content-first compose + 상태(mode/layout/palette/help)
  app/main.py          `forgekit` 엔트리 (textual 부재 시 graceful degrade)
  assets/forgekit-avatar.txt  사전-베이크 half-block(opt-in 경로용, 기본 미표시)
```

- **순수 코어(models/commands/data/tui.render·avatar·keymap·styles)는 textual 없이 import·테스트 가능.**
  TUI 위젯/앱은 textual 설치 시 pilot 스모크로 검증(`tests/forgekit/test_tui_smoke.py`).
- **상태 분리**: input/palette/help/mode/layout 이 섞이지 않는다 — palette 는 순수 상태머신, 나머지는 app 보유, 렌더는 순수 render 함수.
- status 는 기존 yule 헬퍼를 **best-effort 재사용**(실패 시 alert 로 degrade, 콘솔은 안 죽음).
- 레지스트리는 data-driven — 이후 `skills/*.md` / grants / agent projection 으로 hydrate 하는 seam.

## 6. 이번 범위 / 범위 밖

**범위(3차 레이아웃):** content-first 단일 컬럼 reading flow, inline help(모달 제거), inline
palette, 작은 워드마크 아바타(래스터 기본 제거), 얇은 입력 + 절제된 mode pill + 1줄 hint,
focus/dashboard 레이아웃 모드(`/layout`), operator 정보 3단계화, 테스트/문서.

**범위 밖(후속):** 실제 Claude/Codex/Gemini live chat loop, Agent Town 연동, macOS 알림,
Discord push, approval inbox 조작, multi-provider interactive session persistence.

## 7. 관련
- [`runtime-operator-surfaces.md`](runtime-operator-surfaces.md) (재사용하는 surface) ·
  [`operations.md`](operations.md) · [`monorepo-structure.md`](monorepo-structure.md)
