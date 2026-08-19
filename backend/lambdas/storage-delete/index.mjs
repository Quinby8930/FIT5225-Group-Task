import { createStorageDeleteService } from './service.mjs';


function response(statusCode, payload) {
  return {
    statusCode,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  };
}

function validInvocation(event) {
  return (
    event
    && typeof event === 'object'
    && !Array.isArray(event)
    && typeof event.user_id === 'string'
    && event.user_id.length > 0
    && !event.user_id.includes('/')
    && Array.isArray(event.keys)
    && event.keys.every((key) => typeof key === 'string')
  );
}

export function createHandler({ service }) {
  return async function storageDeleteHandler(event) {
    if (!validInvocation(event)) return response(400, { code: 'INVALID_REQUEST' });

    try {
      const deletedCount = await service.deleteForUser(event.user_id, event.keys);
      return response(200, { deleted_count: deletedCount });
    } catch (error) {
      if (error?.code === 'INVALID_REQUEST') return response(400, { code: 'INVALID_REQUEST' });
      if (error?.code === 'FORBIDDEN_KEY') return response(403, { code: 'FORBIDDEN_KEY' });
      return response(500, { code: 'INTERNAL_ERROR' });
    }
  };
}

export function createS3DeleteKeys({ client, bucket, DeleteObjectsCommand }) {
  return async function deleteKeys(keys) {
    const result = await client.send(new DeleteObjectsCommand({
      Bucket: bucket,
      Delete: { Objects: keys.map((Key) => ({ Key })) },
    }));
    if (Array.isArray(result?.Errors) && result.Errors.length > 0) {
      const error = new Error('S3 reported one or more object deletion failures');
      error.code = 'STORAGE_DELETE_FAILED';
      throw error;
    }
  };
}

async function createProductionHandler() {
  const { DeleteObjectsCommand, S3Client } = await import('@aws-sdk/client-s3');
  const deleteKeys = createS3DeleteKeys({
    client: new S3Client({}),
    bucket: process.env.MEDIA_BUCKET_NAME,
    DeleteObjectsCommand,
  });
  return createHandler({ service: createStorageDeleteService({ deleteKeys }) });
}

let productionHandler;

export async function handler(event) {
  productionHandler ??= createProductionHandler();
  return (await productionHandler)(event);
}
