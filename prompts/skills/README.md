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

## 다른 "skill" 개념과의 구분

이 디렉터리(`prompts/skills/`)는 런타임에 inline 으로 붙이는 **portable prompt recipe**(도메인 워크플로우)다.
이것과 별개로, ECC 레지스트리(`agents/<agent>/skills/*.md`) + grant(`agents/grants/slash-command-grants.json`)는
Claude Code/Codex 의 **harness 스킬/슬래시 명령어**로 생성 투영된다(이슈 #185). 둘은 보완 관계 —
전자는 프롬프트 재료, 후자는 harness 가 실제로 호출하는 슬래시 단위다. harness 쪽 상세는
[`docs/agent-slash-commands.md`](../../docs/agent-slash-commands.md).

## 참고
- `github.com/phuryn/pm-skills` — PM lifecycle 65 skills 의 reference 카탈로그.
- `policies/runtime/agents/corporate-org-chart.md` — 부서 / 역할 매트릭스.
- `docs/agent-slash-commands.md` — harness 슬래시 명령어 / 스킬 / 플러그인 (#185).
