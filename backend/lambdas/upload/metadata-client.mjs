import { UploadError } from './validation.mjs';

export function createMetadataClient({ baseUrl, internalApiKey, fetchImpl = fetch }) {
  const endpoint = `${String(baseUrl).replace(/\/$/, '')}/internal/uploads/reserve`;
  return {
    async reserveUpload(record) {
      let response;
      try {
        response = await fetchImpl(endpoint, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            ...(internalApiKey ? { 'X-Internal-Api-Key': internalApiKey } : {}),
          },
          body: JSON.stringify(record),
        });
      } catch {
        throw new UploadError('DEPENDENCY_UNAVAILABLE');
      }
      if (response.status === 201) return;
      if (response.status === 409) {
        try {
          const duplicate = await response.json();
          if (typeof duplicate?.existing_file_id === 'string') {
            throw new UploadError('DUPLICATE_FILE', { existing_file_id: duplicate.existing_file_id });
          }
        } catch (error) {
          if (error?.code === 'DUPLICATE_FILE') throw error;
        }
      }
      throw new UploadError('DEPENDENCY_UNAVAILABLE');
    },
  };
}
