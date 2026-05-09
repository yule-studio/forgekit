"""ContextPack — discussion mode가 사용하는 입력 묶음.

마스터 플랜 §7.2 / §8.1을 그대로 따른다. 하나의 사용자 메시지가 들어오면
tech-lead는 다음 묶음을 본다:

- 현재 user message
- 최근 thread 요약
- session.extra 요약 (research_pack 메타, coding_proposal 등)
- 관련 issue / PR 요약
- 관련 Obsidian note (RelevantMemorySelector가 골라준 것)
- 관련 코드/파일 힌트
- role profile + role research profile

본 모듈은 순수 dataclass + builder만 둔다. 외부 fetch는 호출자(주로
gateway 채널 라우터, forum hook)가 책임진다 — issue/PR/Obsidian/코드
어디서 끌어오는지는 환경에 따라 다르고, builder는 받은 입력을 묶어 주는
얇은 합성기 역할만 한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Sequence


# ---------------------------------------------------------------------------
# 작은 reference dataclass — 모두 frozen, 외부 fetch 결과를 그대로 받는다.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ThreadMessage:
    """thread 내 발화 한 줄.

    ``role``은 ``user`` / ``tech-lead`` / ``backend-engineer`` 등 자유 라벨.
    ``content``는 단일 문자열. 길이는 builder가 cap한다.
    """

    role: str
    content: str
    posted_at: Optional[str] = None


@dataclass(frozen=True)
class ObsidianNoteRef:
    """Obsidian vault에서 retrieval된 note 한 건의 메타데이터."""

    title: str
    path: Optional[str] = None
    summary: Optional[str] = None
    tags: Sequence[str] = field(default_factory=tuple)
    kind: Optional[str] = None  # research / decision / reference / task-log
    project: Optional[str] = None
    updated_at: Optional[str] = None


@dataclass(frozen=True)
class GithubIssueRef:
    """GitHub issue 한 건. issue body는 별도 fetch 후 ``summary``로 줄여서."""

    number: int
    title: str
    state: Optional[str] = None  # open / closed
    summary: Optional[str] = None
    url: Optional[str] = None
    labels: Sequence[str] = field(default_factory=tuple)
    repo: Optional[str] = None


@dataclass(frozen=True)
class GithubPRRef:
    """GitHub PR 한 건. ``state``는 open / closed / merged."""

    number: int
    title: str
    state: Optional[str] = None
    summary: Optional[str] = None
    url: Optional[str] = None
    branch: Optional[str] = None
    repo: Optional[str] = None


@dataclass(frozen=True)
class CodeHint:
    """관련 파일/심볼 힌트.

    실제 코드 본문을 들고 다니지는 않는다 — 경로 + 심볼 이름 + 한 줄
    이유만. 본문이 필요하면 호출자가 별도로 가져온다.
    """

    path: str
    symbol: Optional[str] = None
    summary: Optional[str] = None
    line: Optional[int] = None


# ---------------------------------------------------------------------------
# ContextPack
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ContextPack:
    """tech-lead가 한 요청을 판단할 때 사용하는 입력 묶음.

    어떤 슬롯도 비어 있을 수 있다 — 외부 source가 미설정이면 비운 채로
    들어와서 ``DiscussionSynthesizer``가 "이 부분은 비어 있다"를 명시
    적으로 출력한다.
    """

    current_message: str
    session_id: Optional[str] = None
    task_type: Optional[str] = None
    suggested_task_type: Optional[str] = None
    role_for_research: str = "engineering-agent/tech-lead"
    recent_thread: Sequence[ThreadMessage] = field(default_factory=tuple)
    thread_summary: Optional[str] = None
    session_extra_summary: Optional[str] = None
    related_issues: Sequence[GithubIssueRef] = field(default_factory=tuple)
    related_prs: Sequence[GithubPRRef] = field(default_factory=tuple)
    relevant_notes: Sequence[ObsidianNoteRef] = field(default_factory=tuple)
    code_hints: Sequence[CodeHint] = field(default_factory=tuple)
    role_profile_summary: Optional[str] = None
    role_research_profile_summary: Optional[str] = None
    write_requested: bool = False
    write_blocked_reason: Optional[str] = None
    blockers: Sequence[str] = field(default_factory=tuple)
    extra: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Mapping[str, Any]:
        """LLM seam / 디버그 dump용 직렬화. dataclass는 평면 dict로."""

        return {
            "current_message": self.current_message,
            "session_id": self.session_id,
            "task_type": self.task_type,
            "suggested_task_type": self.suggested_task_type,
            "role_for_research": self.role_for_research,
            "recent_thread": [
                {
                    "role": m.role,
                    "content": m.content,
                    "posted_at": m.posted_at,
                }
                for m in self.recent_thread
            ],
            "thread_summary": self.thread_summary,
            "session_extra_summary": self.session_extra_summary,
            "related_issues": [
                {
                    "number": i.number,
                    "title": i.title,
                    "state": i.state,
                    "summary": i.summary,
                    "url": i.url,
                    "labels": list(i.labels),
                    "repo": i.repo,
                }
                for i in self.related_issues
            ],
            "related_prs": [
                {
                    "number": p.number,
                    "title": p.title,
                    "state": p.state,
                    "summary": p.summary,
                    "url": p.url,
                    "branch": p.branch,
                    "repo": p.repo,
                }
                for p in self.related_prs
            ],
            "relevant_notes": [
                {
                    "title": n.title,
                    "path": n.path,
                    "summary": n.summary,
                    "tags": list(n.tags),
                    "kind": n.kind,
                    "project": n.project,
                    "updated_at": n.updated_at,
                }
                for n in self.relevant_notes
            ],
            "code_hints": [
                {
                    "path": h.path,
                    "symbol": h.symbol,
                    "summary": h.summary,
                    "line": h.line,
                }
                for h in self.code_hints
            ],
            "role_profile_summary": self.role_profile_summary,
            "role_research_profile_summary": self.role_research_profile_summary,
            "write_requested": self.write_requested,
            "write_blocked_reason": self.write_blocked_reason,
            "blockers": list(self.blockers),
            "extra": dict(self.extra),
        }


# ---------------------------------------------------------------------------
# Builder — 외부 source는 callable seam으로 주입
# ---------------------------------------------------------------------------


@dataclass
class ContextPackBuilder:
    """ContextPack 합성기.

    외부 source는 모두 콜러블 seam으로 받는다. 각 seam은 실패해도 builder
    가 멈추면 안 된다 — 실패 시 해당 슬롯을 비우고 ``blockers``에 사람이
    읽기 쉬운 한 줄을 남긴다.

    seam 시그니처:

    - ``thread_loader(session_id) -> Iterable[ThreadMessage]``
    - ``issue_loader(query) -> Iterable[GithubIssueRef]``
    - ``pr_loader(query) -> Iterable[GithubPRRef]``
    - ``note_loader(query) -> Iterable[ObsidianNoteRef]``
    - ``code_hint_loader(query) -> Iterable[CodeHint]``
    - ``role_profile_loader(role) -> Optional[str]`` (역할 프로필 요약 한 단락)
    - ``role_research_profile_loader(role) -> Optional[str]`` (research 프로필 요약)

    ``memory_selector``는 ``RelevantMemorySelector`` 호환 콜러블. 받은
    note 후보를 추려서 돌려준다. 없으면 raw note 결과를 그대로 사용한다.
    """

    thread_loader: Optional[Any] = None
    issue_loader: Optional[Any] = None
    pr_loader: Optional[Any] = None
    note_loader: Optional[Any] = None
    code_hint_loader: Optional[Any] = None
    role_profile_loader: Optional[Any] = None
    role_research_profile_loader: Optional[Any] = None
    memory_selector: Optional[Any] = None
    max_thread_messages: int = 12
    max_issues: int = 5
    max_prs: int = 5
    max_notes: int = 5
    max_code_hints: int = 6
    max_thread_message_chars: int = 280

    def build(
        self,
        *,
        message_text: str,
        session: Optional[Any] = None,
        suggested_task_type: Optional[str] = None,
        role_for_research: str = "engineering-agent/tech-lead",
        retrieval_query: Optional[str] = None,
    ) -> ContextPack:
        """주어진 메시지 + 선택적 :class:`WorkflowSession`로 pack을 만든다.

        *retrieval_query*는 issue/PR/note/code 검색용 쿼리. 비어 있으면
        message_text를 그대로 쓴다. 호출자가 더 정제된 쿼리(예: 토픽
        키워드만)를 만들었다면 넘겨주면 된다.
        """

        query = (retrieval_query or message_text or "").strip()
        blockers: list[str] = []

        # 1. session 정보
        session_id = getattr(session, "session_id", None)
        task_type = getattr(session, "task_type", None)
        write_requested = bool(getattr(session, "write_requested", False))
        write_blocked_reason = getattr(session, "write_blocked_reason", None)
        extra = dict(getattr(session, "extra", {}) or {}) if session else {}
        session_extra_summary = self._summarize_session_extra(extra)

        # 2. thread loader
        recent_thread, thread_summary, thread_blocker = self._collect_thread(
            session_id=session_id,
            session=session,
        )
        if thread_blocker:
            blockers.append(thread_blocker)

        # 3. issue / PR loader
        related_issues, issue_blocker = self._collect_with_seam(
            self.issue_loader, query, self.max_issues, "issue_loader"
        )
        if issue_blocker:
            blockers.append(issue_blocker)
        related_prs, pr_blocker = self._collect_with_seam(
            self.pr_loader, query, self.max_prs, "pr_loader"
        )
        if pr_blocker:
            blockers.append(pr_blocker)

        # 4. note loader + memory selector
        raw_notes, note_blocker = self._collect_with_seam(
            self.note_loader, query, self.max_notes * 4, "note_loader"
        )
        if note_blocker:
            blockers.append(note_blocker)
        relevant_notes = self._select_relevant_notes(
            raw_notes,
            query=query,
            task_type=task_type or suggested_task_type,
            role=role_for_research,
        )

        # 5. code hint loader
        code_hints, code_blocker = self._collect_with_seam(
            self.code_hint_loader, query, self.max_code_hints, "code_hint_loader"
        )
        if code_blocker:
            blockers.append(code_blocker)

        # 6. role profile + research profile
        role_profile_summary = self._safe_call(
            self.role_profile_loader, role_for_research, "role_profile_loader", blockers
        )
        role_research_profile_summary = self._safe_call(
            self.role_research_profile_loader,
            role_for_research,
            "role_research_profile_loader",
            blockers,
        )

        return ContextPack(
            current_message=message_text or "",
            session_id=session_id,
            task_type=task_type,
            suggested_task_type=suggested_task_type,
            role_for_research=role_for_research,
            recent_thread=tuple(recent_thread),
            thread_summary=thread_summary,
            session_extra_summary=session_extra_summary,
            related_issues=tuple(related_issues),
            related_prs=tuple(related_prs),
            relevant_notes=tuple(relevant_notes),
            code_hints=tuple(code_hints),
            role_profile_summary=role_profile_summary,
            role_research_profile_summary=role_research_profile_summary,
            write_requested=write_requested,
            write_blocked_reason=write_blocked_reason,
            blockers=tuple(blockers),
            extra={"session_extra_keys": sorted(extra.keys())} if extra else {},
        )

    # ---- 내부 helpers ------------------------------------------------------

    def _collect_thread(
        self,
        *,
        session_id: Optional[str],
        session: Optional[Any],
    ) -> tuple[list[ThreadMessage], Optional[str], Optional[str]]:
        if self.thread_loader is None:
            return [], None, None
        try:
            raw = self.thread_loader(session_id) if session_id else []
        except TypeError:
            try:
                raw = self.thread_loader()
            except Exception as exc:  # noqa: BLE001
                return [], None, f"thread_loader 호출 실패: {exc}"
        except Exception as exc:  # noqa: BLE001
            return [], None, f"thread_loader 호출 실패: {exc}"
        messages: list[ThreadMessage] = []
        for item in raw or ():
            if isinstance(item, ThreadMessage):
                msg = item
            elif isinstance(item, Mapping):
                msg = ThreadMessage(
                    role=str(item.get("role") or "user"),
                    content=str(item.get("content") or ""),
                    posted_at=item.get("posted_at"),
                )
            else:
                continue
            content = msg.content
            if len(content) > self.max_thread_message_chars:
                content = content[: self.max_thread_message_chars - 1].rstrip() + "…"
                msg = ThreadMessage(role=msg.role, content=content, posted_at=msg.posted_at)
            messages.append(msg)
        if len(messages) > self.max_thread_messages:
            messages = messages[-self.max_thread_messages :]
        summary = _summarize_thread(messages) if messages else None
        return messages, summary, None

    def _collect_with_seam(
        self,
        seam: Optional[Any],
        query: str,
        cap: int,
        seam_name: str,
    ) -> tuple[list[Any], Optional[str]]:
        if seam is None:
            return [], None
        try:
            raw = seam(query)
        except TypeError:
            try:
                raw = seam()
            except Exception as exc:  # noqa: BLE001
                return [], f"{seam_name} 호출 실패: {exc}"
        except Exception as exc:  # noqa: BLE001
            return [], f"{seam_name} 호출 실패: {exc}"
        items = list(raw or ())
        return items[:cap], None

    def _select_relevant_notes(
        self,
        raw_notes: Sequence[Any],
        *,
        query: str,
        task_type: Optional[str],
        role: Optional[str],
    ) -> Sequence[ObsidianNoteRef]:
        notes: list[ObsidianNoteRef] = []
        for item in raw_notes:
            if isinstance(item, ObsidianNoteRef):
                notes.append(item)
            elif isinstance(item, Mapping):
                notes.append(
                    ObsidianNoteRef(
                        title=str(item.get("title") or "(제목 없음)"),
                        path=item.get("path"),
                        summary=item.get("summary"),
                        tags=tuple(item.get("tags") or ()),
                        kind=item.get("kind"),
                        project=item.get("project"),
                        updated_at=item.get("updated_at"),
                    )
                )
        if not notes:
            return []
        if self.memory_selector is None:
            return notes[: self.max_notes]
        try:
            picked = self.memory_selector(
                candidates=notes,
                query=query,
                task_type=task_type,
                role=role,
                limit=self.max_notes,
            )
        except TypeError:
            try:
                picked = self.memory_selector(notes)
            except Exception:  # noqa: BLE001
                picked = notes[: self.max_notes]
        except Exception:  # noqa: BLE001
            picked = notes[: self.max_notes]
        return tuple(picked)[: self.max_notes]

    def _safe_call(
        self,
        seam: Optional[Any],
        argument: Any,
        seam_name: str,
        blockers: list[str],
    ) -> Optional[str]:
        if seam is None:
            return None
        try:
            value = seam(argument)
        except TypeError:
            try:
                value = seam()
            except Exception as exc:  # noqa: BLE001
                blockers.append(f"{seam_name} 호출 실패: {exc}")
                return None
        except Exception as exc:  # noqa: BLE001
            blockers.append(f"{seam_name} 호출 실패: {exc}")
            return None
        if value is None:
            return None
        return str(value).strip() or None

    @staticmethod
    def _summarize_session_extra(extra: Mapping[str, Any]) -> Optional[str]:
        """session.extra에서 toi 판단에 도움되는 키만 추려 한 줄 요약.

        세부 payload는 절대 dump하지 않는다 — secret이 섞일 위험. 키 존재
        여부만 체크해서 "research_pack: 있음, coding_proposal: 없음" 식.
        """

        if not extra:
            return None
        relevant_keys = (
            "research_pack",
            "research_synthesis",
            "coding_proposal",
            "coding_job",
            "work_report",
            "research_loop_report",
            "active_research_roles",
            "role_research_results",
            "forum_thread_id",
            "research_forum_thread_id",
        )
        bits: list[str] = []
        for key in relevant_keys:
            value = extra.get(key)
            if value is None or (hasattr(value, "__len__") and len(value) == 0):
                continue
            bits.append(f"{key}: 있음")
        if not bits:
            return None
        return ", ".join(bits)


def _summarize_thread(messages: Sequence[ThreadMessage]) -> str:
    """thread 요약 — 발화 횟수 + 가장 최근 user 발화 한 줄.

    매우 단순한 휴리스틱. 본격적인 LLM 요약은 후속 단계에서.
    """

    if not messages:
        return ""
    last_user = next(
        (m for m in reversed(messages) if "user" in m.role.lower()), None
    )
    last_user_text = last_user.content.strip() if last_user else ""
    if len(last_user_text) > 160:
        last_user_text = last_user_text[:157] + "…"
    summary = f"최근 thread 발화 {len(messages)}건"
    if last_user_text:
        summary += f" · 마지막 user 발화: \"{last_user_text}\""
    return summary


__all__ = (
    "ContextPack",
    "ContextPackBuilder",
    "ObsidianNoteRef",
    "GithubIssueRef",
    "GithubPRRef",
    "CodeHint",
    "ThreadMessage",
)
