# Design-to-Code Asset — engineering-agent 부서 공통 (P0-G)

> **소유:** `engineering-agent` 부서 전체. *icon / logo / favicon* 등 시각 자산을 `product-designer` 가 의미 / 형태 / 컬러 / 비율 / 용도로 정의하고 `frontend-engineer` 가 SVG / 컴포넌트로 구현하는 hand-off 정책.
> **목적:** 같은 자산이 surface 마다 다른 형태로 흩어지거나, 디자이너 의도가 코드 구현 단계에서 사라지는 회귀 차단.
> **출처:** Issue #139 (parent #138) — P0-G 1차.

본 정책은 [`write-ownership.md`](write-ownership.md) §5 의 7 역할 surface 매트릭스 중 `product-designer` ↔ `frontend-engineer` 협업 surface 의 *자산 영역* 만 책임.

## 1. 적용 범위

| 자산 종류 | 본 정책 적용? | 비고 |
| --- | --- | --- |
| **logo** (브랜드 / 제품 로고) | ✅ | SVG source-of-truth |
| **favicon** (브라우저 탭 / 앱) | ✅ | SVG source + PNG export 매트릭스 |
| **icon** (UI inline icon set) | ✅ | SVG component (React/Vue 등) |
| **illustration** (히어로 이미지 등) | 부분 | 단순 = SVG. 복잡 = raster (PNG/WebP) — 본 정책 §4 의 경계 |
| **photography / 실사** | ❌ | raster 전용. 본 정책 적용 X |
| **screenshot / mock-up** | ❌ | raster 전용. 본 정책 적용 X |

## 2. product-designer 의 책임 — 자산 정의

자산이 신규로 등장하면 `product-designer` 가 *코드 작성 전에* 다음 5 차원을 정의한다.

### 2.1 의미 (semantic)

- 자산이 *무엇을 의미하는가*. 어떤 컨텍스트에서 사용되는가.
- naming convention: `<surface>-<intent>-<modifier?>`. 예: `logo-primary` / `icon-status-success` / `favicon-light`.

### 2.2 형태 (form)

- 윤곽 / stroke / 곡률.
- viewBox 의 기본 (예: 24×24, 48×48). 일관된 viewBox 가 inline 사용성을 결정.
- stroke vs fill — 어느 쪽이 source 인지 명시.

### 2.3 컬러 (color)

- 사용 가능한 컬러 토큰 (디자인 시스템 token 이름). hex 직접 박지 않는다.
- light / dark theme 동시 정의. theme 별 별도 자산이면 그것도 명시.

### 2.4 비율 (proportion)

- 권장 표시 크기 (예: header 16 / 24 / 32, app 192 / 512 등).
- aspect ratio 고정 여부. 자르거나 늘릴 수 있는가.

### 2.5 용도 (usage)

- 허용 surface: header / nav / button / favicon / og-image 등.
- 금지 surface: 디자이너가 명시한 사용 금지 컨텍스트 (예: "favicon 으로 logo-primary 사용 금지 — favicon-light 만").
- 접근성 — alt text / aria-label 의 기본 권장 문구.

위 5 차원은 *디자인 소스* (Figma / Sketch 등) 의 metadata 로 1 차 land. 본 레포에 mirror 가 land 될 때는:

- `notes/vault-mirror/.../resources/design/<asset-name>.md` 에 5 차원 명세 노트로 mirror.
- 또는 `assets/design/<asset-name>.md` (실제 자산 코드 옆) 의 README 로 cross-link.

## 3. frontend-engineer 의 책임 — 자산 구현

`product-designer` 의 5 차원이 land 된 *후에* `frontend-engineer` 가 코드로 구현한다.

### 3.1 SVG source-of-truth

- icon / logo / favicon 의 **source-of-truth 는 SVG**. 다른 포맷은 export.
- SVG source 는 `assets/icons/` / `assets/logos/` 등 권한이 명확한 폴더에 land. 한 자산 = 한 파일.
- viewBox / stroke / fill / aria-label 모두 §2 의 5 차원과 정확히 일치 (회귀 test).

### 3.2 컴포넌트 wrapping

- React / Vue 등 프레임워크 컴포넌트로 wrapping 할 때 *디자인 토큰을 prop 으로 받게*. 컴포넌트 내부에서 hex / 직접 컬러 박지 않는다.
- `<Icon name="status-success" />` 처럼 name 기반 lookup 이 권장. 자산 file path 를 caller 가 알 필요 없다.

### 3.3 raster export 매트릭스

favicon / og-image 등 raster 가 필요한 곳:

| 자산 | raster 포맷 | 크기 | 빌드 시점 |
| --- | --- | --- | --- |
| favicon | PNG | 16 / 32 / 48 / 192 / 512 | build 시점 자동 export (스크립트) |
| apple-touch-icon | PNG | 180 | 같음 |
| og-image | PNG / WebP | 1200×630 | 같음 |
| social card | PNG | 800×420 | 같음 |

raster 는 *항상 SVG 에서 자동 생성*. raster 를 source 로 쓰지 않는다 (회귀 test).

## 4. SVG vs raster 경계

| 조건 | 선택 |
| --- | --- |
| 단색 / 평면 아이콘 | SVG |
| 그라데이션 / 부드러운 컬러 전환 | SVG |
| 실사 사진 / 텍스처 | raster |
| 복잡한 illustration (수십 개 path / 그라데이션 다단계) | raster 또는 SVG — 디자이너 판단 |
| 5KB 이하 표현 가능 | SVG 권장 |
| > 50KB 의 SVG | raster 검토 |

경계가 모호한 경우 — `product-designer` 가 §2.5 (용도) 에 SVG / raster 선택 근거 명시.

## 5. 충돌 매트릭스 — 다른 정책과의 관계

| 다른 정책 | 충돌 가능성 | 해소 |
| --- | --- | --- |
| [`write-ownership.md`](write-ownership.md) §5 product-designer surface | 본 정책이 5 차원을 추가 정의 — write-ownership 의 *role-owned* 카테고리에 자산 명세 노트 / SVG source 포함 | 본 정책이 우선. |
| [`obsidian-governance.md`](obsidian-governance.md) §2 naming | mirror 노트의 naming convention 은 obsidian 정책 그대로 (`YYYY-MM-DD_issue-<n>-<kind>-<slug>.md`) | obsidian 정책 우선. 본 정책은 *내부 구조* 만 책임. |
| [`growth-loop.md`](growth-loop.md) §1 resources | 재사용 가능한 자산 노트는 `20-resources/design/` 에 land | growth-loop 정책 우선. |
| 외부 repo 의 design 컨벤션 ([`repo-contract-discovery.md`](repo-contract-discovery.md)) | 외부 repo 가 별도 design system 사용 시 | 외부 repo 의 컨벤션 우선. 본 정책은 본 레포 / yule 제품군에 한정. |

## 6. 본 정책의 코드 land 단계

- 본 commit 은 정책 SSoT 만 land.
- 실제 자산 폴더 구조 (`assets/icons/` / `assets/logos/`) 의 신설 + raster export 자동화 스크립트 + `<Icon>` 컴포넌트는 P0-G 3차 (#141) scope (vault repo / design repo workspace 확인 후 결정).
- 본 레포 (`yule-studio-agent`) 가 *현재* engineering-agent backend 위주라 frontend 자산 의 production code 가 부재. 자산 코드는 frontend 가 생기는 시점에 본 정책에 따라 land — fake success 금지.

## 7. 검증

`tests/engineering/test_policy_stack_completeness.py` (P0-G commit 7 신설) 가 본 정책 파일 존재 + §2 5 차원 / §3 SVG source-of-truth / §4 SVG vs raster 경계 키워드를 lint.

## 8. 변경 이력

| 일자 | 변경 |
| --- | --- |
| 2026-05-14 | 초안 (Issue #139 — P0-G stage 1 정책 8종 1차 land. parent #138.) |

## 관련 문서

- [[CLAUDE]]
- [[governance]]
- [[write-ownership]]
- [[obsidian-governance]]
- [[growth-loop]]
