import assert from 'node:assert/strict';
import test from 'node:test';


async function loadHandlerModule() {
  return import('../index.mjs').catch(() => ({}));
}


function event({ method = 'POST', sub = 'user-1', body = { keys: [] }, headers } = {}) {
  return {
    requestContext: {
      http: { method },
      authorizer: sub === null ? {} : { jwt: { claims: { sub } } },
    },
    body: typeof body === 'string' ? body : JSON.stringify(body),
    headers,
  };
}


function parseBody(response) {
  return response.body ? JSON.parse(response.body) : null;
}


test('returns signed assets for the authenticated Cognito subject', async () => {
  const { createHandler } = await loadHandlerModule();
  assert.equal(typeof createHandler, 'function');

  const calls = [];
  const invoke = createHandler({
    allowedOrigin: 'http://localhost:3000',
    service: {
      createUrlsForUser: async (userId, keys) => {
        calls.push({ userId, keys });
        return {
          assets: [{
            key: keys[0],
            url: 'https://signed.example/thumbnail.jpg',
            expires_in: 900,
          }],
          errors: [],
        };
      },
    },
  });
  const key = 'thumbnails/user-1/file-1/thumbnail.jpg';

  const response = await invoke(event({ body: { keys: [key] }, headers: { origin: 'http://localhost:3000' } }));

  assert.equal(response.statusCode, 200);
  assert.equal(response.headers['Access-Control-Allow-Origin'], 'http://localhost:3000');
  assert.equal(response.headers['Cache-Control'], 'no-store');
  assert.deepEqual(parseBody(response), {
    assets: [{ key, url: 'https://signed.example/thumbnail.jpg', expires_in: 900 }],
    errors: [],
  });
  assert.deepEqual(calls, [{ userId: 'user-1', keys: [key] }]);
});


test('rejects missing identity and malformed JSON before calling the service', async () => {
  const { createHandler } = await loadHandlerModule();
  assert.equal(typeof createHandler, 'function');

  let calls = 0;
  const invoke = createHandler({
    service: {
      createUrlsForUser: async () => {
        calls += 1;
        return { assets: [] };
      },
    },
  });

  const unauthenticated = await invoke(event({ sub: null }));
  const malformed = await invoke(event({ body: '{not-json' }));

  assert.equal(unauthenticated.statusCode, 401);
  assert.deepEqual(parseBody(unauthenticated), { code: 'UNAUTHENTICATED' });
  assert.equal(malformed.statusCode, 400);
  assert.deepEqual(parseBody(malformed), { code: 'INVALID_REQUEST' });
  assert.equal(calls, 0);
});


test('handles unauthenticated preflight without invoking the service', async () => {
  const { createHandler } = await loadHandlerModule();
  assert.equal(typeof createHandler, 'function');

  const invoke = createHandler({
    service: {
      createUrlsForUser: async () => {
        throw new Error('must not be called');
      },
    },
  });

  const response = await invoke(event({ method: 'OPTIONS', sub: null, headers: { origin: 'http://localhost:3000' } }));

  assert.equal(response.statusCode, 204);
  assert.equal(response.body, '');
  assert.equal(response.headers['Access-Control-Allow-Methods'], 'POST,OPTIONS');
});

test('echoes only allowlisted local and GitHub Pages origins', async () => {
  const { createHandler } = await loadHandlerModule();
  const invoke = createHandler({
    allowedOrigin: 'http://localhost:3000',
    allowedOrigins: ['http://localhost:3000', 'https://quinby8930.github.io'],
    service: { createUrlsForUser: async () => ({ assets: [], errors: [] }) },
  });

  for (const origin of ['http://localhost:3000', 'https://quinby8930.github.io']) {
    const response = await invoke(event({ headers: { Origin: origin } }));
    assert.equal(response.headers['Access-Control-Allow-Origin'], origin);
  }
});

test('omits allow-origin for a request origin outside the configured allowlist', async () => {
  const { createHandler } = await loadHandlerModule();
  const invoke = createHandler({
    allowedOrigins: ['http://localhost:3000', 'https://quinby8930.github.io'],
    service: { createUrlsForUser: async () => ({ assets: [], errors: [] }) },
  });

  const response = await invoke(event({ headers: { origin: 'https://untrusted.example' } }));

  assert.equal(response.statusCode, 200);
  assert.equal('Access-Control-Allow-Origin' in response.headers, false);
});


test('maps authorization and unexpected failures without exposing private details', async () => {
  const { createHandler } = await loadHandlerModule();
  assert.equal(typeof createHandler, 'function');

  for (const [errorCode, expectedStatus, expectedCode] of [
    ['AUTHORIZATION_UNAVAILABLE', 503, 'AUTHORIZATION_UNAVAILABLE'],
    [undefined, 500, 'INTERNAL_ERROR'],
  ]) {
    const invoke = createHandler({
      service: {
        createUrlsForUser: async () => {
          const error = new Error('originals/another-user/private.jpg');
          error.code = errorCode;
          throw error;
        },
      },
    });

    const response = await invoke(event());

    assert.equal(response.statusCode, expectedStatus);
    assert.deepEqual(parseBody(response), { code: expectedCode });
    assert.equal(response.body.includes('another-user'), false);
  }
});


test('S3 adapter signs a private GetObject request for 900 seconds', async () => {
  const { createS3Presigner } = await loadHandlerModule();
  assert.equal(typeof createS3Presigner, 'function');

  let commandInput;
  let signingOptions;
  class GetObjectCommand {
    constructor(input) {
      commandInput = input;
    }
  }
  const presignGet = createS3Presigner({
    client: {},
    bucket: 'private-media',
    GetObjectCommand,
    getSignedUrl: async (_client, _command, options) => {
      signingOptions = options;
      return 'https://signed.example/private';
    },
  });

  const url = await presignGet('originals/user-1/file-1/wombat.jpg');

  assert.equal(url, 'https://signed.example/private');
  assert.deepEqual(commandInput, {
    Bucket: 'private-media',
    Key: 'originals/user-1/file-1/wombat.jpg',
  });
  assert.deepEqual(signingOptions, { expiresIn: 900 });
});

test('production bootstrap returns the standard 503 response for missing metadata configuration', async () => {
  const { createProductionHandler } = await loadHandlerModule();
  assert.equal(typeof createProductionHandler, 'function');
  for (const environment of [
    { INTERNAL_API_KEY: 'secret', ALLOWED_ORIGIN: 'https://app.example' },
    { METADATA_API_BASE_URL: 'https://metadata.example', ALLOWED_ORIGIN: 'https://app.example' },
  ]) {
    const invoke = await createProductionHandler({ environment });
    const result = await invoke(event({ body: { keys: ['originals/u/file.jpg'] }, headers: { origin: 'https://app.example' } }));
    assert.equal(result.statusCode, 503);
    assert.deepEqual(parseBody(result), { code: 'AUTHORIZATION_UNAVAILABLE' });
    assert.equal(result.headers['Access-Control-Allow-Origin'], 'https://app.example');
    assert.equal(result.body.includes('secret'), false);
  }
});
