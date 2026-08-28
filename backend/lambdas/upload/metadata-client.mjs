import { TextDecoder } from 'node:util';

import { UploadError } from './validation.mjs';

const MAX_JSON_RESPONSE_BYTES = 1024 * 1024;

function reservationEndpoint(baseUrl) {
  let url;
  try {
    url = new URL(String(baseUrl));
  } catch {
    throw new UploadError('INVALID_CONFIGURATION');
  }
  if (
    url.protocol !== 'https:'
    || !url.hostname
    || url.username
    || url.password
    || url.search
    || url.hash
  ) throw new UploadError('INVALID_CONFIGURATION');
  return `${url.href.replace(/\/$/, '')}/internal/uploads/reserve`;
}

async function readBoundedJson(response) {
  const reader = response.body?.getReader?.({ mode: 'byob' });
  if (!reader) throw new UploadError('DEPENDENCY_UNAVAILABLE');

  const body = new Uint8Array(MAX_JSON_RESPONSE_BYTES + 1);
  let totalBytes = 0;
  try {
    while (true) {
      const requestedBytes = Math.min(64 * 1024, body.byteLength - totalBytes);
      const { done, value } = await reader.read(new Uint8Array(requestedBytes));
      if (done) break;
      if (
        !(value instanceof Uint8Array)
        || value.byteLength === 0
        || value.byteLength > requestedBytes
      ) throw new UploadError('DEPENDENCY_UNAVAILABLE');
      body.set(value, totalBytes);
      totalBytes += value.byteLength;
      if (totalBytes > MAX_JSON_RESPONSE_BYTES) {
        await reader.cancel?.();
        throw new UploadError('DEPENDENCY_UNAVAILABLE');
      }
    }
  } finally {
    reader.releaseLock?.();
  }

  try {
    const decoded = new TextDecoder('utf-8', { fatal: true }).decode(
      body.subarray(0, totalBytes),
    );
    return JSON.parse(decoded);
  } catch {
    throw new UploadError('DEPENDENCY_UNAVAILABLE');
  }
}

export function createMetadataClient({
  baseUrl,
  internalApiKey,
  fetchImpl = fetch,
  timeoutMs = 5_000,
  AbortControllerImpl = AbortController,
  setTimeoutImpl = setTimeout,
  clearTimeoutImpl = clearTimeout,
}) {
  const endpoint = reservationEndpoint(baseUrl);
  return {
    async reserveUpload(record) {
      let response;
      const controller = new AbortControllerImpl();
      const timeout = setTimeoutImpl(() => controller.abort(), timeoutMs);
      try {
        response = await fetchImpl(endpoint, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            ...(internalApiKey ? { 'X-Internal-Api-Key': internalApiKey } : {}),
          },
          body: JSON.stringify(record),
          signal: controller.signal,
          redirect: 'error',
        });
        if (response.status === 201) {
          if (!response.body?.getReader) return undefined;
          const reservation = await readBoundedJson(response);
          if (
            typeof reservation?.file_id === 'string'
            && typeof reservation?.object_key === 'string'
          ) {
            return {
              file_id: reservation.file_id,
              object_key: reservation.object_key,
            };
          }
          throw new UploadError('DEPENDENCY_UNAVAILABLE');
        }
        if (response.status === 409) {
          const duplicate = await readBoundedJson(response);
          if (typeof duplicate?.existing_file_id === 'string') {
            throw new UploadError('DUPLICATE_FILE', { existing_file_id: duplicate.existing_file_id });
          }
        }
      } catch (error) {
        if (error?.code === 'DUPLICATE_FILE') throw error;
        throw new UploadError('DEPENDENCY_UNAVAILABLE');
      } finally {
        clearTimeoutImpl(timeout);
      }
      throw new UploadError('DEPENDENCY_UNAVAILABLE');
    },
  };
}
