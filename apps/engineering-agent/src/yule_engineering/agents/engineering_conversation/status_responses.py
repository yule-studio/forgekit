"""engineering_conversation — read-only status / session responders (P0-J + P0-K).

The 6 responder functions here are the bodies behind the
``READ_ONLY_INTENTS`` tuple. The auto_collect path must *never* trigger
for any intent that ends up calling one of these — they read existing
state and return a Korean answer. Hard-blocklist enforcement lives in
``response_formatters.build_engineering_conversation_response``.

Functions in this module:

- :func:`format_status_diagnostic_response` — the big "현재 세션 상태"
  surface. Renders repo / mode / topology / branch / PR / RepoContract /
  Obsidian mirror / tracking chain / growth ledger / PR slice / vault
  push audit / coding job / forum status / role activity log / role
  research results / activity log summary / research loop report /
  tech-lead synthesis / active roles / work_report / progress notes /
  diagnostic signals — every key the gateway has stamped into
  ``session.extra``.
- :func:`format_session_count_response` — single-line count answer.
- :func:`format_session_list_response` — multi-line open-session list.
- :func:`format_blocked_reason_response` — surfaces tracking_blocked_reason
  and diagnostic signals for a specific session.
- :func:`format_continue_existing_response` — ack continue-existing-work,
  never spawns new intake / forum thread.
- :func:`format_change_direction_response` — ack direction update on the
  same session, never spawns new intake.

Private helpers:

- :func:`_open_states_set`, :func:`_safe_list_sessions`, :func:`_coerce_str`
- :func:`_format_coding_status_line`
- :func:`_asks_about_member_bots` (used by the response-formatters main
  entry to decide member-bot focus mode).

Dependencies: ``_normalize`` lives in ``intent_detection`` (audit doc §2).
Until that module is extracted in step 6, ``_asks_about_member_bots`` reads
the helper lazily from ``._legacy``.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional

from yule_engineering.agents.lifecycle.session_status import (
    diagnose_session,
    render_member_bot_summary,
)


# ---------------------------------------------------------------------------
# Internals — list_sessions helper + value coercion
# ---------------------------------------------------------------------------


def _open_states_set() -> set:
    """States considered 'open' for session count/list responses."""

    return {"new", "queued", "in_progress", "needs_research", "awaiting_review"}


def _safe_list_sessions(lister):
    """Call the injected lister; return None on failure (caller surfaces hint)."""

    if lister is None:
        try:
            from yule_engineering.agents.workflow_state import list_sessions as _list

            lister = _list
        except Exception:  # noqa: BLE001
            return None
    try:
        try:
            return tuple(lister(limit=100))
        except TypeError:
            return tuple(lister())
    except Exception:  # noqa: BLE001
        return None


def _coerce_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _format_coding_status_line(
    proposal_payload: Any,
    job_payload: Any,
) -> Optional[str]:
    """Render a single ``- coding_job: ...`` line for the diagnostic.

    Coding job (approved) wins over a still-pending proposal. Empty
    extras yield ``None`` so the caller skips the line entirely.
    """

    if isinstance(job_payload, Mapping) and job_payload:
        status = job_payload.get("status") or "ready"
        executor = job_payload.get("executor_role") or "unknown"
        scope = job_payload.get("write_scope") or ()
        if isinstance(scope, (list, tuple)) and scope:
            preview = ", ".join(str(s) for s in tuple(scope)[:2])
            if len(scope) > 2:
                preview += " 외"
            return (
                f"- coding_job: {status} (executor=`{executor}`, write_scope={preview})"
            )
        return f"- coding_job: {status} (executor=`{executor}`)"
    if isinstance(proposal_payload, Mapping) and proposal_payload:
        executor = proposal_payload.get("executor_role") or "unknown"
        return (
            f"- coding_job: pending-approval (executor=`{executor}`) — "
            "사용자 `수정 승인` 대기"
        )
    return None


# ---------------------------------------------------------------------------
# session_count / session_list responders
# ---------------------------------------------------------------------------


def format_session_count_response(session_lister=None) -> str:
    """Render a count-only answer for the session_count_query intent.

    *session_lister* is the injected ``list_sessions``-style callable
    (production: ``agents.workflow_state.list_sessions``). Returns
    a single-line Korean answer.
    """

    sessions = _safe_list_sessions(session_lister)
    if sessions is None:
        return "ℹ️ 세션 카운트를 조회할 수 없어요 (workflow state 미연결)."
    open_set = _open_states_set()
    open_count = sum(
        1
        for s in sessions
        if _coerce_str(getattr(getattr(s, "state", None), "value", getattr(s, "state", None)))
        in open_set
    )
    total = len(sessions)
    return f"현재 열려 있는 engineering-agent 세션은 **{open_count}개** 입니다 (전체 캐시 {total}개)."


def format_session_list_response(
    session_lister=None, *, limit: int = 10
) -> str:
    """Render a list of open sessions for the session_list_query intent.

    Shows id / state / task_type / updated_at + thread/PR if present.
    """

    sessions = _safe_list_sessions(session_lister)
    if sessions is None:
        return "ℹ️ 세션 목록을 조회할 수 없어요 (workflow state 미연결)."
    open_set = _open_states_set()
    rows: list[str] = []
    for s in sessions:
        state_value = _coerce_str(
            getattr(getattr(s, "state", None), "value", getattr(s, "state", None))
        )
        if state_value not in open_set:
            continue
        sid = _coerce_str(getattr(s, "session_id", None)) or "?"
        task = _coerce_str(getattr(s, "task_type", None)) or "?"
        updated = _coerce_str(getattr(s, "updated_at", None)) or ""
        extra = getattr(s, "extra", None) or {}
        thread_id = (
            extra.get("research_forum_thread_id")
            or extra.get("forum_thread_id")
            or getattr(s, "thread_id", None)
        )
        pr_n = extra.get("pull_request_number")
        anchors: list[str] = []
        if thread_id is not None:
            anchors.append(f"thread `{thread_id}`")
        if pr_n is not None:
            anchors.append(f"PR #{pr_n}")
        anchor_text = (" · " + " · ".join(anchors)) if anchors else ""
        rows.append(
            f"- `{sid}` · {state_value} · {task} · {updated}{anchor_text}"
        )
        if len(rows) >= limit:
            break
    if not rows:
        return "현재 열려 있는 engineering-agent 세션이 없어요."
    return "현재 열려 있는 engineering-agent 세션 목록:\n\n" + "\n".join(rows)


# ---------------------------------------------------------------------------
# blocked / continue / change-direction responders
# ---------------------------------------------------------------------------


def format_blocked_reason_response(
    session: Optional[Any] = None,
) -> str:
    """Surface the blocked reason / signals for the active session.

    Reuses ``diagnose_session`` to derive signals + tracking blocked
    reason. When no session is provided, returns a hint to specify.
    """

    if session is None:
        return (
            "현재 채널에 매칭되는 열린 세션이 없어 막힘 원인을 특정할 수 없어요.\n"
            "확인할 session id 를 알려주시거나, 이어갈 thread 안에서 다시 말씀해 주세요."
        )
    try:
        from yule_engineering.agents.lifecycle.session_status import diagnose_session as _diagnose

        report = _diagnose(session)
    except Exception:  # noqa: BLE001
        report = None

    sid = _coerce_str(getattr(session, "session_id", None)) or "?"
    lines: list[str] = [f"세션 `{sid}` 의 막힘 원인 진단:"]

    blocked_reason = None
    extra = getattr(session, "extra", None) or {}
    if isinstance(extra, Mapping):
        blocked_reason = _coerce_str(extra.get("tracking_blocked_reason"))
    if blocked_reason:
        lines.append(f"- tracking blocked: {blocked_reason}")
    if report is not None and getattr(report, "signals", None):
        for signal in report.signals[:5]:
            code = getattr(signal, "code", "?")
            title = getattr(signal, "title", "")
            detail = getattr(signal, "detail", "")
            severity = getattr(signal, "severity", "?")
            tail = f" — {detail}" if detail else ""
            lines.append(f"- [{severity}] {code}: {title}{tail}")
    if len(lines) == 1:
        lines.append("- 감지된 막힘 신호가 없어요. 상태는 정상 흐름으로 보여요.")
    return "\n".join(lines)


def format_continue_existing_response(session: Optional[Any] = None) -> str:
    """Ack continue_existing_work — do NOT create a new intake."""

    if session is None:
        return (
            "이어갈 세션을 찾지 못했어요. session id 또는 thread 를 명시해 주세요. "
            "(새 세션을 만들지 않고 기존 세션 위에서 진행합니다.)"
        )
    sid = _coerce_str(getattr(session, "session_id", None)) or "?"
    state = _coerce_str(
        getattr(getattr(session, "state", None), "value", getattr(session, "state", None))
    ) or "?"
    return (
        f"✅ 세션 `{sid}` 을 이어서 진행할게요. (state: {state})\n"
        "새 intake / research thread 를 만들지 않습니다."
    )


def format_change_direction_response(
    session: Optional[Any] = None,
    *,
    user_text: str = "",
) -> str:
    """Ack change_direction — same session update, no new intake."""

    sid = _coerce_str(getattr(session, "session_id", None)) if session else None
    head = "✅ 방향 수정 신호를 받았어요."
    sid_line = f" 세션 `{sid}` 위에서 진행 방향을 갱신합니다." if sid else ""
    note = (
        f"\n새 받아온 방향 메모: {user_text.strip()[:200]}" if user_text else ""
    )
    return (
        f"{head}{sid_line}\n"
        "새 intake / research thread 를 만들지 않고 기존 세션의 prompt/scope 만 "
        "업데이트합니다." + note
    )


# ---------------------------------------------------------------------------
# status diagnostic — the big surface
# ---------------------------------------------------------------------------


def format_status_diagnostic_response(
    session: Optional[Any],
    *,
    is_member_bot_question: bool = False,
) -> str:
    """Render a real-state status answer for the gateway.

    Reads ``session.state``, ``session.extra``, and known keys
    (``research_pack``, ``forum_thread_id``/``research_forum_thread_id``,
    ``research_loop_report``, ``forum_publish_error``) so the gateway
    can say "research_pack: 있음 · forum: 게시 실패 · 마지막 오류: 4000자
    초과" instead of guessing. When *session* is None we explicitly tell
    the operator we couldn't find an open session.

    *is_member_bot_question* tilts the answer toward "멤버 봇들은 뭐 하고
    있어?" — we still print the full state header but append a short
    member-bot focused section pointing at the forum thread instead of
    duplicating role comments here.
    """

    if session is None:
        return (
            "현재 채널/스레드에 매칭되는 열린 engineering-agent 세션이 보이지 않아요.\n"
            "확인하려는 작업의 session id를 알려 주시거나, "
            "이어갈 thread 안에서 다시 말씀해 주세요."
        )

    extra = dict(getattr(session, "extra", {}) or {})
    research_pack = extra.get("research_pack")
    forum_thread_id = (
        extra.get("research_forum_thread_id")
        or extra.get("forum_thread_id")
    )
    forum_thread_url = extra.get("forum_thread_url") or extra.get(
        "research_forum_thread_url"
    )
    forum_publish_error = (
        extra.get("forum_publish_error")
        or extra.get("research_forum_error")
    )
    research_loop_report = extra.get("research_loop_report")
    synthesis = extra.get("research_synthesis")
    forum_comment_mode = extra.get("forum_comment_mode")
    forum_kickoff_posted = extra.get("forum_kickoff_posted")
    forum_kickoff_error = extra.get("forum_kickoff_error")
    coding_proposal_payload = extra.get("coding_proposal")
    coding_job_payload = extra.get("coding_job")
    canonical_prompt_override = _coerce_str(extra.get("canonical_prompt_override"))
    latest_continuation_prompt = _coerce_str(
        extra.get("latest_continuation_prompt")
    )
    resumed_thread_id = extra.get("resumed_thread_id")

    state_value = getattr(session, "state", None)
    state_label = getattr(state_value, "value", state_value) or "unknown"
    session_id = getattr(session, "session_id", None) or "unknown"
    task_type = getattr(session, "task_type", None) or "unknown"

    lines = [
        "현재 engineering-agent 세션 상태를 확인했어요.",
        "",
        f"- 세션: `{session_id}`",
        f"- 상태: {state_label}",
        f"- 종류: {task_type}",
        f"- research_pack: {'있음' if research_pack else '없음'}",
    ]

    # P0-H stage 2 — gateway 가 박은 repo / mode / topology / branch /
    # PR / RepoContract / Obsidian mirror 정보. 값이 없으면 라인 자체 생략.
    github_target_payload = extra.get("github_target")
    repository = None
    pr_number = None
    branch_name = None
    if isinstance(github_target_payload, Mapping) and github_target_payload:
        owner = _coerce_str(github_target_payload.get("owner"))
        repo = _coerce_str(github_target_payload.get("repo"))
        if owner and repo:
            repository = f"{owner}/{repo}"
        if _coerce_str(github_target_payload.get("kind")) == "pull_request":
            pr_number = github_target_payload.get("number")
        branch_name = _coerce_str(
            github_target_payload.get("branch_or_sha")
        )
    explicit_branch = _coerce_str(extra.get("branch_name"))
    if explicit_branch:
        branch_name = explicit_branch
    explicit_pr = extra.get("pull_request_number")
    if explicit_pr is not None:
        pr_number = explicit_pr

    repo_value = _coerce_str(extra.get("repository")) or repository
    if repo_value:
        lines.append(f"- repo: `{repo_value}`")

    work_mode_value = _coerce_str(extra.get("work_mode"))
    if work_mode_value:
        lines.append(f"- mode: `{work_mode_value}`")
    topology_value = _coerce_str(extra.get("topology"))
    if topology_value:
        lines.append(f"- topology: `{topology_value}`")
    scope_value = _coerce_str(extra.get("scope"))
    if scope_value:
        lines.append(f"- scope: `{scope_value}`")

    if branch_name:
        lines.append(f"- branch: `{branch_name}`")
    if pr_number is not None:
        lines.append(f"- PR: #{pr_number}")

    repo_contract_payload = extra.get("repo_contract")
    if isinstance(repo_contract_payload, Mapping) and repo_contract_payload:
        detected = not bool(repo_contract_payload.get("fallback"))
        summary = _coerce_str(
            extra.get("repo_contract_summary")
        ) or _coerce_str(repo_contract_payload.get("summary_line"))
        if summary:
            lines.append(f"- repo contract: {summary}")
        else:
            lines.append(
                f"- repo contract detected: {'예' if detected else '아니오 (Yule 기본 규칙)'}"
            )

    obsidian_mirror_path = _coerce_str(extra.get("obsidian_mirror_path"))
    if obsidian_mirror_path:
        lines.append(f"- Obsidian mirror: `{obsidian_mirror_path}`")

    # P0-I stage 3 — enforcement surface. 값 없으면 라인 자체 생략.
    tracking_payload = extra.get("tracking_validation")
    if isinstance(tracking_payload, Mapping) and tracking_payload:
        status_value = _coerce_str(tracking_payload.get("status"))
        blocked = bool(tracking_payload.get("blocked"))
        missing = tracking_payload.get("missing_links") or ()
        allowed_ex = bool(tracking_payload.get("allowed_via_contract_exception"))
        if status_value == "ok":
            lines.append("- tracking chain: ✅ complete")
        elif status_value == "standalone_no_target":
            lines.append(
                "- tracking chain: ℹ️ GitHub target 없음 (research/discussion only)"
            )
        else:
            flag = "⚠️" if blocked else "ℹ️"
            missing_text = (
                ", ".join(str(m) for m in missing) if missing else "unknown"
            )
            suffix = " (RepoContract 예외 적용)" if allowed_ex else ""
            lines.append(
                f"- tracking chain: {flag} missing {missing_text}{suffix}"
            )

    growth_ledger = extra.get("growth_ledger")
    if isinstance(growth_ledger, list) and growth_ledger:
        try:
            from yule_engineering.agents.lifecycle.growth_ledger import summarize_for_status

            growth_line = summarize_for_status(extra)
        except Exception:  # noqa: BLE001
            growth_line = f"🌱 growth ledger: {len(growth_ledger)} events"
        if growth_line:
            lines.append(f"- {growth_line}")

    pr_slice_payload = extra.get("pr_slice_classification")
    if isinstance(pr_slice_payload, Mapping) and pr_slice_payload:
        primary = _coerce_str(pr_slice_payload.get("primary_slice"))
        warning = bool(pr_slice_payload.get("size_warning"))
        if primary:
            warning_tag = " ⚠️ size > 800 lines" if warning else ""
            lines.append(f"- PR slice: `{primary}`{warning_tag}")

    vault_push_audit = extra.get("vault_push_audit")
    if isinstance(vault_push_audit, list) and vault_push_audit:
        last = vault_push_audit[-1]
        if isinstance(last, Mapping):
            status_value = _coerce_str(last.get("status")) or "unknown"
            action = _coerce_str(last.get("action")) or "vault_action"
            if status_value == "not_configured":
                reason = (
                    _coerce_str(last.get("not_configured_reason"))
                    or _coerce_str(extra.get("vault_push_not_configured_reason"))
                    or "unknown"
                )
                lines.append(
                    f"- vault {action}: ⚠️ not configured ({reason})"
                )
            elif status_value == "queued_for_approval":
                lines.append(f"- vault {action}: 📬 queued for approval")
            elif status_value == "queued_auto":
                lines.append(f"- vault {action}: 📦 queued (auto)")

    coding_status_line = _format_coding_status_line(
        coding_proposal_payload, coding_job_payload
    )
    if coding_status_line:
        lines.append(coding_status_line)

    if canonical_prompt_override:
        canonical_short = canonical_prompt_override
        if len(canonical_short) > 160:
            canonical_short = canonical_short[:157] + "..."
        lines.append(f"- canonical 작업 prompt: {canonical_short}")
    if latest_continuation_prompt and (
        not canonical_prompt_override
        or latest_continuation_prompt != canonical_prompt_override
    ):
        cont_short = latest_continuation_prompt
        if len(cont_short) > 160:
            cont_short = cont_short[:157] + "..."
        lines.append(f"- 최근 continuation prompt: {cont_short}")
    if resumed_thread_id is not None:
        lines.append(f"- 이어붙인 thread id: `{resumed_thread_id}`")

    if forum_thread_id or forum_thread_url:
        thread_label = forum_thread_url or f"thread `{forum_thread_id}`"
        lines.append(f"- 운영-리서치 forum: 게시됨 ({thread_label})")
    elif forum_publish_error:
        lines.append("- 운영-리서치 forum: 게시 실패")
        lines.append(f"  · 마지막 오류: {forum_publish_error}")
    elif research_pack:
        lines.append("- 운영-리서치 forum: 아직 게시되지 않음 (자료는 수집 완료)")
    else:
        lines.append("- 운영-리서치 forum: 자료 수집 전이라 게시 단계가 아님")

    # Forum comment mode signals — only meaningful once the forum
    # publish actually ran (so we condition on having a thread or an
    # explicit error). In member-bots mode we explain that per-role
    # comments come from each member bot, not the gateway.
    if forum_comment_mode == "member-bots":
        lines.append("- 모드: member-bots (각 멤버 봇이 자기 계정으로 댓글)")
        # Phase B canonical names (research_open_call_*) override the
        # legacy forum_kickoff_* keys when both are present so the
        # diagnostic always describes the latest writer's intent.
        kickoff_posted = extra.get("research_open_call_posted")
        kickoff_error = extra.get("research_open_call_error")
        if kickoff_posted is None and forum_kickoff_posted is not None:
            kickoff_posted = forum_kickoff_posted
            kickoff_error = forum_kickoff_error
        if kickoff_posted is True:
            lines.append("  · open-call directive: 게시 완료")
        elif kickoff_posted is False:
            reason = kickoff_error or "원인 미확인"
            lines.append(f"  · open-call directive: 게시 실패 — {reason}")
        # Always close with a pointer to where the actual role comments
        # land so the operator knows the gateway summary isn't where to
        # judge member bot work.
        lines.append(
            "  · 후속 댓글은 운영-리서치 thread에서 직접 확인해 주세요."
        )
    elif forum_comment_mode == "gateway":
        lines.append("- 모드: gateway (역할별 댓글을 게이트웨이가 직접 게시)")

    role_turns = extra.get("role_turns")
    if isinstance(role_turns, Mapping) and role_turns:
        # Phase B activity log — show each role that actually spoke (or
        # tried to). Sorted by role name for stable diagnostic output.
        lines.append("- 역할 활동 기록:")
        for role_name in sorted(role_turns.keys()):
            entry = role_turns.get(role_name)
            if not isinstance(entry, Mapping):
                continue
            status = entry.get("status") or "?"
            kind = entry.get("kind") or "?"
            posted_at = entry.get("posted_at")
            error = entry.get("error")
            descriptor = f"{role_name}: {status} ({kind}"
            if posted_at:
                descriptor += f", {posted_at}"
            descriptor += ")"
            if error:
                descriptor += f" — {error}"
            lines.append(f"  · {descriptor}")

    # Phase 5 — surface the role-scoped research outcomes recorded by
    # Phase 4's ``record_role_research_result``. Answers "누가 어디까지
    # 자료를 모았는지" without re-running collection: each role line
    # shows provider, source count, status, and a one-line top finding.
    role_research_results = extra.get("role_research_results")
    if isinstance(role_research_results, Mapping) and role_research_results:
        lines.append("- 역할 연구 결과:")
        for role_name in sorted(role_research_results.keys()):
            record = role_research_results.get(role_name)
            if not isinstance(record, Mapping):
                continue
            status = str(record.get("status") or "?")
            provider = record.get("provider")
            source_count = record.get("source_count") or 0
            try:
                source_count = int(source_count)
            except (TypeError, ValueError):
                source_count = 0
            descriptor = f"{role_name}: {status}"
            if provider:
                descriptor += f" (provider: {provider}, {source_count}건)"
            else:
                descriptor += f" ({source_count}건)"
            error = record.get("error")
            if error:
                descriptor += f" — {error}"
            lines.append(f"  · {descriptor}")
            top_findings = record.get("top_findings") or []
            if isinstance(top_findings, list) and top_findings:
                first = str(top_findings[0]).strip()
                if first:
                    if len(first) > 120:
                        first = first[:117] + "..."
                    lines.append(f"    · 핵심: {first}")

    # Phase 5 — activity log summary. Counts each event type and shows
    # the last activity timestamp + last failure (if any) so the
    # operator can answer "왜 멈췄지?" / "마지막으로 무엇이 일어났지?"
    # at a glance without scanning the full audit trail.
    role_activity_log = extra.get("role_activity_log")
    if isinstance(role_activity_log, list) and role_activity_log:
        counts: dict[str, int] = {}
        last_event: Optional[Mapping[str, Any]] = None
        last_failure: Optional[Mapping[str, Any]] = None
        for raw_event in role_activity_log:
            if not isinstance(raw_event, Mapping):
                continue
            event_type = str(raw_event.get("event_type") or "?")
            counts[event_type] = counts.get(event_type, 0) + 1
            last_event = raw_event
            status = str(raw_event.get("status") or "")
            if status and status != "ok":
                last_failure = raw_event
        if counts:
            counts_text = ", ".join(
                f"{kind}={counts[kind]}" for kind in sorted(counts.keys())
            )
            lines.append(f"- 활동 로그: {counts_text}")
        if last_event:
            timestamp = last_event.get("timestamp") or "?"
            role_name = last_event.get("role") or "?"
            event_type = last_event.get("event_type") or "?"
            lines.append(
                f"  · 마지막 이벤트: {timestamp} {role_name} {event_type}"
            )
        if last_failure and last_failure is not last_event:
            timestamp = last_failure.get("timestamp") or "?"
            role_name = last_failure.get("role") or "?"
            event_type = last_failure.get("event_type") or "?"
            err = last_failure.get("error") or last_failure.get("status") or ""
            tail = f" — {err}" if err else ""
            lines.append(
                f"  · 마지막 실패: {timestamp} {role_name} {event_type}{tail}"
            )

    if research_loop_report:
        report_error = None
        report_status = None
        if isinstance(research_loop_report, Mapping):
            report_error = research_loop_report.get("error")
            report_status = research_loop_report.get("forum_status_message")
        else:
            report_error = getattr(research_loop_report, "error", None)
            report_status = getattr(
                research_loop_report, "forum_status_message", None
            )
        if report_error:
            lines.append(f"- research loop 오류: {report_error}")
        elif report_status:
            short = " ".join(str(report_status).split())
            if len(short) > 160:
                short = short[:157] + "..."
            lines.append(f"- 최근 보고: {short}")

    if synthesis:
        lines.append("- tech-lead synthesis: 기록됨")
    elif research_pack:
        lines.append("- tech-lead synthesis: 아직 기록되지 않음")

    # Phase 4 — surface role_selection + work_report so the user can
    # see *who* participated and *whether* a deliverable already
    # landed. ``active_research_roles`` comes from the role_selection
    # module; ``work_report`` is the snapshot the gateway posts at
    # lifecycle close.
    active_roles_value = extra.get("active_research_roles")
    if isinstance(active_roles_value, list) and active_roles_value:
        role_names = ", ".join(str(r) for r in active_roles_value if r)
        if role_names:
            selection_source = extra.get("role_selection_source") or "?"
            lines.append(
                f"- 활성 role: {role_names} (선정: {selection_source})"
            )

    work_report_payload = extra.get("work_report")
    if isinstance(work_report_payload, Mapping):
        title = str(work_report_payload.get("title") or "?")
        if len(title) > 80:
            title = title[:77] + "..."
        requires_change = bool(
            work_report_payload.get("requires_code_change")
        )
        code_flag = (
            "코드 수정 필요"
            if requires_change
            else "코드 수정 없음"
        )
        ref_count = work_report_payload.get("reference_count") or 0
        stop_reason = work_report_payload.get("research_stop_reason")
        # Phase 3 stabilisation — status is the load-bearing field that
        # tells the operator whether the report is a draft (interim),
        # blocked (insufficient), ready, or final.
        status = str(work_report_payload.get("status") or "?")
        missing_roles = work_report_payload.get("missing_roles") or []
        meta_bits = [f"status={status}", f"자료 {ref_count}건", code_flag]
        if stop_reason:
            meta_bits.append(f"stop: {stop_reason}")
        if isinstance(missing_roles, list) and missing_roles:
            meta_bits.append(
                "미완료 role: " + ", ".join(str(r) for r in missing_roles)
            )
        lines.append(
            f"- 업무 보고서: 작성됨 — \"{title}\" · "
            + " · ".join(meta_bits)
        )
    elif synthesis:
        lines.append("- 업무 보고서: 아직 미작성")

    progress_notes = tuple(getattr(session, "progress_notes", ()) or ())
    if progress_notes:
        last = progress_notes[-1]
        last_short = " ".join(str(last).split())
        if len(last_short) > 160:
            last_short = last_short[:157] + "..."
        lines.append(f"- 마지막 진행 노트: {last_short}")

    # Phase E: surface the structured diagnostic helper signals so the
    # operator sees "왜 멈췄는지" without re-deriving the rules.
    # ``primary_signal`` skips info-only signals so we never crowd the
    # response with "research_pack 미수집" noise when the session is
    # genuinely just at the start.
    report = diagnose_session(session)
    actionable = tuple(s for s in report.signals if s.severity != "info")
    if actionable:
        lines.append("")
        lines.append("감지된 다음 단계:")
        for signal in actionable:
            tag = _STATUS_SEVERITY_TAGS.get(signal.severity, signal.severity)
            lines.append(f"- {tag} {signal.title}")
            if signal.detail:
                detail = " ".join(str(signal.detail).split())
                if len(detail) > 200:
                    detail = detail[:197] + "..."
                lines.append(f"  · 원인: {detail}")
            if signal.propose:
                propose = " ".join(str(signal.propose).split())
                if len(propose) > 200:
                    propose = propose[:197] + "..."
                lines.append(f"  · 제안: {propose}")

    if is_member_bot_question:
        lines.append("")
        lines.extend(render_member_bot_summary(report).splitlines())

    lines.append("")
    lines.append(
        "추가로 보고 싶은 항목(예: 출처 목록, role take 진행)을 알려 주시면 그 부분만 더 자세히 정리해 드릴게요."
    )
    return "\n".join(lines)


_STATUS_SEVERITY_TAGS = {
    "failed": "[FAILED]",
    "blocked": "[BLOCKED]",
    "stale": "[STALE]",
    "info": "[INFO]",
}


_MEMBER_BOT_PHRASES = (
    "멤버 봇",
    "멤버봇",
    "역할 봇",
    "역할봇",
    "member bot",
    "member-bot",
)


def _asks_about_member_bots(message_text: str) -> bool:
    from .intent_detection import _normalize

    normalized = _normalize(message_text)
    return any(phrase in normalized for phrase in _MEMBER_BOT_PHRASES)


__all__ = (
    "_asks_about_member_bots",
    "_coerce_str",
    "_format_coding_status_line",
    "_open_states_set",
    "_safe_list_sessions",
    "_MEMBER_BOT_PHRASES",
    "_STATUS_SEVERITY_TAGS",
    "format_blocked_reason_response",
    "format_change_direction_response",
    "format_continue_existing_response",
    "format_session_count_response",
    "format_session_list_response",
    "format_status_diagnostic_response",
)
