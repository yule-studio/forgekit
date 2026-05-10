"""tech-lead discussion synthesizer.

마스터 플랜 §7.3을 따른다. ``ContextPack`` + ``DiscussionModeMatch``를
받아 다음을 만들어 준다:

- 사용자에게 보여줄 응답 텍스트
- 어떤 next_actions가 따라붙는지
- 추가 질문이 필요하면 무엇을 물어야 하는지
- 구현 후보로 넘어갔을 때 handoff 메타가 채워지는지

출력 정책:

- 자유 토의 (``DISCUSSION``): 관점 제안 + 사용자 의견 요청 + 필요 시 조사
  로 회수하거나 구현 후보로 끌어올리는 다음 단계 옵션.
- 설계 논의 (``DISCUSSION`` 중에서도 reviewer 키워드 매칭): 영역별 관점
  요약 (backend/frontend/devops 등) + 결정 필요 항목 + tradeoff.
- 조사 단계 (``RESEARCH_ONLY``): 어떤 source/role profile에 따라 무엇을
  더 모을지를 명시. 구현은 별도 요청 전까지 유보.
- 구현 후보 (``IMPLEMENTATION_CANDIDATE``): handoff blob을 채워 두고,
  사용자에게는 "권한 제안을 만들 수 있다"라고 안내.
- clarification (``CLARIFICATION_NEEDED``): 단순 reset — 어디서/무엇을
  알려달라는 짧은 질문.

한 turn의 결과만 담는다. 채널/사용자 단위 stash는 호출자가 한다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Mapping, Optional, Sequence

from .context_pack import ContextPack
from .mode import DiscussionMode, DiscussionModeMatch


@dataclass(frozen=True)
class DiscussionTemplate:
    """한 모드의 응답 템플릿 — 메시지 + 다음 단계 셋.

    호출자가 응답 본문 + ``next_actions`` 리스트를 그대로 노출할 수 있게
    분리해 둔다. 본문 어휘는 한국어이며, gateway가 그대로 Discord에 게시
    한다.
    """

    body: str
    next_actions: Sequence[str] = field(default_factory=tuple)


@dataclass(frozen=True)
class DiscussionSynthesis:
    """tech-lead가 한 turn에서 생산하는 토의 합성 결과.

    - ``mode``: 이 turn의 판단 모드
    - ``rationale``: 왜 그 모드인지 한 줄 — gateway가 status diagnostic
      에 그대로 가져다 쓸 수 있다.
    - ``response_text``: Discord에 그대로 게시할 본문 (header/menu 포함).
    - ``next_actions``: 다음에 사용자/시스템이 취해야 할 행동 bullet.
    - ``open_questions``: clarification 필요 시 사용자에게 물을 항목들.
    - ``implementation_ready``: 구현 후보 모드일 때만 True. handoff
      payload가 별도 :func:`build_implementation_handoff`로 전달된다.
    - ``research_followups``: research_only / discussion 모드에서 추가로
      모아야 할 자료 항목.
    - ``role_perspectives``: 영역별 관점 (backend/frontend/...) — 설계
      논의 출력에 사용.
    - ``blockers``: pack의 blocker + synthesizer가 발견한 blocker.
    - ``knowledge_evidence_block``: ``ContextPack.format_knowledge_evidence_block``
      결과를 미리 만들어 둔 마크다운 블록. operator-facing surface 가
      response_text 외에 따로 PR body / Obsidian decision note 등에
      그대로 끼워 넣을 수 있도록 노출한다.
    - ``knowledge_short_summary``: 한 줄 요약 ("근거 자료 N건 (public ...) — ...").
      운영자가 본문 펼치기 전에 share_scope 분포 + 상위 자료를 한 눈에
      보게 하는 용도. response_text 안에도 같은 한 줄이 포함된다.
    - ``share_boundary``: ``ContextPack.share_boundary_breakdown()`` 결과
      그대로. 외부 publisher (status poster / digest hook) 가 이 turn 의
      자료 boundary 분포를 그대로 쓸 수 있도록 carries through.
    """

    mode: DiscussionMode
    rationale: str
    response_text: str
    next_actions: Sequence[str] = field(default_factory=tuple)
    open_questions: Sequence[str] = field(default_factory=tuple)
    implementation_ready: bool = False
    research_followups: Sequence[str] = field(default_factory=tuple)
    role_perspectives: Mapping[str, str] = field(default_factory=dict)
    blockers: Sequence[str] = field(default_factory=tuple)
    suggested_handoff_role: Optional[str] = None
    knowledge_evidence_block: str = ""
    knowledge_short_summary: str = ""
    share_boundary: Mapping[str, int] = field(default_factory=dict)

    def to_dict(self) -> Mapping[str, object]:
        return {
            "mode": self.mode.value,
            "rationale": self.rationale,
            "response_text": self.response_text,
            "next_actions": list(self.next_actions),
            "open_questions": list(self.open_questions),
            "implementation_ready": self.implementation_ready,
            "research_followups": list(self.research_followups),
            "role_perspectives": dict(self.role_perspectives),
            "blockers": list(self.blockers),
            "suggested_handoff_role": self.suggested_handoff_role,
            "knowledge_evidence_block": self.knowledge_evidence_block,
            "knowledge_short_summary": self.knowledge_short_summary,
            "share_boundary": dict(self.share_boundary),
        }


# ---------------------------------------------------------------------------
# 메인 entry point
# ---------------------------------------------------------------------------


def synthesize_discussion(
    *,
    pack: ContextPack,
    classification: DiscussionModeMatch,
    llm_synthesizer: Optional[object] = None,
) -> DiscussionSynthesis:
    """``pack`` + ``classification``으로 한 turn의 합성 결과를 만든다.

    ``llm_synthesizer``가 주어지면 deterministic 합성을 거쳐 그 결과를
    LLM에 한 번 더 통과시켜 본문을 다듬을 수 있다. 본 모듈에서는 seam만
    노출하고 실제 호출은 향후 milestone에서 연결한다 — LLM이 미설정이거
    나 실패하면 deterministic 결과가 그대로 노출된다.
    """

    mode = classification.mode

    if mode == DiscussionMode.CLARIFICATION_NEEDED:
        synthesis = _synthesize_clarification(pack, classification)
    elif mode == DiscussionMode.RESEARCH_ONLY:
        synthesis = _synthesize_research(pack, classification)
    elif mode == DiscussionMode.IMPLEMENTATION_CANDIDATE:
        synthesis = _synthesize_implementation(pack, classification)
    else:  # DISCUSSION (default)
        synthesis = _synthesize_discussion(pack, classification)

    if llm_synthesizer is None:
        return synthesis
    try:
        polished = llm_synthesizer(synthesis=synthesis, pack=pack)
    except Exception:  # noqa: BLE001 - never let LLM crash the gateway
        return synthesis
    if isinstance(polished, DiscussionSynthesis):
        return polished
    return synthesis


# ---------------------------------------------------------------------------
# 모드별 합성기 — deterministic
# ---------------------------------------------------------------------------


def _synthesize_clarification(
    pack: ContextPack,
    classification: DiscussionModeMatch,
) -> DiscussionSynthesis:
    questions = [
        "어느 화면 / 흐름 / 모듈을 다루고 싶은지",
        "지금 막혀 있는 지점이나 원하는 결과",
        "참고할 수 있는 링크 / PR / 스크린샷이 있는지",
    ]
    body_lines = [
        "받았어요. 다만 지금 내용만으로는 어디부터 봐야 할지 잡히지 않아 한 번만 더 여쭐게요.",
        "",
        "다음 중 한두 가지만 알려 주시면 충분합니다:",
    ]
    body_lines.extend(f"- {q}" for q in questions)
    body_lines.append("")
    body_lines.append(
        "추가 정보가 정리되면 토의로 이어 갈지, 바로 조사로 보낼지를 함께 정해 보겠습니다."
    )
    return DiscussionSynthesis(
        mode=DiscussionMode.CLARIFICATION_NEEDED,
        rationale=classification.rationale,
        response_text="\n".join(body_lines),
        open_questions=tuple(questions),
        next_actions=(
            "사용자 추가 정보 대기",
            "정보가 충분해지면 다시 분류",
        ),
        blockers=tuple(pack.blockers),
        share_boundary=dict(pack.share_boundary_breakdown()),
    )


def _synthesize_research(
    pack: ContextPack,
    classification: DiscussionModeMatch,
) -> DiscussionSynthesis:
    topic = _short_topic(pack.current_message)
    role_short = _role_short_name(pack.role_for_research)
    followups = _research_followups(pack)
    body_lines = [
        f"좋아요. \"{topic}\"는 코드 변경 전에 자료부터 모으는 단계로 받겠습니다.",
    ]
    short_summary = pack.knowledge_short_summary()
    if short_summary:
        body_lines.append(f"_이미 모인 자료: {short_summary}_")
    body_lines.append("")
    body_lines.append("모을 자료 후보:")
    if followups:
        body_lines.extend(f"- {item}" for item in followups)
    else:
        body_lines.append(
            "- 우선 사용자 메시지의 키워드로 1차 검색을 돌리고, 결과를 함께 보면서 좁혀 갈게요."
        )
    evidence_block = pack.format_knowledge_evidence_block(
        heading="이미 vault 에 있는 근거 자료"
    )
    if evidence_block:
        body_lines.append("")
        body_lines.append(evidence_block)
    body_lines.append("")
    body_lines.append(
        "정리한 결과는 운영-리서치 thread와 Obsidian에 남기고, 그때 구현 여부를 다시 결정하시면 됩니다."
    )
    pack_blockers = list(pack.blockers)
    if not pack.role_research_profile_summary:
        pack_blockers.append(
            f"{role_short} research profile 미주입 — 우선순위는 기본 휴리스틱으로 진행"
        )
    return DiscussionSynthesis(
        mode=DiscussionMode.RESEARCH_ONLY,
        rationale=classification.rationale,
        response_text="\n".join(body_lines),
        next_actions=(
            "research collector 호출",
            "결과 정리 후 사용자에게 검토 요청",
            "구현 필요 시 사용자에게 권한 제안 다시 요청",
        ),
        research_followups=tuple(followups),
        blockers=tuple(pack_blockers),
        suggested_handoff_role=None,
        knowledge_evidence_block=evidence_block,
        knowledge_short_summary=short_summary,
        share_boundary=dict(pack.share_boundary_breakdown()),
    )


def _synthesize_discussion(
    pack: ContextPack,
    classification: DiscussionModeMatch,
) -> DiscussionSynthesis:
    topic = _short_topic(pack.current_message)
    perspectives = _role_perspectives(pack)
    next_actions = _discussion_next_actions(pack)
    research_followups = _research_followups(pack)

    short_summary = pack.knowledge_short_summary()
    evidence_block = pack.format_knowledge_evidence_block()

    body_lines = [
        f"좋아요. \"{topic}\"는 토의로 받아 다음 단계를 함께 정해 보겠습니다.",
    ]
    rationale_short = _truncate(classification.rationale, 140)
    body_lines.append(f"_분류 근거: {rationale_short}_")
    if short_summary:
        body_lines.append(f"_{short_summary}_")
    body_lines.append("")

    if perspectives:
        body_lines.append("관련 관점 후보:")
        for role, text in perspectives.items():
            body_lines.append(f"- **{role}** — {text}")
        body_lines.append("")

    if pack.relevant_notes:
        body_lines.append("참고할 만한 메모:")
        for note in pack.relevant_notes[:3]:
            label = note.title or "(제목 없음)"
            location = f" · `{note.path}`" if note.path else ""
            body_lines.append(f"- {label}{location}")
        body_lines.append("")

    if pack.related_issues or pack.related_prs:
        body_lines.append("연결된 GitHub:")
        for issue in pack.related_issues[:3]:
            body_lines.append(
                f"- issue #{issue.number} {issue.title} ({issue.state or '?'})"
            )
        for pr in pack.related_prs[:3]:
            body_lines.append(
                f"- PR #{pr.number} {pr.title} ({pr.state or '?'})"
            )
        body_lines.append("")

    if evidence_block:
        body_lines.append(evidence_block)
        body_lines.append("")

    body_lines.append("다음에 할 수 있는 선택:")
    for action in next_actions:
        body_lines.append(f"- {action}")

    return DiscussionSynthesis(
        mode=DiscussionMode.DISCUSSION,
        rationale=classification.rationale,
        response_text="\n".join(body_lines),
        next_actions=tuple(next_actions),
        research_followups=tuple(research_followups),
        role_perspectives=dict(perspectives),
        blockers=tuple(pack.blockers),
        knowledge_evidence_block=evidence_block,
        knowledge_short_summary=short_summary,
        share_boundary=dict(pack.share_boundary_breakdown()),
    )


def _synthesize_implementation(
    pack: ContextPack,
    classification: DiscussionModeMatch,
) -> DiscussionSynthesis:
    topic = _short_topic(pack.current_message)
    short_summary = pack.knowledge_short_summary()
    evidence_block = pack.format_knowledge_evidence_block(
        heading="이번 결정 근거 자료"
    )
    body_lines = [
        f"\"{topic}\"는 구현 후보로 보입니다. 곧바로 코드를 만지지는 않고, "
        "권한 제안을 먼저 만들어 보여 드릴게요.",
    ]
    if short_summary:
        body_lines.append(f"_결정 근거 요약: {short_summary}_")
    body_lines.extend(
        [
            "",
            "다음 단계:",
            "- tech-lead가 어느 역할(executor)이 맞을지 추천",
            "- 사용자가 `수정 승인` 또는 `이대로 구현 진행`이라 답하면 코딩 작업으로 넘김",
            "- 그 전까지는 어떤 파일도 수정하지 않습니다",
        ]
    )
    if pack.write_blocked_reason:
        body_lines.append("")
        body_lines.append(f"_쓰기 차단 사유: {pack.write_blocked_reason}_")
    blockers = list(pack.blockers)
    suggested_role = _suggest_executor_hint(pack)
    if suggested_role:
        body_lines.append("")
        body_lines.append(f"_초기 추천 executor: `{suggested_role}` (권한 제안 단계에서 재계산)_")
    if evidence_block:
        body_lines.append("")
        body_lines.append(evidence_block)
    return DiscussionSynthesis(
        mode=DiscussionMode.IMPLEMENTATION_CANDIDATE,
        rationale=classification.rationale,
        response_text="\n".join(body_lines),
        next_actions=(
            "build_implementation_handoff 호출",
            "권한 제안을 사용자에게 표시",
            "사용자 승인 phrase 대기",
        ),
        implementation_ready=True,
        blockers=tuple(blockers),
        suggested_handoff_role=suggested_role,
        knowledge_evidence_block=evidence_block,
        knowledge_short_summary=short_summary,
        share_boundary=dict(pack.share_boundary_breakdown()),
    )


# ---------------------------------------------------------------------------
# 보조 helpers
# ---------------------------------------------------------------------------


def _short_topic(text: Optional[str], max_chars: int = 60) -> str:
    if not text:
        return "(요청 본문 없음)"
    head = text.strip().splitlines()[0].strip()
    if not head:
        return "(요청 본문 없음)"
    if len(head) <= max_chars:
        return head
    return head[: max_chars - 1].rstrip() + "…"


def _truncate(text: Optional[str], max_chars: int) -> str:
    if not text:
        return ""
    cleaned = " ".join(text.split())
    if len(cleaned) <= max_chars:
        return cleaned
    return cleaned[: max_chars - 1].rstrip() + "…"


_PERSPECTIVE_TRIGGERS = {
    "backend-engineer": (
        "backend",
        "back-end",
        "api",
        "schema",
        "db",
        "database",
        "auth",
        "인증",
        "권한",
        "마이그레이션",
        "migration",
        "queue",
        "transaction",
        "트랜잭션",
        "백엔드",
    ),
    "frontend-engineer": (
        "frontend",
        "front-end",
        "ui",
        "컴포넌트",
        "component",
        "react",
        "next.js",
        "vue",
        "css",
        "접근성",
        "accessibility",
        "프론트",
    ),
    "devops-engineer": (
        "devops",
        "ci",
        "cd",
        "deploy",
        "docker",
        "k8s",
        "kubernetes",
        "terraform",
        "supervisor",
        "runtime",
        "observability",
        "monitor",
        "infra",
        "github action",
        "rollout",
    ),
    "qa-engineer": (
        "qa",
        "회귀",
        "regression",
        "테스트",
        "test plan",
        "acceptance",
        "edge case",
    ),
    "ai-engineer": (
        "ai",
        "rag",
        "llm",
        "prompt",
        "agent",
        "memory",
        "evaluation",
        "embedding",
    ),
    "product-designer": (
        "designer",
        "design",
        "디자이너",
        "ux",
        "디자인",
        "moodboard",
        "wireframe",
        "톤",
        "user flow",
    ),
}


_PERSPECTIVE_HINTS = {
    "backend-engineer": "데이터/인증/마이그레이션 영향 — 어디까지 손이 가는지부터 합의",
    "frontend-engineer": "컴포넌트/상태/접근성 — UX와 어떤 상태가 깨지는지 점검",
    "devops-engineer": "배포/CI/runtime/관측성 — 운영 사고 가능 지점부터 정리",
    "qa-engineer": "회귀/엣지 케이스 — 어떤 시나리오가 깨지면 안 되는지 정의",
    "ai-engineer": "LLM/RAG/agent runtime — 비용·지연·hallucination 트레이드오프 정리",
    "product-designer": "UX/디자인 톤 — 사용자 흐름과 일관성 어디서 갈리는지 정리",
}


def _role_perspectives(pack: ContextPack) -> Mapping[str, str]:
    text = " ".join(
        filter(
            None,
            [
                pack.current_message or "",
                pack.thread_summary or "",
                pack.session_extra_summary or "",
            ],
        )
    ).lower()
    if not text:
        return {}
    matched: dict[str, str] = {}
    for role, keywords in _PERSPECTIVE_TRIGGERS.items():
        if any(kw in text for kw in keywords):
            matched[role] = _PERSPECTIVE_HINTS[role]
    if not matched:
        # 기본: tech-lead 자기 관점만 노출.
        matched["tech-lead"] = (
            "구조/의존성/리스크/롤아웃 — 다른 역할 신호가 약하면 우선 tech-lead가 정리"
        )
    return matched


def _discussion_next_actions(pack: ContextPack) -> Sequence[str]:
    """토의 다음 단계 옵션을 다음 우선순위로 만든다.

    1. 자료가 부족하다고 판단되면 조사로 회수.
    2. 시그널이 명확해지면 구현 후보로 끌어올릴 수 있다는 옵션.
    3. 추가 질문/확인이 필요한지.
    """

    options: list[str] = [
        "구체화된 결정이 필요한 부분이 있으면 알려 주세요 — 한 항목씩 합의",
        "조사가 더 필요하면 `조사만 진행`이라고 말씀해 주세요",
        "방향이 정해지면 `수정 권한 제안`이라고 답해 권한 제안으로 넘어갈 수 있습니다",
    ]
    if pack.write_requested and not pack.write_blocked_reason:
        options.insert(0, "쓰기가 이미 승인된 상태이므로 합의 즉시 권한 제안으로 이어 갈 수 있습니다")
    return tuple(options)


def _research_followups(pack: ContextPack) -> Sequence[str]:
    role_short = _role_short_name(pack.role_for_research)
    base = [
        f"{role_short} research profile 우선순위 source 1차 수집",
        "관련 issue / PR 제목과 라벨 정리",
    ]
    if pack.suggested_task_type:
        base.append(
            f"task_type 힌트({pack.suggested_task_type})에 맞춘 reference 카테고리 보강"
        )
    if not pack.relevant_notes:
        base.append("Obsidian 관련 note retrieval (현재 후보 없음 — 비어 있는 슬롯)")
    return tuple(base)


_EXECUTOR_KEYWORDS = {
    "backend-engineer": ("api", "schema", "auth", "백엔드", "migration", "transaction", "queue"),
    "frontend-engineer": ("ui", "react", "컴포넌트", "프론트", "css", "next.js", "vue"),
    "devops-engineer": ("ci", "cd", "deploy", "docker", "k8s", "terraform", "supervisor", "infra"),
    "qa-engineer": ("회귀", "regression", "테스트", "acceptance"),
    "ai-engineer": ("rag", "llm", "prompt", "agent", "embedding"),
    "product-designer": ("ux", "디자인", "moodboard", "wireframe"),
}


def _suggest_executor_hint(pack: ContextPack) -> Optional[str]:
    text = " ".join(
        filter(
            None,
            [
                pack.current_message or "",
                pack.thread_summary or "",
            ],
        )
    ).lower()
    if not text:
        return None
    best: Optional[str] = None
    best_hits = 0
    for role, keywords in _EXECUTOR_KEYWORDS.items():
        hits = sum(1 for kw in keywords if kw in text)
        if hits > best_hits:
            best = role
            best_hits = hits
    return best


def _role_short_name(role: str) -> str:
    if not role:
        return "tech-lead"
    return role.split("/")[-1] if "/" in role else role


__all__ = (
    "DiscussionSynthesis",
    "DiscussionTemplate",
    "synthesize_discussion",
)
