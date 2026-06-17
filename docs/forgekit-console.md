# Forgekit console — 운영자 콘솔 (Claude Code-스타일)

> **브랜드 테마**: 콘솔은 forgekit 워드마크 배너(`assets/forgekit.png`, cyan→magenta
> 그라데이션 픽셀아트)에서 추출한 **cyan/magenta-on-black** 팔레트를 쓴다. SSoT 는
> [`tui/theme.py`](../apps/forgekit-console/src/forgekit_console/tui/theme.py) — 명명 상수
> (BG/FG/MUTED/ACCENT_PRIMARY(cyan)/ACCENT_SECONDARY(magenta)/ACCENT_DIM/BORDER/
> WARNING/SUCCESS/ERROR) + `wordmark()`(forge=cyan·kit=magenta 그라데이션 마크).
> 이전 orange 토큰은 전부 제거했다. neon 은 **악센트/마커로만**(prompt 마커 `›`,
> active 탭, 브랜드 워드마크, 상태 점) 쓰고 본문은 foreground/muted 로 둬 Claude-Code 식
> 절제를 유지한다. CSS 변수(`$accent`/`$accent-secondary`/`$brand-border`/`$text` …)는
> `App.get_css_variables` 로 전역 등록돼 위젯 `DEFAULT_CSS` 에서도 해석된다.

> `forgekit` 는 이 레포(사용자-facing 이름 **forgekit**)의 운영자 콘솔이다. 터미널에서
> `forgekit` 한 줄이면 전체화면 TUI 콘솔이 열린다. UI 는 **Claude Code CLI 처럼 chat-first**:
> 상단 작은 실-이미지 아바타 intro(고정 배너) → 조용한 issue line → 본문(transcript) →
> **세션을 따라가는 inline composer(채팅 입력)** → hint 한 줄. composer 는 viewport
> 하단에 고정된 footer 가 아니라 **본문 바로 아래에 inline 으로** 렌더된다 — 세션이 짧으면
> 상단 가까이에 있고 아래는 비어 있다가, transcript 가 늘면 content 가 composer 를 아래로
> 밀고 flow scroll 이 composer 를 화면에 유지한다. `/help` 는 modal 도 아니고 transcript
> 에 쌓이는 것도 아니다 — 본문 영역을 **help 뷰로 통째로 전환**(transcript 숨김)하고
> composer 는 그 help 뷰 **바로 아래**에 그대로 inline 으로 보인다. Esc 로 transcript 로
> 돌아온다.

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

### 인트로 브랜드 마크 — forgekit 워드마크 배너 (image FIRST)

인트로 상단의 브랜드 마크는 **forgekit 워드마크 배너**다. 원본 배너
(`assets/brand/forgekit-banner.png`, 1916×821 cyan→magenta 픽셀아트 워드마크)를
**사전-베이크**(`python -m forgekit_console.assets.brand.bake`, Pillow)해 작은 인트로
배너(`assets/brand/forgekit-banner-intro.png`, ≈360px)로 만들어 커밋한다. 콘솔은 큰
1916px master 를 그대로 슬램하지 않고 이 작은 PNG 만 렌더한다.

표시는 **image-first**: graphics-capable 터미널이면 `textual-image` 로 작은 배너를 **진짜
인라인 이미지**로 그리고(`tui/brand_panel.py` + `tui/image_renderer.py` 의
`make_brand_renderer`/`BrandBannerRenderer`), 그렇지 않으면 **compact 텍스트 워드마크**
(`tui/theme.py` 의 `wordmark()` — forge=cyan·kit=magenta 그라데이션)로 떨어진다. 텍스트
워드마크는 그 자체로 깔끔하게 보이도록 디자인됐다. 배너는 항상 compact(작게) 유지하며 full
master 를 크게 띄우지 않는다.

### 아바타 — 이미지가 1순위, 3-tier 우선순위 (image FIRST)

**방향: avatar 는 모든 경로에서 제공된 픽셀 아트 계열을 쓴다(배지는 최후 fallback).**
콘솔 avatar 우선순위:
1. **true-raster(tgp/sixel)** → 픽셀 아이콘을 **진짜 픽셀 이미지**로(사용자 제공 03:05 시트의 128/96
   를 resize, 임의 실루엣 재창작 아님).
2. **non-raster(VS Code 등)** → **같은 픽셀 아이콘의 half-block** 이미지(grayscale, ~16 cols). 즉
   내가 평소 보는 화면에서도 **픽셀 이미지 계열**이 나온다(`fk` 배지 아님).
3. **Pillow/asset 부재 등 정말 불가** → `fk` 배지 → bare text (최후 fallback).

`/render` 의 `avatar asset … → pixel-icon / pixel-halfblock / fk-badge` 줄로 지금 어느 family 인지
드러난다. detail portrait 는 archive / opt-in `FORGEKIT_AVATAR=portrait` 용으로 남긴다.

| 분류 | 파일 | 용도 |
| --- | --- | --- |
| 보관용 원본(archive) | `forgekit-avatar-source-2026-06-17-33.png` (+ `-38` · `-2026-06-15-original`) | portrait 후보 3종 |
| master alias | `avatar-source.png` | portrait bake 입력(채택 33 과 byte 동일) |
| **terminal icon source** | `forgekit-terminal-icon-source.png` | **제공된 pixel-art 아이콘**(icon bake 입력) |
| **terminal icon (기본)** | `forgekit-terminal-icon-master.png` / `-128` / `-96` | **콘솔 기본 렌더(true-raster)** |
| **runtime alias** | `forgekit-avatar.png` / `forgekit-avatar-96.png` | **렌더가 실제로 읽는 파일**(terminal-icon-128/96 과 byte 동일) |
| detail portrait (opt-in/archive) | `forgekit-avatar-display-128.png` / `-96` | 상세 portrait — 향후 GUI / `FORGEKIT_AVATAR=portrait` |

콘솔의 **tiny-intro 기본 렌더 = terminal icon(pixel art)** — 코드(`image_renderer`)는 runtime alias
`forgekit-avatar.png`(= `forgekit-terminal-icon-128.png`)를 1순위로 읽는다(`display_png_path()`).
상세 portrait 는 `portrait_png_path()`(`forgekit-avatar-display-128.png`)로 분리되어 opt-in portrait
모드(`HalfBlockRenderer`)에서만. terminal icon 은 `bake.py` 가 `forgekit-terminal-icon-source.png`
(제공 픽셀 아트)를 resize 해 만들고, portrait 는 `_tune_portrait`(crop+contrast+sharpen)로 만든다 —
`python -m forgekit_console.assets.avatar.bake` 한 번에 둘 다 결정적 생성, runtime alias 는 icon 과
byte 동일. operator 는 `/render` 의 `avatar asset` 줄로 지금 terminal-icon 인지 portrait 인지 본다.

> master 채택: 후보 3개(2026-06-17 33/38 · 2026-06-15 original) 중 얼굴이 가장 밝고 또렷한
> **33** 을 채택. 작은 크기와 **Python 3.10+ real-image** 양쪽에서 가장 잘 읽힌다. 나머지 2개는
> archive 로 보존해 사람이 재선택할 수 있게 둔다. 베이크 절차(`_CROP`/`_tune` = autocontrast +
> UnsharpMask)는 `bake.py` 에 코드로 박혀 재현 가능하다.
>
> **픽셀 아이콘 채택(2026-06-17 03:05 시트):** 제공된 pixel-art 헤드폰 girl 을 실제 terminal
> icon 으로 시험·**채택**했다. 시트의 클린 아이콘(라벨 제외)을 `forgekit-terminal-icon-source.png`
> 로 추출 → 128/96 resize. **경로 분리 평가**:
> - **true-raster(iTerm2/WezTerm/Kitty)**: 128px 에서 또렷한 pixel 아이콘 → **채택, 기본 렌더**.
> - **non-raster fallback(VS Code half-block ~14셀)**: 28px 로 줄이면 여전히 muddy/noisy(근거:
>   `/tmp` 28px 다운샘플 확인) → 이 경로에서만 `fk` 배지 fallback 유지(억지 도트 portrait 금지 규칙대로).
>
> 즉 "임의 새 아이콘 생성" 이 아니라 **제공 asset 을 true-raster terminal icon 으로 적용**, non-raster
> 는 배지 유지(뭉개짐 근거 명시).
>
> **real-image 검증 환경:** `textual-image` 는 Python ≥3.10 을 요구한다(메인 `.venv`(3.9)는
> import 자체가 깨짐). 검증은 별도 console venv(`.venv-console`, `python3.13 -m venv` +
> `pip install -e 'apps/forgekit-console[image]'`)에서 한다. 단 **import 성공만으로 real
> raster 가 아니다** — true raster 는 backend 가 `tgp`/`sixel` 일 때뿐이고, 자세한 backend
> 분류·정책·VS Code 실측은 아래 "렌더 경로 진단" 절을 본다.

표시는 **image-first** — 항상 실제 portrait 를 보여주려 하고, 텍스트 마크는 정말 마지막
수단일 때만 쓴다. 코드(`tui/image_renderer.py`)의 우선순위는 위→아래로:

1. **REAL 인라인 래스터** — image-capable 터미널(Kitty graphics / iTerm2 inline / Sixel,
   그리고 **VS Code 통합 터미널** — 최근 버전은 iTerm2 inline-image 프로토콜을 지원하므로
   `TERM_PROGRAM=vscode` 도 시도)이면 `textual-image` 로 그 PNG 를 **진짜 인라인 이미지**로
   그린다. **full real raster 는 `pip install -e '.[console]'`(textual-image) + graphics-capable
   터미널이 있어야 보인다.**
2. **IMAGE-DERIVED half-block** — 인라인 그래픽이 안 되는 터미널이면 그래도 **이미지**를
   보여준다: 같은 베이크 PNG 를 Pillow 로 downscale 해 만든 작고 깔끔한(~12-16 cols) Unicode
   half-block(`▀`) 래스터(`tui/halfblock.py`). 타이핑한 텍스트로 흉내 낸 게 아니라 실제
   포트레이트의 픽셀에서 유도된 이미지다.
3. **TEXT/로고 마크** — Pillow / 에셋마저 없을 때만 두 줄짜리 깔끔한 브랜드 마크로 떨어진다.

- capability 판정과 renderer 선택은 **순수·주입가능** (env / `force` 인자) — `tui/image_renderer.py`
  의 `detect_image_capability` / `select_renderer`. capable → real(tier1), not-capable →
  image-derived half-block(tier2), 둘 다 render 시점에 text(tier3)로 단계적 degrade. 그래서
  실제 터미널 없이 테스트된다. `FORGEKIT_AVATAR=image|text` 로 강제할 수 있다.
- 현실 주의: headless / 이미지-비지원 터미널에서는 tier-1 실 이미지 렌더를 눈으로 확인할 수
  없다. 그 경우 tier-2 image-derived half-block 이 대신 보인다. 코드/테스트는
  **추상화 + capability 검출 + 3-tier fallback 로직 + 레이아웃 구조** 를 보장하고,
  우선순위(real → image-derived half-block → text)를 명시한다.

### 렌더 경로 진단 — `FORGEKIT_DEBUG_RENDERERS` (진짜 backend 기준)

핵심: **`textual-image` import 성공 ≠ real raster.** `textual-image` 는 import 시점에
터미널을 probe 해서 `Image` 를 4 backend 중 하나로 바인딩한다.

| backend | 의미 | 분류 |
| --- | --- | --- |
| `tgp` | Terminal Graphics Protocol (Kitty) | **true raster** (진짜 픽셀) |
| `sixel` | Sixel (xterm/foot/WezTerm…) | **true raster** |
| `halfcell` | 셀당 ▀ half-cell (textual-image 자체 fallback) | fallback (셀/도트로 깨져 보임) |
| `unicode` | 평범한 unicode (no tty / no graphics) | fallback |

즉 "`textual_image.renderable.Image` 객체면 real-image" 는 **거짓 양성**이다 —
halfcell/unicode 까지 real 로 뭉뚱그린다. 그래서 진단은 객체의 **backend 클래스**
(`type(obj).__module__` 의 마지막 토큰)를 보고 분류한다. 추가로 `query_terminal_support()`
는 Textual 이 stdin 을 잡기 **전에만** 동작하므로, 렌더 직전 lazy import 면 capable 터미널
에서도 halfcell 로 떨어지기 쉽다 — 어느 쪽이든 우리는 절대 raster 라고 가정하지 않는다.

```bash
.venv/bin/forgekit                              # 진단 off — chrome 없음
FORGEKIT_DEBUG_RENDERERS=1 .venv/bin/forgekit   # 진단 on — intro 아래 dim 한 줄
```

표시 형태(예):

```
renderers · avatar=avatar-mark (managed-fallback) · brand=brand-text (managed-fallback) · cap=term_program=vscode · lib=ok:unicode
renderers · avatar=sixel (true-raster) · brand=sixel (true-raster) · cap=iterm2 inline images · lib=ok:sixel
```

- `avatar=<backend> (<policy>)` : 실제로 그려진 backend + 정책 state. 정책은 3단 —
  **true-raster**(tgp/sixel 진짜 픽셀) / **managed-fallback**(의도된 깔끔한 대체: 브랜드
  배지·워드마크·opt-in portrait) / **hard-fallback**(라이브러리·asset 부족 → bare text).
- `cap=…` : capability 검출 사유(휴리스틱 추측, backend 와 별개).
- `lib=ok:<backend>` : `textual-image` import 가능 + **그것이 고른 backend**. `lib=ok:unicode`
  는 "라이브러리는 되지만 raster 는 아님". import 실패면 `lib=✗ <이유>`.

이 줄 하나로 operator 는 **import 가능 / capability / 라이브러리가 고른 backend / 실제 그린
backend(+정책)** 를 분리해서 본다. managed-fallback 을 절대 "real-image" 라고 부르지 않는다.

debug flag 없이도 보고 싶으면 콘솔에서 **`/render`** — readiness(파이썬·라이브러리·터미널·
정책 + 권장 터미널 + 지원 매트릭스)를 한 화면에 보여준다.

구현: `image_renderer` 의 `renderable_backend()`(결과→backend, import-free) · `is_true_raster()`
(tgp/sixel 만) · `policy_state()`(backend→3단) · `image_library_status()` · `prime_image_backend()`
(early probe) · `diagnose_renderers()` · `render.renderer_debug_line()` · `tui/render_readiness.py`
(`/render`).

#### 렌더 정책 매트릭스

| 환경 | 예상 backend | avatar 정책 | brand 정책 | operator 권장 |
| --- | --- | --- | --- | --- |
| VS Code 통합 터미널 | `halfcell`/`unicode` | **brand 배지**(managed) | **워드마크**(managed) | fallback-first — 그대로 운영 OK |
| iTerm2 | `sixel` | portrait **픽셀 이미지**(true-raster) | banner 이미지 | **권장** (Python 3.10+ console env) |
| WezTerm | `sixel`(tgp 보고) | portrait 픽셀 이미지 | banner 이미지 | **권장** |
| Kitty | `tgp` | portrait 픽셀 이미지 | banner 이미지 | **권장** |
| no-tty / 라이브러리 없음 | `none` | 배지(managed) 또는 text(hard) | 워드마크 | — |

정책 핵심:

- **avatar**: true raster(tgp/sixel)일 때만 portrait 픽셀 이미지. 아니면 **깔끔한 브랜드 배지**
  (cyan `f` + magenta `k` 의 framed 모노그램). 14-col half-block 에서 라인아트 portrait 는
  도트로 뭉개지므로(실측: threshold/posterize 모두 28px 에서 노이즈) **portrait 를 강행하지 않는다.**
  portrait half-block 은 `FORGEKIT_AVATAR=portrait` 로 opt-in.
- **brand**: true raster 일 때만 banner 이미지. 아니면 **cyan→magenta 워드마크**(halfcell/unicode
  배너보다 훨씬 깔끔).
- override: `FORGEKIT_AVATAR=image|portrait|mark|text`.

#### true raster 공식화 + 터미널별 확인 절차

`textual-image` 의 sixel/TGP probe 는 **Textual 이 stdin 을 잡기 전에만** 동작한다. 그래서
엔트리포인트(`app/main.launch_console`)에서 `App.run()` 직전 `image_renderer.prime_image_backend()`
로 backend 를 미리 확정한다(early import) — capable 터미널이 halfcell 로 떨어지는 걸 줄인다.

- **VS Code 통합 터미널**: 실측 **halfcell**(real TTY 에서 sixel/tgp 무응답) → true raster 아님.
  forgekit 는 avatar=브랜드 배지 / brand=워드마크(둘 다 managed-fallback). `/render` 또는
  `FORGEKIT_DEBUG_RENDERERS=1` 로 `lib=ok:halfcell`(파이프 시 `unicode`) 확인.
- **iTerm2 / WezTerm / Kitty**: 같은 빌드를 그 터미널 + Python 3.10+ console env 에서 실행하면
  `lib=ok:sixel`(또는 `tgp`) + `avatar=sixel (true-raster)` 로 바뀐다 — 그때가 진짜 픽셀 이미지.
  (이 세션에서 GUI 터미널 직접 실행은 불가 — tgp 시뮬로 `avatar=tgp (true-raster)` 만 확인. 실제
  교차검증 절차/근거는 [`docs/forgekit-render-policy-evidence.md`](forgekit-render-policy-evidence.md).)

> **요약:** import 가능(lib ok)·capability 추측·고른 backend·실제 그린 backend(+정책)는 서로
> 다른 신호다. true raster 는 **tgp/sixel** 뿐. VS Code 는 fallback-first(브랜드 배지/워드마크),
> 진짜 픽셀 이미지는 iTerm2/WezTerm/Kitty 가 공식 권장 경로다.

## 3. 화면 구성 (Claude Code chat-first 위→아래 흐름)

Claude Code 터미널 UI 처럼 **intro → issue line → 본문(main panel) → inline composer → hint**
의 **TOP-ALIGNED** 세로 흐름이다. 핵심은 **composer 가 본문 바로 아래에 inline 으로 따라간다**
는 점 — viewport 하단에 고정된 footer 가 아니다. 세션이 짧으면 composer 가 상단 가까이에
있고 아래는 비어 있다(Claude 스크린샷과 동일).

```
🜲  forgekit v0.1.0                     ← intro: 작은 실-이미지 아바타(좌) + 브랜드/버전/provider/profile/repo(우) — 고정 배너
    operator console
    provider —   profile operator
    /repo/path
ready · /status                        ← issue line (텍스트 1줄, 조용)
› /help                                ← 본문(transcript) — content 만큼만(height: auto)
  └ …
╭─────────────────────────────────────────╮  ← #composer-shell = 입력 BAR (round rule, 위 1칸 gap)
│ › ● operator  명령 입력 …                 │     #inputrow (주인공)
│ /help · / palette · Tab 완성 · ^C quit    │     #hint (bar 바닥, 항상)
╰─────────────────────────────────────────╯
  ▎palette 3 · Tab · ↑/↓ · Esc                ← #palette: bar **아래의 별도 surface**
  ▎  ▸ /help    …                                (slash 입력 시) compact·auto-height·
  ▎    /harness …                                 max-height 8+scroll, giant box 아님
(아래는 비어 있음 — composer bar 는 viewport 하단이 아니다)
```

- **intro(IntroHeader)**: 작은 **실-이미지** 아바타(좌, image-capable 시) + `forgekit` 이름/버전,
  provider/profile, repo(우). 비지원 터미널이면 아바타 자리에 tier-2 image-derived half-block.
  intro 는 flow 밖 **고정 배너**.
- **issue line(1줄)**: 텍스트 중심 한 줄 — 기본 `ready · /status`, 이슈 있으면 `N issues: … · /doctor`. 긴 operator 상태 행은 첫 화면에서 약화(자세히는 `/status`).
- **session flow(`tui/session_flow.py`)**: issue line · 본문 · composer · hint 를 담는 하나의
  **top-aligned `VerticalScroll`**. 본문이 `height: auto` 라 composer 가 바로 아래에 inline 으로
  붙고, transcript 가 늘면 content 가 composer 를 아래로 밀며 flow 가 `scroll_end` 로 composer
  를 화면에 유지한다(새 입력/출력마다 `follow_tail`).
- **main panel(본문, `height: auto`)**: `transcript` 와 `help 뷰` 의 **상호 배타 state machine**
  (textual `ContentSwitcher`, `tui/main_panel.py`). 평소엔 transcript(명령 echo·결과가 위→아래로
  쌓이는 chat-first 영역)만 보인다. `/help`(또는 F1)이면 본문 전체가 **help 뷰로 전환**되고
  transcript 는 숨는다 — transcript 에 아무것도 append 되지 않는다. Esc 로 transcript 가 그대로
  복원된다.
- **composer = 입력 BAR + 별도 palette surface(inline, 세션 추종)**: `dock` **없음** —
  content 바로 아래 inline. **transcript / 입력 bar / slash palette 3개가 구조·시각적으로
  분리**된다.
  - **입력 BAR(`#composer-shell`)**: 입력 행+힌트를 감싸는 **단정한 round rule
    (`border: round $brand-border`)** + 위 1칸 `margin` 으로 transcript 와 분리(얇은 1줄
    separator 도 heavy box 도 아닌 중간). `#inputrow`(accent 마커 `›`(cyan) + muted mode pill +
    입력창 — 주인공) + `#hint`(bar 바닥, 항상 보이는 짧은 힌트). 이 bar 가 독립 입력 영역이다.
  - **slash palette(`#palette`)**: 입력 box **안이 아니라** shell **아래의 별도 surface**.
    `/` 는 입력 필드에 typed text 로 들어가지만, 명령 **목록**은 bar 아래에 thin **desaturated**
    accent rule(`$accent-dim`)로 연결된 **compact·auto-height** strip 으로 열린다(`max-height 8` +
    scroll → giant box 금지). 일반 텍스트면 숨김. transcript 와 절대 섞이지 않는다.
  - **입력창은 비어 있다**: in-field placeholder 문구 없음(Claude 식). `/help · / palette · Tab ·
    quit` 안내는 입력창 **안이 아니라** bar 아래 `#hint` row 에 둔다.
  - `/help` 는 별도 **full-VIEW 전환**(MainPanel). **help/탭 뷰에서는 composer bar 가 숨는다**
    (Claude 식 "help 모드" — 입력창이 help 본문 아래 어정쩡하게 남지 않음). Esc/Tab 안내는 help
    본문 상단이 담당하고, Esc 로 닫으면 bar 복원 + 입력 focus. help 탭 색은 절제 — active 탭은
    loud reverse-cyan 블록이 아니라 작은 `▸` accent 마커 + bold, inactive 는 muted, divider 는
    `$brand-border`(덜 공격적). accent 는 포인트에만.

## 3b. 키 바인딩 / 상호작용

| 키 | 동작 |
| --- | --- |
| `/` | command palette(입력 아래 inline) 오픈·필터 |
| `Tab` / `Shift+Tab` | palette 자동완성·순환 (`/he` + Tab → `/help`) · help 뷰 열림 시 **탭 제자리 전환**(append 없음) |
| `↑` / `↓` | palette 후보 순환 |
| `Enter` | 정확한 명령 실행, 후보 선택 중이면 선택 명령 실행 |
| `Esc` | help 뷰 닫기(→ transcript 복원) → palette 닫기 → agent 모드 해제 (순서) |
| `F1` | help 뷰 토글 |
| `^L` / `^R` / `^C` | 로그 지우기 / issue line 새로고침 / 종료 |

- **slash palette**: 평소 숨김, `/` 입력 시 (inline composer 안의) 입력 바로 위 얇은 inline 메뉴로 필터·하이라이트.
- **`/help`**: 모달/사이드패널/아코디언 아님, **transcript 누적도 아님** → 본문 영역을 **help 뷰로
  통째로 전환**한다(transcript 숨김, `tui/help_panel.py` + `tui/main_panel.py`). 사용자는 "지금 help
  화면을 보고 있다" 고 느낀다. 상단 탭 strip `Help · General · Commands · Agents`, **기본 General**,
  한 번에 활성 탭만 표시(Tab 으로 **제자리** 전환 — 같은 위젯 re-render, append 없음). modal 이 아니라
  in-app 패널이므로 `screen_stack` 길이는 1 그대로다. **composer 는 그 동안에도 help 뷰 바로 아래에
  inline 으로 보인다.** Esc 로 transcript 가 그대로 복원된다(help 흔적 없음).

## 4. slash 명령

| 명령 | 동작 |
| --- | --- |
| `/help` | 본문을 help 뷰로 전환(탭, 기본 General) — transcript 누적 아님, Esc 로 복귀 |
| `/agents` | 에이전트 레지스트리(로그에 inline 출력) |
| `/status` | operator 요약(provider/eval/self-improve/token) — 자세한 상태는 여기서 |
| `/runtime` · `/doctor` | runtime status · 환경 진단 요약 |
| `/render` | 렌더 readiness — true-raster vs fallback · avatar asset(icon/portrait) · 권장 터미널 |
| `/blocked` | 반복 실패 에스컬레이션 목록 — 왜 안 되나 · 대안 · 다음 단계 · operator 필요 여부 |
| `/pm-agent` | **Product intake gate** — 요구 보강·결정 질문·spec packet handoff(엔지니어링 앞단). [`product-intake-gate.md`](product-intake-gate.md) |
| `/planning-agent`·`/backend-agent`·`/security-agent`·`/ops-observer` | 에이전트 모드 진입 **stub** |

`/agents` 는 platform-runtime-engineer / knowledge-engineer / ops-observer 를 포함한 28 역할의
책임·커밋권한·vault lane·색을 보여준다. 역할 호출 계약은 [`agent-invocation-contract.md`](agent-invocation-contract.md),
공통 vault 색/메타데이터 정책은 [`obsidian-agent-color-policy.md`](obsidian-agent-color-policy.md).
| `/layout` | (예약) 현재는 help 로 안내 |
| `/clear` | 로그 지우기 |
| `/quit` · **`/exit`** | 종료 (`/exit` 는 `/quit` alias) |

일반 텍스트는 아직 **live submit 미연결** — 안내만 표시한다.

### 반복 실패 에스컬레이션 (조용한 실패 금지)

같은 실패가 **임계값(기본 3, `FORGEKIT_BLOCKED_THRESHOLD` 로 1~5 조정) 이상 반복**되면,
대화에만 남기지 않고 **자동으로 표면화**한다 — `docs/troubleshooting-mandatory.md` 의
"최소 2 surface" 원칙과 맞춘다. 코드 SSoT 는
[`lifecycle/failure_escalation.py`](../apps/forgekit-console/src/forgekit_console/lifecycle/failure_escalation.py).

- **"같은 실패" 기준**: 단순 문자열이 아니라 `FailureSignature(kind, reason, scope)` —
  `renderer` / `import` / `command` / `policy` / `dependency` / `status-surface` 등 kind 로
  묶어, renderer fallback 과 status-surface unavailable 이 절대 섞이지 않는다.
- **임계값 전**: 조용한 advisory 한 줄(`↻ … 2/3 — 아직 시도 중`).
- **임계값 도달 시 자동 mini-RCA**: 증상 · 증거 · 시도한 것 · **왜 안 되나** · **대안 2~3개**
  (kind 별 remedy KB + 호출자 제공 병합) · **다음 단계** · operator 답변/승인 필요 여부.
- **표면(항상 ≥2)**: escalation **ledger**(JSON) + operator **inbox**(JSON, `#승인-대기` 성격) +
  live **console alert**(issue line 이 `● blocked` 배너로 전환, transcript 에 RCA). opt-in 3번째로
  **macOS 알림**(`FORGEKIT_NOTIFY=1`, best-effort). `yule_engineering` troubleshooting ledger 가
  import 가능하면 거기에도 bridge(best-effort).
- **확인**: 콘솔에서 **`/blocked`** — 열린 반복 실패를 signature·횟수·다음 단계·대안과 함께 본다.
  retry/fallback 으로 결국 성공하더라도 반복 구조가 있으면 ledger 에 남는다(조용한 반복 금지).
- **wired producer (UI/render)**: 실패 명령(`KIND_ERROR`)은 `command`/`status-surface` kind 로,
  **`/render` 를 돌렸는데 계속 true-raster 가 아니면** `renderer` kind(reason=`no-true-raster`/
  `lib-unavailable`/`terminal-no-graphics`)로 자동 기록 — 임계값 도달 시 "왜 fallback 인지 +
  대안(그래픽 터미널 / Python 3.10+)" 을 RCA 로 띄우고 issue line 을 `● blocked` 배너로 전환.
  composer/palette 위젯 오류는 명령 실패 경로(`KIND_ERROR`)로 동일하게 capture 된다.

> 경로: ledger/inbox 는 `~/.forgekit/state/`(또는 `$FORGEKIT_HOME/state/`). 모든 IO 는 guarded —
> 알림/bridge/파일 실패가 에스컬레이션 코어를 절대 깨지 않는다. 순수·stdlib 라 bare CI 에서도 동작.

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
  tui/theme.py         브랜드 팔레트 SSoT — cyan/magenta-on-black 명명 상수 + wordmark() + css_variables() (순수)
  tui/render.py        문자열 렌더(welcome/intro-meta/issue-line/hint/mode-pill/help-panel-document/palette) — 순수, theme 토큰 참조
  tui/image_renderer.py 아바타+브랜드 렌더 추상화 — capability 검출·backend 분류(tgp/sixel/halfcell/unicode)·3단 정책(true-raster/managed/hard)·진단·early probe
  tui/render_readiness.py 렌더 readiness(/render) — python·textual-image·terminal·정책·권장 터미널 + 지원 매트릭스 (순수, env 주입)
  tui/brand_panel.py   브랜드 배너 위젯(인트로 워드마크 마크, true raster→텍스트 워드마크)
  tui/halfblock.py     opt-in portrait half-block 렌더(베이크 PNG → Pillow downscale → ▀ 래스터; FORGEKIT_AVATAR=portrait)
  tui/avatar_panel.py  아바타 위젯(선택된 renderer 의 renderable 을 mount, textual)
  tui/header.py        IntroHeader 위젯 — 아바타(좌) + 브랜드/버전/provider/profile/repo(우)
  tui/session_flow.py  SessionFlow(VerticalScroll) — issue·본문·composer·hint 의 top-aligned inline flow + follow_tail
  tui/main_panel.py    MainPanel(ContentSwitcher) — transcript XOR help 뷰 상호배타 전환(height: auto)
  tui/transcript.py    Transcript 위젯 — chat-first 본문(명령 echo·결과만; help 누적 안 함; height: auto)
  tui/help_panel.py    HelpPanel 위젯 — help 뷰(탭 strip + 활성 탭 본문, Tab 제자리 re-render)
  tui/composer.py      Composer 위젯 — 세션 추종 inline 채팅 입력(NOT dock; 강조 rule + palette + mode pill + input)
  tui/keymap.py        키 바인딩 + 힌트 (순수 데이터)
  tui/styles.py        Screen CSS 상수(app.py 분리, 위젯별 CSS 는 각 위젯에)
  tui/palette.py       inline command palette 위젯 (textual, composer 안)
  tui/app.py           Textual App — Claude-Code chat-first compose(session flow) + 상태(mode/palette) + help 뷰 전환 wiring
  app/main.py          `forgekit` 엔트리 (textual 부재 시 graceful degrade)
  assets/avatar/avatar-source.png          source/master(사람 교체용 portrait 원본; 콘솔이 직접 렌더하지 않음)
  assets/avatar/forgekit-avatar.png        display 에셋 128px primary(image-first 표시: tier1 real / tier2 half-block 소스)
  assets/avatar/forgekit-avatar-96.png     display 에셋 96px(보조 후보)
  assets/avatar/bake.py                    source → display PNG 베이크 build-time 도구(crop+contrast+sharpen, Pillow)
  assets/brand/forgekit-banner.png         full 워드마크 master(1916×821, cyan→magenta) — README 도 같은 이미지를 root assets/forgekit.png 로 보관
  assets/brand/forgekit-banner-intro.png   작은 베이크 인트로 배너(≈360px, image-first 브랜드 마크)
  assets/brand/bake.py                     master → 작은 인트로 배너 베이크 build-time 도구(Pillow)
```

- **순수 코어(models/commands/data/tui.render·image_renderer 의 검출·선택·keymap·styles)는 textual 없이 import·테스트 가능.**
  TUI 위젯/앱은 textual 설치 시 pilot 스모크로 검증(`tests/forgekit/test_tui_smoke.py`),
  아바타 capability/선택/fallback 은 `tests/forgekit/test_image_renderer.py`.
- **상태 분리**: input/palette/help/mode 가 섞이지 않는다 — palette 는 순수 상태머신, 본문 뷰(transcript↔help)
  전환은 `MainPanel`(ContentSwitcher), help 활성 탭은 `HelpPanel` 보유, mode 는 app 보유, 렌더는 순수 render 함수.
- status 는 기존 yule 헬퍼를 **best-effort 재사용**(실패 시 alert 로 degrade, 콘솔은 안 죽음).

## 6. 이번 범위 / 범위 밖

**범위(Claude Code clone 디자인):** chat-first **top-aligned** 세로 흐름(작은 **실-이미지** 아바타
intro → issue line → 본문(transcript XOR help 뷰) → **세션 추종 inline composer(NOT dock:bottom,
본문 바로 아래)** → hint), 아바타 **image-first 3-tier**(real raster → image-derived half-block →
text), `/help` 를 **단일 패널 뷰 전환**(모달 아님, transcript 누적 아님), `/exit`(=`/quit` alias),
상단 정보 밀도 완화(operator 상세는 `/status`), 테스트/문서. brain/setup/provider 는 미포함.

**범위 밖(후속):** brain/setup/provider 코어, 실제 live chat loop, Agent Town, macOS 알림,
Discord push, approval inbox 조작, multi-provider session persistence.

## 7. 관련
- [`runtime-operator-surfaces.md`](runtime-operator-surfaces.md) (재사용하는 surface) ·
  [`operations.md`](operations.md) · [`monorepo-structure.md`](monorepo-structure.md)
