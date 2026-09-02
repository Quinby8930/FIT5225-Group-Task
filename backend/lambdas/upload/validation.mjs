const ALLOWED_CONTENT_TYPES = new Set([
  'image/jpeg',
  'image/png',
  'image/webp',
  'video/mp4',
  'video/quicktime',
]);
const MAX_DUPLICATE_TAGS = 64;
const MAX_DUPLICATE_TAG_NAME_BYTES = 128;
const MAX_DUPLICATE_TAG_COUNT = 1_000_000;

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

function isPlainObject(value) {
  return value !== null
    && typeof value === 'object'
    && !Array.isArray(value)
    && Object.getPrototypeOf(value) === Object.prototype;
}

export function validateDuplicateDetails(input) {
  if (!isPlainObject(input)) throw new UploadError('DEPENDENCY_UNAVAILABLE');
  const fields = Object.keys(input).sort();
  if (fields.length !== 2 || fields[0] !== 'existing_file_id' || fields[1] !== 'tags') {
    throw new UploadError('DEPENDENCY_UNAVAILABLE');
  }
  const fileId = input.existing_file_id;
  if (
    typeof fileId !== 'string'
    || !fileId
    || fileId !== fileId.trim()
    || fileId.length > 256
    || [...fileId].some((character) => character.codePointAt(0) < 0x20)
  ) throw new UploadError('DEPENDENCY_UNAVAILABLE');
  if (!isPlainObject(input.tags)) throw new UploadError('DEPENDENCY_UNAVAILABLE');
  const entries = Object.entries(input.tags);
  if (entries.length > MAX_DUPLICATE_TAGS) {
    throw new UploadError('DEPENDENCY_UNAVAILABLE');
  }
  const tags = {};
  for (const [name, count] of entries) {
    if (
      !name
      || name !== name.trim()
      || Buffer.byteLength(name, 'utf8') > MAX_DUPLICATE_TAG_NAME_BYTES
      || [...name].some((character) => character.codePointAt(0) < 0x20)
      || !Number.isSafeInteger(count)
      || count <= 0
      || count > MAX_DUPLICATE_TAG_COUNT
    ) throw new UploadError('DEPENDENCY_UNAVAILABLE');
    tags[name] = count;
  }
  return { existing_file_id: fileId, tags };
}

export function validateUploadRequest(
  input,
  { maxBytes = 262_144_000, maxImageBytes = 12_582_912 } = {},
) {
  if (!input || typeof input !== 'object') throw new UploadError('INVALID_REQUEST');
  if (!ALLOWED_CONTENT_TYPES.has(input.content_type)) throw new UploadError('UNSUPPORTED_FILE_TYPE');
  if (!Number.isInteger(input.size_bytes) || input.size_bytes <= 0) throw new UploadError('INVALID_REQUEST');
  const fileType = input.content_type.startsWith('image/') ? 'image' : 'video';
  const sizeLimit = fileType === 'image' ? Math.min(maxBytes, maxImageBytes) : maxBytes;
  if (input.size_bytes > sizeLimit) throw new UploadError('FILE_TOO_LARGE');
  if (!isCanonicalChecksum(input.checksum_sha256)) throw new UploadError('INVALID_CHECKSUM');

  const filename = sanitizeFilename(input.filename);
  if (!filename || filename === '.' || filename === '..') throw new UploadError('INVALID_REQUEST');

  return {
    filename,
    contentType: input.content_type,
    sizeBytes: input.size_bytes,
    checksumSha256: input.checksum_sha256,
    fileType,
  };
}
