import assert from 'node:assert/strict';
import test from 'node:test';

import { createStorageDeleteService } from '../service.mjs';


test('allows each user-owned media prefix and returns the unique deletion count', async () => {
  const batches = [];
  const service = createStorageDeleteService({
    deleteKeys: async (keys) => batches.push(keys),
  });
  const keys = [
    'originals/user-1/file-1/wombat.jpg',
    'thumbnails/user-1/file-1/thumbnail.jpg',
    'processing/user-1/file-1/frames/frame-000001.jpg',
  ];

  const count = await service.deleteForUser('user-1', keys);

  assert.equal(count, 3);
  assert.deepEqual(batches, [keys]);
});


test('rejects a cross-user key before deleting any otherwise valid key', async () => {
  const batches = [];
  const service = createStorageDeleteService({
    deleteKeys: async (keys) => batches.push(keys),
  });

  await assert.rejects(
    service.deleteForUser('user-1', [
      'originals/user-1/file-1/allowed.jpg',
      'originals/user-2/file-2/forbidden.jpg',
    ]),
    (error) => error.code === 'FORBIDDEN_KEY',
  );
  assert.deepEqual(batches, []);
});


test('rejects similar but incomplete prefixes', async () => {
  const service = createStorageDeleteService({ deleteKeys: async () => {} });

  for (const key of [
    'originals/user-1',
    'originals/user-10/file/a.jpg',
    'archive/user-1/file/a.jpg',
  ]) {
    await assert.rejects(
      service.deleteForUser('user-1', [key]),
      (error) => error.code === 'FORBIDDEN_KEY',
    );
  }
});


test('deduplicates keys while preserving first-seen order', async () => {
  const batches = [];
  const service = createStorageDeleteService({
    deleteKeys: async (keys) => batches.push(keys),
  });
  const first = 'originals/user-1/file-1/a.jpg';
  const second = 'thumbnails/user-1/file-1/thumbnail.jpg';

  const count = await service.deleteForUser('user-1', [first, second, first, second]);

  assert.equal(count, 2);
  assert.deepEqual(batches, [[first, second]]);
});


test('returns zero for an empty key list without calling S3', async () => {
  let calls = 0;
  const service = createStorageDeleteService({
    deleteKeys: async () => { calls += 1; },
  });

  const count = await service.deleteForUser('user-1', []);

  assert.equal(count, 0);
  assert.equal(calls, 0);
});


test('splits 1,001 unique keys at the S3 1,000-key service limit', async () => {
  const batches = [];
  const service = createStorageDeleteService({
    deleteKeys: async (keys) => batches.push(keys),
  });
  const keys = Array.from(
    { length: 1_001 },
    (_, index) => `processing/user-1/file-1/frames/frame-${index}.jpg`,
  );

  const count = await service.deleteForUser('user-1', keys);

  assert.equal(count, 1_001);
  assert.deepEqual(batches.map((batch) => batch.length), [1_000, 1]);
  assert.equal(batches[0][0], keys[0]);
  assert.equal(batches[1][0], keys[1_000]);
});


test('rejects a non-positive batch size instead of silently skipping deletion', () => {
  assert.throws(
    () => createStorageDeleteService({ deleteKeys: async () => {}, batchSize: 0 }),
    (error) => error.code === 'INVALID_REQUEST',
  );
});
