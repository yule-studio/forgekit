# yule-agent-runtime

engineering-agent 코어(agents/runtime)에서 추출한 stdlib leaf — 에이전트
runtime 루프(decide / loop / recall / understand / models / policies).
packages/runtime(circuit_breaker·services·supervisor)와 이름 충돌을 피해 agent-prefix.
옛 compat shim 은 제거됨 — 호출부가 패키지를 직접 import.
