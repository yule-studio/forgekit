# Department Boundary Policy

F15 #126 corporate-structure 후속. 부서 경계가 흐려지는 시점에 무엇을
보고 결정할지를 박는다.

## 1. 부서 분리 원칙

### 1.1 1 C-level → 1 부서 매핑

| C-level | 부서 |
|---|---|
| CTO | engineering-agent |
| CPO | product-agent |
| CMO | marketing-agent |
| CHRO | hr-agent (예정) |
| CFO | finance-agent (예정) |
| CRO | sales-cs-agent (예정) |
| GC | legal-agent (예정) |

전체 매트릭스는 `corporate-org-chart.md` 참조.

### 1.2 product-agent vs marketing-agent — 의도적 분리

겹쳐 보이는 두 부서지만 책임 / 리스크 / 산출물 경로가 다르다.
합치지 않는다.

| 차원 | product-agent (CPO) | marketing-agent (CMO) |
|---|---|---|
| 1차 책임 | 제품 발견 / PRD / OKR / 사용자 리서치 | 그로스 / 콘텐츠 / SEO / 브랜드 |
| 산출물 소비처 | engineering-agent (구현 입력) | 외부 사용자 (게시/캠페인) |
| Discord 채널 라우팅 | `#product-*` | `#marketing-*` |
| 리스크 표면 | 잘못된 우선순위 → 개발 낭비 | 잘못된 카피 → 브랜드/PR 손상 |
| Hard rail 게이트 | PRD 결정 노트 cross-link | brand-manager 게시 전 가드 |

### 1.3 합치고 싶어질 때 멈춤 신호

다음 중 하나라도 해당하면 본 정책 + decision 노트
(`vault/.../decisions/2026-05-11_decision_product-vs-marketing-cpo-cmo-separation.md`)
를 다시 읽고 운영자 명시 승인을 받은 뒤에만 통합한다.

- "A/B test 는 어차피 둘 다 함" → 책임 주체가 다르다.
  product/growth-analyst = 제품 funnel 진단, marketing/growth-marketer =
  채널 캠페인 attribution. 활성화 권한도 분리.
- "리서치는 한 명만 있으면 됨" → user-researcher (제품 사용자) vs
  brand-manager (시장 인식) 는 1차 소스가 다르다.
- "콘텐츠도 PM 이 쓸 수 있음" → content-strategist 의 톤/일관성 보존
  책임이 사라진다.

## 2. 부서 간 협업 매트릭스

| 출발 부서 | 도착 부서 | 협업 예시 | 게이트 |
|---|---|---|---|
| product → engineering | PRD → 구현 분배 | tech-lead 수용 → 분해 |
| product/growth-analyst → marketing/growth-marketer | funnel 진단 결과 → 캠페인 가설 | cross-review 후 운영자 승인 |
| marketing/seo-specialist → engineering/backend | robots.txt/sitemap 변경 | backend-engineer cross-review |
| marketing/brand-manager → legal-agent | PR 위기 응대 초안 | 운영자 승인 시 게시 |
| 모든 부서 → engineering-agent | 인프라/툴/내부 시스템 요청 | 부서별 게이트웨이 통과 |

부서 간 직접 채널 침범은 금지. 게이트웨이를 거친다.

## 3. 새 부서/역할 추가 절차

`corporate-org-chart.md` 5 단계 절차 준수:

1. 본 정책에 신규 부서 줄 추가 + 1.1 표 갱신
2. `agents/<부서>-agent/` 생성, `manifest.json` (F11 schema) 등록
3. 역할별 `manifest.json` + `prompt.md`
4. 부서 경계 결정 노트 (`vault/.../decisions/<날짜>_decision_<부서>-boundary.md`)
5. governance 테스트 보강 (`tests/engineering/test_corporate_structure_governance.py`)

## 4. 본 정책의 갱신 트리거

- C-level 매트릭스 1.1 표 변경 시
- product vs marketing 분리 1.2 표 변경 시
- 부서 간 협업 매트릭스 2장 변경 시

변경 시 PR 본문에 본 파일 + decision 노트 링크를 함께 첨부.
