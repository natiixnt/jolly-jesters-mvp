/**
 * k6 load test for Jolly Jesters MVP
 * Usage: k6 run --env BASE_URL=http://localhost tools/loadtest.js
 * Scenarios: smoke (1 user), load (10 users), stress (50 users)
 */

import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Trend } from 'k6/metrics';

const BASE_URL = __ENV.BASE_URL || 'http://localhost';
const PASSWORD = __ENV.UI_PASSWORD || '1234';

const errorRate = new Rate('errors');
const apiDuration = new Trend('api_duration');

export const options = {
    scenarios: {
        smoke: {
            executor: 'constant-vus',
            vus: 1,
            duration: '30s',
            tags: { scenario: 'smoke' },
        },
        load: {
            executor: 'ramping-vus',
            startVUs: 0,
            stages: [
                { duration: '30s', target: 10 },
                { duration: '1m', target: 10 },
                { duration: '30s', target: 0 },
            ],
            startTime: '35s',
            tags: { scenario: 'load' },
        },
    },
    thresholds: {
        http_req_duration: ['p(95)<2000'],
        errors: ['rate<0.05'],
    },
};

function login() {
    const res = http.post(`${BASE_URL}/login`, { password: PASSWORD }, {
        redirects: 0,
    });
    const cookies = res.cookies;
    return cookies;
}

export default function () {
    // login
    const loginRes = http.post(`${BASE_URL}/login`, { password: PASSWORD }, { redirects: 0 });

    const jar = http.cookieJar();
    if (loginRes.cookies && loginRes.cookies['jj_session']) {
        jar.set(BASE_URL, 'jj_session', loginRes.cookies['jj_session'][0].value);
    }

    // health check
    let res = http.get(`${BASE_URL}/api/v1/status`);
    check(res, { 'status ok': (r) => r.status === 200 });
    errorRate.add(res.status !== 200);
    apiDuration.add(res.timings.duration);

    // list categories
    res = http.get(`${BASE_URL}/api/v1/categories/`);
    check(res, { 'categories ok': (r) => r.status === 200 });
    errorRate.add(res.status !== 200);
    apiDuration.add(res.timings.duration);

    // list recent runs
    res = http.get(`${BASE_URL}/api/v1/analysis?limit=20`);
    check(res, { 'runs ok': (r) => r.status === 200 });
    errorRate.add(res.status !== 200);
    apiDuration.add(res.timings.duration);

    // list active runs
    res = http.get(`${BASE_URL}/api/v1/analysis/active`);
    check(res, { 'active ok': (r) => r.status === 200 });
    errorRate.add(res.status !== 200);
    apiDuration.add(res.timings.duration);

    // market data
    res = http.get(`${BASE_URL}/api/v1/market-data?limit=20`);
    check(res, { 'market ok': (r) => r.status === 200 });
    errorRate.add(res.status !== 200);
    apiDuration.add(res.timings.duration);

    sleep(1);
}
