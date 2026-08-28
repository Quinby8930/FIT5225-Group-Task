import assert from 'node:assert/strict';
import test from 'node:test';

async function loadClient() {
  return import('../metadata-client.mjs').catch(() => ({}));
}

function response({ status = 200, contentType = 'application/json', body = '{"decisions":[]}' } = {}) {
  return new Response(body, { status, headers: { 'content-type': contentType } });
}

test('posts canonical requested keys to the metadata authorization endpoint without redirects', async () => {
  const { createMetadataAuthorizationClient } = await loadClient();
  assert.equal(typeof createMetadataAuthorizationClient, 'function');
  let captured;
  const client = createMetadataAuthorizationClient({
    baseUrl: 'https://metadata.example/service', internalApiKey: 'secret',
    fetchImpl: async (url, options) => {
      captured = { url, options };
      return response({ body: JSON.stringify({ decisions: [{ key: 'originals/u/file.jpg', allowed: true }] }) });
    },
  });

  const decisions = await client.authorize(['originals/u/file.jpg']);

  assert.deepEqual(decisions, [{ key: 'originals/u/file.jpg', allowed: true }]);
  assert.equal(captured.url, 'https://metadata.example/service/internal/assets/authorize');
  assert.equal(captured.options.redirect, 'error');
  assert.equal(captured.options.headers['X-Internal-Api-Key'], 'secret');
  assert.equal(captured.options.body, '{"keys":["originals/u/file.jpg"]}');
});

test('fails closed for insecure URLs and malformed authorization responses', async () => {
  const { createMetadataAuthorizationClient } = await loadClient();
  assert.equal(typeof createMetadataAuthorizationClient, 'function');
  assert.throws(() => createMetadataAuthorizationClient({ baseUrl: 'http://metadata.example', internalApiKey: 'secret' }));
  assert.throws(() => createMetadataAuthorizationClient({ baseUrl: 'https://user:pass@metadata.example', internalApiKey: 'secret' }));

  for (const body of [
    '{"decisions":[]}',
    '{"decisions":[{"key":"originals/u/file.jpg","allowed":true},{"key":"originals/u/file.jpg","allowed":true}]}',
    '{"decisions":[{"key":"unrequested","allowed":true}]}',
    '{"decisions":[{"key":"originals/u/file.jpg","allowed":false,"code":"NOPE"}]}',
  ]) {
    const client = createMetadataAuthorizationClient({
      baseUrl: 'https://metadata.example', internalApiKey: 'secret',
      fetchImpl: async () => response({ body }),
    });
    await assert.rejects(client.authorize(['originals/u/file.jpg']), (error) => error?.code === 'AUTHORIZATION_UNAVAILABLE');
  }
});

test('fails closed for non-json, oversized, non-success, and timeout responses', async () => {
  const { createMetadataAuthorizationClient } = await loadClient();
  assert.equal(typeof createMetadataAuthorizationClient, 'function');
  for (const fetchImpl of [
    async () => response({ status: 503 }),
    async () => response({ contentType: 'text/plain' }),
    async () => response({ body: JSON.stringify({ decisions: [] }).padEnd(70_000, 'x') }),
    async (_url, options) => new Promise((_resolve, reject) => options.signal.addEventListener('abort', () => reject(new Error('aborted')))),
  ]) {
    const client = createMetadataAuthorizationClient({ baseUrl: 'https://metadata.example', internalApiKey: 'secret', fetchImpl, timeoutMs: 1 });
    await assert.rejects(client.authorize(['originals/u/file.jpg']), (error) => error?.code === 'AUTHORIZATION_UNAVAILABLE');
  }
});

test('cancels the response stream when its body exceeds the authorization limit', async () => {
  const { createMetadataAuthorizationClient } = await loadClient();
  assert.equal(typeof createMetadataAuthorizationClient, 'function');
  let cancelled = false;
  const stream = new ReadableStream({
    start(controller) {
      controller.enqueue(new TextEncoder().encode('x'.repeat(70_000)));
    },
    cancel() { cancelled = true; },
  });
  const client = createMetadataAuthorizationClient({
    baseUrl: 'https://metadata.example', internalApiKey: 'secret',
    fetchImpl: async () => new Response(stream, { headers: { 'content-type': 'application/json' } }),
  });

  await assert.rejects(client.authorize(['originals/u/file.jpg']), (error) => error?.code === 'AUTHORIZATION_UNAVAILABLE');
  assert.equal(cancelled, true);
});

test('does not cancel a normally completed authorization response stream', async () => {
  const { createMetadataAuthorizationClient } = await loadClient();
  assert.equal(typeof createMetadataAuthorizationClient, 'function');
  let cancelled = false;
  const stream = new ReadableStream({
    start(controller) {
      controller.enqueue(new TextEncoder().encode('{"decisions":[{"key":"originals/u/file.jpg","allowed":true}]}'));
      controller.close();
    },
    cancel() { cancelled = true; },
  });
  const client = createMetadataAuthorizationClient({
    baseUrl: 'https://metadata.example', internalApiKey: 'secret',
    fetchImpl: async () => new Response(stream, { headers: { 'content-type': 'application/json' } }),
  });

  assert.deepEqual(await client.authorize(['originals/u/file.jpg']), [{ key: 'originals/u/file.jpg', allowed: true }]);
  assert.equal(cancelled, false);
});
