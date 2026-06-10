"""Single source of truth for the engineering-agent ``/help`` body.

The same content is rendered by:

- the ``/help`` and ``/engineer_help`` slash commands
  (:mod:`yule_engineering.discord.commands`),
- the natural-language ``GENERAL_ENGINEERING_HELP`` intent inside
  :mod:`yule_engineering.discord.engineering_conversation`,
- the legacy fallback string in ``bot/_legacy`` when the
  ``engineering_conversation`` module is not importable.

Keeping a single canonical body here ensures the three surfaces stay in
sync — if we add a new command or workflow channel, the help message is
edited in one place.
"""

from __future__ import annotations

from typing import Iterable


def render_engineer_help_message() -> str:
    """Return the full ``/help`` body for the engineering gateway.

    The body is plain Discord-friendly markdown (no code-fences, no
    embeds) so it fits inside ``interaction.followup.send`` and any
    natural-language ``EngineeringConversationResponse``.
    """

    sections: list[str] = [
        _header(),
        _quick_start(),
        _free_conversation_vs_intake(),
        _command_overview(),
        _examples(),
        _channel_guide(),
        _status_and_followups(),
        _troubleshooting(),
    ]
    return "\n\n".join(section.strip("\n") for section in sections if section)


def render_engineer_help_short() -> str:
    """Compact one-screen variant — used when the long body would crowd
    out a conversational reply (e.g. inline NL help inside a thread).

    The slash command always renders the full body; the conversational
    fallback chooses based on context.
    """

    return "\n\n".join(
        section.strip("\n")
        for section in (
            _header(),
            _quick_start(),
            _free_conversation_vs_intake(),
        )
    )


def _header() -> str:
    return (
        "**engineering-agent 사용법**\n"
        "이 봇은 자유 대화도 받고, 실행이 필요한 일은 intake → approval → PR 흐름으로 이어줘요. "
        "명령을 외우기 전에 그냥 말로 시작해도 됩니다."
    )


def _quick_start() -> str:
    return _bullet_block(
        "🚀 Quick start",
        (
            "그냥 질문/상담은 평소처럼 채팅하듯 말해 주세요 — 답변/브리핑/사용법은 자유 대화로 처리됩니다.",
            "실제 코드/문서를 만들거나 PR 까지 가야 하는 작업은 `/engineer_intake` 로 시작하거나, "
            "자연어로 말해도 봇이 \"intake 로 승격할까요?\" 라고 안내합니다.",
            "구조를 잘 모르겠으면 이 메시지(`/help`)를 다시 부르거나 `도움말` 이라고만 적어도 같은 안내가 나옵니다.",
        ),
    )


def _free_conversation_vs_intake() -> str:
    free = (
        "사용법/기능 문의 (예: \"이 봇 뭐 할 수 있어?\")",
        "상태 / 진행 상황 문의 (예: \"지금 뭐 하는 중이야?\", \"왜 막혔어?\")",
        "브레인스토밍·scope 정리·기술 선택 논의",
        "\"이거 가능한가?\" 같은 가벼운 상담",
    )
    intake = (
        "실제 코드/문서 작성 요청",
        "특정 repo 를 가리키는 작업 (`owner/repo` 또는 GitHub URL 포함)",
        "issue/PR 까지 이어져야 하는 구현 요청",
        "approval 이 필요한 운영 변경",
    )
    return (
        "💬 **자유 대화 vs intake**\n"
        "이럴 땐 그냥 말씀하세요 (자유 대화로 처리):\n"
        + _bulleted(free)
        + "\n\n이럴 땐 intake 로 승격합니다 (실행 workflow 진입):\n"
        + _bulleted(intake)
        + "\n\n자연어로 말씀하시면 봇이 어느 쪽인지 판단해서, 실행 요청이면 \"이대로 진행하시겠어요?\" 라고 한 번 묻고 넘어갑니다. "
        "처음부터 `/engineer_intake` 로 시작하셔도 동일하게 동작합니다."
    )


def _command_overview() -> str:
    return (
        "🛠️ **주요 명령**\n"
        "- `/help` 또는 `/engineer_help` — 이 안내를 다시 보여줍니다.\n"
        "- `/engineer_intake <prompt>` — 새 작업 intake. `task_type`/`write_requested` 옵션 있음.\n"
        "- `/engineer_show <session_id>` — 세션 상태/실행 후보/승인 대기 사유 조회.\n"
        "- `/engineer_approve <session_id>` — 대기 중인 세션 승인 (실행 풀어줌).\n"
        "- `/engineer_reject <session_id> <reason>` — 세션 거절 (사유 필수).\n"
        "- `/engineer_progress <session_id> <note>` — 진행 메모 (PR/Thread 링크 그대로 붙여도 됨).\n"
        "- `/engineer_complete <session_id> <summary>` — 완료 처리 + 요약 게시.\n"
        "- `/engineer_review <session_id> ...` — PR review / Copilot / 외부 피드백 입력.\n"
        "- `/engineer_review_reply <session_id> <feedback_id> ...` — 위 피드백에 회신."
    )


def _examples() -> str:
    return (
        "🧪 **예시 프롬프트**\n"
        "자유 대화:\n"
        "- \"이 봇 뭐 할 수 있어?\"\n"
        "- \"지금 어떻게 돌아가고 있어?\"\n"
        "- \"OAuth refresh 흐름 어떻게 짜는 게 안전할까?\"\n\n"
        "구현 요청 (intake 후보):\n"
        "- \"users API 에 email_verified 필드 추가하고 마이그레이션 짜줘\"\n\n"
        "repo 포함 요청 (intake 확정):\n"
        "- \"codwithyc/yule-studio-agent 에서 /engineer_show 응답 포맷 손보기\"\n\n"
        "approval_required (운영 변경):\n"
        "- \"production 배포 파이프라인에 staging gate 추가해줘\""
    )


def _channel_guide() -> str:
    return (
        "📡 **채널 안내**\n"
        "- `#업무-접수` — 자유 대화 + intake 의 메인 입구. 자연어로 시작해도 OK.\n"
        "- `#승인-대기` — 봇이 운영자 결정을 요청할 때 카드가 떠요. 카드의 버튼/멘션 으로 응답.\n"
        "- `#운영-리서치` — 리서치 forum / mistake ledger / troubleshooting 노트가 쌓이는 곳."
    )


def _status_and_followups() -> str:
    return (
        "📊 **상태/팔로업 확인**\n"
        "- 세션 상태가 궁금하면 `/engineer_show <session_id>` 또는 채널에서 \"지금 진행 상황 알려줘\" 라고 물어보세요.\n"
        "- 활성 세션 목록이 궁금하면 \"열린 세션 목록 보여줘\" / \"세션 몇 개 떠 있어?\" 같이 물으면 됩니다.\n"
        "- 막힌 이유는 \"왜 멈췄어?\" / \"왜 막혔어?\" 로 바로 확인할 수 있어요."
    )


def _troubleshooting() -> str:
    return (
        "🆘 **잘 안 될 때**\n"
        "- 자유 대화가 의도와 다른 방향으로 잡혔다면, 한두 문장으로 \"이건 사실 …\" 하고 다시 알려 주세요.\n"
        "- intake 가 잡혔는데 단순 질문이었다면 \"이건 그냥 질문이야\" 라고 답하시면 새 세션을 정리할게요.\n"
        "- 명령 실행이 실패하면 그대로 채널에 \"왜 실패했어?\" 라고 물어 주세요. 상태/원인 진단으로 안내합니다."
    )


def _bullet_block(title: str, lines: Iterable[str]) -> str:
    body = "\n".join(f"- {line}" for line in lines)
    return f"{title}\n{body}"


def _bulleted(lines: Iterable[str]) -> str:
    return "\n".join(f"- {line}" for line in lines)


__all__ = ("render_engineer_help_message", "render_engineer_help_short")
