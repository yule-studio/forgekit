# ForgeKit 콘솔 UI — copy / scroll / palette 모델 (Claude parity)

> **bare `forgekit` = inline 기본** (Claude-Code 처럼 기존 터미널 흐름 안 — alt-screen 미사용,
> native scrollback/선택, 터미널 기본 배경 비침). `--full` / `FORGEKIT_UI_MODE=full` 은
> alt-screen escape hatch. SSoT: `tui/ui_mode.py` (`resolve_ui_mode` 기본값 inline).
>
> **terminal-native 배경:** Screen·composer·palette·prompt_area background = `transparent` —
> 터미널 기본 배경이 이어져 "별도 boxed app 창" 느낌 제거. 기본 텍스트 아바타(`_AVATAR_MARK`)는
> foreground-only 라 검은 박스 없이 렌더. (PNG 그래픽-프로토콜 아바타는 이미지 자체 배경 — 에셋 한계.)

콘솔 TUI 의 입력/복사/스크롤 동작 SSoT. 코드: `tui/app.py` (layout·copy dispatch),
`tui/session_flow.py` (scroll owner), `tui/composer.py`·`tui/palette.py` (docked
composer + 입력창 바로 아래 열리는 palette), `tui/transcript_store.py` (copy 모델), `tui/clipboard.py`.

## 1. Layout — content-driven reading flow + 하단 고정 composer
```
SessionFlow              ← 유일한 vertical scroll owner. intro+issue+transcript/help (composer 제외).
  #intro (IntroHeader)     첫 인상 요소 — flow 의 첫 child 라 대화가 쌓이면 위로 스크롤되어 사라짐
  #issue                   inline: height auto, max-height 100% (content-driven). 현재 mode 라이브 표시
  #main (transcript XOR help, height auto)   full : height 1fr (alt-screen 채움)
  #livestatus            (thinking→generating 마커 · mode 전환 flash)
Composer                 ← inline 에서 dock:bottom. full 에서는 1fr flow 뒤 마지막 child(자연히 하단).
                           입력 bar + palette(입력 바로 아래) + hint(현재 runtime mode 반영).
```
**intro lifecycle:** IntroHeader 는 더 이상 flow 밖 고정 top chrome 가 아니라 **SessionFlow 의 첫
child** 다. 첫 진입엔 보이고(첫 인상), 대화가 쌓여 viewport 를 넘으면 자연히 **위로 스크롤되어
사라진다**(고정 panel 로 공간을 영구 점유하지 않음). inline 은 compact, full 은 hero→compact.

**transcript turn 마커 vocabulary:** 한 turn 을 스캔하기 쉽게 역할별 마커를 둔다 — 사용자 입력은
cyan `›`(`write_echo`), slash 결과는 `» <title>`(`result_block`), **free-text LLM 응답은 magenta
`●`**(`render.RESPONSE_MARKER`, 응답 첫 비어있지 않은 줄에 1회). 예전엔 free-text 응답이 마커 없이
echo 뒤에 본문만 떠서 응답 시작 구분이 어려웠다. 마커는 **렌더 전용** — `/copy` plain-text 엔 미포함.
측정: `test_tui_response_marker`, 증거 `examples/tui-ux/response-marker.txt`.

**mode 전환(Shift+Tab)은 ephemeral:** 예전엔 `_cycle_runtime_mode` 가 매 키프레스마다
`transcript.write(runtime_mode_line)` 해서 대화 로그를 mode 줄로 도배했다. 이제 **transcript append
0** — 현재 mode 는 **교체형 live surface** 에만: `#issue`(`◆ <mode> · routing …`) + 하단 `#hint`
(`▶▶ <mode> mode · …` — 고정 'operator' 아님) + 짧은 `▶▶ <mode> mode on` flash(#livestatus, dwell 후
소멸). `/mode` 명령은 전체 표 유지용으로 남는다. 측정: `test_mode_intro_scroll`.
**inline 누적 흐름(이번 라운드 핵심):** 예전엔 `Screen.-inline #flow { height: 14 }` 로 reading
flow 를 **14줄 고정 박스**로 잘라, 출력이 길어지면 이전 내용이 그 작은 창 밖으로 밀려나 "내용이
날아간다"는 느낌을 줬다. 이제 inline flow 는 **content-driven**(`height: auto; max-height: 100%`):
- 짧은 세션 → flow 가 내용 높이만큼만(예: 2~10줄) — 빈 박스 없음.
- 긴 `/doctor`·`/provider`·`/usage`·긴 paste·긴 응답 → flow 가 **viewport 까지 자라며 누적**
  (고정 14 cap 제거). viewport 를 넘으면 그때 비로소 SessionFlow(단일 owner)가 스크롤되어 이전
  내용이 **사라지지 않고 접근 가능**. 실측 evidence: `examples/inline-accumulating-flow/render-evidence.txt`
  (flow 2→10→16→25, composer 하단 고정, overflow 시 scrollable·max_scroll_y>0).
- composer 는 **항상 viewport 하단**(inline=dock, full=마지막 child). 긴 대화에도 위로 스크롤되어
  사라지지 않는다.
- `/` 입력 시 palette 가 **입력 bar 바로 아래**(flush, gap ≈ 0)에 열리고, composer 가 위로 자란다
  (command area — bounded pane 아님). 측정: `test_tui_palette_below` · `test_inline_accumulating_flow`.
- **매칭은 prefix-first + substring fallback** (`commands/parser.py` `palette_matches`): 이름이
  query 로 시작하는 명령이 있으면 그 set 을 그대로(기존 동작·순서 유지), **하나도 없을 때만**
  substring fallback 으로 넘어가 query 를 포함하는 명령을 surface 한다 — 의미 있는 단어가 빈
  palette 로 dead-end 되던 것을 해소(`/improve`→`self-improve`, `/blue`→`red-blue`,
  `/observer`→`ops-observer`). fallback 은 prefix 결과를 절대 넓히지 않아 회귀 0. 측정:
  `test_palette`·`test_parser`, 증거 `examples/tui-palette-below/palette-matching.txt`.

> **정직한 한계:** 이건 "viewport 까지 누적 + 그 뒤 단일 스크롤"이지 **진짜 terminal-native
> scrollback 누적(print-flow)은 아직 아니다.** Textual inline 은 매 프레임 region 을 다시 그려서,
> region 위로 넘어간 내용은 앱의 scroll buffer 가 갖지(터미널 native history 가 아님). seam 은
> `tui/transcript_sink.py` (TranscriptSink). **이번 라운드: turn boundary 가 sink 로 배선됐다** —
> `app.py` 가 모든 reading-flow turn(slash·free-text·copy·attach·paste)을 `_begin_turn()` 으로 열고
> 완료 시 `_finalize_turn()` 으로 닫아, **끝난 turn 이 sink 가 emit 할 수 있는 실제 unit** 이 됐다
> (`_turns_finalized` 로 런타임 측정 가능 — 증거 `examples/tui-parity-lane/measurements.txt`). 즉
> migration step 1-2(write surface + turn 경계)는 **done**, **PrintFlowSink(step 3: region 위 emit)만
> 여전히 미연결** — Textual inline 이 above-region write primitive 를 노출하지 않아 honest
> `NotImplementedError` 로 둔다(가짜 emit 금지). 측정: `test_tui_console_parity_lane` ·
> `test_inline_accumulating_flow`.

## 2. Scroll model — 읽기 흐름 단일 owner + visible gutter 0
**선택한 모델: content-driven reading flow(고정 박스 아님) + 단일 reading-flow scroll owner +
gutter 없음** (inline 모드는 추가로 alt-screen/mouse capture 제거 — §6). flow 는 viewport 까지
자라며 누적하고 그 뒤에만 SessionFlow 가 스크롤한다(§1). 진짜 terminal-native scrollback
누적(print-flow)은 다음 단계 seam(§6 · `tui/transcript_sink.py`).

런타임 감사(실 widget property, CSS 추측 아님 — `examples/tui-scroll/audit.txt`):
- **content scroll owner = `SessionFlow` 단 하나.** Transcript/Help/Palette/Composer 는 전부
  `allow_vertical_scroll=False` (자체 스크롤 없음 — nested content scroll 0).
- **visible gutter = 어느 widget 도 안 그림(full·inline 둘 다 NONE).** SessionFlow·PromptArea·
  Composer 모두 `scrollbar-size-vertical: 0` — 색으로 숨김이 아니라 gutter 자체를 0 폭으로.
  내부 pane 세로바 존재감 제거 → 터미널 흐름처럼 읽힘.
- **입력창(PromptArea)** 만 bounded(max-height 12) + **gutter-less** 내부 스크롤(Claude 의
  입력창도 거대 입력 시 스크롤) — content pane 이 아니라 입력 편의이며 시각적 gutter 없음.
- **입력창 텍스트 선택 contrast**: in-app 선택(`.text-area--selection`)은 브랜드 `$accent-dim`
  배경 + `$text` 전경으로 정합 — transparent 다크 테마 위에서 FG-on-selection 대비 ≈4.75:1 로
  또렷(Textual 기본 저대비 대체). 런타임 property 검증 `test_tui_selection_contrast`, 증거
  `examples/tui-ux/selection-contrast.txt`.
- **transcript(크로스위젯) 드래그-선택 contrast**: full-screen 이 마우스를 캡처하면 Textual 8.x
  의 `screen--selection` 으로 transcript 를 드래그 선택할 수 있다. 이 하이라이트도 브랜드
  `$accent-dim` 배경 + `$text` 전경으로 테마(`theme.css_variables` 의 `screen-selection-background`
  + `styles.py` 의 `color`) — Textual 기본 ~50%-alpha blue(`#0178D47F`, 근-검정에서 저대비) 대체,
  런타임 실측 4.75:1(WCAG AA). 검증 `test_tui_transcript_selection`, 증거+SVG
  `examples/selection-contrast/`. 선택·복사 안내는 mode-aware(§3).
- 스크롤 자체는 유지(follow_tail 로 tail 추적). 테스트 `test_tui_scroll_model`.

## 3. Copy — 명시 UX (`/copy`)
plain-text snapshot 을 OS clipboard 로 **실제** 복사(markup/receipt 제외). 정책: 붙여넣을 때
실제로 원하는 텍스트(plain) 를 복사한다.
```
/copy            마지막 응답 (= /copy last)
/copy turn <n>   n 번째 턴(질문+응답)
/copy block <n>  n 번째 블록
/copy all        전체 transcript
```
- `tui/transcript_store.py` 가 turn/block 단위 plain-text 를 기록 → 긴 transcript 에서도
  복사 대상이 명확.
- empty payload 는 **실패**로 표면화(가짜 "복사됨" 없음). 성공/실패는 pbcopy/xclip rc 기반.
- 실측 round-trip(pbcopy→pbpaste readback): `measurements.txt` [D].

## 4. 구조적 한계 (정직 — 숨기지 않음)
ForgeKit 콘솔은 **full-screen Textual TUI** (alternate screen + mouse capture):
- **마우스 드래그 선택/네이티브 복사 불가** — TUI 가 마우스 이벤트를 캡처한다. 이건 CSS 로
  못 고치는 구조적 제약이다.
  - 우회(터미널 native 선택): **iTerm2 = Option+드래그**, 기타 다수 = **Shift+드래그**.
  - ForgeKit 의 1급 copy 경로는 위 `/copy` (실제 OS clipboard).
- **터미널 native scrollback 없음** — alt-screen 이라 터미널 자체 스크롤백에 세션이 안 쌓인다.
  세션 스크롤은 SessionFlow 가 담당(키/휠).

### Claude 와 여전히 다른 점 + 원인
| 차이 | 단순 미세조정? | 구조 원인 |
| --- | --- | --- |
| 마우스 드래그로 transcript 선택 복사 | ❌ 구조 한계 | alt-screen + mouse capture. `/copy` 로 대체, native 는 Option/Shift+드래그 |
| 터미널 스크롤백에 세션이 남음 | ❌ 구조 한계 | alt-screen. inline(non-alt-screen) 모드로 가야 함(아래 seam) |
| 입력창 하단 고정 / palette 입력 바로 아래 / 단일 scroll | ✅ 해결됨 | docked composer + SessionFlow 단독 owner |

### 다음 리팩터링 seam (구조 한계를 넘으려면)
터미널 native scrollback + 드래그 복사를 원하면 **alternate-screen full-screen → inline 모드**
전환이 필요하다 (Textual `App.run(inline=True)` 계열). 이는 전체 앱 모델 변경이라 별도 작업:
`app/main.py` 의 `App.run()` 진입점 + 모든 docked/1fr 레이아웃 가정을 inline 높이 모델로
재작성해야 한다. 현재 라운드는 full-screen 구조 안에서 가능한 부분(dock/scroll/palette/copy)
을 끝까지 닫고, 이 한계는 위처럼 정직하게 남긴다.

증거: `apps/forgekit-console/examples/tui-ux-v2/` (SVG 스크린샷 + measurements.txt),
테스트 `test_tui_parity_hotfix2` · `test_tui_smoke` · `test_tui_scroll_copy` · `test_tui_ux_redesign`.

> **콘솔 parity lane — 통합 측정 검증:** 6 목표(palette-below / 단일 scroll owner /
> copy·paste·attach 정직 상태 / terminal-native turn finalize / process feed 실측 / progressive
> chunk reveal)를 **한 harness** 로 런타임 측정한다 — geometry(region) + counter(`_turns_finalized`,
> `_gen_chunks`) + 측정 duration + content-derived chunk 수. CSS 추측·가짜 숫자·fake typing 없음.
> 코드 `test_tui_console_parity_lane.py`, 증거 `examples/tui-parity-lane/measurements.txt`
> (+ `measurements-ingest.txt` = paste/clipboard/multiline/image staged 상태) —
> `FORGEKIT_PARITY_EVIDENCE=<path>` 로 재생성. copy 는 pbcopy/pbpaste 존재 시 **실 round-trip**
> 검증(없으면 honest skip), multiline 은 store 보존, large paste 는 raw 보존, image 는 staged_only.
>
> **process feed 어휘(정직):** 실제 행동에만 매핑 — `Routing /x` → `Routed` → `Submitting to <provider>`
> → `Sent` → `Generating`(실 chunk 수) → `Done`. coding shell 이 아니라 provider 콘솔이라 가짜
> `Reading`/`Thinking` 라벨은 두지 않는다(없는 단계를 만들지 않음). 코드 `tui/process_events.py`.

## 5. Paste / attach ingestion (large paste · image)
**근본 원인(실측):** ForgeKit 은 `[Pasted text #N]`/`[Image #N]` 를 **생성하지 않는다**(grep clean).
이건 **host(터미널/IDE/wrapper)** 가 붙여넣기를 가로채 치환한 것이고, ForgeKit 은 placeholder
문자열만 받는다. `PromptArea`(TextArea)는 **진짜 멀티라인 bracketed paste 를 정상 수신**한다
(newline 보존, 더블삽입 없음 — 검증). 즉 "한 문장만 받는다"의 원인은 multiline 미지원이 아니라
**host placeholder + rehydration 경로 부재**였다. 코드: `tui/ingest.py`, `tui/attachment.py`,
`tui/clipboard.py`(image), `tui/app.py`(`on_paste`/submit rehydrate/`/attach`).

- **large text** — `on_paste` 가 placeholder 를 감지하면 OS clipboard(pbpaste)에서 **raw 본문을
  복원**해 composer buffer 를 multiline 으로 rehydrate. 제출 시에도 한 번 더 resolve → provider
  는 **full 본문**을 받는다(placeholder 미전송). clipboard 복구 불가면 **정직 blocked**(제출
  안 함, 조용한 truncate 없음).
- **paste lifecycle** (`tui/paste_store.py`) — 큰 paste(>8줄)는 raw 를 `PasteStore` 에 id 로
  **보존**하고 transcript 에는 compact block `[Pasted #<id> · N lines]` + seam 줄을 보인다.
  `/paste list` · `/paste expand <id>`(본문 표시) · `/paste resend <id>`(raw 재제출) ·
  `/copy paste <id>`(raw 복사 — placeholder 아님). 화면 표현과 저장 payload 분리 → "paste 성공"
  은 raw 보존 시에만. 실측: `examples/tui-qa/qa-results.txt` [C], 테스트 `test_tui_paste_lifecycle`.
- **image** — `/attach <path>` 파일 stage, 또는 `[Image #N]` paste / `/attach` 시 clipboard 이미지
  (pngpaste/osascript/xclip)로 실파일 stage. `/attach status|clear`. honest 상태:
  `staged`/`missing`/`blocked`/`no_attachment`.
- **provider 전송** — console submit 은 **텍스트 전용** → 이미지는 `staged_only`(받아서 실파일로
  보관했으나 **미전송**), 이유 표기. 가짜 업로드 없음. multimodal transport 는 planned.

| 항목 | 상태 |
| --- | --- |
| 멀티라인 실제 paste 수신 | working (TextArea, newline 보존) |
| large paste placeholder → clipboard rehydrate | working (`on_paste` + submit) |
| rehydrate 불가 시 honest blocked | working (제출 안 함) |
| `/attach <path>` 파일 staging | working (staged_only) |
| clipboard 이미지 staging | working where reader 존재(pngpaste/osascript/xclip), 없으면 honest |
| 이미지 provider 전송(multimodal) | **planned** (현재 staged_only) |

증거: `examples/tui-paste/ingestion.txt`, `examples/tui-attach/staging.txt`,
테스트 `test_tui_ingest_attach`.

## 6. Inline UI mode (alt-screen → terminal-flow)
§4 의 두 한계(드래그 선택 불가 / native scrollback 없음)는 **alternate-screen + mouse capture**
때문이었다. **감사(실측)로 확인:** Textual 8.2.7 의 `LinuxDriver`(기본)는 `\x1b[?1049h`(alt-screen)을
쓰지만, **`LinuxInlineDriver`(inline)는 alt-screen 을 쓰지 않고** mouse 도 `self._mouse` 로 gated.
→ inline 모드는 구조적으로 두 한계를 완화한다. 코드: `tui/ui_mode.py`, `app/main.py`, `tui/app.py`
(`inline` flag), `tui/styles.py`(`.-inline`).

| 모드 | 실행 | alt-screen | mouse | scrollback/선택 | 레이아웃 |
| --- | --- | --- | --- | --- | --- |
| **full** (기본) | `App.run()` | 사용(`?1049h`) | 캡처 | 앱 소유 | full-screen, flow 1fr |
| **inline** | `App.run(inline=True, inline_no_clear=True, mouse=False)` | **미사용** | **미캡처** | **터미널 native** | bounded(`#flow height:14`)+compact intro |

- 선택: `forgekit --inline` / `forgekit --full` / `FORGEKIT_UI_MODE=full|inline|auto`. `auto` 는 **정직히
  full**(터미널 선호 추측 안 함 — inline 은 opt-in).
- inline 에서도 유지: 입력창 하단 dock · palette 입력창 바로 아래 · SessionFlow 단일 owner · `/copy` ·
  multiline · help view (테스트 `test_tui_inline_mode`).

### inline 이 truly 닫는 것 vs 남는 것 (정직)
- ✅ **alt-screen 미사용** — 실행 시 터미널 history 안 지워지고, 종료 시(`inline_no_clear`) 최종 프레임이
  scrollback 에 남는다. (driver 실측: `examples/tui-inline/audit.txt`)
- ✅ **mouse=False** — 터미널이 드래그 선택/복사를 직접 처리(앱이 마우스 안 가로챔).
- ✅ **bounded inline block** — full-screen 점유가 아니라 터미널의 일부를 쓰는 inline 도구로 읽힘.
- ⚠️ **live transcript 의 줄단위 native scrollback 누적은 아직** — Textual inline 은 앱 영역을 in-place
  로 재렌더하므로, 완료된 turn 을 한 줄씩 터미널 scrollback 으로 **print** 하려면 print-based 아키텍처
  (앱 위젯이 아니라 stdout 출력 + 최소 prompt region)가 필요하다. 이는 다음 리팩터 seam — 구조적으로
  unavoidable 은 아니나 별도 작업.
- ⚠️ **headless 한계** — 실제 TTY inline 렌더(누적/선택)는 real terminal 에서만 — 자동 harness 로는
  run kwargs/driver 사실/레이아웃까지만 검증(정직).

증거: `examples/tui-inline/` (idle/slash SVG + audit.txt), `examples/tui-full/` (idle/slash SVG),
테스트 `test_tui_inline_mode`.

## 7. Process event feed (실제 행동 타임라인)
"무반응 후 결과가 툭" 대신 **진행 단계**를 보여준다. 단, **실제로 발생한 행동만** — 가짜
`Reading`/`Bash`/`Thinking` 라벨 없음. ForgeKit 은 coding shell 이 아니라 provider 콘솔이라
**ForgeKit 의 실제 행위**(route/submit/generate/copy/attach/paste)에 매핑한다. 코드:
`tui/process_events.py`(순수 모델), `render.process_feed_lines`(line builder), `tui/app.py`(wiring만).

- **모델**: `ProcessEvent`(kind/label/status/started_at/ended_at/**duration_ms**(실측)/detail/
  source/severity) + `ProcessFeed`(turn 단위 group, 최근 window cap). `start`→`finish` 로 duration
  **실측**; instant marker(route_done/copy_*)는 `duration_ms=None`(정직, 가짜 1초 없음).
- **transcript 와 분리**: feed 는 `#livestatus` zone(입력 위, 별도 surface). transcript 본문에
  안 섞임 → `/copy [last|turn|block|all]` 에 event noise **미포함**(검증).
- **실제 순서**:
  - free-text: `submit_start` → `submit_sent` → `generate_start`(실 chunk 수 반영) → `done`.
  - blocked/failed: `submit_blocked`/`error` + **category-specific**(unsupported_in_console /
    no_provider_configured / budget_throttled / transport_error …) — vague "실패" 뭉개기 없음.
  - slash: `route_start` → `route_done` → `done`/`error`.
  - copy: `copy_success`/`copy_failed`(empty payload). attach: `attach_staged`/`attach_blocked`.
    paste: `paste_stored`/`paste_expanded`.
- **chunked, NOT token streaming**: `generate_start` 이벤트 detail 이 실제 append 된 chunk 수를
  반영. 가짜 typing 애니메이션 없음.
- **history**: turn 단위 group(새 입력 시 reset), 최근 ~6개만 노출 — 무한 누적 없음. idle→빈
  surface(0행). clear 시 feed 도 정리.
- **active-state 가시성**: 실행 중(running) 이벤트는 **bold accent `▸` 마커 + 밝은 라벨**로
  도드라지고(=지금 일어나는 일), 끝난 이벤트는 조용한 dim `•` + dim 라벨로 가라앉는다. 순수
  `status==RUNNING` 기반 — 가짜 spinner/typing 아님. running 은 실측 duration 이 없으므로 정직하게
  `…` 만(가짜 ~1초 없음), 끝나면 실측 `(N.Ns)`. 검증 `test_tui_process_feed`(ProcessFeedRenderTests).

증거: `examples/tui-process-feed/timeline.txt`, 테스트 `test_tui_process_feed`.
