# Console parity — 진행중 motion + selection visibility + transcript readability

ForgeKit operator cockpit 을 Claude Code 에 더 가깝게 — **fake parity 가 아니라 실제 구조**로.

## 무엇이 바뀌었나 (real, no fake)

1. **진행중 motion** — process feed 의 active(running) step 이 정적 `▸` 에서 **amber
   braille spinner + 실시간 elapsed `(X.Xs)`** 로 바뀜. spinner 는 `frame` 으로
   advance 하고 elapsed 는 **실제 monotonic clock** 에서 측정. motion 은 step 이 진짜
   running 일 때만 — idle 애니메이션도, fake typing 도 없음 (`render.SPINNER_FRAMES` +
   `app._motion_tick`).
2. **selection visibility** — selection 하이라이트가 quiet accent-dim(#2f6f7a, 배경과
   너무 비슷) 에서 **saturated SELECTION_BG(#2d72b8)** 로. 배경 대비 3.40:1 → **3.90:1**
   로 올라가 "선택됐는지" 가 바로 보임. composer + cross-widget selection 동일 토큰.
3. **transcript readability** — user 질문 head 가 **bold** = 각 turn 의 anchor.

## Evidence (재생성: `python3 _regen.py`)

- `progress-selection-evidence.txt` — mounted app + 실제 clock 에서 실측한 runtime 값
  (selection 색/대비, motion 3 frame, echo head). 가짜 0.
- `running-feed.svg` — `export_screenshot` 으로 캡처한 **실제 렌더** — 활성 feed 의
  amber spinner + "Generating" 가 SVG 에 그대로 들어 있음.

## 재현 테스트

- `tests/forgekit/test_tui_progress_selection_lane.py` — motion tick(running 에서만 advance),
  selection 토큰, echo head bold, inline 비회귀.
- `tests/forgekit/test_tui_process_feed.py` — active=spinner+amber+live elapsed, 정적 fallback.
- `tests/forgekit/test_tui_selection_contrast.py` · `test_tui_transcript_selection.py` —
  selection 배경 대비 floor (runtime property).
