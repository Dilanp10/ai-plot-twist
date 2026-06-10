/*
 * k6 burst load test — GET /api/v1/chapters/today.
 *
 * Module 004 / Task T-011.
 *
 * Acceptance (spec NFR-004, T-011 done-when):
 *   - p95 < 500 ms
 *   - 0 server errors (status >= 500)
 *
 * Profile:
 *   - Ramp from 0 → 200 RPS over 10 s using ramping-arrival-rate.
 *   - Hold 200 RPS for 60 s.
 *   - Cooldown to 0 over 5 s.
 *
 * The ETag/Cache-Control pipeline in the handler should let most traffic
 * short-circuit cheaply; the real DB query (Q-1 join) is what we exercise
 * to confirm spec NFR-001 (p95 < 100 ms server-side) holds under burst.
 *
 * Usage (against an instance you control — DO NOT point at prod without
 * coordinating with the on-call PO; this fires 14k requests in 75 s):
 *
 *   # Local dev (uv run uvicorn app.main:app --port 8000):
 *   k6 run --env BASE_URL=http://localhost:8000 \
 *          --summary-export var/k6-report.json \
 *          scripts/k6/today_burst.js
 *
 *   # Fly prod (after T-015 deploy and a bootstrapped cycle):
 *   k6 run --env BASE_URL=https://ai-plot-twist.fly.dev \
 *          --summary-export var/k6-report.json \
 *          scripts/k6/today_burst.js
 *
 * The --summary-export flag writes the full run summary as JSON; the
 * thresholds block below also makes k6 exit non-zero on breach so CI can
 * pick up regressions if we ever wire this in.
 */

import http from 'k6/http';
import { check } from 'k6';

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8000';
const ENDPOINT = `${BASE_URL}/api/v1/chapters/today`;

export const options = {
    scenarios: {
        burst: {
            executor: 'ramping-arrival-rate',
            // Sized so 200 RPS × 60 s hold = 12,000 requests in the steady phase,
            // plus ~1,000 in the ramp-up — total ~13k requests.
            preAllocatedVUs: 50,
            maxVUs: 200,
            startRate: 0,
            timeUnit: '1s',
            stages: [
                { target: 200, duration: '10s' },  // ramp up
                { target: 200, duration: '60s' },  // hold
                { target: 0,   duration: '5s'  },  // cooldown
            ],
        },
    },
    thresholds: {
        // Spec T-011 acceptance: p95 latency under 500 ms.
        http_req_duration: ['p(95)<500'],
        // Spec T-011 acceptance: zero server errors. http_req_failed counts
        // any status >= 400 by default; we tighten by checking explicitly.
        http_req_failed:   ['rate==0'],
        // Custom: every request returned a status we expected (200 or 304).
        checks:            ['rate==1'],
    },
    // Quieter output for CI; full JSON goes to --summary-export.
    summaryTrendStats: ['avg', 'min', 'med', 'max', 'p(90)', 'p(95)', 'p(99)'],
};

export default function () {
    const res = http.get(ENDPOINT, {
        headers: { 'Accept': 'application/json' },
        tags:    { endpoint: 'today' },
    });

    check(res, {
        'status is 200 or 304': (r) => r.status === 200 || r.status === 304,
        'no server error':      (r) => r.status < 500,
    });
}

/*
 * Reading the report
 * ------------------
 * After the run, ``var/k6-report.json`` contains a top-level ``metrics`` map.
 * The two acceptance values live at:
 *
 *   metrics.http_req_duration.values["p(95)"]   // milliseconds
 *   metrics.http_req_failed.values.rate         // 0..1
 *
 * Example one-liner check from the repo root:
 *
 *   jq '.metrics.http_req_duration.values["p(95)"], .metrics.http_req_failed.values.rate' \
 *     var/k6-report.json
 *
 * If the run failed the thresholds, k6's exit code is non-zero AND the JSON
 * has ``.root_group.checks`` showing which scenarios broke.
 */
