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
# 콘솔은 textual 이 필요 — console extra 로 설치
pip install -e '.[console]'

forgekit            # = forgekit console (전체화면 콘솔 열기)
forgekit console
forgekit --version
forgekit console --repo-root /path/to/repo   # status 기준 경로 지정
```

- `pip install -e .`(코어, textual 미포함) 만 해도 `forgekit` 명령은 등록된다. 단 textual 이 없으면
  콘솔 실행 시 **친절한 설치 안내(exit 3)** 를 출력한다(트레이스백 아님).
- 기본 repo root 해석 우선순위: `--repo-root` > `YULE_REPO_ROOT` > 현재 디렉터리.

## 3. 화면 구성 (operator cockpit)

```
┌ forgekit · operator console · <repo> ───────────────────────────────┐
│ agents          │  center log                  │  status pane        │
│ ● Engineering   │  welcome banner / quick cmds  │  provider runtime   │
│ ● Planning      │  › /status                    │  self-improvement   │
│ ● Product (PM)  │  » operator dashboard …       │  eval gate / token  │
│ ○ Marketing …   │                               │  alerts / next      │
├─ palette (입력창에 `/` 입력 시 후보) ───────────────────────────────┤
│ > 명령 입력 …                                                        │
└ footer: ^C Quit  ^L Clear  ^R Refresh ───────────────────────────────┘
```

- **header**: forgekit 이름 · profile · 현재 repo root
- **left**: agent quick list (engineering / planning / product / backend / security / ops-observer / …)
- **center**: welcome banner → slash 명령 결과 로그
- **right**: runtime/harness 요약 + 최근 alerts + what-to-do-next
- **bottom**: 입력창(+ `/` palette), footer 단축키

## 4. slash 명령 (1차)

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
  models.py            순수 dataclass 계약 (stdlib)
  commands/
    registry.py        agent/command 레지스트리 (data-driven 확장 seam)
    parser.py          slash 파서 + palette 매처 (순수)
    router.py          ParsedInput → CommandResult (순수, 로더 주입)
  data/status_loader.py  기존 surface 재사용 (compose_dashboard/run_doctor/build_runtime_status) + 순수 shaper
  tui/render.py        문자열 렌더 헬퍼 (순수, textual 무관)
  tui/app.py           Textual App (textual 지연 import)
  app/main.py          `forgekit` 엔트리 (textual 부재 시 graceful degrade)
```

- **순수 코어(models/commands/data/tui.render)는 textual 없이 import·테스트 가능.** 테스트가
  실제 그렇게 돈다(`tests/forgekit/`).
- status 는 기존 yule 헬퍼를 **best-effort 재사용**(실패 시 alert 로 degrade, 콘솔은 안 죽음).
- 레지스트리는 data-driven — 이후 `skills/*.md` / grants / agent projection 으로 hydrate 하는 seam.

## 6. 이번 범위 / 범위 밖

**범위(1차):** 콘솔 프레임, slash palette, status pane 재사용, 입력창, agent-entry stub, 테스트, 문서.

**범위 밖(후속):** 실제 Claude/Codex/Gemini live chat loop, Agent Town 연동, macOS 알림,
Discord push, approval inbox 조작, multi-provider interactive session persistence.

## 7. 관련
- [`runtime-operator-surfaces.md`](runtime-operator-surfaces.md) (재사용하는 surface) ·
  [`operations.md`](operations.md) · [`monorepo-structure.md`](monorepo-structure.md)
