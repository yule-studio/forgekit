"""locustfile.py — memory-search 미러링 (선택적 Locust stub).

본 파일은 k6 ``memory-search.js`` 를 미러링한 **선택적** 부하 stub 이다.
기본 부하 도구는 k6 이며, Locust 는 UI 기반 탐색이 필요할 때만 쓴다.

부하 대상은 이 레포의 memory backend 이고, 기본은 MOCK endpoint 다.
mock 서버는 기본으로 자동 기동되지 않으며 ``--host`` 가 가리키는 곳에서
직접 띄워야 한다. 실제/유료 LLM API 를 무제한 호출하지 않는다.

실행 (선택):
    locust -f apps/loadtest-runner/locust/locustfile.py --host http://localhost:8787
"""

from __future__ import annotations

import random

try:  # Locust 는 선택 의존성 — 미설치 환경에서도 import 가 깨지지 않게 한다.
    from locust import HttpUser, between, task
except ImportError:  # pragma: no cover - 선택 도구 미설치 시 no-op
    HttpUser = object  # type: ignore[assignment,misc]

    def between(_a, _b):  # type: ignore[no-redef]
        return None

    def task(func):  # type: ignore[no-redef]
        return func


QUERIES = ["runtime status", "review loop", "vault sync", "job queue", "retrieval eval"]


class MemorySearchUser(HttpUser):  # type: ignore[misc]
    """memory search latency 를 보는 최소 Locust user (k6 미러)."""

    wait_time = between(0.5, 1.5)

    @task
    def search(self) -> None:
        query = random.choice(QUERIES)
        # GET /mock/memory/search?q=... (placeholder mock endpoint)
        self.client.get("/mock/memory/search", params={"q": query}, name="memory_search")
