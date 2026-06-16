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
- **아바타는 source image 기반 terminal-safe asset** (4차에서 변경). 원본 이미지(헤드폰
  portrait)를 얼굴 중심으로 crop → 작은(≈18 cols) grayscale half-block 으로 **사전-베이크**해
  `assets/forgekit-avatar.txt` 로 커밋한다. 런타임은 이 작은 에셋만 렌더 — 큰 래스터/흐린
  픽셀 덩어리가 아니라 Claude 아이콘처럼 작고 선명한 브랜드 마크. 에셋이 없으면 텍스트 마크.

## 3. 화면 구성 (Claude-like 위→아래 흐름)

4차에서 Claude Code 터미널 UI 에 가깝게 **compact intro → issue line → 입력 → 본문** 의
단일 세로 흐름으로 정리했다(좌/우 패널·두꺼운 footer 제거).

```
▀▀  forgekit v0.1.0                    ← compact intro: 작은 아바타(좌) + 브랜드/버전(우)
▄▄  operator console
    provider —   profile operator
    /repo/path
ready · /status                        ← setup/status issue line (텍스트 1줄, 조용)
● operator  > 명령 입력 …               ← 얇은 입력 줄(mode pill + input)
  palette …            (slash 입력 시 입력 바로 아래 inline)
─ 본문: welcome → 명령 결과 / help 문서가 위→아래로 한 흐름에 쌓임 ─────────────
/help · / palette · Tab 완성 · ^C quit  ← 1줄 hint
```

- **intro(컴팩트)**: 작은 source-image 아바타(좌) + `forgekit` 이름/버전, profile/provider, repo(우).
- **issue line(1줄)**: 텍스트 중심 한 줄 — 기본 `ready · /status`, 이슈 있으면 `N issues: … · /doctor`. 긴 operator 상태 행은 첫 화면에서 약화(자세히는 `/status`).
- **input(1줄)**: 절제된 mode pill(`● operator` / `● palette` / `● <agent>`) + 얇은 입력창.
- **content(1fr)**: 하나의 큰 읽기 영역. 평소 log, `/help` 시 **전체 폭 help 문서로 swap**(모달/사이드패널/아코디언 아님).

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

- **slash palette**: 평소 숨김, `/` 입력 시 입력 바로 아래 얇은 inline 메뉴로 필터·하이라이트.
- **`/help`**: 모달 아님 → **본문 영역 전체 폭 문서**. 탭 `Help · General · Commands · Agents`, **기본 General**, 한 번에 활성 탭만 표시(Tab 으로 전환, Esc 로 닫고 log 복귀).

## 4. slash 명령

| 명령 | 동작 |
| --- | --- |
| `/help` | 본문에 inline help 문서(탭, 기본 General) |
| `/agents` | 에이전트 레지스트리(로그에 inline 출력) |
| `/status` | operator 요약(provider/eval/self-improve/token) — 자세한 상태는 여기서 |
| `/runtime` · `/doctor` | runtime status · 환경 진단 요약 |
| `/pm-agent` | **Product intake gate** — 요구 보강·결정 질문·spec packet handoff(엔지니어링 앞단). [`product-intake-gate.md`](product-intake-gate.md) |
| `/planning-agent`·`/backend-agent`·`/security-agent`·`/ops-observer` | 에이전트 모드 진입 **stub** |
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
  tui/render.py        문자열 렌더(welcome/issue-line/hint/mode-pill/help-document/palette) — 순수
  tui/intro.py         compact intro(아바타 좌 + 브랜드/버전/profile/repo 우) — 순수
  tui/avatar.py        source-image 베이크 에셋 + 텍스트 fallback tier
  tui/keymap.py        키 바인딩 + 힌트 (순수 데이터)
  tui/styles.py        Screen CSS 상수(app.py 분리)
  tui/palette.py       inline command palette 위젯 (textual)
  tui/help_document.py 본문 전체 폭 inline help 문서 위젯(탭, 모달 아님)
  tui/app.py           Textual App — Claude-like 세로 흐름 compose + 상태(mode/palette/help-tab)
  app/main.py          `forgekit` 엔트리 (textual 부재 시 graceful degrade)
  assets/forgekit-avatar.txt  source image → 작은 grayscale half-block 사전-베이크(기본 표시)
```

- **순수 코어(models/commands/data/tui.render·intro·avatar·keymap·styles)는 textual 없이 import·테스트 가능.**
  TUI 위젯/앱은 textual 설치 시 pilot 스모크로 검증(`tests/forgekit/test_tui_smoke.py`).
- **상태 분리**: input/palette/help/mode 가 섞이지 않는다 — palette 는 순수 상태머신, help-tab/mode 는 app 보유, 렌더는 순수 render 함수.
- status 는 기존 yule 헬퍼를 **best-effort 재사용**(실패 시 alert 로 degrade, 콘솔은 안 죽음).

## 6. 이번 범위 / 범위 밖

**범위(4차 디자인 교정):** Claude-like compact intro(source-image 아바타) → issue line → 얇은
입력 → 본문 단일 흐름, `/help` 본문 전체 폭 inline 문서(탭, 기본 General), `/exit` alias,
상단 정보 밀도 완화(operator 상세는 `/status`), 테스트/문서. brain/setup/provider 는 미포함.

**범위 밖(후속):** brain/setup/provider 코어, 실제 live chat loop, Agent Town, macOS 알림,
Discord push, approval inbox 조작, multi-provider session persistence.

## 7. 관련
- [`runtime-operator-surfaces.md`](runtime-operator-surfaces.md) (재사용하는 surface) ·
  [`operations.md`](operations.md) · [`monorepo-structure.md`](monorepo-structure.md)
