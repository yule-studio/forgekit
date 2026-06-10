// runtime-status.js — runtime status latency + job queue throughput 시나리오 (k6)
//
// 부하 대상: 이 레포의 runtime backend (Agent Town UI 아님).
// 기본은 MOCK endpoint 다. mock 서버가 반드시 필요하며, 기본으로 자동
// 기동되지 않는다 — BASE_URL 이 가리키는 곳에서 직접 띄워야 한다.
//
//   GET {BASE_URL}/mock/runtime/status
//
// 실행: k6 run apps/loadtest-runner/k6/runtime-status.js
//       (BASE_URL 미설정 시 http://localhost:8787 로 폴백)

import http from 'k6/http';
import { check, sleep } from 'k6';
import { Trend, Counter } from 'k6/metrics';

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8787';

const statusLatency = new Trend('runtime_status_latency', true);
const statusPolls = new Counter('runtime_status_polls'); // job queue throughput 관찰용

export const options = {
  stages: [
    { duration: '10s', target: 10 }, // ramp-up
    { duration: '40s', target: 10 }, // steady poll
    { duration: '10s', target: 0 },  // ramp-down
  ],
  thresholds: {
    http_req_failed: ['rate<0.01'],
    runtime_status_latency: ['p(95)<250'], // status 폴링은 빨라야 함
  },
};

export default function () {
  const res = http.get(`${BASE_URL}/mock/runtime/status`);

  statusLatency.add(res.timings.duration);
  statusPolls.add(1);
  check(res, {
    'status is 200': (r) => r.status === 200,
  });

  sleep(0.5); // ~2 polls/sec/VU
}
