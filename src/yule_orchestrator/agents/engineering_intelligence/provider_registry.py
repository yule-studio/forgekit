"""Auth + availability registry for live knowledge providers.

The :mod:`.providers` module owns the *transport spec* — given a
:class:`SourceEntry`, :func:`provider_spec_for` returns the
:class:`LiveProviderSpec` describing how the bytes arrive (RSS / Atom
/ Sitemap / HTML / GitHub Releases / GitHub API / Manual).

This module owns the *registry* layer that complements that spec:

  * Which provider implementations actually exist for each transport.
  * What env contract each provider requires before it may dispatch
    a live call (mirrors the decision-classifier dual gate — env keys
    *and* an explicit enable flag must both be set).
  * Whether the configured implementation is currently
    ``available`` / ``disabled_by_flag`` / ``missing_env`` /
    ``no_live_impl`` / ``manual_only`` for the operator's environment.
  * A deterministic fake (:class:`FakeKnowledgeProvider`) that every
    transport falls back to when live dispatch is not authorised.

Strict offline. Live HTTP / API code lives in a follow-up — this
module defines the *seam* without importing urllib / requests /
socket / sqlite / vault. Tests pin the seam so the contract can't
silently regress when the live impl lands.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from typing import (
    Any,
    Callable,
    Iterable,
    Mapping,
    Optional,
    Sequence,
    Tuple,
)

from .models import EngineeringKnowledgeItem, SourceEntry
from .providers import (
    FakeKnowledgeProvider,
    LiveProviderSpec,
    LiveSourceFetcher,
    ProviderTransport,
    StubLiveSourceFetcher,
    provider_spec_for,
)


# ---------------------------------------------------------------------------
# Auth requirement
# ---------------------------------------------------------------------------


_TRUTHY = {"1", "true", "yes", "on", "y"}


@dataclass(frozen=True)
class ProviderAuthRequirement:
    """Env contract a provider must satisfy before live dispatch.

    Two-part dual gate, intentionally identical in shape to the
    decision-classifier env policy:

      1. ``env_keys`` — every key listed here must resolve to a
         non-empty value in the operator env mapping.
      2. ``enable_flag`` — the boolean flag that the operator flips
         after manual cost / safety review. ``None`` means the
         transport has no dedicated flag (e.g. ``MANUAL`` — there is
         no live call to gate).

    Both conditions are necessary; either alone leaves the registry
    in fallback (fake / stub) mode. This matches the project's
    "key found ≠ enabled" rule from .env.example §classifier.
    """

    env_keys: Tuple[str, ...] = ()
    enable_flag: Optional[str] = None
    notes: str = ""

    def env_keys_present(self, env: Mapping[str, str]) -> bool:
        """All required keys map to a non-blank value in *env*."""

        for key in self.env_keys:
            value = (env.get(key) or "").strip()
            if not value:
                return False
        return True

    def enable_flag_set(self, env: Mapping[str, str]) -> bool:
        """The enable flag is explicitly truthy (or unset → True)."""

        if not self.enable_flag:
            return True
        value = (env.get(self.enable_flag) or "").strip().lower()
        return value in _TRUTHY

    def to_payload(self) -> Mapping[str, Any]:
        return {
            "env_keys": list(self.env_keys),
            "enable_flag": self.enable_flag,
            "notes": self.notes,
        }


# ---------------------------------------------------------------------------
# Availability
# ---------------------------------------------------------------------------


class ProviderAvailability(str, Enum):
    """Live-or-fallback decision for a provider entry.

    Distinguishes the *reason* a provider isn't currently live so the
    operator dashboard can call out exactly the env / flag / impl
    that's missing instead of a generic "fake".
    """

    AVAILABLE = "available"
    DISABLED_BY_FLAG = "disabled_by_flag"
    MISSING_ENV = "missing_env"
    NO_LIVE_IMPL = "no_live_impl"
    MANUAL_ONLY = "manual_only"


# ---------------------------------------------------------------------------
# Registration row
# ---------------------------------------------------------------------------


# A live factory takes the env mapping and returns a fetcher.
# Concretely the live impl needs to thread through user-agent +
# rate-limit + auth headers, all of which depend on env. The factory
# pattern keeps the registry purely declarative — no live object is
# constructed unless availability resolves to AVAILABLE.
LiveFetcherFactory = Callable[[Mapping[str, str]], LiveSourceFetcher]


@dataclass(frozen=True)
class KnowledgeProviderRegistration:
    """One row in the provider registry — transport → provider seat.

    The fake fetcher is required (offline default for tests + dev +
    cost-safe operator); the live factory is optional and only fires
    if availability resolves to ``AVAILABLE``. ``manual=True`` marks
    the slot where there is no live call by design (the operator
    enters items by hand) — availability collapses to
    ``MANUAL_ONLY`` regardless of env.
    """

    provider_id: str
    transport: ProviderTransport
    auth: ProviderAuthRequirement
    fake_fetcher: LiveSourceFetcher
    live_factory: Optional[LiveFetcherFactory] = None
    manual: bool = False
    description: str = ""

    def has_live_impl(self) -> bool:
        return self.live_factory is not None and not self.manual

    def evaluate_availability(
        self, env: Mapping[str, str]
    ) -> ProviderAvailability:
        """Resolve the env into an availability state.

        Order matters: ``manual=True`` short-circuits to
        ``MANUAL_ONLY``; absence of a live impl is checked next so
        that "no impl" is not masked by a fully-set env. Then env
        keys, then enable flag. The first failing condition wins so
        the operator dashboard can fix exactly that one thing.
        """

        if self.manual:
            return ProviderAvailability.MANUAL_ONLY
        if self.live_factory is None:
            return ProviderAvailability.NO_LIVE_IMPL
        if not self.auth.env_keys_present(env):
            return ProviderAvailability.MISSING_ENV
        if not self.auth.enable_flag_set(env):
            return ProviderAvailability.DISABLED_BY_FLAG
        return ProviderAvailability.AVAILABLE

    def select_fetcher(self, env: Mapping[str, str]) -> LiveSourceFetcher:
        """Return the fetcher to use right now for this transport.

        ``AVAILABLE`` → live factory output; everything else → fake.
        The caller never has to branch on availability when it just
        needs a callable.
        """

        if self.evaluate_availability(env) is ProviderAvailability.AVAILABLE:
            assert self.live_factory is not None  # narrowed by guard above
            return self.live_factory(env)
        return self.fake_fetcher

    def to_payload(self) -> Mapping[str, Any]:
        return {
            "provider_id": self.provider_id,
            "transport": self.transport.value,
            "auth": self.auth.to_payload(),
            "manual": self.manual,
            "has_live_impl": self.has_live_impl(),
            "description": self.description,
        }


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class KnowledgeProviderRegistry:
    """Mutable provider registry keyed on :class:`ProviderTransport`.

    Designed for explicit construction:

      * :func:`default_registry` produces the baseline seed (one
        registration per transport with ``fake`` only and the env
        contract attached).
      * Tests / a future live-wiring PR call ``register_live`` to
        plug a factory for one transport. No global mutation —
        callers thread the registry through.
    """

    def __init__(
        self,
        registrations: Iterable[KnowledgeProviderRegistration] = (),
    ) -> None:
        self._by_transport: dict[
            ProviderTransport, KnowledgeProviderRegistration
        ] = {}
        for reg in registrations:
            self._by_transport[reg.transport] = reg

    # -- mutation ------------------------------------------------------

    def register(self, registration: KnowledgeProviderRegistration) -> None:
        """Replace any existing entry for ``registration.transport``."""

        self._by_transport[registration.transport] = registration

    def register_live(
        self,
        transport: ProviderTransport,
        *,
        live_factory: LiveFetcherFactory,
    ) -> None:
        """Attach a live factory to an existing registration.

        Raises :class:`KeyError` if the transport is unknown — keeps
        a typo from silently producing a registry that never goes
        live. Reuses the existing auth contract / fake; the operator
        only has to write the live factory in their PR.
        """

        existing = self._by_transport.get(transport)
        if existing is None:
            raise KeyError(
                f"cannot attach live factory: transport "
                f"{transport.value!r} not in registry"
            )
        if existing.manual:
            raise ValueError(
                f"transport {transport.value!r} is manual-only — "
                f"refusing to attach live factory"
            )
        self._by_transport[transport] = replace(
            existing, live_factory=live_factory
        )

    # -- read-only ----------------------------------------------------

    def __contains__(self, transport: ProviderTransport) -> bool:
        return transport in self._by_transport

    def get(
        self, transport: ProviderTransport
    ) -> KnowledgeProviderRegistration:
        try:
            return self._by_transport[transport]
        except KeyError as exc:  # pragma: no cover — defensive
            raise KeyError(
                f"no provider registered for transport "
                f"{transport.value!r}"
            ) from exc

    def iter_registrations(
        self,
    ) -> Tuple[KnowledgeProviderRegistration, ...]:
        # Sorted on transport.value for deterministic iteration in
        # tests + audit dumps.
        return tuple(
            sorted(
                self._by_transport.values(),
                key=lambda r: r.transport.value,
            )
        )

    def evaluate(
        self,
        transport: ProviderTransport,
        *,
        env: Mapping[str, str],
    ) -> ProviderAvailability:
        return self.get(transport).evaluate_availability(env)

    def select_fetcher_for(
        self,
        source: SourceEntry,
        *,
        env: Mapping[str, str],
    ) -> Tuple[LiveProviderSpec, LiveSourceFetcher, ProviderAvailability]:
        """Resolve a :class:`SourceEntry` into (spec, fetcher, availability).

        Encapsulates the "build spec + look up registration + decide
        availability + pick fetcher" sequence so a future scheduler
        runner just calls one helper per source.
        """

        spec = provider_spec_for(source)
        registration = self.get(spec.transport)
        availability = registration.evaluate_availability(env)
        fetcher = (
            registration.live_factory(env)
            if availability is ProviderAvailability.AVAILABLE
            and registration.live_factory is not None
            else registration.fake_fetcher
        )
        return spec, fetcher, availability

    def availability_report(
        self, env: Mapping[str, str]
    ) -> Mapping[str, str]:
        """``transport.value → availability.value`` for the dashboard."""

        return {
            reg.transport.value: reg.evaluate_availability(env).value
            for reg in self.iter_registrations()
        }


# ---------------------------------------------------------------------------
# Default registry seed
# ---------------------------------------------------------------------------


# Env contract per transport — keys are "necessary" and the flag is
# the dual-gate "sufficient" half. None of these are read by the
# registry; they're declared here so the operator dashboard + tests
# pin the contract independently of the live impl.
_KNOWLEDGE_LIVE_ENABLED_FLAG = "YULE_KNOWLEDGE_{transport}_LIVE_ENABLED"


def _flag_for(transport: ProviderTransport) -> str:
    return _KNOWLEDGE_LIVE_ENABLED_FLAG.format(
        transport=transport.value.upper()
    )


def default_registry(
    *,
    fake_fixture: Optional[
        Mapping[str, Sequence[EngineeringKnowledgeItem]]
    ] = None,
) -> KnowledgeProviderRegistry:
    """Baseline registry: one entry per transport, fake-only by default.

    *fake_fixture* — optional ``source_id → items`` map for the fake
    fetcher. If ``None``, the registry uses :class:`StubLiveSourceFetcher`
    everywhere (records calls, returns empty). Tests that want
    deterministic items wired through the registry pass a fixture
    once at construction.
    """

    fake = (
        FakeKnowledgeProvider(fake_fixture)
        if fake_fixture is not None
        else StubLiveSourceFetcher()
    )

    rss = KnowledgeProviderRegistration(
        provider_id="rss-feed",
        transport=ProviderTransport.RSS,
        auth=ProviderAuthRequirement(
            env_keys=(),
            enable_flag=_flag_for(ProviderTransport.RSS),
            notes="Public RSS — no auth, only the operator enable flag.",
        ),
        fake_fetcher=fake,
        description="Generic RSS 2.0 / RDF feed parser.",
    )
    atom = KnowledgeProviderRegistration(
        provider_id="atom-feed",
        transport=ProviderTransport.ATOM,
        auth=ProviderAuthRequirement(
            env_keys=(),
            enable_flag=_flag_for(ProviderTransport.ATOM),
            notes="Public Atom 1.0 — no auth, only the operator enable flag.",
        ),
        fake_fetcher=fake,
        description="Atom 1.0 feed parser.",
    )
    github_releases = KnowledgeProviderRegistration(
        provider_id="github-releases-atom",
        transport=ProviderTransport.GITHUB_RELEASES_ATOM,
        auth=ProviderAuthRequirement(
            env_keys=(),
            enable_flag=_flag_for(ProviderTransport.GITHUB_RELEASES_ATOM),
            notes=(
                "Public Atom feed exposed by github.com/<owner>/<repo>/"
                "releases.atom — keep cadence ≤ 20/min to stay under the "
                "unauthenticated rate limit."
            ),
        ),
        fake_fetcher=fake,
        description="GitHub releases atom (no auth).",
    )
    sitemap = KnowledgeProviderRegistration(
        provider_id="sitemap-walker",
        transport=ProviderTransport.SITEMAP,
        auth=ProviderAuthRequirement(
            env_keys=(),
            enable_flag=_flag_for(ProviderTransport.SITEMAP),
            notes="Sitemap walks are heavier; cadence pinned low in the spec.",
        ),
        fake_fetcher=fake,
        description="sitemap.xml walker (lastmod-aware).",
    )
    html_list = KnowledgeProviderRegistration(
        provider_id="html-list",
        transport=ProviderTransport.HTML_LIST,
        auth=ProviderAuthRequirement(
            env_keys=(),
            enable_flag=_flag_for(ProviderTransport.HTML_LIST),
            notes="Index-page scrape — link + summary only per content_policy.",
        ),
        fake_fetcher=fake,
        description="HTML index / list page scraper.",
    )
    html_detail = KnowledgeProviderRegistration(
        provider_id="html-detail",
        transport=ProviderTransport.HTML_DETAIL,
        auth=ProviderAuthRequirement(
            env_keys=(),
            enable_flag=_flag_for(ProviderTransport.HTML_DETAIL),
            notes="Detail page fetch — slowest path; called only when index missed.",
        ),
        fake_fetcher=fake,
        description="HTML detail-page scraper.",
    )
    github_api = KnowledgeProviderRegistration(
        provider_id="github-api-repo-activity",
        transport=ProviderTransport.GITHUB_API_REPO_ACTIVITY,
        auth=ProviderAuthRequirement(
            # Reuse the existing GitHub App env triple — no new secret
            # surface area. Knowledge fetcher will share the App
            # installation with the GitHub agent.
            env_keys=(
                "YULE_GITHUB_APP_ID",
                "YULE_GITHUB_APP_INSTALLATION_ID",
                "YULE_GITHUB_APP_PRIVATE_KEY_PATH",
            ),
            enable_flag=_flag_for(
                ProviderTransport.GITHUB_API_REPO_ACTIVITY
            ),
            notes=(
                "Backed by the existing GitHub App. Private repo "
                "endpoints fail closed without the App env triple."
            ),
        ),
        fake_fetcher=fake,
        description="GitHub REST: issues / releases / commits via App.",
    )
    manual = KnowledgeProviderRegistration(
        provider_id="manual",
        transport=ProviderTransport.MANUAL,
        auth=ProviderAuthRequirement(
            env_keys=(),
            enable_flag=None,
            notes=(
                "MANUAL — no live call. Operator enters knowledge items "
                "directly via the vault / Discord intake."
            ),
        ),
        fake_fetcher=fake,
        manual=True,
        description="Manual entry slot — no transport.",
    )

    return KnowledgeProviderRegistry(
        registrations=(
            rss,
            atom,
            github_releases,
            sitemap,
            html_list,
            html_detail,
            github_api,
            manual,
        )
    )


__all__ = [
    "FakeKnowledgeProvider",
    "KnowledgeProviderRegistration",
    "KnowledgeProviderRegistry",
    "LiveFetcherFactory",
    "ProviderAuthRequirement",
    "ProviderAvailability",
    "default_registry",
]
