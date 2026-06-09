// discord-burst.js — Discord message burst handling 시나리오 (k6)
//
// 부하 대상: 이 레포의 agent runtime inbound 경로 (Agent Town UI 아님).
// 기본은 MOCK endpoint 다. mock 서버가 반드시 필요하며, 기본으로 자동
// 기동되지 않는다 — BASE_URL 이 가리키는 곳에서 직접 띄워야 한다.
//
//   POST {BASE_URL}/mock/discord/inbound
//
// 짧은 시간에 메시지가 몰릴 때 inbound 처리가 버티는지 본다 (spike).
// LLM gateway 로 흘러가는 경로는 stub/rate-limited mock 이어야 하며,
// 실제/유료 LLM API 를 무제한 호출하지 않는다.
//
// 실행: k6 run apps/loadtest-runner/k6/discord-burst.js
//       (BASE_URL 미설정 시 http://localhost:8787 로 폴백)

import http from 'k6/http';
import { check } from 'k6';
import { Trend } from 'k6/metrics';

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8787';

const inboundLatency = new Trend('discord_inbound_latency', true);

export const options = {
  scenarios: {
    burst: {
      executor: 'ramping-arrival-rate',
      startRate: 5,
      timeUnit: '1s',
      preAllocatedVUs: 50,
      maxVUs: 200,
      stages: [
        { duration: '10s', target: 5 },   // baseline
        { duration: '5s', target: 100 },  // burst spike
        { duration: '10s', target: 100 }, // hold burst
        { duration: '10s', target: 5 },   // recover
      ],
    },
  },
  thresholds: {
    http_req_failed: ['rate<0.02'],            // burst 중 약간의 여유
    discord_inbound_latency: ['p(95)<800'],
  },
};

export default function () {
  const payload = JSON.stringify({
    channel_id: 'mock-channel',
    author_id: `mock-user-${Math.floor(Math.random() * 1000)}`,
    content: 'mock burst message',
  });

  const res = http.post(`${BASE_URL}/mock/discord/inbound`, payload, {
    headers: { 'Content-Type': 'application/json' },
  });

  inboundLatency.add(res.timings.duration);
  check(res, {
    'status is 2xx': (r) => r.status >= 200 && r.status < 300,
  });
}
