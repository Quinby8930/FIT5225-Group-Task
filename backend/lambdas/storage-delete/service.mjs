export class StorageDeleteError extends Error {
  constructor(code, message) {
    super(message);
    this.code = code;
  }
}

export function createStorageDeleteService({ deleteKeys, batchSize = 1_000 }) {
  if (!Number.isInteger(batchSize) || batchSize <= 0) {
    throw new StorageDeleteError('INVALID_REQUEST', 'Batch size must be a positive integer');
  }
  const effectiveBatchSize = Math.min(batchSize, 1_000);

  return {
    async deleteForUser(userId, keys) {
      if (
        typeof userId !== 'string'
        || userId.length === 0
        || userId.includes('/')
        || !Array.isArray(keys)
      ) {
        throw new StorageDeleteError('INVALID_REQUEST', 'A user ID and key list are required');
      }

      const prefixes = [
        `originals/${userId}/`,
        `thumbnails/${userId}/`,
        `processing/${userId}/`,
      ];
      if (keys.some((key) => typeof key !== 'string' || !prefixes.some((prefix) => key.startsWith(prefix)))) {
        throw new StorageDeleteError('FORBIDDEN_KEY', 'A key is outside the user media prefixes');
      }

      const uniqueKeys = [...new Set(keys)];
      for (let offset = 0; offset < uniqueKeys.length; offset += effectiveBatchSize) {
        await deleteKeys(uniqueKeys.slice(offset, offset + effectiveBatchSize));
      }
      return uniqueKeys.length;
    },
  };
}
