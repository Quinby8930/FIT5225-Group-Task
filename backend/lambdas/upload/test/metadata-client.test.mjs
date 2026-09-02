import assert from 'node:assert/strict';
import test from 'node:test';

import { createMetadataClient } from '../metadata-client.mjs';

const record = { file_id: 'new-file', user_id: 'user-1', checksum: Buffer.alloc(32, 7).toString('base64'), filename: 'wombat.jpg', file_type: 'image', content_type: 'image/jpeg', size_bytes: 12, object_key: 'originals/user-1/new-file/wombat.jpg', status: 'pending_upload' };

test('POSTs the reservation contract, serializes JSON, and sends the optional internal key', async () => {
  let request;
  const fetchImpl = async (endpoint, options) => {
    request = { endpoint, options };
    return { status: 201 };
  };

  await createMetadataClient({
    baseUrl: 'https://metadata.example/service',
    internalApiKey: 'secret',
    fetchImpl,
  }).reserveUpload(record);

  assert.equal(request.endpoint, 'https://metadata.example/service/internal/uploads/reserve');
  assert.equal(request.options.method, 'POST');
  assert.equal(request.options.headers['Content-Type'], 'application/json');
  assert.equal(request.options.headers['X-Internal-Api-Key'], 'secret');
  assert.deepEqual(JSON.parse(request.options.body), record);
  assert.equal(request.options.redirect, 'error');
});

test('returns the reusable upload identity from a successful reservation', async () => {
  const fetchImpl = async () => new Response(
    JSON.stringify({
      file_id: 'existing-file',
      object_key: 'originals/user-1/existing-file/wombat.jpg',
      status: 'pending_upload',
      reused: true,
    }),
    { status: 201, headers: { 'Content-Type': 'application/json' } },
  );

  const reservation = await createMetadataClient({
    baseUrl: 'https://metadata.example', fetchImpl,
  }).reserveUpload(record);

  assert.deepEqual(reservation, {
    file_id: 'existing-file',
    object_key: 'originals/user-1/existing-file/wombat.jpg',
  });
});

test('validates and preserves the approved duplicate identifier and tags', async () => {
  const fetchImpl = async () => new Response(
    JSON.stringify({
      existing_file_id: 'existing-file-123',
      tags: { cat: 1, wombat: 2 },
    }),
    { status: 409, headers: { 'Content-Type': 'application/json' } },
  );
  await assert.rejects(
    () => createMetadataClient({ baseUrl: 'https://metadata.example', fetchImpl }).reserveUpload(record),
    {
      code: 'DUPLICATE_FILE',
      existing_file_id: 'existing-file-123',
      tags: { cat: 1, wombat: 2 },
    },
  );
});

test('fails closed for malformed duplicate identifiers, tags, or extra fields', async () => {
  const tooManyTags = Object.fromEntries(
    Array.from({ length: 65 }, (_, index) => [`species-${index}`, 1]),
  );
  const invalidPayloads = [
    { existing_file_id: '', tags: {} },
    { existing_file_id: ' spaced ', tags: {} },
    { existing_file_id: 'bad\nline', tags: {} },
    { existing_file_id: 'x'.repeat(257), tags: {} },
    { existing_file_id: 123, tags: {} },
    { existing_file_id: 'existing-file', tags: null },
    { existing_file_id: 'existing-file', tags: [] },
    { existing_file_id: 'existing-file', tags: { '': 1 } },
    { existing_file_id: 'existing-file', tags: { ' cat ': 1 } },
    { existing_file_id: 'existing-file', tags: { cat: 0 } },
    { existing_file_id: 'existing-file', tags: { cat: -1 } },
    { existing_file_id: 'existing-file', tags: { cat: 1.5 } },
    { existing_file_id: 'existing-file', tags: { cat: '1' } },
    { existing_file_id: 'existing-file', tags: { cat: true } },
    { existing_file_id: 'existing-file', tags: { cat: 1_000_001 } },
    { existing_file_id: 'existing-file', tags: tooManyTags },
    { existing_file_id: 'existing-file', tags: {}, owner: 'user-2' },
    { existing_file_id: 'existing-file', tags: {}, object_key: 'private/key' },
  ];

  for (const payload of invalidPayloads) {
    const fetchImpl = async () => new Response(
      JSON.stringify(payload),
      { status: 409, headers: { 'Content-Type': 'application/json' } },
    );
    await assert.rejects(
      () => createMetadataClient({ baseUrl: 'https://metadata.example', fetchImpl }).reserveUpload(record),
      { code: 'DEPENDENCY_UNAVAILABLE' },
    );
  }
});

test('stops reading an oversized duplicate response at one MiB', async () => {
  let deliveredBytes = 0;
  let cancelled = false;
  const reader = {
    async read(view) {
      view.fill(0x20);
      deliveredBytes += view.byteLength;
      return { done: false, value: view };
    },
    async cancel() { cancelled = true; },
    releaseLock() {},
  };
  const fetchImpl = async () => ({
    status: 409,
    body: { getReader: () => reader },
  });

  await assert.rejects(
    () => createMetadataClient({ baseUrl: 'https://metadata.example', fetchImpl }).reserveUpload(record),
    { code: 'DEPENDENCY_UNAVAILABLE' },
  );

  assert.equal(deliveredBytes, 1_048_577);
  assert.equal(cancelled, true);
});

test('rejects invalid UTF-8 in a duplicate response', async () => {
  const invalidBody = Buffer.concat([
    Buffer.from('{"existing_file_id":"'),
    Buffer.from([0xff]),
    Buffer.from('"}'),
  ]);
  const fetchImpl = async () => new Response(invalidBody, { status: 409 });

  await assert.rejects(
    () => createMetadataClient({ baseUrl: 'https://metadata.example', fetchImpl }).reserveUpload(record),
    { code: 'DEPENDENCY_UNAVAILABLE' },
  );
});

test('maps unsuccessful metadata and connection failures to dependency unavailable', async () => {
  await assert.rejects(
    () => createMetadataClient({
      baseUrl: 'https://metadata.example',
      fetchImpl: async () => ({ status: 500 }),
    }).reserveUpload(record),
    { code: 'DEPENDENCY_UNAVAILABLE' },
  );
  await assert.rejects(
    () => createMetadataClient({
      baseUrl: 'https://metadata.example',
      fetchImpl: async () => { throw new Error('connection unavailable'); },
    }).reserveUpload(record),
    { code: 'DEPENDENCY_UNAVAILABLE' },
  );
});

test('rejects non-HTTPS metadata configuration before scheduling or sending', () => {
  for (const baseUrl of ['http://metadata.example', 'ftp://metadata.example', 'not-a-url']) {
    let fetchCalled = false;
    let timerCalled = false;
    assert.throws(
      () => createMetadataClient({
        baseUrl,
        internalApiKey: 'must-not-be-sent',
        fetchImpl: async () => { fetchCalled = true; },
        setTimeoutImpl: () => { timerCalled = true; },
      }),
      { code: 'INVALID_CONFIGURATION' },
    );
    assert.equal(fetchCalled, false);
    assert.equal(timerCalled, false);
  }
});

test('aborts a stalled reservation after the default five-second deadline', async () => {
  let scheduledCallback;
  let scheduledMilliseconds;
  const clearedTimers = [];
  let requestSignal;
  const fetchImpl = async (_endpoint, options) => {
    requestSignal = options.signal;
    return new Promise((_resolve, reject) => {
      options.signal.addEventListener('abort', () => {
        const error = new Error('aborted');
        error.name = 'AbortError';
        reject(error);
      });
      scheduledCallback();
    });
  };
  const client = createMetadataClient({
    baseUrl: 'https://metadata.example',
    fetchImpl,
    setTimeoutImpl: (callback, milliseconds) => {
      scheduledCallback = callback;
      scheduledMilliseconds = milliseconds;
      return 'reservation-timeout';
    },
    clearTimeoutImpl: (timer) => clearedTimers.push(timer),
  });

  await assert.rejects(
    () => client.reserveUpload(record),
    { code: 'DEPENDENCY_UNAVAILABLE' },
  );

  assert.equal(scheduledMilliseconds, 5_000);
  assert.equal(requestSignal.aborted, true);
  assert.deepEqual(clearedTimers, ['reservation-timeout']);
});

test('keeps the abort deadline active while decoding a duplicate response', async () => {
  const order = [];
  const encoded = Buffer.from(JSON.stringify({
    existing_file_id: 'existing-file',
    tags: { cat: 1 },
  }));
  let delivered = false;
  const client = createMetadataClient({
    baseUrl: 'https://metadata.example',
    fetchImpl: async () => ({
      status: 409,
      body: {
        getReader: () => ({
          async read() {
            if (delivered) return { done: true };
            delivered = true;
            order.push('decode duplicate');
            return { done: false, value: encoded };
          },
          releaseLock() {},
        }),
      },
    }),
    setTimeoutImpl: () => 'reservation-timeout',
    clearTimeoutImpl: () => order.push('clear deadline'),
  });

  await assert.rejects(
    () => client.reserveUpload(record),
    { code: 'DUPLICATE_FILE', existing_file_id: 'existing-file', tags: { cat: 1 } },
  );

  assert.deepEqual(order, ['decode duplicate', 'clear deadline']);
});
