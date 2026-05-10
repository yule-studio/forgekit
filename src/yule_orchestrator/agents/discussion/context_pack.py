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


@dataclass(frozen=True)
class EngineeringKnowledgeRef:
    """`engineering_intelligence` 가 vault 에 쌓아둔 knowledge 한 건.

    ContextPackBuilder 가 retrieval seam 으로 받아서 ``ContextPack``의
    ``relevant_knowledge`` 슬롯에 그대로 끼워 넣는다. 외부 fetch는
    호출자(주로 vault index loader)가 책임지고, 본 dataclass 는 뷰
    전용 — 합성기/디버그 dump 가 사용한다.

    필드 의미는 :class:`engineering_intelligence.KnowledgeRecord` 와
    동일하지만, 본 모듈은 engineering_intelligence 를 import 하지 않는다
    (서로 독립적으로 import-able 해야 한다는 운영 원칙). 합성 어댑터는
    ``ContextPackBuilder._coerce_knowledge_ref`` 가 담당.

    ``share_scope`` / ``share_scope_reason`` 는 외부 surface (Discord,
    합성 응답, PR 본문) 가 어디까지 인용해도 되는지를 결정하는 boundary
    플래그다. 값이 없으면 ``"public"`` 으로 간주한다 — collector 가
    명시하지 않은 자료는 운영자 합의로 PUBLIC default 로 처리한다.

    ``score`` / ``signals`` / ``evidence_labels`` 은 retrieval 이 왜 이
    자료를 골랐는지를 사람이 읽을 수 있는 형태로 surface 에 남기기
    위한 슬롯이다. 합성 응답 / Obsidian decision note 가 "근거 자료"
    블록을 만들 때 그대로 사용한다.
    """

    title: str
    role: str
    topic_key: str = ""
    source_url: str = ""
    source_name: str = ""
    summary: Optional[str] = None
    axes: Sequence[str] = field(default_factory=tuple)
    rag_tags: Sequence[str] = field(default_factory=tuple)
    importance: Optional[str] = None
    collected_at: Optional[str] = None
    note_path: Optional[str] = None
    score: Optional[float] = None
    signals: Sequence[str] = field(default_factory=tuple)
    # Retrieval evidence + role-feed provenance are both preserved so the
    # discussion surface can explain why this row was picked and which role
    # axis feed produced it.
    evidence_labels: Sequence[str] = field(default_factory=tuple)
    share_scope: str = "public"
    share_scope_reason: str = ""
    matched_axes: Sequence[str] = field(default_factory=tuple)
    relevance_reason: Optional[str] = None


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
    relevant_knowledge: Sequence[EngineeringKnowledgeRef] = field(
        default_factory=tuple
    )
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
            "relevant_knowledge": [
                {
                    "title": k.title,
                    "role": k.role,
                    "topic_key": k.topic_key,
                    "source_url": k.source_url,
                    "source_name": k.source_name,
                    "summary": k.summary,
                    "axes": list(k.axes),
                    "rag_tags": list(k.rag_tags),
                    "importance": k.importance,
                    "collected_at": k.collected_at,
                    "note_path": k.note_path,
                    "score": k.score,
                    "signals": list(k.signals),
                    "evidence_labels": list(k.evidence_labels),
                    "share_scope": k.share_scope,
                    "share_scope_reason": k.share_scope_reason,
                    "matched_axes": list(k.matched_axes),
                    "relevance_reason": k.relevance_reason,
                }
                for k in self.relevant_knowledge
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

    def format_knowledge_evidence_block(
        self,
        *,
        max_items: int = 5,
        heading: str = "근거 자료 (engineering_intelligence)",
    ) -> str:
        """``relevant_knowledge`` 를 마크다운 evidence 블록으로 렌더한다.

        합성 응답 / Obsidian decision note / PR body footer 가 "이번
        토의에서 어떤 사내 자료가 근거로 쓰였는가" 를 사람에게 보여주기
        위한 구조화된 출력이다. 빈 리스트일 때는 빈 문자열을 반환해
        호출자가 if-string-truthy 로 분기할 수 있다.

        규칙:

        - ``share_scope=public`` — 제목 + 출처 링크 + summary 한 줄.
        - ``share_scope=team_internal`` — 제목 + 출처 링크 + 'team-
          internal' 라벨, summary 는 의도적으로 생략. vault 외부에서는
          본문 합성에 인용하지 않는다는 정책을 따른다.
        - ``share_scope=restricted`` — 제목/링크/summary 모두 가리고
          topic_key + share_scope_reason 만 노출. 외부에 자료가 있다는
          신호만 남긴다.

        retrieval 점수 / signal 은 ``evidence_labels`` 가 채워져 있으면
        한국어 라벨로, 아니면 raw signal 문자열로 노출한다.
        """

        if not self.relevant_knowledge:
            return ""
        lines: list[str] = [f"### {heading}"]
        for ref in list(self.relevant_knowledge)[: max(max_items, 0)]:
            lines.extend(_format_knowledge_evidence_lines(ref))
        return "\n".join(lines)

    def share_boundary_breakdown(self) -> Mapping[str, int]:
        """``relevant_knowledge`` 항목 수를 share_scope 별로 집계한다.

        외부 surface (Discord 다이제스트 / 합성 응답 / PR body) 가
        "이번 응답에 인용한 자료가 어떤 boundary 안에 있는지" 를
        한 눈에 보여줄 때 쓴다. 키는 항상 4 종 (`public`,
        `team_internal`, `restricted`, `total`) 이 모두 들어 있고
        값은 정수다 — 호출자가 dict[k] 접근으로 바로 쓸 수 있다.
        """

        counts = {"public": 0, "team_internal": 0, "restricted": 0}
        for ref in self.relevant_knowledge:
            scope = (ref.share_scope or "public").lower()
            if scope not in counts:
                scope = "public"
            counts[scope] += 1
        counts["total"] = sum(counts.values())
        return counts

    def knowledge_short_summary(self, *, max_topics: int = 2) -> str:
        """근거 자료를 한 줄로 요약한다 — 합성 응답 / 운영 dashboard 용.

        - 총 건수 + share_scope 별 카운트 (0 인 scope 는 생략).
        - 상위 ``max_topics`` 건의 제목 / topic_key — share_scope 정책을
          그대로 따른다 (restricted 는 topic_key 만, team_internal/public
          은 제목).
        - relevant_knowledge 가 비어 있으면 빈 문자열.

        예시 출력:
        ``"근거 자료 3건 (public 2 · team_internal 1) — Spring Security 인증 흐름 외 1건"``
        """

        refs = list(self.relevant_knowledge)
        if not refs:
            return ""
        breakdown = self.share_boundary_breakdown()
        scope_bits: list[str] = []
        for scope_key in ("public", "team_internal", "restricted"):
            value = breakdown.get(scope_key, 0)
            if value:
                scope_bits.append(f"{scope_key} {value}")
        scope_part = f" ({' · '.join(scope_bits)})" if scope_bits else ""
        topic_labels: list[str] = []
        for ref in refs[: max(max_topics, 0)]:
            scope = (ref.share_scope or "public").lower()
            if scope == "restricted":
                topic_labels.append(f"🔒 `{ref.topic_key or 'restricted'}`")
            else:
                title = (ref.title or "(제목 없음)").strip()
                if scope == "team_internal":
                    topic_labels.append(f"{title} (🔒 team-internal)")
                else:
                    topic_labels.append(title)
        topic_part = ""
        if topic_labels:
            extra = max(len(refs) - len(topic_labels), 0)
            preview = ", ".join(topic_labels)
            if extra:
                topic_part = f" — {preview} 외 {extra}건"
            else:
                topic_part = f" — {preview}"
        return f"근거 자료 {breakdown['total']}건{scope_part}{topic_part}"


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
    knowledge_loader: Optional[Any] = None
    knowledge_retriever: Optional[Any] = None
    max_thread_messages: int = 12
    max_issues: int = 5
    max_prs: int = 5
    max_notes: int = 5
    max_knowledge: int = 5
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

        # 5b. engineering_intelligence knowledge loader + retriever.
        raw_knowledge, knowledge_blocker = self._collect_with_seam(
            self.knowledge_loader,
            query,
            self.max_knowledge * 5,
            "knowledge_loader",
        )
        if knowledge_blocker:
            blockers.append(knowledge_blocker)
        relevant_knowledge = self._select_relevant_knowledge(
            raw_knowledge,
            query=query,
            task_type=task_type or suggested_task_type,
            role=role_for_research,
        )

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
            relevant_knowledge=tuple(relevant_knowledge),
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

    def _select_relevant_knowledge(
        self,
        raw: Sequence[Any],
        *,
        query: str,
        task_type: Optional[str],
        role: Optional[str],
    ) -> Sequence[EngineeringKnowledgeRef]:
        """Score + truncate engineering knowledge candidates.

        Two paths:

        1. ``knowledge_retriever`` is provided — call its
           ``with_signals`` method (or fall back to plain ``__call__``)
           so the deterministic scoring (typically
           :class:`engineering_intelligence.KnowledgeRetriever`) drives
           ordering. ``with_signals`` matches are unpacked so the role-
           feed provenance (``matched_axes``, ``relevance_reason``,
           ``signals``, ``score``) lands on the
           :class:`EngineeringKnowledgeRef`.
        2. No retriever — fall back to "first ``max_knowledge`` items
           after coercion / role match filter".
        """

        coerced = [self._coerce_knowledge_ref(item) for item in raw]
        coerced = [ref for ref in coerced if ref is not None]
        if not coerced:
            return ()
        if self.knowledge_retriever is None:
            # Lightweight fallback: prefer same-role refs, then keep
            # registry order, capped by ``max_knowledge``.
            normalized_role = (role or "").strip().lower().split("/")[-1]
            same_role = [
                r for r in coerced if r.role.lower() == normalized_role
            ]
            other = [
                r for r in coerced if r.role.lower() != normalized_role
            ]
            picked = (same_role + other)[: self.max_knowledge]
            return tuple(picked)

        # Prefer the retriever's ``with_signals`` form so we can carry
        # the score + signals through to the surface. The plain
        # ``__call__`` form just returns records and loses the "왜 이
        # 자료가 골라졌는가" trace, which is exactly what this PR is
        # trying to surface.
        with_signals = getattr(self.knowledge_retriever, "with_signals", None)
        if callable(with_signals):
            try:
                matches = with_signals(
                    candidates=raw,
                    query=query,
                    role=role,
                    task_type=task_type,
                    limit=self.max_knowledge,
                )
            except Exception:  # noqa: BLE001
                matches = ()
            normalized: list[EngineeringKnowledgeRef] = []
            for match in matches:
                ref = self._coerce_knowledge_ref(match)
                if ref is not None:
                    normalized.append(ref)
            if normalized:
                return tuple(normalized[: self.max_knowledge])
            # Fall through to the plain retriever — sometimes
            # ``with_signals`` returns nothing while the plain call
            # would, and we'd rather show *something* than nothing.

        try:
            picked = self.knowledge_retriever(
                candidates=raw,
                query=query,
                role=role,
                task_type=task_type,
                limit=self.max_knowledge,
            )
        except TypeError:
            try:
                picked = self.knowledge_retriever(raw)
            except TypeError:
                picked = coerced[: self.max_knowledge]
            except Exception:  # noqa: BLE001
                picked = coerced[: self.max_knowledge]
        normalized: list[EngineeringKnowledgeRef] = []
        for item in picked:
            ref = self._coerce_knowledge_ref(item)
            if ref is not None:
                normalized.append(ref)
        return tuple(normalized[: self.max_knowledge])

    @staticmethod
    def _coerce_knowledge_ref(value: Any) -> Optional[EngineeringKnowledgeRef]:
        """Best-effort projection of vault row / KnowledgeRecord / dict.

        Accepts:

        - ``EngineeringKnowledgeRef`` — passthrough.
        - ``KnowledgeMatch`` (duck-typed: has ``record`` / ``score`` /
          ``signals`` attrs; bonus: ``evidence_labels()`` method) — the
          score / signal trace is unpacked onto the resulting ref so
          the synthesizer can show "왜 이 자료가 골라졌는가" without a
          second lookup.
        - ``KnowledgeRecord`` / ``EngineeringKnowledgeItem`` — flat
          attribute walk.
        - Plain mapping — the dict carries the same field names.
        """

        if value is None:
            return None
        if isinstance(value, EngineeringKnowledgeRef):
            return value

        # KnowledgeMatch duck-typing: unwrap into (record, score, signals).
        score: Optional[float] = None
        signals_seq: tuple[str, ...] = ()
        evidence_labels_seq: tuple[str, ...] = ()
        matched_axes_seq: tuple[str, ...] = ()
        relevance_reason: Optional[str] = None
        record_attr = getattr(value, "record", None)
        if (
            record_attr is not None
            and not isinstance(value, Mapping)
            and hasattr(value, "signals")
        ):
            try:
                score = float(getattr(value, "score"))
            except (TypeError, ValueError):
                score = None
            signals_seq = tuple(getattr(value, "signals", ()) or ())
            label_method = getattr(value, "evidence_labels", None)
            if callable(label_method):
                try:
                    evidence_labels_seq = tuple(label_method())
                except Exception:  # noqa: BLE001
                    evidence_labels_seq = ()
            axes_raw = getattr(value, "matched_axes", ()) or ()
            matched_axes_seq = tuple(
                str(getattr(a, "value", a)) for a in axes_raw if a
            )
            relevance_reason = getattr(value, "relevance_reason", None) or None
            value = record_attr
        # KnowledgeRecord 와 EngineeringKnowledgeItem (engineering_intelligence
        # 패키지) 둘 다 있으면 직접 import 하지 않고 duck typing 으로 처리.
        title = getattr(value, "title", None)
        role = getattr(value, "role", None)
        if title and role and not isinstance(value, Mapping):
            axes_attr = getattr(value, "axes", ())
            axes_seq: list[str] = []
            for axis in axes_attr or ():
                axis_value = getattr(axis, "value", axis)
                if axis_value:
                    axes_seq.append(str(axis_value))
            importance_attr = getattr(value, "importance", None)
            importance_value = getattr(importance_attr, "value", importance_attr)
            share_attr = getattr(value, "share_scope", None)
            share_value = getattr(share_attr, "value", share_attr)
            share_scope = str(share_value) if share_value else "public"
            return EngineeringKnowledgeRef(
                title=str(title),
                role=str(role),
                topic_key=str(getattr(value, "topic_key", "") or ""),
                source_url=str(getattr(value, "source_url", "") or ""),
                source_name=str(getattr(value, "source_name", "") or ""),
                summary=getattr(value, "summary", None) or None,
                axes=tuple(axes_seq),
                rag_tags=tuple(getattr(value, "rag_tags", ()) or ()),
                importance=str(importance_value) if importance_value else None,
                collected_at=getattr(value, "collected_at", None) or None,
                note_path=getattr(value, "note_path", None),
                score=score if score is not None else getattr(value, "score", None),
                signals=signals_seq or tuple(getattr(value, "signals", ()) or ()),
                evidence_labels=evidence_labels_seq,
                share_scope=share_scope,
                share_scope_reason=str(
                    getattr(value, "share_scope_reason", "") or ""
                ),
                matched_axes=matched_axes_seq
                or tuple(
                    str(getattr(a, "value", a))
                    for a in (getattr(value, "matched_axes", ()) or ())
                    if a
                ),
                relevance_reason=relevance_reason
                or (getattr(value, "relevance_reason", None) or None),
            )
        if isinstance(value, Mapping):
            title_v = str(value.get("title") or "").strip()
            role_v = str(value.get("role") or "").strip()
            if not (title_v and role_v):
                return None
            axes_raw = value.get("axes") or ()
            axes_seq = [str(a) for a in axes_raw if a]
            matched_raw = value.get("matched_axes") or ()
            matched_seq = tuple(
                str(getattr(a, "value", a)) for a in matched_raw if a
            )
            mapping_signals = tuple(value.get("signals") or ()) or signals_seq
            mapping_labels = tuple(value.get("evidence_labels") or ()) or evidence_labels_seq
            mapping_score = value.get("score")
            return EngineeringKnowledgeRef(
                title=title_v,
                role=role_v,
                topic_key=str(value.get("topic_key") or ""),
                source_url=str(value.get("source_url") or ""),
                source_name=str(value.get("source_name") or ""),
                summary=value.get("summary") or None,
                axes=tuple(axes_seq),
                rag_tags=tuple(value.get("rag_tags") or ()),
                importance=value.get("importance"),
                collected_at=value.get("collected_at"),
                note_path=value.get("note_path"),
                score=mapping_score if mapping_score is not None else score,
                signals=mapping_signals,
                evidence_labels=mapping_labels,
                share_scope=str(value.get("share_scope") or "public"),
                share_scope_reason=str(value.get("share_scope_reason") or ""),
                matched_axes=matched_seq,
                relevance_reason=value.get("relevance_reason") or relevance_reason,
            )
        return None

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


_RESTRICTED_LINE_FALLBACK = "🔒 공개 제한된 자료"


def _format_knowledge_evidence_lines(ref: EngineeringKnowledgeRef) -> list[str]:
    """Format one ``EngineeringKnowledgeRef`` as evidence bullet lines.

    Splits into a heading bullet + an optional indented signal bullet
    so the operator can scan title-first and only drop into "왜 이
    자료가 골라졌는가" details when needed.
    """

    scope = (ref.share_scope or "public").lower()
    lines: list[str] = []
    if scope == "restricted":
        marker = _RESTRICTED_LINE_FALLBACK
        if ref.share_scope_reason:
            marker = f"{marker} — {ref.share_scope_reason}"
        else:
            marker = f"{marker} (`{ref.topic_key}`)"
        lines.append(f"- {marker}")
    else:
        title = ref.title or "(제목 없음)"
        if ref.source_url:
            head = f"- **{title}** — [{ref.source_name or '출처'}]({ref.source_url})"
        else:
            head = f"- **{title}**"
        if scope == "team_internal":
            head = f"{head} · 🔒 team-internal"
        lines.append(head)
        if scope == "public" and ref.summary:
            lines.append(f"  - 요약: {ref.summary}")
    detail_bits: list[str] = []
    if ref.role:
        detail_bits.append(f"role={ref.role}")
    if ref.score is not None:
        detail_bits.append(f"score={ref.score:.1f}")
    if ref.matched_axes:
        detail_bits.append("axes=" + ",".join(str(axis) for axis in ref.matched_axes))
    labels = list(ref.evidence_labels) if ref.evidence_labels else list(ref.signals)
    if labels:
        detail_bits.append("근거: " + ", ".join(labels))
    if ref.relevance_reason:
        detail_bits.append(f"설명: {ref.relevance_reason}")
    if detail_bits:
        lines.append(f"  - {' · '.join(detail_bits)}")
    return lines


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
