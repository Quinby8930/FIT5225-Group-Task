import { randomUUID } from 'node:crypto';

import { createMetadataClient } from './metadata-client.mjs';
import { createS3Presigner } from './presigner.mjs';
import { createUploadService } from './service.mjs';

const DEFAULT_MAX_UPLOAD_BYTES = 262_144_000;
const DEFAULT_MAX_IMAGE_UPLOAD_BYTES = 12_582_912;

function response(statusCode, payload, allowedOrigin) {
  return {
    statusCode,
    headers: {
      'Content-Type': 'application/json',
      'Access-Control-Allow-Origin': allowedOrigin,
      'Access-Control-Allow-Headers': 'Authorization,Content-Type',
      'Access-Control-Allow-Methods': 'POST,OPTIONS',
    },
    body: payload === undefined ? '' : JSON.stringify(payload),
  };
}

function errorResponse(error, allowedOrigin) {
  const code = error?.code;
  if (code === 'FILE_TOO_LARGE') return response(413, { code }, allowedOrigin);
  if (code === 'INVALID_REQUEST' || code === 'UNSUPPORTED_FILE_TYPE' || code === 'INVALID_CHECKSUM') return response(400, { code }, allowedOrigin);
  if (code === 'UNAUTHENTICATED') return response(401, { code }, allowedOrigin);
  if (code === 'DUPLICATE_FILE') return response(409, { code, ...(error.existing_file_id ? { existing_file_id: error.existing_file_id } : {}) }, allowedOrigin);
  if (code === 'DEPENDENCY_UNAVAILABLE') return response(503, { code }, allowedOrigin);
  return response(500, { code: 'INTERNAL_ERROR' }, allowedOrigin);
}

export function createHandler({ createService, maxUploadBytes = DEFAULT_MAX_UPLOAD_BYTES, maxImageUploadBytes = DEFAULT_MAX_IMAGE_UPLOAD_BYTES, allowedOrigin = 'http://localhost:3000' }) {
  let service;
  return async function uploadHandler(event = {}) {
    if (event.requestContext?.http?.method === 'OPTIONS' || event.httpMethod === 'OPTIONS') return response(204, undefined, allowedOrigin);
    const userId = event.requestContext?.authorizer?.jwt?.claims?.sub;
    if (!userId) return response(401, { code: 'UNAUTHENTICATED' }, allowedOrigin);
    let request;
    try {
      request = JSON.parse(event.body);
    } catch {
      return response(400, { code: 'INVALID_REQUEST' }, allowedOrigin);
    }
    try {
      service ??= createService({ maxBytes: maxUploadBytes, maxImageBytes: maxImageUploadBytes });
      return response(200, await service.createUpload({ userId, request }), allowedOrigin);
    } catch (error) {
      return errorResponse(error, allowedOrigin);
    }
  };
}

function configuredMaxBytes() {
  const parsed = Number(process.env.MAX_UPLOAD_BYTES);
  return Number.isInteger(parsed) && parsed > 0 ? parsed : DEFAULT_MAX_UPLOAD_BYTES;
}

function configuredMaxImageBytes() {
  const parsed = Number(process.env.MAX_IMAGE_UPLOAD_BYTES);
  return Number.isInteger(parsed) && parsed > 0 ? parsed : DEFAULT_MAX_IMAGE_UPLOAD_BYTES;
}

async function createProductionHandler() {
  const [{ S3Client, PutObjectCommand }, { getSignedUrl }] = await Promise.all([
    import('@aws-sdk/client-s3'),
    import('@aws-sdk/s3-request-presigner'),
  ]);
  const metadataClient = createMetadataClient({
    baseUrl: process.env.METADATA_API_BASE_URL,
    internalApiKey: process.env.INTERNAL_API_KEY,
  });
  const presignUpload = createS3Presigner({
    client: new S3Client({}),
    bucket: process.env.UPLOAD_BUCKET,
    PutObjectCommand,
    getSignedUrl,
  });
  return createHandler({
    maxUploadBytes: configuredMaxBytes(),
    maxImageUploadBytes: configuredMaxImageBytes(),
    allowedOrigin: process.env.ALLOWED_ORIGIN || 'http://localhost:3000',
    createService: ({ maxBytes, maxImageBytes }) => createUploadService({
      createFileId: randomUUID,
      reserveUpload: metadataClient.reserveUpload,
      presignUpload,
      maxBytes,
      maxImageBytes,
    }),
  });
}

let productionHandler;
export async function handler(event) {
  productionHandler ??= createProductionHandler();
  return (await productionHandler)(event);
}
