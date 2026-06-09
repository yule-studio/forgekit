"""Compatibility shim — external service integrations now live in
``yule_integrations``.

The calendar (CalDAV) and GitHub integrations were extracted into the
standalone ``yule-integrations`` package, which depends on
``yule_storage`` at runtime for its JSON cache and calendar-state sync.
The nested submodule shims alias the new modules via ``sys.modules`` so
existing ``from yule_orchestrator.integrations...`` imports — and test
patches against deep paths like
``yule_orchestrator.integrations.github.issues.subprocess`` — operate on
the *same* module objects the integrations package uses.
"""
