"""Discord research forum — package facade (P0-Q decomposition complete).

Historical monolith (``research_forum.py``, 1052 lines) split into 4
responsibility-aligned modules per the audit at
``docs/p0q-discord-large-files-decomposition.md``:

  * :mod:`.config`      — env + dataclass (``ResearchForumContext``).
  * :mod:`.prefixes`    — thread title / comment prefix vocabulary.
  * :mod:`.formatters`  — pure title / body / comment formatters.
  * :mod:`.posting`     — Discord async create/post layer + outcomes.

This ``__init__.py`` is the thin facade — re-exports the public API so
``from yule_discord.research_forum import X`` keeps
working for every external import site (bot.py + tests) without source
changes.
"""

from __future__ import annotations

from .config import ResearchForumContext
from .formatters import (
    DISCORD_MESSAGE_CONTENT_LIMIT,
    DISCORD_MESSAGE_REPLY_LIMIT,
    DISCORD_THREAD_TITLE_LIMIT,
    FORUM_STARTER_CONTENT_LIMIT,
    FORUM_STARTER_CONTINUATION_NOTICE,
    FORUM_STARTER_OVERFLOW_NOTICE,
    TOPIC_BUDGET,
    derive_research_topic,
    format_agent_comment,
    format_research_post_body,
    format_thread_markdown_fallback,
    normalize_thread_title,
    split_forum_starter_and_replies,
    truncate_for_starter_message,
)
from .posting import (
    ForumCommentOutcome,
    ForumPostOutcome,
    chunk_for_discord_message,
    create_research_post,
    post_agent_comment,
)
from .prefixes import (
    ALL_PREFIXES,
    COMMENT_PREFIXES,
    PREFIX_DECISION,
    PREFIX_OBSIDIAN,
    PREFIX_REFERENCE,
    PREFIX_RESEARCH,
    PREFIX_TOOL,
    THREAD_TITLE_PREFIXES,
    detect_thread_prefix,
)


__all__ = (
    # config
    "ResearchForumContext",
    # prefixes
    "PREFIX_RESEARCH",
    "PREFIX_TOOL",
    "PREFIX_REFERENCE",
    "PREFIX_DECISION",
    "PREFIX_OBSIDIAN",
    "THREAD_TITLE_PREFIXES",
    "COMMENT_PREFIXES",
    "ALL_PREFIXES",
    "detect_thread_prefix",
    # formatter constants
    "DISCORD_MESSAGE_CONTENT_LIMIT",
    "DISCORD_MESSAGE_REPLY_LIMIT",
    "DISCORD_THREAD_TITLE_LIMIT",
    "FORUM_STARTER_CONTENT_LIMIT",
    "FORUM_STARTER_CONTINUATION_NOTICE",
    "FORUM_STARTER_OVERFLOW_NOTICE",
    "TOPIC_BUDGET",
    # formatters
    "derive_research_topic",
    "format_agent_comment",
    "format_research_post_body",
    "format_thread_markdown_fallback",
    "normalize_thread_title",
    "split_forum_starter_and_replies",
    "truncate_for_starter_message",
    # posting
    "ForumCommentOutcome",
    "ForumPostOutcome",
    "chunk_for_discord_message",
    "create_research_post",
    "post_agent_comment",
)
