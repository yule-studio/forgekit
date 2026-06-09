// memory-search.js — memory search latency 부하 시나리오 (k6)
//
// 부하 대상: 이 레포의 memory backend (Agent Town UI 아님).
// 기본은 MOCK endpoint 다. mock 서버가 반드시 필요하며, 기본으로 자동
// 기동되지 않는다 — BASE_URL 이 가리키는 곳에서 직접 띄워야 한다.
//
//   GET {BASE_URL}/mock/memory/search?q=<query>
//
// 실행: k6 run apps/loadtest-runner/k6/memory-search.js
//       (BASE_URL 미설정 시 http://localhost:8787 로 폴백)

import http from 'k6/http';
import { check, sleep } from 'k6';
import { Trend } from 'k6/metrics';

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8787';

const searchLatency = new Trend('memory_search_latency', true);

const QUERIES = ['runtime status', 'review loop', 'vault sync', 'job queue', 'retrieval eval'];

export const options = {
  stages: [
    { duration: '15s', target: 5 },  // ramp-up
    { duration: '30s', target: 5 },  // steady
    { duration: '10s', target: 0 },  // ramp-down
  ],
  thresholds: {
    http_req_failed: ['rate<0.01'],            // 실패율 1% 미만
    http_req_duration: ['p(95)<400'],          // p95 < 400ms
    memory_search_latency: ['p(95)<400'],
  },
};

export default function () {
  const q = QUERIES[Math.floor(Math.random() * QUERIES.length)];
  const res = http.get(`${BASE_URL}/mock/memory/search?q=${encodeURIComponent(q)}`);

  searchLatency.add(res.timings.duration);
  check(res, {
    'status is 200': (r) => r.status === 200,
  });

  sleep(1);
}
