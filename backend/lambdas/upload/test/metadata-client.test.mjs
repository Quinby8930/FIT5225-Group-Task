import assert from 'node:assert/strict';
import http from 'node:http';
import test from 'node:test';

import { createMetadataClient } from '../metadata-client.mjs';

const record = { file_id: 'new-file', user_id: 'user-1', checksum: Buffer.alloc(32, 7).toString('base64'), filename: 'wombat.jpg', file_type: 'image', content_type: 'image/jpeg', size_bytes: 12, object_key: 'originals/user-1/new-file/wombat.jpg', status: 'pending_upload' };

async function withServer(responder, run) {
  const server = http.createServer(responder);
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  try {
    await run(`http://127.0.0.1:${server.address().port}`);
  } finally {
    await new Promise((resolve, reject) => server.close((error) => error ? reject(error) : resolve()));
  }
}

test('POSTs the reservation contract, serializes JSON, and sends the optional internal key', async () => {
  await withServer(async (req, res) => {
    let body = ''; for await (const chunk of req) body += chunk;
    assert.equal(req.method, 'POST'); assert.equal(req.url, '/internal/uploads/reserve');
    assert.equal(req.headers['content-type'], 'application/json'); assert.equal(req.headers['x-internal-api-key'], 'secret');
    assert.deepEqual(JSON.parse(body), record);
    res.writeHead(201).end();
  }, async (baseUrl) => createMetadataClient({ baseUrl, internalApiKey: 'secret' }).reserveUpload(record));
});

test('maps a metadata duplicate response to the existing file identifier', async () => {
  await withServer((_req, res) => res.writeHead(409, { 'Content-Type': 'application/json' }).end(JSON.stringify({ existing_file_id: 'existing-file' })), async (baseUrl) => {
    await assert.rejects(() => createMetadataClient({ baseUrl }).reserveUpload(record), (error) => error?.code === 'DUPLICATE_FILE' && error.existing_file_id === 'existing-file');
  });
});

test('maps unsuccessful metadata and connection failures to dependency unavailable', async () => {
  await withServer((_req, res) => res.writeHead(500).end('ignored'), async (baseUrl) => {
    await assert.rejects(() => createMetadataClient({ baseUrl }).reserveUpload(record), { code: 'DEPENDENCY_UNAVAILABLE' });
  });
  await assert.rejects(() => createMetadataClient({ baseUrl: 'http://127.0.0.1:1' }).reserveUpload(record), { code: 'DEPENDENCY_UNAVAILABLE' });
});
