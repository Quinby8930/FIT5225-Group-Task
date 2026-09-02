import assert from 'node:assert/strict';
import test from 'node:test';

import { createHandler } from '../index.mjs';

const validBody = JSON.stringify({ filename: 'wombat.jpg', content_type: 'image/jpeg', size_bytes: 12, checksum_sha256: Buffer.alloc(32, 7).toString('base64') });

function event(overrides = {}) {
  return { requestContext: { authorizer: { jwt: { claims: { sub: 'user-1' } } } }, body: validBody, ...overrides };
}

function body(response) { return JSON.parse(response.body); }

test('rejects a request with no authenticated Cognito subject', async () => {
  const response = await createHandler({ createService: () => ({ createUpload: async () => ({}) }) })(event({ requestContext: {} }));
  assert.equal(response.statusCode, 401); assert.deepEqual(body(response), { code: 'UNAUTHENTICATED' });
});

test('rejects malformed request JSON', async () => {
  const response = await createHandler({ createService: () => ({ createUpload: async () => ({}) }) })(event({ body: '{' }));
  assert.equal(response.statusCode, 400); assert.deepEqual(body(response), { code: 'INVALID_REQUEST' });
});

test('returns the upload contract on success and applies both configured size caps', async () => {
  let received;
  let serviceOptions;
  const response = await createHandler({ maxUploadBytes: 12, maxImageUploadBytes: 10, createService: (options) => { serviceOptions = options; return { createUpload: async (input) => { received = input; return { file_id: 'file-1' }; } }; } })(event());
  assert.equal(response.statusCode, 200); assert.deepEqual(body(response), { file_id: 'file-1' });
  assert.equal(received.userId, 'user-1'); assert.equal(received.request.size_bytes, 12);
  assert.deepEqual(serviceOptions, { maxBytes: 12, maxImageBytes: 10 });
});

test('uses the contract defaults for both upload caps', async () => {
  let serviceOptions;
  await createHandler({ createService: (options) => { serviceOptions = options; return { createUpload: async () => ({}) }; } })(event());
  assert.deepEqual(serviceOptions, { maxBytes: 262_144_000, maxImageBytes: 12_582_912 });
});

test('maps an over-limit file to HTTP 413', async () => {
  const error = new Error('too large'); error.code = 'FILE_TOO_LARGE';
  const response = await createHandler({ createService: () => ({ createUpload: async () => { throw error; } }) })(event());
  assert.equal(response.statusCode, 413); assert.deepEqual(body(response), { code: 'FILE_TOO_LARGE' });
});

test('maps duplicate reservations and echoes an allowed request origin', async () => {
  const duplicate = new Error('duplicate');
  Object.assign(duplicate, {
    code: 'DUPLICATE_FILE',
    existing_file_id: 'existing-file',
    tags: { cat: 1, wombat: 2 },
    owner: 'must-not-leak',
    email: 'must-not-leak@example.com',
    filename: 'must-not-leak.jpg',
    object_key: 'must/not/leak',
    thumbnail_key: 'must/not/leak-thumb',
  });
  const handler = createHandler({ allowedOrigin: 'https://app.example', createService: () => ({ createUpload: async () => { throw duplicate; } }) });
  const response = await handler(event({ headers: { origin: 'https://app.example' } }));
  assert.equal(response.statusCode, 409);
  assert.deepEqual(body(response), {
    code: 'DUPLICATE_FILE',
    existing_file_id: 'existing-file',
    tags: { cat: 1, wombat: 2 },
  });
  assert.equal(response.headers['Access-Control-Allow-Origin'], 'https://app.example');
});

test('fails closed instead of exposing a malformed duplicate error', async () => {
  const duplicate = new Error('duplicate');
  Object.assign(duplicate, {
    code: 'DUPLICATE_FILE',
    existing_file_id: 'existing-file',
    tags: { cat: 0 },
  });
  const response = await createHandler({
    createService: () => ({ createUpload: async () => { throw duplicate; } }),
  })(event());

  assert.equal(response.statusCode, 503);
  assert.deepEqual(body(response), { code: 'DEPENDENCY_UNAVAILABLE' });
});

test('handles CORS preflight without invoking the service', async () => {
  const response = await createHandler({ createService: () => { throw new Error('must not be called'); } })(event({ headers: { origin: 'http://localhost:3000' }, body: null, version: '2.0', routeKey: 'OPTIONS /upload-url', requestContext: { http: { method: 'OPTIONS' } } }));
  assert.equal(response.statusCode, 204); assert.equal(response.headers['Access-Control-Allow-Origin'], 'http://localhost:3000');
});

test('echoes only allowlisted local and GitHub Pages origins', async () => {
  const handler = createHandler({
    allowedOrigin: 'http://localhost:3000',
    allowedOrigins: ['http://localhost:3000', 'https://quinby8930.github.io'],
    createService: () => ({ createUpload: async () => ({ file_id: 'file-1' }) }),
  });

  for (const origin of ['http://localhost:3000', 'https://quinby8930.github.io']) {
    const response = await handler(event({ headers: { Origin: origin } }));
    assert.equal(response.headers['Access-Control-Allow-Origin'], origin);
  }
});

test('omits allow-origin for a request origin outside the configured allowlist', async () => {
  const handler = createHandler({
    allowedOrigins: ['http://localhost:3000', 'https://quinby8930.github.io'],
    createService: () => ({ createUpload: async () => ({ file_id: 'file-1' }) }),
  });

  const response = await handler(event({ headers: { origin: 'https://untrusted.example' } }));

  assert.equal(response.statusCode, 200);
  assert.equal('Access-Control-Allow-Origin' in response.headers, false);
});

test('maps unexpected failures to an internal error without exposing details', async () => {
  const response = await createHandler({ createService: () => ({ createUpload: async () => { throw new Error('secret detail'); } }) })(event());
  assert.equal(response.statusCode, 500); assert.deepEqual(body(response), { code: 'INTERNAL_ERROR' });
});
