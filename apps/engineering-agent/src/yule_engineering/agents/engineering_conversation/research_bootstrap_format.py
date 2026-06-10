"""engineering_conversation — collection-aware intake body formatters.

Extracted from ``research_bootstrap`` (split axis: bootstrap / runtime /
formatting). This module owns the **formatting** responsibility — the
collection-aware intake responders that render the team-lead voice once
the autonomous collector has run:

- ``_format_intake_with_collection``
- ``_format_coding_bootstrap_body``
- ``_format_collection_announcement``
- ``_format_collection_meta_block``

These are pure rendering helpers over a ``CollectionOutcome``-shaped
object (read via ``getattr`` so tests can pass ``SimpleNamespace``).

Note: ``_summarize_topic`` / ``_pretty_task_type`` / ``_pretty_provider``
belong to ``response_formatters`` (audit doc §2). The few call sites here
import them lazily inside the functions to avoid a circular dependency
(``response_formatters`` imports these formatters).
"""

from __future__ import annotations

from typing import Any, Optional


def _format_intake_with_collection(
    *,
    message_text: str,
    suggested_task_type: Optional[str],
    write_likely: bool,
    collection: Any,
) -> str:
    """Unified intake response when the auto-collector ran.

    Output structure (matches the team-lead voice spec):

    1. Greeting that names what we're doing.
    2. Understanding paragraph echoing a short topic + classification.
    3. Action paragraph describing what just happened or what's next.
    4. (auto_collected / user_provided only) compact meta tail.
    5. Confirmation prompt — except in NEEDS_USER_INPUT where we wait
       for the user's reply instead of asking them to confirm.
    """

    # response_formatters owns _summarize_topic / _pretty_task_type per
    # audit §2 — lazy-import to avoid the circular research_bootstrap ↔
    # response_formatters reference (response_formatters imports us).
    from .response_formatters import _pretty_task_type, _summarize_topic

    mode = getattr(collection, "mode", None)
    mode_value = getattr(mode, "value", str(mode))
    topic = _summarize_topic(message_text)

    paragraphs: list[str] = []

    # 1. greeting
    if mode_value == "auto_collected":
        paragraphs.append("좋아요. 먼저 1차 자료를 모아볼게요.")
    elif mode_value == "user_provided":
        paragraphs.append("받았어요. 보내주신 자료를 1순위로 두고 시작할게요.")
    elif mode_value == "needs_user_input":
        paragraphs.append("받았어요. 다만 더 정확하게 도와드리려면 자료가 조금 더 필요해요.")
    else:
        paragraphs.append("작업 내용을 받았어요.")

    # 2. understanding
    understand = [f"이번 요청은 “{topic}”으로 이해했어요."]
    if write_likely:
        understand.append(
            "코드나 문서 쓰기가 동반되는 작업으로 보여서, 진행 전에 한 번 확인할게요."
        )
    elif suggested_task_type:
        understand.append(
            f"분석·검토 위주의 {_pretty_task_type(suggested_task_type)} 작업으로 이해하고 있습니다."
        )
    paragraphs.append("\n".join(understand))

    # 3. action — depends on mode
    count = getattr(collection, "auto_collected_count", 0) or 0
    if mode_value == "auto_collected":
        paragraphs.append(
            f"방금 {count}개의 참고 자료 후보를 수집했어요.\n"
            "이 자료들은 운영-리서치에 정리해두고, 이어서 각 역할이 자기 관점으로 검토하게 할게요."
        )
    elif mode_value == "user_provided":
        paragraphs.append(
            "보내주신 자료로 바로 검토를 시작하고, 정리된 결과는 운영-리서치에 함께 남길게요."
        )
    elif mode_value == "needs_user_input":
        prompt = getattr(collection, "user_prompt", None) or (
            "관련 자료를 한두 개 붙여 주시면 더 정확하게 도와드릴 수 있어요."
        )
        paragraphs.append(
            "자동 수집이 비어 있어서, 자료를 한 번 같이 보고 가는 게 좋겠어요.\n"
            f"{prompt}"
        )

    # 4. meta tail (auto_collected / user_provided only)
    if mode_value in ("auto_collected", "user_provided"):
        paragraphs.append(_format_collection_meta_block(collection))

    # 5. confirm — skip when we're waiting for more user input
    if mode_value != "needs_user_input":
        paragraphs.append(
            "맞으면 `이대로 진행`이라고 답해 주세요. 빠진 부분이 있으면 추가로 알려주셔도 좋아요."
        )

    return "\n\n".join(paragraphs)


def _format_coding_bootstrap_body(
    *,
    message_text: str,
    bootstrap: Any,
    suggested_task_type: Optional[str],
) -> str:
    """P0-J (#145) — replace 'NEEDS_USER_INPUT' surface with bootstrap ack.

    When the gateway has repo + stack + write intent, the autonomous
    collector's "자료 부족" follow-up is wrong: the *anchor* is the
    repo itself. This body explains what the gateway will do next
    (seed docs + coding handoff) so the user knows we're proceeding,
    not stalling.
    """

    from .response_formatters import _pretty_task_type, _summarize_topic

    topic = _summarize_topic(message_text)
    stacks = ", ".join(getattr(bootstrap, "stacks_mentioned", ()) or ())
    seeded = ", ".join(getattr(bootstrap, "seeded_docs", ()) or ())
    task_label = (
        _pretty_task_type(suggested_task_type) if suggested_task_type else None
    )
    paragraphs: list[str] = [
        "🚀 coding bootstrap 활성 — repo target + stack mention + write intent 조합으로 "
        "추가 자료 요청 없이 coding handoff 로 진행합니다.",
        f"이번 요청은 “{topic}” 으로 이해했고,"
        + (f" `{task_label}` 작업으로 분류했어요." if task_label else ""),
    ]
    if stacks:
        paragraphs.append(f"📚 감지된 스택: {stacks}")
    if seeded:
        paragraphs.append(f"📖 official docs 자동 seed: {seeded}")
    paragraphs.append(
        "코드 컨텍스트는 repo target 으로부터 부트스트랩될 예정입니다. "
        "다른 자료가 필요해지면 그때 다시 알려주세요."
    )
    return "\n\n".join(paragraphs)


def _format_collection_announcement(collection: Any) -> str:
    """Conversational paragraph(s) added when auto-collection ran.

    Tone follows the team-lead voice: 1) what we just did, 2) what's
    next. Internal jargon (collector / query / forum / deliberation) is
    rephrased — collector → 수집 방식, forum → 운영-리서치, deliberation →
    역할별 검토.

    Three modes:
    - AUTO_COLLECTED → "방금 N개의 참고 자료 후보를 수집했어요. ..." + meta
    - USER_PROVIDED → "보내주신 자료를 1순위로 두고 검토할게요." + meta
    - NEEDS_USER_INPUT → 사용자에게 자료 요청 (collector가 빈 결과)
    """

    mode = getattr(collection, "mode", None)
    mode_value = getattr(mode, "value", str(mode))

    if mode_value == "auto_collected":
        count = getattr(collection, "auto_collected_count", 0) or 0
        body = (
            f"먼저 1차 자료를 모아 봤어요. 방금 {count}개의 참고 자료 후보를 찾았습니다.\n"
            "이 자료들은 운영-리서치에 정리해두고, 이어서 각 역할이 자기 관점으로 검토하게 할게요."
        )
        return body + "\n\n" + _format_collection_meta_block(collection)

    if mode_value == "user_provided":
        body = (
            "사용자 제공 자료를 1순위로 두고 검토를 시작할게요.\n"
            "정리한 결과는 운영-리서치에 함께 남길 예정이에요."
        )
        return body + "\n\n" + _format_collection_meta_block(collection)

    if mode_value == "needs_user_input":
        prompt = getattr(collection, "user_prompt", None) or (
            "관련 자료를 한두 개 붙여 주시면 더 정확하게 도와드릴 수 있어요."
        )
        return (
            "자동 수집이 비어 있어, 자료를 한 번 같이 보고 가는 게 좋겠어요.\n"
            f"{prompt}"
        )

    return ""


def _format_collection_meta_block(collection: Any) -> str:
    """Compact key-value tail used under the collection announcement.

    Format:
        수집 정보:
        - 수집 방식: 기본 검색(mock)
        - 수집 자료: N건
        - 다음 단계: 역할별 검토
    """

    from .response_formatters import _pretty_provider

    count = getattr(collection, "auto_collected_count", 0) or 0
    name = getattr(collection, "collector_name", "?")
    return (
        "수집 정보:\n"
        f"- 수집 방식: {_pretty_provider(name)}\n"
        f"- 수집 자료: {count}건\n"
        "- 다음 단계: 역할별 검토"
    )


__all__ = (
    "_format_intake_with_collection",
    "_format_coding_bootstrap_body",
    "_format_collection_announcement",
    "_format_collection_meta_block",
)
