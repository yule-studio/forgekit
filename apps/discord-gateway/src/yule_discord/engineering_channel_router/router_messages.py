"""engineering_channel_router — router-facing message text builders.

Pure formatting responsibility extracted from ``main.py`` (axis
"router, formatting"). Hosts the user-facing Korean clarification /
refusal / error strings that ``route_engineering_message`` and
``_drive_clarification_create_new_work`` post via ``send_chunks``.

These are *display only* — no routing/dispatch logic lives here. The
router decides *when* to send; this module decides *what text* to send.
Several of these strings (notably the "engineering thread not found"
refusal) were previously inlined verbatim at multiple call sites, so
centralising them here also removes the duplication.

PURE MOVE: every string below is the verbatim text that previously sat
inline in ``main.py``. No behavior change.
"""

from __future__ import annotations


# Clarification cache present but the canonical task body was lost
# (older entry from before the canonical_prompt fix, or candidates
# truncated). Refuse to spawn a session with the routing-command phrase.
CLARIFICATION_CANONICAL_MISSING = (
    "직전 clarification 캐시에서 원문 task 본문을 찾지 못했어요.\n"
    "진행할 업무 원문을 다시 알려주세요. \"새 작업으로 진행\"은 "
    "routing 명령이라 작업 본문으로 사용할 수 없어요."
)


# ``_drive_clarification_create_new_work`` defensive guard — the user's
# reply was a routing command AND the cache had no canonical body.
DRIVE_CLARIFICATION_NON_ACTIONABLE = (
    "방금 받은 메시지는 routing 명령(`새 작업으로 진행`) 이라 "
    "session.prompt 로 쓸 수 없고, 직전 clarification 캐시에서도 "
    "원문 task 본문을 찾지 못했어요. 진행할 업무 원문을 다시 "
    "알려주세요."
)


# Confirm-routing firewall — the reply is a paste-back of a gateway
# notice (bot echo) and we have no canonical task to substitute.
BOT_ECHO_CLARIFICATION = (
    "방금 받은 메시지가 gateway가 보낸 안내문 문구와 똑같아서 "
    "새 작업으로 등록하지 않았어요.\n"
    "진행할 업무 원문을 다시 알려주세요. 짧은 확인 문구는 "
    "작업 본문으로 사용할 수 없어요."
)


# Confirm-routing firewall — the reply is a bare confirmation phrase
# with no canonical task to substitute.
NON_ACTIONABLE_ROUTING_CLARIFICATION = (
    "진행할 업무 원문을 다시 알려주세요. \"이대로 진행\" / "
    "\"새 작업으로 진행\" 같은 확인 문구는 작업 본문으로 "
    "사용할 수 없어요.\n"
    "기존 작업을 이어가려면 `기존 세션 <id>`로 답해 주세요."
)


# JOIN/append fell through (no matching open thread) — never silently
# create a new session when the user signalled continuation.
ENGINEERING_THREAD_NOT_FOUND = (
    "열려 있는 engineering-agent thread를 찾지 못해서 새 작업 세션은 만들지 않았습니다.\n"
    "이어갈 thread 안에서 다시 말해주시거나, 새 작업으로 시작하려면 `새 작업으로 진행`이라고 답해 주세요."
)


# Defensive intake guard — the CREATE branch is the last writer of
# session.prompt; refuse to persist a zombie session whose prompt is a
# confirmation phrase / gateway notice paste-back.
DEFENSIVE_INTAKE_NON_ACTIONABLE = (
    "진행할 업무 원문을 다시 알려주세요. \"이대로 진행\" / "
    "\"새 작업으로 진행\" 같은 확인 문구나 gateway 안내문은 "
    "작업 본문으로 사용할 수 없어요."
)


def format_intake_failure(exc: object) -> str:
    """Surface an intake-layer exception to the user (do not crash bot)."""

    return f"⚠️ engineer intake 실패: {exc}"


def format_kickoff_failure(exc: object, *, session_id: object) -> str:
    """Surface a thread-kickoff failure; intake already landed."""

    return f"⚠️ thread kickoff 실패: {exc}\n세션 `{session_id or '?'}` 은 이미 생성되어 있습니다."


def format_kickoff_failure_short(exc: object) -> str:
    """Short kickoff-failure notice (clarification create-new path)."""

    return f"⚠️ thread kickoff 실패: {exc}"
