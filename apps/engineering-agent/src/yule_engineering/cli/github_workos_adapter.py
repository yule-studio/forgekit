"""G2 ↔ G3 protocol adapter for the ``yule github`` CLI surface.

Split out of :mod:`yule_engineering.cli.github_workos` along the
"subcommand per module" axis so the CLI handler module stays thin. This
module is **declaration-only** — it holds the schema-bridge between G2's
:class:`TriagePlan` and G3's :class:`TriagePlanLike` Protocol, with no
CLI dispatch logic.

G2's :class:`TriagePlan` and G3's modules (:mod:`branching`,
:mod:`pr_template`, :mod:`actions`) ship slightly different field
names — G2 calls the section ``scope`` / ``non_scope`` / ``decisions``
/ ``role_work_orders`` while G3's :class:`TriagePlanLike` Protocol
spells them ``in_scope`` / ``out_of_scope`` / ``approvals_needed`` /
``work_orders``. Each side is internally consistent + tested in
isolation; G6's job is to bridge them so a single ``yule github
plan-pr`` / ``smoke-pr`` flow can drive both.

Strategy: a small adapter that exposes both vocabularies (so the G3
Protocol getters see what they expect) and copies through the few
enrichments the CLI knows (issue number / repo / session id /
rendered title + body / labels / trace links).

Keeping the adapter inside the CLI layer — not the public
``agents.github_workos`` API — avoids leaking the schema-bridge into
the runtime where it could mask future model rename drift.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Optional, Sequence

from ..agents.github_workos.models import RoleWorkOrder, TriagePlan


@dataclass(frozen=True)
class _G3PlanAdapter:
    """Adapter exposing G2 :class:`TriagePlan` with G3 field aliases.

    Construct via :func:`_adapt_plan_for_g3`. Consumers call
    ``getattr`` and read the aliases (``in_scope``, ``out_of_scope``,
    …) without knowing they came from a G2 plan.
    """

    g2: TriagePlan
    title: str
    body: str
    issue_number: Optional[int]
    session_id: Optional[str]
    repo: Optional[str]
    base_branch: str
    source: str

    # ----- G3 field aliases ------------------------------------------------
    @property
    def primary_role(self) -> str:
        return self.g2.primary_role

    @property
    def autonomy_level(self) -> str:
        # G3 reads ``autonomy_level`` as a plain string for ranking;
        # G2 stores a :class:`PermissionLevel` enum so we surface the
        # ``L1`` / ``L2`` / ``L3`` short label most G3 callers grep for.
        raw = getattr(self.g2, "autonomy_level", None)
        value = getattr(raw, "value", str(raw))
        # value comes through as e.g. ``L2_PLAN`` — keep the level
        # prefix so G3's ``_autonomy_rank`` can match.
        return str(value).split("_", 1)[0]

    @property
    def in_scope(self) -> Sequence[str]:
        return tuple(self.g2.scope)

    @property
    def out_of_scope(self) -> Sequence[str]:
        return tuple(self.g2.non_scope)

    @property
    def test_plan(self) -> Sequence[str]:
        return tuple(self.g2.test_plan)

    @property
    def risks(self) -> Sequence[str]:
        return tuple(self.g2.hidden_risks)

    @property
    def approvals_needed(self) -> Sequence[str]:
        return tuple(self.g2.approval_required_actions)

    @property
    def work_orders(self) -> Sequence[Mapping[str, str]]:
        # G3's pr_template expects each order as a mapping with
        # ``autonomy_level`` / ``action`` / ``target`` keys. G2's
        # role_work_orders carry ``role`` / ``mission`` / ``expected_output``;
        # we project the most informative pair so the rendered "agent
        # work orders" block is non-empty when G2 produced any.
        out: list[Mapping[str, str]] = []
        for order in self.g2.role_work_orders or ():
            if not isinstance(order, RoleWorkOrder):
                continue
            out.append(
                {
                    "autonomy_level": self.autonomy_level,
                    "action": f"{order.role}: {order.mission}",
                    "target": order.expected_output,
                }
            )
        return out

    @property
    def labels(self) -> Sequence[str]:
        # G2 has no labels field. Production callers may layer labels
        # via ``additional_labels`` on build_github_action_plan; the CLI
        # just exposes an empty tuple here so the action plan honours
        # whatever the caller injects.
        return ()

    @property
    def excluded_roles(self) -> Sequence[str]:
        return tuple(self.g2.excluded_roles)

    @property
    def support_roles(self) -> Sequence[str]:
        return tuple(self.g2.support_roles)

    @property
    def rationale_by_role(self) -> Mapping[str, str]:
        return dict(self.g2.rationale_by_role)

    @property
    def request_type(self) -> str:
        return self.g2.request_type

    @property
    def coding_required(self) -> bool:
        return bool(self.g2.coding_required)

    @property
    def approval_required_before_write(self) -> bool:
        return bool(self.g2.approval_required_before_write)

    @property
    def suggested_branch(self) -> str:
        return self.g2.suggested_branch


def _adapt_plan_for_g3(
    plan: TriagePlan,
    *,
    title: str,
    body: str,
    issue_number: Optional[int],
    session_id: Optional[str],
    repo: Optional[str],
    base_branch: str = "main",
    source: str = "github",
) -> _G3PlanAdapter:
    """Wrap a G2 :class:`TriagePlan` so G3 modules see their schema."""

    return _G3PlanAdapter(
        g2=plan,
        title=title,
        body=body,
        issue_number=issue_number,
        session_id=session_id,
        repo=repo,
        base_branch=base_branch,
        source=source,
    )
