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
- **Pillow 는 선택**: 시작 아바타는 사전-베이크된 에셋(`assets/forgekit-avatar.txt`)으로 렌더되어
  런타임에 이미지 라이브러리가 필요 없다. Pillow 는 라이브 이미지 렌더/아바타 재생성용일 뿐.

## 3. 화면 구성 (operator cockpit)

```
┌ forgekit · operator console · <repo> ───────────────────────────────┐
│ agents          │  ▀▀ forgekit avatar ▀▀         │  status pane        │
│ ● Engineering   │  welcome banner / quick cmds   │  provider runtime   │
│ ● Planning      │  › /status                     │  self-improvement   │
│ ● Product (PM)  │  » operator dashboard …        │  eval gate / token  │
│ ○ Marketing …   │                                │  alerts / next      │
├─ command palette (입력창에 `/` 입력 시 하단 오버레이로 열림) ────────┤
│ [OPERATOR] > 명령 입력 …                                             │
└ footer: / palette  Tab 완성  Esc 닫기  F1 help  ^L  ^R  ^C ──────────┘
```

- **header**: forgekit 이름 · profile · 현재 repo root
- **center**: 시작 시 **forgekit 아바타**(브랜드 이미지 기반 half-block, 미지원 시 텍스트 마크) + welcome banner → slash 결과 로그
- **left**: agent quick list / **right**: runtime·harness 요약 + alerts + next
- **bottom**: **mode badge + 입력창**(존재감 강화). `/` 입력 시 입력창과 분리된 **command palette 오버레이**가 위에 뜸
- **mode badge**: `OPERATOR` / `PALETTE` / `AGENT · <name>` 로 현재 상태 표시

## 3b. 키 바인딩 / 상호작용

| 키 | 동작 |
| --- | --- |
| `/` | command palette 오픈(입력에 따라 필터링) |
| `Tab` | 자동완성 / 다음 후보 (`/he` + Tab → `/help`) |
| `Shift+Tab` | 이전 후보 |
| `↑` / `↓` | 후보 순환 |
| `Enter` | 정확한 명령이면 실행, 후보 선택 중이면 선택 명령 실행 |
| `Esc` | palette 닫기 → (없으면) agent 모드 해제 → operator 복귀 |
| `F1` | help 오버레이 열기 |
| `^L` / `^R` / `^C` | 로그 지우기 / 상태 새로고침 / 종료 |

- **slash palette**: 평소 숨김, `/` 입력 시 하단 별도 surface 로 나타나고 입력값으로 필터·하이라이트.
- **`/help`**: 단순 텍스트가 아니라 **탭형 help 오버레이**(Help / General / Commands / Agents) 모달.
- **아바타 fallback**: 베이크 에셋 → (선택)런타임 Pillow → 텍스트 브랜드마크. 항상 깨지지 않음.

## 4. slash 명령

| 명령 | 동작 |
| --- | --- |
| `/help` | 명령 목록 |
| `/agents` | 에이전트 레지스트리 |
| `/status`, `/harness` | operator 대시보드 요약(provider/eval/self-improve/token + next actions) |
| `/runtime` | `runtime status` 요약 |
| `/doctor` | 환경 진단 요약 |
| `/pm-agent`·`/planning-agent`·`/backend-agent`·`/security-agent`·`/ops-observer` | 에이전트 모드 진입 **stub** (+추천 액션; ops-observer 는 현재 alerts 동반) |
| `/clear` | 센터 로그 지우기 |
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
  tui/render.py        문자열 렌더(banner/help/mode-badge/palette) — 순수, textual 무관
  tui/avatar.py        아바타 tier(베이크 에셋 / 런타임 Pillow / 텍스트 fallback)
  tui/keymap.py        키 바인딩 + 힌트 (순수 데이터)
  tui/palette.py       command palette 위젯 (textual)
  tui/help_view.py     탭형 help 모달 스크린 (textual)
  tui/app.py           Textual App — 상태(mode/palette) 보유 + 위젯 결선
  app/main.py          `forgekit` 엔트리 (textual 부재 시 graceful degrade)
  assets/forgekit-avatar.txt  사전-베이크된 half-block 아바타(런타임 이미지 라이브러리 불필요)
```

- **순수 코어(models/commands/data/tui.render·avatar·keymap)는 textual 없이 import·테스트 가능.**
  TUI 위젯/앱은 textual 설치 시 pilot 스모크로 검증(`tests/forgekit/test_tui_smoke.py`).
- **상태 분리**: input/palette/help/mode 상태가 섞이지 않는다 — palette 는 순수 상태머신, mode 는 app 보유, help 는 별도 스크린.
- status 는 기존 yule 헬퍼를 **best-effort 재사용**(실패 시 alert 로 degrade, 콘솔은 안 죽음).
- 레지스트리는 data-driven — 이후 `skills/*.md` / grants / agent projection 으로 hydrate 하는 seam.

## 6. 이번 범위 / 범위 밖

**범위(2차 UX):** Tab 자동완성 + 후보 순환, 하단 command palette 오버레이, 탭형 help 오버레이,
브랜드 아바타(+fallback), 강화된 하단 입력 + mode badge, 테스트/문서.

**범위 밖(후속):** 실제 Claude/Codex/Gemini live chat loop, Agent Town 연동, macOS 알림,
Discord push, approval inbox 조작, multi-provider interactive session persistence.

## 7. 관련
- [`runtime-operator-surfaces.md`](runtime-operator-surfaces.md) (재사용하는 surface) ·
  [`operations.md`](operations.md) · [`monorepo-structure.md`](monorepo-structure.md)
