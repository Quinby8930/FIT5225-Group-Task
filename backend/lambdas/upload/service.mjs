import { validateUploadRequest } from './validation.mjs';

export function createUploadService({ createFileId, reserveUpload, presignUpload, maxBytes = 262_144_000, maxImageBytes = 12_582_912, expiresIn = 300 }) {
  return {
    async createUpload({ userId, request }) {
      const upload = validateUploadRequest(request, { maxBytes, maxImageBytes });
      const fileId = createFileId();
      const objectKey = `originals/${userId}/${fileId}/${upload.filename}`;
      const reservation = await reserveUpload({
        file_id: fileId,
        user_id: userId,
        checksum: upload.checksumSha256,
        filename: upload.filename,
        file_type: upload.fileType,
        content_type: upload.contentType,
        size_bytes: upload.sizeBytes,
        object_key: objectKey,
        status: 'pending_upload',
      });
      const reservedFileId = reservation?.file_id ?? fileId;
      const reservedObjectKey = reservation?.object_key ?? objectKey;
      const uploadUrl = await presignUpload({
        objectKey: reservedObjectKey,
        contentType: upload.contentType,
        checksumSha256: upload.checksumSha256,
        sizeBytes: upload.sizeBytes,
      });
      return {
        file_id: reservedFileId,
        object_key: reservedObjectKey,
        upload_url: uploadUrl,
        expires_in: expiresIn,
        required_headers: {
          'Content-Type': upload.contentType,
          'x-amz-checksum-sha256': upload.checksumSha256,
        },
      };
    },
  };
}
