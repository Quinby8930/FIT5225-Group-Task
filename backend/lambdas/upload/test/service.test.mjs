import assert from 'node:assert/strict';
import test from 'node:test';

import { createUploadService } from '../service.mjs';
import { createS3Presigner } from '../presigner.mjs';

const request = {
  filename: 'wombat.jpg',
  content_type: 'image/jpeg',
  size_bytes: 2849132,
  checksum_sha256: Buffer.alloc(32, 7).toString('base64'),
};

test('reserves the exact metadata record before creating the pre-signed URL', async () => {
  const calls = [];
  const service = createUploadService({
    createFileId: () => 'file-123',
    reserveUpload: async (record) => { calls.push({ kind: 'reserve', record }); },
    presignUpload: async (input) => { calls.push({ kind: 'presign', input }); return 'https://upload.example'; },
  });

  const result = await service.createUpload({ userId: 'user-456', request });

  assert.deepEqual(calls, [
    {
      kind: 'reserve',
      record: {
        file_id: 'file-123', user_id: 'user-456', checksum: request.checksum_sha256,
        filename: 'wombat.jpg', file_type: 'image', content_type: 'image/jpeg',
        size_bytes: 2849132, object_key: 'originals/user-456/file-123/wombat.jpg', status: 'pending_upload',
      },
    },
    {
      kind: 'presign',
      input: {
        objectKey: 'originals/user-456/file-123/wombat.jpg', contentType: 'image/jpeg',
        checksumSha256: request.checksum_sha256, sizeBytes: 2849132,
      },
    },
  ]);
  assert.deepEqual(result, {
    file_id: 'file-123', object_key: 'originals/user-456/file-123/wombat.jpg', upload_url: 'https://upload.example', expires_in: 300,
    required_headers: { 'Content-Type': 'image/jpeg', 'x-amz-checksum-sha256': request.checksum_sha256 },
  });
});

test('does not pre-sign when duplicate reservation fails', async () => {
  let presigned = false;
  const service = createUploadService({
    createFileId: () => 'file-123',
    reserveUpload: async () => { const error = new Error('duplicate'); error.code = 'DUPLICATE_FILE'; throw error; },
    presignUpload: async () => { presigned = true; },
  });
  await assert.rejects(() => service.createUpload({ userId: 'user-456', request }), { code: 'DUPLICATE_FILE' });
  assert.equal(presigned, false);
});

test('binds the declared size and required headers to the 300-second PUT signature', async () => {
  let commandInput;
  let signingOptions;
  class PutObjectCommand { constructor(input) { commandInput = input; } }
  const presignUpload = createS3Presigner({
    client: {}, bucket: 'private-originals', PutObjectCommand,
    getSignedUrl: async (_client, _command, options) => { signingOptions = options; return 'https://signed.example'; },
  });
  const url = await presignUpload({
    objectKey: 'originals/user/file/wombat.jpg',
    contentType: 'image/jpeg',
    checksumSha256: request.checksum_sha256,
    sizeBytes: 2849132,
  });
  assert.equal(url, 'https://signed.example');
  assert.deepEqual(commandInput, {
    Bucket: 'private-originals',
    Key: 'originals/user/file/wombat.jpg',
    ContentType: 'image/jpeg',
    ContentLength: 2849132,
    ChecksumSHA256: request.checksum_sha256,
  });
  assert.deepEqual(signingOptions, {
    expiresIn: 300,
    signableHeaders: new Set(['content-type', 'content-length']),
    unhoistableHeaders: new Set(['x-amz-checksum-sha256']),
  });
});
