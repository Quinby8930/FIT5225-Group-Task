import assert from 'node:assert/strict';
import test from 'node:test';

import { validateUploadRequest } from '../validation.mjs';

const checksum = Buffer.alloc(32, 7).toString('base64');

function request(overrides = {}) {
  return {
    filename: 'wombat.jpg',
    content_type: 'image/jpeg',
    size_bytes: 2849132,
    checksum_sha256: checksum,
    ...overrides,
  };
}

function expectCode(fn, code) {
  assert.throws(fn, (error) => error?.code === code);
}

test('accepts every permitted image and video media family', () => {
  for (const contentType of ['image/jpeg', 'image/png', 'image/webp', 'video/mp4', 'video/quicktime']) {
    const result = validateUploadRequest(request({ content_type: contentType }));
    assert.equal(result.contentType, contentType);
    assert.equal(result.fileType, contentType.startsWith('image/') ? 'image' : 'video');
  }
});

test('rejects unsupported media types', () => {
  expectCode(() => validateUploadRequest(request({ content_type: 'application/pdf' })), 'UNSUPPORTED_FILE_TYPE');
});

test('rejects malformed and noncanonical SHA-256 checksums', () => {
  expectCode(() => validateUploadRequest(request({ checksum_sha256: 'not base64' })), 'INVALID_CHECKSUM');
  expectCode(() => validateUploadRequest(request({ checksum_sha256: `${checksum}\n` })), 'INVALID_CHECKSUM');
  expectCode(() => validateUploadRequest(request({ checksum_sha256: Buffer.alloc(31).toString('base64') })), 'INVALID_CHECKSUM');
});

test('rejects zero, non-integer, and over-limit sizes', () => {
  expectCode(() => validateUploadRequest(request({ size_bytes: 0 })), 'INVALID_REQUEST');
  expectCode(() => validateUploadRequest(request({ size_bytes: 1.5 })), 'INVALID_REQUEST');
  expectCode(() => validateUploadRequest(request({ size_bytes: 101 }), { maxBytes: 100, maxImageBytes: 100 }), 'FILE_TOO_LARGE');
  assert.equal(validateUploadRequest(request({ size_bytes: 100 }), { maxBytes: 100, maxImageBytes: 100 }).sizeBytes, 100);
});

test('applies the 12 MiB inference cap only to images', () => {
  const maxImageBytes = 12_582_912;
  const maxBytes = 262_144_000;
  const limits = { maxBytes, maxImageBytes };

  assert.equal(validateUploadRequest(request({ size_bytes: maxImageBytes }), limits).sizeBytes, maxImageBytes);
  expectCode(() => validateUploadRequest(request({ size_bytes: maxImageBytes + 1 }), limits), 'FILE_TOO_LARGE');
  assert.equal(validateUploadRequest(request({ content_type: 'video/mp4', size_bytes: maxImageBytes + 1 }), limits).sizeBytes, maxImageBytes + 1);
  assert.equal(validateUploadRequest(request({ content_type: 'video/mp4', size_bytes: maxBytes }), limits).sizeBytes, maxBytes);
  expectCode(() => validateUploadRequest(request({ content_type: 'video/mp4', size_bytes: maxBytes + 1 }), limits), 'FILE_TOO_LARGE');
});

test('removes paths and unsafe filename characters', () => {
  const result = validateUploadRequest(request({ filename: '../../wo mbat?.jpg' }));
  assert.equal(result.filename, 'wombat.jpg');
});

test('rejects a filename with no safe characters', () => {
  expectCode(() => validateUploadRequest(request({ filename: '../??//' })), 'INVALID_REQUEST');
});
