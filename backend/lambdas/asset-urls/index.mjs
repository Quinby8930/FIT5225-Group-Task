import { createAssetUrlService } from './service.mjs';
import { createMetadataAuthorizationClient } from './metadata-client.mjs';


const DEFAULT_EXPIRES_IN_SECONDS = 900;


function configuredAllowedOrigins(allowedOrigin, allowedOrigins) {
  return [...new Set([allowedOrigin, allowedOrigins]
    .flatMap((origins) => Array.isArray(origins) ? origins : String(origins || '').split(','))
    .map((origin) => origin.trim())
    .filter(Boolean))];
}

function requestOrigin(event) {
  const headers = event?.headers || {};
  const match = Object.entries(headers).find(([name]) => name.toLowerCase() === 'origin');
  return typeof match?.[1] === 'string' ? match[1] : undefined;
}

function allowedRequestOrigin(event, allowedOrigins) {
  const origin = requestOrigin(event);
  return origin && allowedOrigins.includes(origin) ? origin : undefined;
}

function response(statusCode, payload, allowedOrigin) {
  return {
    statusCode,
    headers: {
      'Content-Type': 'application/json',
      'Access-Control-Allow-Headers': 'Authorization,Content-Type',
      'Access-Control-Allow-Methods': 'POST,OPTIONS',
      'Cache-Control': 'no-store',
      ...(allowedOrigin ? { 'Access-Control-Allow-Origin': allowedOrigin } : {}),
    },
    body: payload === undefined ? '' : JSON.stringify(payload),
  };
}


function errorResponse(error, allowedOrigin) {
  if (error?.code === 'INVALID_REQUEST') {
    return response(400, { code: 'INVALID_REQUEST' }, allowedOrigin);
  }
  if (error?.code === 'AUTHORIZATION_UNAVAILABLE') {
    return response(503, { code: 'AUTHORIZATION_UNAVAILABLE' }, allowedOrigin);
  }
  return response(500, { code: 'INTERNAL_ERROR' }, allowedOrigin);
}


export function createHandler({
  service,
  allowedOrigin = 'http://localhost:3000',
  allowedOrigins,
}) {
  const originAllowlist = configuredAllowedOrigins(allowedOrigin, allowedOrigins);
  return async function assetUrlsHandler(event = {}) {
    const responseOrigin = allowedRequestOrigin(event, originAllowlist);
    const method = event.requestContext?.http?.method || event.httpMethod;
    if (method === 'OPTIONS') {
      return response(204, undefined, responseOrigin);
    }

    const userId = event.requestContext?.authorizer?.jwt?.claims?.sub;
    if (!userId) {
      return response(401, { code: 'UNAUTHENTICATED' }, responseOrigin);
    }

    let request;
    try {
      request = JSON.parse(event.body);
    } catch {
      return response(400, { code: 'INVALID_REQUEST' }, responseOrigin);
    }

    try {
      return response(
        200,
        await service.createUrlsForUser(userId, request?.keys),
        responseOrigin,
      );
    } catch (error) {
      return errorResponse(error, responseOrigin);
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


function unavailableService() {
  return {
    async createUrlsForUser() {
      const error = new Error('AUTHORIZATION_UNAVAILABLE');
      error.code = 'AUTHORIZATION_UNAVAILABLE';
      throw error;
    },
  };
}

async function loadS3Dependencies() {
  const [{ GetObjectCommand, S3Client }, { getSignedUrl }] = await Promise.all([
    import('@aws-sdk/client-s3'),
    import('@aws-sdk/s3-request-presigner'),
  ]);
  return { GetObjectCommand, S3Client, getSignedUrl };
}

export async function createProductionHandler({
  environment = process.env,
  loadS3 = loadS3Dependencies,
} = {}) {
  const allowedOrigin = environment.ALLOWED_ORIGIN || 'http://localhost:3000';
  const allowedOrigins = environment.ALLOWED_ORIGINS;
  let metadataClient;
  try {
    metadataClient = createMetadataAuthorizationClient({
      baseUrl: environment.METADATA_API_BASE_URL,
      internalApiKey: environment.INTERNAL_API_KEY,
    });
  } catch {
    return createHandler({ allowedOrigin, allowedOrigins, service: unavailableService() });
  }
  const { GetObjectCommand, S3Client, getSignedUrl } = await loadS3();
  const presignGet = createS3Presigner({
    client: new S3Client({}),
    bucket: environment.MEDIA_BUCKET_NAME,
    GetObjectCommand,
    getSignedUrl,
  });
  return createHandler({
    allowedOrigin,
    allowedOrigins,
    service: createAssetUrlService({ presignGet, authorize: metadataClient.authorize }),
  });
}


let productionHandlerPromise;


export async function handler(event) {
  productionHandlerPromise ??= createProductionHandler();
  try {
    return await (await productionHandlerPromise)(event);
  } catch (error) {
    productionHandlerPromise = undefined;
    return errorResponse(
      error,
      allowedRequestOrigin(
        event,
        configuredAllowedOrigins(
          process.env.ALLOWED_ORIGIN || 'http://localhost:3000',
          process.env.ALLOWED_ORIGINS,
        ),
      ),
    );
  }
}
