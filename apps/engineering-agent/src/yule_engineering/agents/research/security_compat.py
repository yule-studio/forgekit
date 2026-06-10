"""PasteGuard integration shim for research live providers (F5 / #92).

본 모듈은 :mod:`yule_security.paste_guard` 의
:func:`guard_outbound` 를 live provider 내부에서 부르기 쉽게 감싼다.

* :func:`guard_text` — text 한 줄 (title / summary / tag) 에 대한 PasteGuard
  pass-through. 결과는 redacted plain text. block 된 경우 빈 문자열을
  반환해 caller 가 자연스럽게 skip 하도록 한다.
* 본 모듈은 PasteGuard 외부 의존을 추가하지 않는다 — provider 측 import
  surface 를 최소화하기 위해 별도 파일로 분리만 한 것.
"""

from __future__ import annotations

from yule_security.paste_guard import OutboundChannel, guard_outbound


def guard_text(value: str) -> str:
    """PasteGuard 로 ``value`` 를 정규화해 반환.

    내부적으로 channel=LLM 으로 검증하고, block 시 빈 문자열, 아니면
    redacted 문자열을 반환한다. 입력이 비어 있으면 그대로 빈 문자열.
    """

    if not value:
        return ""
    verdict = guard_outbound(channel=OutboundChannel.LLM, payload=value)
    if verdict.blocked:
        return ""
    return verdict.redacted


__all__ = ("guard_text",)
