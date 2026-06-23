"""Armory intake — the EVALUATED external-candidate set (this wave's curated decisions).

The adoption *framework* already exists (``armory.candidate.AdoptionReview`` /
``adopt_candidate`` — the 8-field artifact + ≥3-axis review + adopt-now/collect-first/hold
gate). What was missing is the actual evaluation of the real candidate set, so this module
applies that existing framework to the wave's named candidates — it does NOT define a new
adoption model (no duplicate).

Each candidate carries a real ``AdoptionReview`` (8축 + PM/tech-lead/specialist 3축) paired
with its ``ArmoryCandidate`` catalog contract. ``adopt_candidate`` couples them: only
adopt-now + a valid contract yields a registrable SkillSpec — **adopted ≠ equipped/installed**.
collect-first keeps evidence (no activation); hold records the block. No fake adoption.

ponytail verdict (lean): a data registry, not a new layer — reuses armory.candidate end to
end. Lives in the console app (operator-curated data), like the company-governance examples.
"""

from __future__ import annotations

from typing import Tuple

from armory.candidate import (
    ADOPT_NOW,
    AXIS_PM,
    AXIS_SPECIALIST,
    AXIS_TECH_LEAD,
    COLLECT_FIRST,
    HOLD,
    AdoptionResult,
    AdoptionReview,
    ArmoryCandidate,
    AxisReview,
    adopt_candidate,
)
from armory.models import KIND_MCP, KIND_SKILL, KIND_TOOL


def _axes(pm, tl, spec_role, spec_pos, *, pm_pos, tl_pos, pm_r, tl_r, spec_r) -> Tuple[AxisReview, ...]:
    return (
        AxisReview(AXIS_PM, "product-manager", pm_pos, pm_r),
        AxisReview(AXIS_TECH_LEAD, "tech-lead", tl_pos, tl_r),
        AxisReview(AXIS_SPECIALIST, spec_role, spec_pos, spec_r),
    )


# Each entry: (ArmoryCandidate contract, AdoptionReview). disposition() is derived by the
# review (most-conservative axis, gated on completeness) — we do not hardcode it.
_CANDIDATES: Tuple[Tuple[ArmoryCandidate, AdoptionReview], ...] = (
    # ── adopt-now : Vale (full contract so adopt_candidate yields a SkillSpec) ──
    (
        ArmoryCandidate(
            id="vale", name="Vale", kind=KIND_TOOL, category="docs",
            summary="style-guide(YAML) 기반 prose 린트 — 문서 문체/용어 일관성을 기계로 강제",
            signals=("vale", "prose lint", "문체 검사", "style guide", "용어 일관성", "doc lint"),
            when_to_use=("vault/README/가이드 문체·용어 일관성을 CI/로컬에서 점검",),
            when_not_to_use=("코드 린트(언어별 린터)", "문서 구조 검증(vault-curate frontmatter)"),
            unsafe_boundary=("외부 서버로 문서 본문 송신 금지(로컬 린트만)", "자동 수정 강제 금지(제안만)"),
            capability_note="prose/style linting (style-guide enforced)",
            install_requirements=("brew install vale", "vale sync (.vale.ini styles)"),
            commands=("vale .",), verification=("vale --version", "vale ."),
            related_weapons=("vale",), related_loadouts=("doc-quality-lint-local",),
            related_roles=("technical-writer", "knowledge-engineer"),
            source="operator", source_ref="https://github.com/errata-ai/vale"),
        AdoptionReview(
            candidate_id="vale",
            current_pain="vault/문서 품질이 사람 리뷰에만 의존 — 문체/용어 일관성을 기계로 강제할 표준 도구 부재.",
            expected_benefit="style-guide 기반 prose 린트를 CI/로컬에서 결정적으로 강제 — 문서 회귀를 사람 전에 잡음.",
            overlap_with_existing="built-in docs-quality 스킬은 구조/명료성 위주 — Vale 는 style-guide 강제(보완재). 중복 아님.",
            operational_cost="단일 Go 바이너리 + style 패키지 sync. 런타임 의존 없음 — 가장 낮음.",
            maintenance_risk="활발히 유지(errata-ai), config 안정. 낮음.",
            provider_runtime_fit="provider-neutral CLI — 어느 backend 든 호출. toolchain 버전 관리 가능.",
            governance_security_impact="로컬 파일만, 네트워크 없음(style sync 제외). 위험 낮음.",
            adopt_timing_reason="비용/위험 최저 + 문서 품질은 핵심 가치 → 지금 카탈로그화(단 미설치=adopted≠equipped).",
            axis_reviews=_axes(
                "product-manager", "tech-lead", "knowledge-engineer", ADOPT_NOW,
                pm_pos=ADOPT_NOW, tl_pos=ADOPT_NOW,
                pm_r="문서 품질은 vault 핵심 — 즉시 카탈로그화 가치 충분.",
                tl_r="단일 바이너리·config 기반, attach contract 명확 — 도입 리스크 최소.",
                spec_r="knowledge-engineer: style-guide 로 '왜' 깊이/용어 강제가 vault 규칙과 정합."),
        ),
    ),
)


def _cf(cid, name, kind, source, pain, benefit, overlap, cost, risk, fit, gov, timing,
        spec_role, spec_pos=COLLECT_FIRST, pm_pos=COLLECT_FIRST, tl_pos=COLLECT_FIRST,
        pm_r="", tl_r="", spec_r=""):
    """Build a (lightweight contract, review) pair for a non-adopt-now candidate."""

    cand = ArmoryCandidate(id=cid, name=name, kind=kind, category="docs" if kind == KIND_TOOL else "",
                           summary=benefit[:80] or name, source="operator", source_ref=source)
    review = AdoptionReview(
        candidate_id=cid, current_pain=pain, expected_benefit=benefit, overlap_with_existing=overlap,
        operational_cost=cost, maintenance_risk=risk, provider_runtime_fit=fit,
        governance_security_impact=gov, adopt_timing_reason=timing,
        axis_reviews=(
            AxisReview(AXIS_PM, "product-manager", pm_pos, pm_r),
            AxisReview(AXIS_TECH_LEAD, "tech-lead", tl_pos, tl_r),
            AxisReview(AXIS_SPECIALIST, spec_role, spec_pos, spec_r),
        ))
    return cand, review


_REST: Tuple[Tuple[ArmoryCandidate, AdoptionReview], ...] = (
    _cf("proselint", "proselint", KIND_TOOL, "https://github.com/amperser/proselint",
        "문서의 상투구/중복/약한 표현을 잡을 보조 린터 부재.",
        "Vale 보완 — 검증된 영어 prose 규칙(중복/jargon) 추가 커버.",
        "Vale 규칙과 상당 부분 겹침 — Vale 채택 후 한계 드러나면 보강.",
        "Python 패키지 — Python 런타임 의존(Vale 대비 추가 부담).",
        "유지보수 빈도 낮은 편 — 중간.", "CLI, provider-neutral. loadout optional 멤버로 적합.",
        "로컬 파일만 — 위험 낮음.", "Vale 로 시작 후 효과 측정 → 동시 도입은 과함.",
        "knowledge-engineer",
        pm_r="Vale 먼저, 효과 측정 후 보강.", tl_r="규칙 중복 — 한계 evidence 누적 후.",
        spec_r="loadout optional 로만 노출, 강제 X."),
    _cf("write-good", "write-good", KIND_TOOL, "https://github.com/btford/write-good",
        "수동태/약한 표현 등 흔한 글쓰기 안티패턴을 기계로 못 잡음.",
        "가벼운 영어 문체 힌트 — 빠른 1차 패스.",
        "Vale/proselint 와 규칙 겹침 + naive 라 정확도 낮음.",
        "npm 패키지 — Node 런타임 의존(멀티 Node 도구 부담 누적).",
        "유지보수 정체(최근 커밋 드묾) — 중상.", "CLI지만 Node 의존이 Vale 단일바이너리보다 불리.",
        "로컬 파일만 — 위험 낮음.", "정확도/유지보수 약함 → 즉시 도입 가치 낮음, 근거만 누적.",
        "knowledge-engineer",
        pm_r="즉시 도입 가치 낮음 — 근거만.", tl_r="Node 의존+유지보수 정체 — Vale 한계 드러나면 재평가.",
        spec_r="Vale 로 대체 가능, evidence 만."),
    _cf("alex", "alex", KIND_TOOL, "https://github.com/get-alex/alex",
        "둔감/배제적 표현(insensitive writing)을 사람이 일일이 못 잡음.",
        "포용적 글쓰기 자동 점검 — 공개 문서/README 톤 관리.",
        "Vale 의 inclusive-language 스타일 팩과 부분 중복.",
        "npm 패키지 — Node 런타임 의존.", "유지보수 양호하나 영어 전용 — 한국어 vault 엔 제한적. 중간.",
        "CLI, provider-neutral. 한국어 비중 높은 vault 엔 적용 범위 좁음.",
        "로컬 파일만 — 위험 낮음.", "공개 문서 톤엔 가치 — 한국어 비중 고려해 선검증.",
        "knowledge-engineer",
        pm_r="공개 문서 톤엔 가치 — 적용 범위 검증 먼저.", tl_r="Vale inclusive 팩과 비교 evidence 필요.",
        spec_r="영어 산출물 한정 optional."),
    _cf("textlint", "textlint", KIND_TOOL, "https://github.com/textlint/textlint",
        "한국어 포함 문서에 플러그형 린터(직접 규칙 작성) 부재.",
        "플러그인으로 한국어/마크다운 규칙까지 확장 — Vale 약점 보완.",
        "Vale 와 목적 겹치나 plugin 모델/한국어 커버는 차별점.",
        "npm + 플러그인 다수 — 설정/유지 부담 가장 큼.", "코어 유지되나 플러그인 품질 편차 — 중상.",
        "CLI. 한국어 vault 엔 Vale 보다 적합할 수 있음(검증 필요).",
        "로컬 파일만 — 위험 낮음.", "한국어 커버 매력 — 설정 부담 커 PoC 먼저.",
        "knowledge-engineer",
        pm_r="한국어 커버 가능성 매력 — PoC 먼저.", tl_r="플러그인 의존 트리 평가 후 — 지금 adopt 과투자.",
        spec_r="한국어 규칙 PoC 로 Vale 와 비교."),
    _cf("ponytail", "ponytail", KIND_SKILL, "https://github.com/example/ponytail",
        "새 모듈/계층 추가 시 과설계를 막을 일관된 단순성 검토 렌즈가 코드화 안 됨.",
        "ponytail 식 단순성 검토(keep/simplify/use-existing/reduce-surface/reject)를 표준화.",
        "ForgeKit 은 이미 council/consult + ponytail consult 모듈 보유 — 상당 부분 겹침.",
        "검토 절차라 도입 부담 낮으나 repo 실체/성숙도 불명확.",
        "repo 유지보수 불확실 — 외부 의존보다 내부 스킬화가 안전.",
        "skill(절차) — provider-neutral, 단 외부 repo 채택보다 내부화가 fit.",
        "검토 절차라 외부 코드 실행 없음 — 위험 낮음.",
        "이미 consult 렌즈로 사용 중 — 외부 repo 채택은 근거 누적 후.",
        "backend-engineer",
        pm_r="단순성 렌즈는 이미 우리 lane 적용 중 — 외부 채택보다 evidence.",
        tl_r="council/consult 와 겹침 — 외부 repo 의존 만들 이유 약함.",
        spec_r="내부 스킬화로 충분 — 외부 도입은 보류."),
    _cf("context7", "Context7", KIND_MCP, "https://github.com/upstash/context7",
        "LLM 이 라이브러리 최신 API 를 몰라 환각 — 버전 맞는 docs 주입 경로 부재.",
        "최신 라이브러리 docs/snippet 을 MCP 로 주입 — 코드 생성 정확도 향상.",
        "Nexus 는 내부 지식 — 외부 라이브러리 docs 는 미커버라 보완재(역할 경계 정리 필요).",
        "MCP 서버 + (호스티드) 의존/키 — 외부 서비스 의존 도입.",
        "3rd-party 호스티드 의존 — 가용성/정책 변화 리스크 중상.",
        "MCP → claude/codex/gemini(mcp-host) attach. ollama 제외.",
        "외부 서버로 쿼리 송신 — outbound/데이터 노출 검토 필요(integrations/mcp 가드 경유).",
        "유망하나 호스티드/키/outbound 가드 정리 전엔 adopt 불가 — PoC 먼저.",
        "platform-runtime-engineer",
        pm_r="환각 감소 효과 크다 — 가능하면 빨리.", tl_r="호스티드 의존+outbound 정책 정리 전 adopt 불가.",
        spec_r="integrations/mcp 가드+키 처리 검증 후."),
    _cf("mcp-fetch", "MCP Fetch (official)", KIND_MCP,
        "https://github.com/modelcontextprotocol/servers/tree/main/src/fetch",
        "에이전트가 임의 URL 본문을 표준 경로로 가져올 reference MCP 미배선.",
        "공식 reference 서버라 신뢰도 높은 fetch capability — 리서치 보강.",
        "discovery sources(RSS/HN/GitHub)와 일부 겹침 — 임의 URL fetch 는 미커버.",
        "공식 서버 설치/attach — 경량이나 MCP host 연결 필요.", "공식 레포 유지보수 양호 — 낮음.",
        "MCP, mcp-host attach. provider-neutral.",
        "임의 URL fetch = SSRF/내부망 위험 — allowlist/sandbox 정책 필수.",
        "유용하나 SSRF allowlist 설계가 adopt 전제 — 그 전까지 근거만 누적.",
        "security-engineer",
        pm_r="리서치엔 유용하나 보안 가드 선행.", tl_r="SSRF allowlist 정책 설계 후 재평가 — 지금 adopt 위험.",
        spec_r="outbound allowlist 설계 전까지 collect-first(미활성)."),
    _cf("mcp-memory", "MCP Memory (official)", KIND_MCP,
        "https://github.com/modelcontextprotocol/servers/tree/main/src/memory",
        "세션 간 기억의 표준 MCP 경로 부재.", "공식 memory 서버로 표준화된 기억 capability.",
        "Nexus(vault)+troubleshooting ledger+claude-mem 으로 이미 기억 레인 보유 — 중복 큼.",
        "서버 설치/attach + 저장소 관리 — 기존 기억 레인과 이중화.",
        "공식 유지보수 양호하나 기억 SSoT 와 충돌 위험 — 중간.",
        "MCP attach 가능하나 우리 기억 SSoT 는 vault/ledger 라 fit 약함.",
        "기억 저장 위치/내용 거버넌스 — 기존 메모리 정책과 정합 필요.",
        "기억은 vault/ledger 로 충당 — 지금 아님, 한계 드러나면 재평가.",
        "platform-runtime-engineer",
        pm_r="기억은 이미 충당 — 굳이 지금 아님, 근거만.", tl_r="Nexus/ledger 와 SSoT 중복 — 한계 드러나면 재평가.",
        spec_r="우리 기억 모델과 비교 evidence 만."),
)

_HOLD: Tuple[Tuple[ArmoryCandidate, AdoptionReview], ...] = (
    _cf("mcp-filesystem", "MCP Filesystem (official)", KIND_MCP,
        "https://github.com/modelcontextprotocol/servers/tree/main/src/filesystem",
        "에이전트 파일 접근을 표준 MCP 로 노출하려는 유혹.", "표준 파일 접근 capability.",
        "executor 가 이미 파일 도구 보유 — 기능 거의 전부 중복.",
        "추가 서버 + 권한 범위 관리.", "우리 가드와 충돌 관리 비용.",
        "ForgeKit 경로 안전(git_path_safety)과 경합.",
        "광범위 FS 접근 MCP 는 git-write hard rail/경로 안전 SSoT 우회 — 거버넌스 충돌(금지).",
        "기존 파일 도구로 충분 + 가드 우회 위험 → 제외.",
        "security-engineer", spec_pos=HOLD, pm_pos=HOLD, tl_pos=HOLD,
        pm_r="추가 가치 없음.", tl_r="경로 안전 hard rail 우회 — 불가.", spec_r="broad FS MCP 가드 우회 — hold."),
    _cf("mcp-git", "MCP Git (official)", KIND_MCP,
        "https://github.com/modelcontextprotocol/servers/tree/main/src/git",
        "git 작업을 MCP 로 노출하려는 유혹.", "표준 git capability.",
        "git-write 를 git_path_safety/repo_write_policy 로 강하게 통제 — 전면 중복.",
        "추가 서버 + 권한.", "git hard rail 과 이중 경로 유지 비용.",
        "우리 git 거버넌스와 정면 충돌.",
        "MCP git write 는 `git -C + 명시 pathspec` hard rail/commit-governance 우회 — 금지.",
        "git 안전은 핵심 가드 — 외부 MCP 로 우회 불가.",
        "security-engineer", spec_pos=HOLD, pm_pos=HOLD, tl_pos=HOLD,
        pm_r="git 안전은 핵심 가드.", tl_r="repo_write_policy SSoT 우회 — 금지.", spec_r="hard rail 충돌 — hold."),
    _cf("mcp-sequential-thinking", "MCP Sequential Thinking (official)", KIND_MCP,
        "https://github.com/modelcontextprotocol/servers/tree/main/src/sequentialthinking",
        "구조화된 단계적 추론을 도구로 강제하려는 유혹.", "단계적 사고 스캐폴딩.",
        "decision_lane(readiness/council)+모델 자체 추론과 겹침 — 한계 효용.",
        "추가 서버 + 프롬프트 표면 증가.", "효용 대비 유지 비용 — 중간.",
        "MCP attach 가능하나 모델 내장 추론과 중복.",
        "위험 낮으나 도입 정당성(효용) 부족이 더 큰 문제.",
        "추론은 모델/lane 이 이미 함 — 추가 가치 미미.",
        "backend-engineer", spec_pos=HOLD, pm_pos=HOLD, tl_pos=HOLD,
        pm_r="추가 가치 미미.", tl_r="프롬프트 표면만 늘림 — hold.", spec_r="decision_lane 과 중복 — hold."),
    _cf("browser-use", "browser-use", KIND_TOOL, "https://github.com/browser-use/browser-use",
        "실제 브라우저 자동화(로그인-게이트 사이트 등) capability 부재.",
        "LLM 주도 브라우저 자동화 — 일부 리서치/검증 시나리오 확장.",
        "discovery 의 YouTube/Figma/Google planned seam 과 목적 일부 겹침(미연결).",
        "Python + 헤드리스 브라우저(Playwright) — 무거운 런타임.",
        "빠르게 바뀌는 신생 — API 불안정, 중상.",
        "무거운 의존이 ForgeKit 경량 toolchain 과 어긋남.",
        "실 브라우저 = 자격증명/exfiltration/자동 액션 위험 매우 큼 — 강 sandbox/승인 없이 금지.",
        "위험·비용 대비 당장 필요 시나리오 없음 — sandbox 설계 후 재평가.",
        "security-engineer", spec_pos=HOLD, pm_pos=HOLD, tl_pos=HOLD,
        pm_r="당장 필요 시나리오 없음.", tl_r="무거운 의존+불안정 API — hold.", spec_r="브라우저 자동화 위험 — hold."),
)


def intake_candidates() -> Tuple[Tuple[ArmoryCandidate, AdoptionReview], ...]:
    """Every evaluated external candidate as a (catalog contract, adoption review) pair."""

    return _CANDIDATES + _REST + _HOLD


def intake_results() -> Tuple[AdoptionResult, ...]:
    """Run the existing adopt_candidate gate over every candidate (no fake adoption)."""

    return tuple(adopt_candidate(c, r) for c, r in intake_candidates())


def by_disposition(disposition: str) -> Tuple[AdoptionResult, ...]:
    return tuple(r for r in intake_results() if r.disposition == disposition)


def intake_summary() -> dict:
    res = intake_results()
    return {
        "total": len(res),
        ADOPT_NOW: sum(1 for r in res if r.disposition == ADOPT_NOW),
        COLLECT_FIRST: sum(1 for r in res if r.disposition == COLLECT_FIRST),
        HOLD: sum(1 for r in res if r.disposition == HOLD),
        "adopted_specs": [r.candidate_id for r in res if r.adopted],
    }


__all__ = ("intake_candidates", "intake_results", "by_disposition", "intake_summary")
