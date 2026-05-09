"""Decision layer — Phase 3 of #73.

Routes Discord intake / continuation messages through a 4-mode
classifier:

  * ``discussion`` — open question, no specific role assigned yet
  * ``research_only`` — gather references, do not implement
  * ``implementation_candidate`` — suspected coding work, run authorization gate
  * ``clarification_needed`` — too ambiguous, ask the user

The router prefers a deterministic fast-path (Korean keyword
matching) over an LLM call. Only when the fast-path is silent does
the optional :class:`Classifier` Protocol fire — production wires
this to Claude / Ollama / Codex; tests pass a fake.

Companion :mod:`context_pack` builds the :class:`ContextPack` the
classifier (or downstream worker) consumes — related notes, recent
threads, related issues / PRs, code hints — all from injectable
sources.
"""

from .classifier_factory import (
    AnthropicClassifier,
    BlockedClassifierError,
    ClassifierResolution,
    OllamaClassifier,
    OllamaClassifierConfig,
    OpenAIClassifier,
    build_classifier_from_env,
)
from .context_pack import (
    CodeHintProvider,
    ContextPack,
    GithubReferenceProvider,
    NoteProvider,
    ThreadProvider,
    build_context_pack,
)
from .router import (
    Classifier,
    DecisionRequest,
    DecisionResult,
    MODE_CLARIFICATION_NEEDED,
    MODE_DISCUSSION,
    MODE_IMPLEMENTATION_CANDIDATE,
    MODE_RESEARCH_ONLY,
    MODES,
    SOURCE_CLASSIFIER,
    SOURCE_FAST_PATH,
    SOURCE_FALLBACK,
    fake_classifier,
    route_decision,
)


__all__ = (
    # router
    "Classifier",
    "DecisionRequest",
    "DecisionResult",
    "MODE_CLARIFICATION_NEEDED",
    "MODE_DISCUSSION",
    "MODE_IMPLEMENTATION_CANDIDATE",
    "MODE_RESEARCH_ONLY",
    "MODES",
    "SOURCE_CLASSIFIER",
    "SOURCE_FALLBACK",
    "SOURCE_FAST_PATH",
    "fake_classifier",
    "route_decision",
    # context pack
    "CodeHintProvider",
    "ContextPack",
    "GithubReferenceProvider",
    "NoteProvider",
    "ThreadProvider",
    "build_context_pack",
    # classifier factory
    "AnthropicClassifier",
    "BlockedClassifierError",
    "ClassifierResolution",
    "OllamaClassifier",
    "OllamaClassifierConfig",
    "OpenAIClassifier",
    "build_classifier_from_env",
)
