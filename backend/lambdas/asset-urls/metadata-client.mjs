const MAX_RESPONSE_BYTES = 64 * 1024;
const DENIAL_CODES = new Set(['FORBIDDEN_KEY', 'NOT_FOUND', 'NOT_COMPLETED']);

function unavailable() {
  const error = new Error('AUTHORIZATION_UNAVAILABLE');
  error.code = 'AUTHORIZATION_UNAVAILABLE';
  return error;
}

function endpointFrom(baseUrl) {
  let parsed;
  try {
    parsed = new URL(baseUrl);
  } catch {
    throw unavailable();
  }
  if (
    parsed.protocol !== 'https:'
    || parsed.username
    || parsed.password
    || parsed.hash
  ) {
    throw unavailable();
  }
  parsed.search = '';
  parsed.pathname = `${parsed.pathname.replace(/\/$/, '')}/internal/assets/authorize`;
  return parsed.toString();
}

async function readLimitedJson(response, maxResponseBytes, controller) {
  const contentType = response.headers.get('content-type') || '';
  if (!contentType.toLowerCase().startsWith('application/json') || !response.body) {
    throw unavailable();
  }
  const reader = response.body.getReader();
  const chunks = [];
  let size = 0;
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > maxResponseBytes) throw unavailable();
      chunks.push(value);
    }
    const text = new TextDecoder('utf-8', { fatal: true }).decode(Buffer.concat(chunks));
    return JSON.parse(text);
  } catch (error) {
    try {
      await reader.cancel();
    } catch {
      // A cancellation failure must not replace the authorization failure.
    }
    controller.abort();
    throw error?.code === 'AUTHORIZATION_UNAVAILABLE' ? error : unavailable();
  } finally {
    reader.releaseLock();
  }
}

function validateDecisions(payload, keys) {
  if (!payload || typeof payload !== 'object' || Array.isArray(payload) || !Array.isArray(payload.decisions)) {
    throw unavailable();
  }
  const requested = new Set(keys);
  if (payload.decisions.length !== keys.length) throw unavailable();
  const seen = new Set();
  for (const decision of payload.decisions) {
    if (!decision || typeof decision !== 'object' || Array.isArray(decision)
      || typeof decision.key !== 'string' || typeof decision.allowed !== 'boolean'
      || !requested.has(decision.key) || seen.has(decision.key)) {
      throw unavailable();
    }
    if (decision.allowed) {
      if (Object.hasOwn(decision, 'code')) throw unavailable();
    } else if (typeof decision.code !== 'string' || !DENIAL_CODES.has(decision.code)) {
      throw unavailable();
    }
    seen.add(decision.key);
  }
  if (seen.size !== requested.size) throw unavailable();
  return payload.decisions;
}

export function createMetadataAuthorizationClient({
  baseUrl,
  internalApiKey,
  fetchImpl = fetch,
  timeoutMs = 4_000,
  maxResponseBytes = MAX_RESPONSE_BYTES,
}) {
  const endpoint = endpointFrom(baseUrl);
  if (typeof internalApiKey !== 'string' || !internalApiKey) throw unavailable();
  return {
    async authorize(keys) {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), timeoutMs);
      try {
        const response = await fetchImpl(endpoint, {
          method: 'POST',
          redirect: 'error',
          signal: controller.signal,
          headers: {
            'Content-Type': 'application/json',
            'X-Internal-Api-Key': internalApiKey,
          },
          body: JSON.stringify({ keys }),
        });
        if (!response || !response.ok) throw unavailable();
        return validateDecisions(await readLimitedJson(response, maxResponseBytes, controller), keys);
      } catch (error) {
        controller.abort();
        if (error?.code === 'AUTHORIZATION_UNAVAILABLE') throw error;
        throw unavailable();
      } finally {
        clearTimeout(timer);
      }
    },
  };
}
