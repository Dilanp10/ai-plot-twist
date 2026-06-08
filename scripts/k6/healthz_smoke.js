// k6 smoke test: GET /healthz — 50 RPS for 60 s
//
// Usage:
//   k6 run --env BASE_URL=https://<app>.fly.dev scripts/k6/healthz_smoke.js
//   k6 run scripts/k6/healthz_smoke.js          # defaults to localhost:8000
//
// Pass condition: p95 latency < 200 ms, error rate < 1 %, check pass rate > 99 %.

import http from "k6/http";
import { check } from "k6";

const BASE_URL = __ENV.BASE_URL || "http://localhost:8000";

export const options = {
  scenarios: {
    healthz_smoke: {
      executor: "constant-arrival-rate",
      // Target throughput
      rate: 50,
      timeUnit: "1s",
      duration: "60s",
      // VU pool — headroom for latency spikes without dropping requests
      preAllocatedVUs: 20,
      maxVUs: 100,
    },
  },
  thresholds: {
    // Core acceptance criterion from T-021 spec
    http_req_duration: ["p(95)<200"],
    // Guard rails
    http_req_failed: ["rate<0.01"],
    checks: ["rate>0.99"],
  },
};

export default function () {
  const res = http.get(`${BASE_URL}/healthz`, {
    tags: { name: "healthz" },
  });

  check(res, {
    "status 200": (r) => r.status === 200,
    'body status ok': (r) => {
      try {
        return JSON.parse(r.body).status === "ok";
      } catch (_) {
        return false;
      }
    },
  });
}
