const ALLOWED_CONTENT_TYPES = new Set([
  'image/jpeg',
  'image/png',
  'image/webp',
  'video/mp4',
  'video/quicktime',
]);

export class UploadError extends Error {
  constructor(code, properties = {}) {
    super(code);
    this.code = code;
    Object.assign(this, properties);
  }
}

function sanitizeFilename(value) {
  if (typeof value !== 'string') return '';
  const basename = value.split(/[\\/]/).pop() || '';
  return basename.replace(/[^A-Za-z0-9._-]/g, '');
}

function isCanonicalChecksum(value) {
  if (typeof value !== 'string' || !/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(value)) return false;
  const decoded = Buffer.from(value, 'base64');
  return decoded.length === 32 && decoded.toString('base64') === value;
}

export function validateUploadRequest(input, maxBytes = 262_144_000) {
  if (!input || typeof input !== 'object') throw new UploadError('INVALID_REQUEST');
  if (!ALLOWED_CONTENT_TYPES.has(input.content_type)) throw new UploadError('UNSUPPORTED_FILE_TYPE');
  if (!Number.isInteger(input.size_bytes) || input.size_bytes <= 0 || input.size_bytes > maxBytes) throw new UploadError('INVALID_REQUEST');
  if (!isCanonicalChecksum(input.checksum_sha256)) throw new UploadError('INVALID_CHECKSUM');

  const filename = sanitizeFilename(input.filename);
  if (!filename || filename === '.' || filename === '..') throw new UploadError('INVALID_REQUEST');

  return {
    filename,
    contentType: input.content_type,
    sizeBytes: input.size_bytes,
    checksumSha256: input.checksum_sha256,
    fileType: input.content_type.startsWith('image/') ? 'image' : 'video',
  };
}
