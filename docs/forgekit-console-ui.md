# ForgeKit 콘솔 UI — copy / scroll / palette 모델 (Claude parity)

콘솔 TUI 의 입력/복사/스크롤 동작 SSoT. 코드: `tui/app.py` (layout·copy dispatch),
`tui/session_flow.py` (scroll owner), `tui/composer.py`·`tui/palette.py` (docked
composer + 위로 열리는 palette), `tui/transcript_store.py` (copy 모델), `tui/clipboard.py`.

## 1. Layout — docked composer (단일 세션 흐름)
```
IntroHeader        (fixed top banner)
SessionFlow (1fr)  ← 유일한 vertical scroll owner. issue + transcript/help 만.
  #issue
  #main (transcript XOR help, height auto)
#livestatus        (thinking→generating 마커)
Composer           ← 하단 DOCK. palette(위) + 입력 bar + hint.
```
- 입력 bar 는 **항상 viewport 하단에 고정**(Claude). 짧은 세션에서 중앙에 부유하지 않는다.
- palette 가 열리면 composer 가 **위로** 자라 SessionFlow(1fr) 가 위로 밀린다 — 입력 bar 는
  하단 고정, 대화는 그 위로 스크롤. `examples/tui-ux-v2/measurements.txt` [A][C].

## 2. Scroll owner — SessionFlow 단독, gutter 없음
- `SessionFlow` 만 `allow_vertical_scroll=True`. Transcript/Help/Palette/Composer 는 전부
  자체 스크롤 없음(측정값 [B]).
- SessionFlow 의 **scrollbar gutter 를 0** 으로(색으로 숨김이 아니라 size 0) — 내부 pane
  처럼 보이는 세로 스크롤바 줄을 제거하고 터미널 세션처럼 읽히게 한다. 스크롤 자체는
  유지(follow_tail 로 tail 추적).

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
| 입력창 하단 고정 / palette 위로 / 단일 scroll | ✅ 해결됨 | docked composer + SessionFlow 단독 owner |

### 다음 리팩터링 seam (구조 한계를 넘으려면)
터미널 native scrollback + 드래그 복사를 원하면 **alternate-screen full-screen → inline 모드**
전환이 필요하다 (Textual `App.run(inline=True)` 계열). 이는 전체 앱 모델 변경이라 별도 작업:
`app/main.py` 의 `App.run()` 진입점 + 모든 docked/1fr 레이아웃 가정을 inline 높이 모델로
재작성해야 한다. 현재 라운드는 full-screen 구조 안에서 가능한 부분(dock/scroll/palette/copy)
을 끝까지 닫고, 이 한계는 위처럼 정직하게 남긴다.

증거: `apps/forgekit-console/examples/tui-ux-v2/` (SVG 스크린샷 + measurements.txt),
테스트 `test_tui_parity_hotfix2` · `test_tui_smoke` · `test_tui_scroll_copy` · `test_tui_ux_redesign`.

## 5. Paste / attach ingestion (large paste · image)
**근본 원인(실측):** ForgeKit 은 `[Pasted text #N]`/`[Image #N]` 를 **생성하지 않는다**(grep clean).
이건 **host(터미널/IDE/wrapper)** 가 붙여넣기를 가로채 치환한 것이고, ForgeKit 은 placeholder
문자열만 받는다. `PromptArea`(TextArea)는 **진짜 멀티라인 bracketed paste 를 정상 수신**한다
(newline 보존, 더블삽입 없음 — 검증). 즉 "한 문장만 받는다"의 원인은 multiline 미지원이 아니라
**host placeholder + rehydration 경로 부재**였다. 코드: `tui/ingest.py`, `tui/attachment.py`,
`tui/clipboard.py`(image), `tui/app.py`(`on_paste`/submit rehydrate/`/attach`).

- **large text** — `on_paste` 가 placeholder 를 감지하면 OS clipboard(pbpaste)에서 **raw 본문을
  복원**해 composer buffer 를 multiline 으로 rehydrate. 제출 시에도 한 번 더 resolve → provider
  는 **full 본문**을 받는다(placeholder 미전송). 8줄 초과는 transcript echo 만 compact, 실제
  제출/`/copy` 는 full. clipboard 복구 불가면 **정직 blocked**(제출 안 함, 조용한 truncate 없음).
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
- inline 에서도 유지: 입력창 하단 dock · palette 입력창 위 · SessionFlow 단일 owner · `/copy` ·
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
