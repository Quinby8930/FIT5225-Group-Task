import { createAssetUrlService } from './service.mjs';


const DEFAULT_EXPIRES_IN_SECONDS = 900;


function response(statusCode, payload, allowedOrigin) {
  return {
    statusCode,
    headers: {
      'Content-Type': 'application/json',
      'Access-Control-Allow-Origin': allowedOrigin,
      'Access-Control-Allow-Headers': 'Authorization,Content-Type',
      'Access-Control-Allow-Methods': 'POST,OPTIONS',
      'Cache-Control': 'no-store',
    },
    body: payload === undefined ? '' : JSON.stringify(payload),
  };
}


function errorResponse(error, allowedOrigin) {
  if (error?.code === 'INVALID_REQUEST') {
    return response(400, { code: 'INVALID_REQUEST' }, allowedOrigin);
  }
  if (error?.code === 'FORBIDDEN_KEY') {
    return response(403, { code: 'FORBIDDEN_KEY' }, allowedOrigin);
  }
  return response(500, { code: 'INTERNAL_ERROR' }, allowedOrigin);
}


export function createHandler({
  service,
  allowedOrigin = 'http://localhost:3000',
}) {
  return async function assetUrlsHandler(event = {}) {
    const method = event.requestContext?.http?.method || event.httpMethod;
    if (method === 'OPTIONS') {
      return response(204, undefined, allowedOrigin);
    }

    const userId = event.requestContext?.authorizer?.jwt?.claims?.sub;
    if (!userId) {
      return response(401, { code: 'UNAUTHENTICATED' }, allowedOrigin);
    }

    let request;
    try {
      request = JSON.parse(event.body);
    } catch {
      return response(400, { code: 'INVALID_REQUEST' }, allowedOrigin);
    }

    try {
      return response(
        200,
        await service.createUrlsForUser(userId, request?.keys),
        allowedOrigin,
      );
    } catch (error) {
      return errorResponse(error, allowedOrigin);
    }
  };
}


export function createS3Presigner({
  client,
  bucket,
  GetObjectCommand,
  getSignedUrl,
  expiresIn = DEFAULT_EXPIRES_IN_SECONDS,
}) {
  return async function presignGet(key) {
    const command = new GetObjectCommand({ Bucket: bucket, Key: key });
    return getSignedUrl(client, command, { expiresIn });
  };
}


async function createProductionHandler() {
  const [{ GetObjectCommand, S3Client }, { getSignedUrl }] = await Promise.all([
    import('@aws-sdk/client-s3'),
    import('@aws-sdk/s3-request-presigner'),
  ]);
  const presignGet = createS3Presigner({
    client: new S3Client({}),
    bucket: process.env.MEDIA_BUCKET_NAME,
    GetObjectCommand,
    getSignedUrl,
  });
  return createHandler({
    allowedOrigin: process.env.ALLOWED_ORIGIN || 'http://localhost:3000',
    service: createAssetUrlService({ presignGet }),
  });
}


let productionHandler;


export async function handler(event) {
  productionHandler ??= createProductionHandler();
  return (await productionHandler)(event);
}
