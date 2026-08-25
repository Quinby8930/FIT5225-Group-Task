function codedError(code) {
  const error = new Error(code);
  error.code = code;
  return error;
}


function ownedAssetKey(userId, key) {
  const prefixes = [
    `originals/${userId}/`,
    `thumbnails/${userId}/`,
  ];
  return prefixes.some((prefix) => key.startsWith(prefix) && key.length > prefix.length);
}


export function createAssetUrlService({
  presignGet,
  expiresIn = 900,
  maxKeys = 100,
}) {
  return {
    async createUrlsForUser(userId, keys) {
      if (
        typeof userId !== 'string'
        || !userId
        || userId.includes('/')
        || !Array.isArray(keys)
        || keys.length > maxKeys
        || keys.some((key) => (
          typeof key !== 'string'
          || !key
          || Buffer.byteLength(key, 'utf8') > 1024
        ))
      ) {
        throw codedError('INVALID_REQUEST');
      }

      const uniqueKeys = [...new Set(keys)];
      if (uniqueKeys.some((key) => !ownedAssetKey(userId, key))) {
        throw codedError('FORBIDDEN_KEY');
      }

      const assets = await Promise.all(
        uniqueKeys.map(async (key) => ({
          key,
          url: await presignGet(key),
          expires_in: expiresIn,
        })),
      );
      return { assets };
    },
  };
}
