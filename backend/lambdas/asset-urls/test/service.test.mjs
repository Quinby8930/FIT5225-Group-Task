import assert from 'node:assert/strict';
import test from 'node:test';


async function loadServiceModule() {
  return import('../service.mjs').catch(() => ({}));
}


test('signs each unique user-owned original and thumbnail in first-seen order', async () => {
  const { createAssetUrlService } = await loadServiceModule();
  assert.equal(typeof createAssetUrlService, 'function');

  const signedKeys = [];
  const service = createAssetUrlService({
    presignGet: async (key) => {
      signedKeys.push(key);
      return `https://signed.example/${key}`;
    },
  });
  const original = 'originals/user-1/file-1/wombat.jpg';
  const thumbnail = 'thumbnails/user-1/file-1/thumbnail.jpg';

  const result = await service.createUrlsForUser(
    'user-1',
    [original, thumbnail, original],
  );

  assert.deepEqual(signedKeys, [original, thumbnail]);
  assert.deepEqual(result, {
    assets: [
      {
        key: original,
        url: `https://signed.example/${original}`,
        expires_in: 900,
      },
      {
        key: thumbnail,
        url: `https://signed.example/${thumbnail}`,
        expires_in: 900,
      },
    ],
  });
});


test('rejects every request containing a cross-user or internal processing key', async () => {
  const { createAssetUrlService } = await loadServiceModule();
  assert.equal(typeof createAssetUrlService, 'function');

  let signed = false;
  const service = createAssetUrlService({
    presignGet: async () => {
      signed = true;
      return 'https://must-not-be-created.example';
    },
  });

  for (const key of [
    'originals/user-2/file-1/wombat.jpg',
    'thumbnails/user-10/file-1/thumbnail.jpg',
    'processing/user-1/file-1/frames/frame-000001.jpg',
    'originals/user-1/',
  ]) {
    await assert.rejects(
      service.createUrlsForUser('user-1', [key]),
      (error) => error?.code === 'FORBIDDEN_KEY',
    );
  }
  assert.equal(signed, false);
});


test('rejects malformed or oversized batches before signing', async () => {
  const { createAssetUrlService } = await loadServiceModule();
  assert.equal(typeof createAssetUrlService, 'function');

  let signed = false;
  const service = createAssetUrlService({
    presignGet: async () => {
      signed = true;
      return 'https://must-not-be-created.example';
    },
  });

  for (const keys of [
    'not-an-array',
    [42],
    [''],
    Array.from({ length: 101 }, (_, index) => `originals/user-1/f-${index}/a.jpg`),
  ]) {
    await assert.rejects(
      service.createUrlsForUser('user-1', keys),
      (error) => error?.code === 'INVALID_REQUEST',
    );
  }
  assert.equal(signed, false);
});


test('rejects S3 keys longer than the 1024-byte service limit', async () => {
  const { createAssetUrlService } = await loadServiceModule();
  assert.equal(typeof createAssetUrlService, 'function');

  let signed = false;
  const service = createAssetUrlService({
    presignGet: async () => {
      signed = true;
      return 'https://must-not-be-created.example';
    },
  });
  const oversizedKey = `originals/user-1/file-1/${'海'.repeat(340)}.jpg`;

  await assert.rejects(
    service.createUrlsForUser('user-1', [oversizedKey]),
    (error) => error?.code === 'INVALID_REQUEST',
  );
  assert.equal(Buffer.byteLength(oversizedKey, 'utf8') > 1024, true);
  assert.equal(signed, false);
});
