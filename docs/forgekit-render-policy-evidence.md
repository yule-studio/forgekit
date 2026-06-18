# forgekit 렌더 정책 — 진단 evidence

WT1/WT2/WT3 정책(true-raster vs managed-fallback)의 실측 근거. `FORGEKIT_DEBUG_RENDERERS=1`
이 보여주는 한 줄(`render.renderer_debug_line`) + `/render` readiness 기준.

## 환경별 진단 (renderer debug line)

| # | 환경 | avatar | brand | lib |
| --- | --- | --- | --- | --- |
| 1 | VS Code 통합 터미널 (Python 3.13 `.venv-console`) | `avatar-mark (managed-fallback)` | `brand-text (managed-fallback)` | `ok:unicode` |
| 2 | true-raster 시뮬 (tgp backend) | `tgp (true-raster)` | `tgp (true-raster)` | `ok:tgp` |
| 3 | bare 환경 (Python 3.9 메인 `.venv`, textual-image import 깨짐) | `avatar-mark (managed-fallback)` | `brand-text (managed-fallback)` | `✗ ImportError: NoneType` |

원본 라인:

```
# (1) VS Code, 3.13
renderers · avatar=avatar-mark (managed-fallback) · brand=brand-text (managed-fallback) · cap=term_program=vscode · lib=ok:unicode

# (2) simulated tgp (true raster)
renderers · avatar=tgp (true-raster) · brand=tgp (true-raster) · cap=term_program=vscode · lib=ok:tgp

# (3) bare, 3.9 (textual-image import fails)
renderers · avatar=avatar-mark (managed-fallback) · brand=brand-text (managed-fallback) · cap=term_program=vscode · lib=✗ ImportError: cannot import name 'NoneType' from…
```

## before / after 정책 비교

| 항목 | before | after (이 작업) |
| --- | --- | --- |
| 비-raster avatar | textual-image halfcell/unicode "real-image" 라고 표시 → 도트로 깨짐 | **managed-fallback = 깔끔한 brand 배지(fk)** — 도트 portrait 강행 안 함 |
| 비-raster brand | 동상 | **managed-fallback = cyan→magenta 워드마크** |
| 진단 표기 | `avatar=real-image`(거짓 양성) | `avatar=<backend> (<policy>)` — true-raster/managed-fallback/hard-fallback |
| readiness | 없음 | `/render` 가 debug 없이 readiness + 권장 터미널 표시 |
| true raster | lazy import 로 capable 터미널도 halfcell 로 떨어짐 | 엔트리포인트 `prime_image_backend()` 로 Textual 시작 전 backend 확정 |

## 결론

- **VS Code 통합 터미널 = managed fallback**(배지/워드마크). sixel/tgp 무응답 → halfcell/unicode,
  즉 true raster 아님. 깔끔한 대체로 운영.
- **true raster 권장 = iTerm2 / WezTerm / Kitty** (+ Python 3.10+ console env). 시뮬에서
  `tgp (true-raster)` 확인. 실제 GUI 교차검증은 사용자가 해당 터미널에서 아래 절차로 수행.

## 사용자 직접 교차검증 절차

```bash
# Python 3.10+ console env
python3.13 -m venv .venv-console
.venv-console/bin/pip install -e 'apps/forgekit-console[image]'

# 1) readiness 먼저 (debug 불필요)
.venv-console/bin/forgekit        # 콘솔에서 `/render` 입력

# 2) backend 직접 확인
FORGEKIT_DEBUG_RENDERERS=1 .venv-console/bin/forgekit   # intro 아래 한 줄
```

- iTerm2/WezTerm/Kitty 에서 `avatar=sixel (true-raster)` 또는 `avatar=tgp (true-raster)` 가
  뜨면 진짜 픽셀 이미지. VS Code 에서 `avatar=avatar-mark (managed-fallback)` 면 정상(설계대로).
