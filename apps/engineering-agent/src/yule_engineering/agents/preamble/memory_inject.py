"""F14 commit 5 — preamble 에 F10 long-term memory auto-wire.

agent spawn prompt 의 "CORE CONTEXT" 섹션 다음에 LongTermMemory shard
요약을 자동 inject. env (`YULE_LONG_TERM_MEMORY_ENABLED`) 가 ON 일 때만,
caller 가 RequestContext 를 넘기면 memory pack 을 build 해 rendered preamble
끝에 append.

hard rails:
  - env OFF → no-op, 빈 추가만.
  - memory 가 None / source 가 비어 있어도 안전.
  - shard.content 가 PasteGuard 통과 (caller 책임 — F10 governance 가드).
"""

from __future__ import annotations

from typing import Optional

from .builder import Preamble


def inject_memory_summary(
    preamble: Preamble,
    *,
    request_context=None,  # type: ignore[arg-type] — runtime import 회피
    long_term_memory=None,  # type: ignore[arg-type]
    max_shards: int = 5,
) -> str:
    """preamble.render_markdown() 결과에 memory shard 요약을 append.

    no-wire / no-env / no-shard 시 plain render 만 반환 (회로 단절 안전).
    """

    rendered = preamble.render_markdown()
    if request_context is None or long_term_memory is None:
        return rendered

    try:
        from yule_agent_memory.long_term_memory import build_memory_pack
    except ImportError:
        return rendered

    pack = build_memory_pack(
        long_term_memory=long_term_memory,
        request_context=request_context,
        limit=max_shards,
    )

    if not pack.shards:
        return rendered

    lines: list = ["", "## LONG-TERM MEMORY (auto-inject, F10)"]
    lines.append(
        f"_(cross-session shard 요약: {len(pack.shards)} top — "
        f"BLOCK mistake 우선)_",
    )
    lines.append("")
    for shard in pack.shards:
        title_hint = (shard.content or "")[:120].replace("\n", " ").strip()
        lines.append(
            f"- **[{shard.kind}]** {shard.source} · "
            f"({shard.related_issue or '-'}) · {title_hint}"
        )
    return rendered + "\n".join(lines) + "\n"


__all__ = ("inject_memory_summary",)
