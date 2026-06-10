# yule-agent-runtime

engineering-agent 코어(agents/runtime)에서 추출한 stdlib leaf — 에이전트
runtime 루프(decide / loop / recall / understand / models / policies).
packages/runtime(circuit_breaker·services·supervisor)와 이름 충돌을 피해 agent-prefix.
옛 경로 `yule_engineering.agents.runtime` 는 compat shim(sys.modules alias, identity 보존).
