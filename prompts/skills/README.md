# prompts/skills — Portable Skill 카탈로그

본 디렉터리는 모든 부서 / 역할이 참조하는 **portable skill markdown** 파일을 담는다.

## 디자인 원칙

1. **Portable**: Claude / Gemini / Cursor 어느 런타임에서든 그대로 inline 으로 붙여 쓸 수 있어야 한다. front-matter 외의 도구 specific 토큰은 금지.
2. **Single-purpose**: 한 skill 은 한 작업만. PRD 작성 / OKR 정렬 / 회고 진행 등 lifecycle 의 단일 단위.
3. **Recipe 형식**: When to use / Inputs / Steps / Output / Quality bar — 5 섹션 표준.
4. **No live secret**: 어떤 환경 변수 / 키 / 토큰도 직접 인용 금지. PasteGuard 와 hookify 가 위반을 차단한다.
5. **`github.com/phuryn/pm-skills` 패턴**: 65 skills × 36 workflows 의 카탈로그 구조를 차용. PM 도메인부터 먼저 land 한다.

## 디렉터리 구조

```
prompts/skills/
├── README.md          ← 본 문서
├── pm/                ← Product Management lifecycle (discovery → strategy → execution → GTM)
├── product/           ← 옛 경로 alias (deprecate 예정 — pm 로 통합)
├── hr/                ← 채용 / 온보딩 / 1:1 / 정책
├── finance/           ← 예산 / cost / burn rate
├── sales/             ← 리드 qualification / discovery / health
└── legal/             ← MSA / NDA / DPIA / privacy 정책
```

## 역할 manifest 와의 연결

각 role 의 `prompt.md` 는 "참고 skills" 섹션에 `prompts/skills/<domain>/<skill>.md` 형태로 referencing. governance test (`tests/engineering/test_corporate_structure_governance.py`) 가 referencing 누락 / 깨진 링크를 차단.

## 새 skill 추가 절차

1. `prompts/skills/<domain>/<verb-noun>.md` 생성 (verb-noun 명명 규칙).
2. 5 섹션 (When to use / Inputs / Steps / Output / Quality bar) 작성.
3. 관련 role `prompt.md` 의 "참고 skills" 섹션에 항목 추가.
4. governance test 가 자동 검증.

## 참고
- `github.com/phuryn/pm-skills` — PM lifecycle 65 skills 의 reference 카탈로그.
- `policies/runtime/agents/corporate-org-chart.md` — 부서 / 역할 매트릭스.
