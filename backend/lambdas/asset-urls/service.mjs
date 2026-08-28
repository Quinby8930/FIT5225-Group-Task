function codedError(code) {
  const error = new Error(code);
  error.code = code;
  return error;
}

const DENIAL_CODES = new Set(['FORBIDDEN_KEY', 'NOT_FOUND', 'NOT_COMPLETED']);

function validDecisions(decisions, keys) {
  if (!Array.isArray(decisions) || decisions.length !== keys.length) return false;
  const expected = new Set(keys);
  const seen = new Set();
  return decisions.every((decision) => {
    if (!decision || typeof decision !== 'object'
      || typeof decision.key !== 'string' || typeof decision.allowed !== 'boolean'
      || !expected.has(decision.key) || seen.has(decision.key)) return false;
    seen.add(decision.key);
    return decision.allowed
      ? !Object.hasOwn(decision, 'code')
      : typeof decision.code === 'string' && DENIAL_CODES.has(decision.code);
  }) && seen.size === expected.size;
}


export function createAssetUrlService({
  presignGet,
  authorize,
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
        || keys.length < 1
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
      let decisions;
      try {
        decisions = await authorize(uniqueKeys);
        if (!validDecisions(decisions, uniqueKeys)) throw codedError('AUTHORIZATION_UNAVAILABLE');
      } catch {
        throw codedError('AUTHORIZATION_UNAVAILABLE');
      }
      const byKey = new Map(decisions.map((decision) => [decision.key, decision]));
      const assets = [];
      const errors = [];
      for (const key of uniqueKeys) {
        const decision = byKey.get(key);
        if (!decision.allowed) {
          errors.push({ key, code: decision.code });
          continue;
        }
        try {
          assets.push({ key, url: await presignGet(key), expires_in: expiresIn });
        } catch {
          errors.push({ key, code: 'SIGNING_FAILED' });
        }
      }
      return { assets, errors };
    },
  };
}
