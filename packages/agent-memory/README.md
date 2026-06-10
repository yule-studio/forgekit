# yule-agent-memory

engineering-agent 코어(agents/memory)에서 추출한 stdlib leaf — 에이전트
long-term memory(long_term_memory / relevance_selector / sources / topic_index).
packages/memory(검색·FTS5 인덱스)와 이름 충돌을 피해 agent-prefix.
옛 경로 `yule_engineering.agents.memory` 는 compat shim(sys.modules alias, identity 보존).
