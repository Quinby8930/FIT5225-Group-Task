import { UploadError } from './validation.mjs';

export function createMetadataClient({
  baseUrl,
  internalApiKey,
  fetchImpl = fetch,
  timeoutMs = 5_000,
  AbortControllerImpl = AbortController,
  setTimeoutImpl = setTimeout,
  clearTimeoutImpl = clearTimeout,
}) {
  const endpoint = `${String(baseUrl).replace(/\/$/, '')}/internal/uploads/reserve`;
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
        });
        if (response.status === 201) return;
        if (response.status === 409) {
          const duplicate = await response.json();
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
