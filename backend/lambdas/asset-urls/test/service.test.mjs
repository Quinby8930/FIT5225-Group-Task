import assert from 'node:assert/strict';
import test from 'node:test';


async function loadServiceModule() {
  return import('../service.mjs').catch(() => ({}));
}


test('signs each metadata-authorized completed asset in first-seen order', async () => {
  const { createAssetUrlService } = await loadServiceModule();
  assert.equal(typeof createAssetUrlService, 'function');

  const signedKeys = [];
  const original = 'originals/user-2/file-1/wombat.jpg';
  const thumbnail = 'thumbnails/user-2/file-1/thumbnail.jpg';
  const service = createAssetUrlService({
    authorize: async () => [{ key: original, allowed: true }, { key: thumbnail, allowed: true }],
    presignGet: async (key) => {
      signedKeys.push(key);
      return `https://signed.example/${key}`;
    },
  });

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
    errors: [],
  });
});


test('returns per-key denial and preserves allowed assets when metadata denies a key', async () => {
  const { createAssetUrlService } = await loadServiceModule();
  assert.equal(typeof createAssetUrlService, 'function');

  const signed = [];
  const service = createAssetUrlService({
    presignGet: async (key) => { signed.push(key); return `https://signed.example/${key}`; },
    authorize: async (keys) => keys.map((key) => key === 'processing/user-1/file.jpg'
      ? { key, allowed: false, code: 'FORBIDDEN_KEY' }
      : { key, allowed: true }),
  });
  const allowed = 'originals/another-user/file.jpg';
  const result = await service.createUrlsForUser('user-1', [allowed, 'processing/user-1/file.jpg']);
  assert.deepEqual(signed, [allowed]);
  assert.deepEqual(result.errors, [{ key: 'processing/user-1/file.jpg', code: 'FORBIDDEN_KEY' }]);
});

test('isolates one presigning failure without dropping other authorized assets', async () => {
  const { createAssetUrlService } = await loadServiceModule();
  assert.equal(typeof createAssetUrlService, 'function');
  const ok = 'originals/u/ok.jpg';
  const broken = 'thumbnails/u/broken.jpg';
  const service = createAssetUrlService({
    authorize: async (keys) => keys.map((key) => ({ key, allowed: true })),
    presignGet: async (key) => { if (key === broken) throw new Error('s3'); return 'https://signed.example/ok'; },
  });
  assert.deepEqual(await service.createUrlsForUser('user-1', [ok, broken]), {
    assets: [{ key: ok, url: 'https://signed.example/ok', expires_in: 900 }],
    errors: [{ key: broken, code: 'SIGNING_FAILED' }],
  });
});


test('rejects malformed or oversized batches before signing', async () => {
  const { createAssetUrlService } = await loadServiceModule();
  assert.equal(typeof createAssetUrlService, 'function');

  let signed = false;
  const service = createAssetUrlService({
    authorize: async () => [],
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
