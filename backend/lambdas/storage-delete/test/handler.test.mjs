import assert from 'node:assert/strict';
import test from 'node:test';

import { createHandler, createS3DeleteKeys } from '../index.mjs';


function parseBody(response) {
  return JSON.parse(response.body);
}


test('validates the direct invocation and returns the deleted count', async () => {
  const calls = [];
  const invoke = createHandler({
    service: {
      deleteForUser: async (userId, keys) => {
        calls.push({ userId, keys });
        return 2;
      },
    },
  });

  const response = await invoke({
    user_id: 'user-1',
    keys: ['originals/user-1/file-1/a.jpg', 'thumbnails/user-1/file-1/thumbnail.jpg'],
  });

  assert.equal(response.statusCode, 200);
  assert.deepEqual(parseBody(response), { deleted_count: 2 });
  assert.deepEqual(calls, [{
    userId: 'user-1',
    keys: ['originals/user-1/file-1/a.jpg', 'thumbnails/user-1/file-1/thumbnail.jpg'],
  }]);
});


test('rejects malformed direct invocation bodies without calling the service', async () => {
  let calls = 0;
  const invoke = createHandler({
    service: { deleteForUser: async () => { calls += 1; } },
  });
  const invalidEvents = [
    null,
    {},
    { user_id: '', keys: [] },
    { user_id: 'user/1', keys: [] },
    { user_id: 'user-1', keys: 'not-an-array' },
    { user_id: 'user-1', keys: [42] },
  ];

  for (const event of invalidEvents) {
    const response = await invoke(event);
    assert.equal(response.statusCode, 400);
    assert.deepEqual(parseBody(response), { code: 'INVALID_REQUEST' });
  }
  assert.equal(calls, 0);
});


test('maps forbidden keys to a structured response without exposing details', async () => {
  const error = new Error('sensitive object key');
  error.code = 'FORBIDDEN_KEY';
  const invoke = createHandler({
    service: { deleteForUser: async () => { throw error; } },
  });

  const response = await invoke({ user_id: 'user-1', keys: ['originals/user-2/f/a.jpg'] });

  assert.equal(response.statusCode, 403);
  assert.deepEqual(parseBody(response), { code: 'FORBIDDEN_KEY' });
  assert.ok(!response.body.includes('sensitive object key'));
});


test('maps unexpected deletion failures to an internal error', async () => {
  const invoke = createHandler({
    service: { deleteForUser: async () => { throw new Error('S3 secret detail'); } },
  });

  const response = await invoke({ user_id: 'user-1', keys: [] });

  assert.equal(response.statusCode, 500);
  assert.deepEqual(parseBody(response), { code: 'INTERNAL_ERROR' });
});


test('S3 adapter constructs a private-bucket DeleteObjects request', async () => {
  const sent = [];
  class DeleteObjectsCommand {
    constructor(input) {
      this.input = input;
    }
  }
  const deleteKeys = createS3DeleteKeys({
    client: { send: async (command) => sent.push(command) },
    bucket: 'private-media',
    DeleteObjectsCommand,
  });

  await deleteKeys(['originals/user-1/file-1/a.jpg']);

  assert.equal(sent.length, 1);
  assert.ok(sent[0] instanceof DeleteObjectsCommand);
  assert.deepEqual(sent[0].input, {
    Bucket: 'private-media',
    Delete: { Objects: [{ Key: 'originals/user-1/file-1/a.jpg' }] },
  });
});

test('S3 adapter rejects an HTTP-successful response with per-object errors', async () => {
  class DeleteObjectsCommand {
    constructor(input) {
      this.input = input;
    }
  }
  const deleteKeys = createS3DeleteKeys({
    client: {
      send: async () => ({
        Deleted: [{ Key: 'originals/user-1/file-1/removed.jpg' }],
        Errors: [{
          Key: 'originals/user-1/file-1/private.jpg',
          Code: 'AccessDenied',
          Message: 'sensitive AWS error body',
        }],
      }),
    },
    bucket: 'private-media',
    DeleteObjectsCommand,
  });

  await assert.rejects(
    () => deleteKeys([
      'originals/user-1/file-1/removed.jpg',
      'originals/user-1/file-1/private.jpg',
    ]),
    (error) => (
      error?.code === 'STORAGE_DELETE_FAILED'
      && !error.message.includes('private.jpg')
      && !error.message.includes('AccessDenied')
      && !error.message.includes('sensitive AWS error body')
    ),
  );
});
