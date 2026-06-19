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
