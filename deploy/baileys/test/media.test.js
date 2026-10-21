'use strict';

/**
 * D3: media and location never reached the backend.
 *
 * `kindOf` could already name an IMAGE, a DOCUMENT and a LOCATION, but
 * `skipReason` dropped every message without text as `no_text`, so the kind it
 * had just worked out was thrown away with the message. These are the rules
 * that decide what counts as content.
 *
 * Run with `npm test` (node --test) — no test dependency, Node 20 ships it.
 */

const test = require('node:test');
const assert = require('node:assert');

const {
  MAX_MEDIA_BYTES,
  carriesContent,
  declaredSizeOf,
  kindOf,
  locationOf,
  mediaDescriptorOf,
  uploadMedia,
} = require('../src/media');

const LOCATION = {
  locationMessage: {
    degreesLatitude: 3.8905,
    degreesLongitude: 11.5139,
    name: 'Bastos',
    address: 'Yaoundé, Cameroun',
  },
};

test('kindOf names each content type WhatsApp delivers', () => {
  assert.strictEqual(kindOf({ conversation: 'salut' }), 'TEXT');
  assert.strictEqual(kindOf({ imageMessage: {} }), 'IMAGE');
  assert.strictEqual(kindOf({ videoMessage: {} }), 'VIDEO');
  assert.strictEqual(kindOf({ audioMessage: {} }), 'AUDIO');
  assert.strictEqual(kindOf({ documentMessage: {} }), 'DOCUMENT');
  assert.strictEqual(kindOf({ stickerMessage: {} }), 'STICKER');
  assert.strictEqual(kindOf(LOCATION), 'LOCATION');
  assert.strictEqual(kindOf({ liveLocationMessage: {} }), 'LOCATION');
  assert.strictEqual(kindOf({ contactMessage: {} }), 'CONTACT');
});

test('locationOf reads the pin WhatsApp sent', () => {
  const point = locationOf(LOCATION);
  assert.strictEqual(point.latitude, 3.8905);
  assert.strictEqual(point.longitude, 11.5139);
  // The place name, then the address: the platform treats it as a hint, so the
  // more specific half leads.
  assert.strictEqual(point.label, 'Bastos, Yaoundé, Cameroun');
});

test('locationOf reads a live location the same way', () => {
  const point = locationOf({
    liveLocationMessage: { degreesLatitude: 4.05, degreesLongitude: 9.7 },
  });
  assert.strictEqual(point.latitude, 4.05);
  assert.strictEqual(point.longitude, 9.7);
  assert.strictEqual(point.label, null);
});

test('locationOf refuses a position the platform could not place', () => {
  // Null Island is what a dropped coordinate field looks like on the wire.
  assert.strictEqual(locationOf({ locationMessage: {} }), null);
  assert.strictEqual(
    locationOf({ locationMessage: { degreesLatitude: 0, degreesLongitude: 0 } }),
    null
  );
  assert.strictEqual(
    locationOf({ locationMessage: { degreesLatitude: 99, degreesLongitude: 11 } }),
    null
  );
  assert.strictEqual(
    locationOf({ locationMessage: { degreesLatitude: 3.89 } }),
    null,
    'one coordinate without the other locates nothing'
  );
  assert.strictEqual(locationOf({ conversation: 'Bastos' }), null);
});

test('mediaDescriptorOf reports the mime type the bytes will be stored under', () => {
  assert.deepStrictEqual(mediaDescriptorOf({ imageMessage: { mimetype: 'image/jpeg' } }), {
    kind: 'IMAGE',
    mimetype: 'image/jpeg',
  });
  assert.deepStrictEqual(
    mediaDescriptorOf({ documentMessage: { mimetype: 'application/pdf' } }),
    { kind: 'DOCUMENT', mimetype: 'application/pdf' }
  );
});

test('mediaDescriptorOf strips the codec parameters off a mime type', () => {
  // `audio/ogg; codecs=opus` is what a voice note announces, and the platform
  // matches its allowlist on the bare type.
  assert.deepStrictEqual(
    mediaDescriptorOf({ audioMessage: { mimetype: 'audio/ogg; codecs=opus' } }),
    { kind: 'AUDIO', mimetype: 'audio/ogg' }
  );
});

test('mediaDescriptorOf falls back to a storable type rather than refusing', () => {
  // WhatsApp occasionally omits the mimetype; guessing by kind keeps the photo
  // rather than dropping the step-9 obligation over a missing header.
  assert.deepStrictEqual(mediaDescriptorOf({ imageMessage: {} }), {
    kind: 'IMAGE',
    mimetype: 'image/jpeg',
  });
  assert.deepStrictEqual(mediaDescriptorOf({ audioMessage: {} }), {
    kind: 'AUDIO',
    mimetype: 'audio/ogg',
  });
});

test('mediaDescriptorOf ignores a message that carries no bytes', () => {
  assert.strictEqual(mediaDescriptorOf(LOCATION), null);
  assert.strictEqual(mediaDescriptorOf({ conversation: 'salut' }), null);
  assert.strictEqual(mediaDescriptorOf(null), null);
});

test('carriesContent accepts a message whose only content is a pin', () => {
  assert.strictEqual(carriesContent(LOCATION), true);
});

test('carriesContent accepts a photo sent with no caption', () => {
  assert.strictEqual(carriesContent({ imageMessage: { mimetype: 'image/jpeg' } }), true);
});

test('carriesContent accepts plain text', () => {
  assert.strictEqual(carriesContent({ conversation: 'salut' }), true);
});

test('carriesContent still rejects a message with nothing in it', () => {
  assert.strictEqual(carriesContent({}), false);
  assert.strictEqual(carriesContent(null), false);
  assert.strictEqual(carriesContent({ conversation: '   ' }), false);
  // A contact card carries neither bytes nor a position the platform can use.
  assert.strictEqual(carriesContent({ contactMessage: {} }), false);
});

const SILENT_LOG = { info() {}, warn() {}, error() {} };

test('declaredSizeOf reads fileLength as a Long, a number or a string', () => {
  assert.strictEqual(declaredSizeOf({ videoMessage: { fileLength: { toNumber: () => 42 } } }), 42);
  assert.strictEqual(declaredSizeOf({ videoMessage: { fileLength: 42 } }), 42);
  assert.strictEqual(declaredSizeOf({ imageMessage: { fileLength: '42' } }), 42);
  assert.strictEqual(declaredSizeOf({ videoMessage: {} }), null);
  assert.strictEqual(declaredSizeOf({ conversation: 'salut' }), null);
});

test('a video past the ceiling is refused before anything is downloaded', async () => {
  let downloaded = false;
  const result = await uploadMedia({
    entry: { key: { id: 'X' } },
    descriptor: { kind: 'VIDEO', mimetype: 'video/mp4' },
    log: SILENT_LOG,
    download: async () => {
      downloaded = true;
      return Buffer.from('x');
    },
    declaredBytes: MAX_MEDIA_BYTES + 1,
  });

  assert.deepStrictEqual(result, { objectKey: null, refusal: 'too_large' });
  assert.strictEqual(downloaded, false);
});

test('a size the platform refuses is reported as too large, not as a failure', async () => {
  const original = global.fetch;
  global.fetch = async () => new Response('too large', { status: 413 });
  try {
    const result = await uploadMedia({
      entry: { key: { id: 'X' } },
      descriptor: { kind: 'VIDEO', mimetype: 'video/mp4' },
      log: SILENT_LOG,
      mediaUrl: 'http://platform/media',
      download: async () => Buffer.from('x'),
    });
    assert.deepStrictEqual(result, { objectKey: null, refusal: 'too_large' });
  } finally {
    global.fetch = original;
  }
});
