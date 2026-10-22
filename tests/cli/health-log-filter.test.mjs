import assert from 'node:assert/strict';
import test from 'node:test';

import { filterHealthLogLines } from '../../frontend/src/features/logs/healthLogFilter.ts';

const healthUrl = 'http://127.0.0.1:8080/health';

test('hides successful exact-path common access lines only', () => {
  const lines = [
    '127.0.0.1 - - [22/Oct/2026:01:02:03 +0000] "GET /health HTTP/1.1" 200 2 "-" "curl/8"',
    '127.0.0.1 - - [22/Oct/2026:01:02:04 +0000] "HEAD /health?check=1 HTTP/1.1" 302 0',
    '127.0.0.1 - - [22/Oct/2026:01:02:05 +0000] "GET /health/details HTTP/1.1" 200 2',
    '127.0.0.1 - - [22/Oct/2026:01:02:06 +0000] "POST /health HTTP/1.1" 200 2',
    '127.0.0.1 - - [22/Oct/2026:01:02:07 +0000] "GET /health HTTP/1.1" 503 2',
    'application started successfully',
  ];

  assert.deepEqual(filterHealthLogLines(lines, healthUrl), [
    lines[2], lines[3], lines[4], lines[5],
  ]);
});

test('hides successful Uvicorn access lines and preserves errors', () => {
  const lines = [
    'INFO:     127.0.0.1:53321 - "GET /health HTTP/1.1" 200 OK',
    'INFO:     127.0.0.1:53321 - "GET /health HTTP/1.1" 404 Not Found',
    'ERROR:    application failed while handling /health',
  ];

  assert.deepEqual(filterHealthLogLines(lines, healthUrl), lines.slice(1));
});

test('handles JSON access fields and keeps explicit error records visible', () => {
  const lines = [
    JSON.stringify({ method: 'GET', path: '/health', status: 200, level: 'info' }),
    JSON.stringify({ request: { method: 'HEAD', url: '/health?probe=1' }, status_code: 204 }),
    JSON.stringify({ method: 'GET', path: '/health', status: 200, level: 'error', message: 'handler error' }),
    JSON.stringify({ method: 'GET', path: '/health/live', status: 200 }),
    JSON.stringify({ message: 'worker initialized' }),
  ];

  assert.deepEqual(filterHealthLogLines(lines, healthUrl), [lines[2], lines[3], lines[4]]);
});

test('without a usable health URL or with display enabled, returns all lines', () => {
  const lines = ['127.0.0.1 - - [date] "GET /health HTTP/1.1" 200 2'];
  assert.equal(filterHealthLogLines(lines, undefined), lines);
  assert.equal(filterHealthLogLines(lines, healthUrl, true), lines);
});
