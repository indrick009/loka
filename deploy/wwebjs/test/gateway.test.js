'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const inbound = require('../src/inbound');
const { outboundContent } = require('../src/outbound');
const { createPlatform } = require('../src/platform');

const quiet = { info() {}, warn() {}, error() {} };
const msg = (over) => ({
  id: { id: 'ABC', _serialized: 'false_x@c.us_ABC' },
  from: '237692617975@c.us',
  type: 'chat',
  body: ' salut ',
  timestamp: 1795300000,
  fromMe: false,
  hasMedia: false,
  _data: { notifyName: 'Indrick' },
  ...over,
});

test('a text from a person is forwarded, trimmed', () => {
  assert.equal(inbound.skipReason(msg()), null);
  assert.equal(inbound.textOf(msg()), 'salut');
});

test('groups, own messages, statuses and empty bodies are skipped', () => {
  assert.equal(inbound.skipReason(msg({ fromMe: true })), 'from_me');
  assert.equal(inbound.skipReason(msg({ from: '1203@g.us' })), 'not_one_to_one');
  assert.equal(inbound.skipReason(msg({ from: 'status@broadcast' })), 'not_one_to_one');
  assert.equal(inbound.skipReason(msg({ body: '  ' })), 'empty');
  assert.equal(inbound.skipReason(msg({ type: 'vcard' })), 'unsupported_type');
});

test('a photo without caption counts, a pin at (0, 0) does not', () => {
  assert.equal(inbound.skipReason(msg({ type: 'image', body: '', hasMedia: true })), null);
  const zero = msg({ type: 'location', body: 'thumb', location: { latitude: 0, longitude: 0 } });
  assert.equal(inbound.locationOf(zero), null);
  assert.equal(inbound.skipReason(zero), 'empty');
});

test('a pin carries its coordinates and label, never the thumbnail as text', () => {
  const pin = msg({
    type: 'location',
    body: '/9j/4AAQ',
    location: { latitude: '3.87', longitude: '11.51', description: 'CNPS Yaoundé\nBastos' },
  });
  assert.deepEqual(inbound.locationOf(pin), { latitude: 3.87, longitude: 11.51, label: 'CNPS Yaoundé' });
  assert.equal(inbound.textOf(pin), null);
});

test('a @lid sender is resolved to its phone number', async () => {
  const lookup = async () => [{ lid: '9448@lid', pn: '237692617975@c.us' }];
  assert.equal(await inbound.senderPhone(msg({ from: '9448@lid' }), lookup), '237692617975');
  assert.equal(await inbound.senderPhone(msg({ from: '9448@lid' }), async () => []), null);
  assert.equal(await inbound.senderPhone(msg(), lookup), '237692617975');
});

test('the delivery has the platform shape; a lost photo becomes a text', () => {
  const lost = inbound.delivery({
    msg: msg({ type: 'image', hasMedia: true }),
    phone: '237692617975',
    session: 'kora-main',
    text: 'voici',
    location: null,
    media: { objectKey: null, refusal: null },
  });
  assert.equal(lost.message_kind, 'TEXT');
  assert.equal(lost.provider, 'wwebjs');
  assert.equal(lost.external_message_id, 'ABC');
  assert.equal(lost.provider_timestamp, new Date(1795300000 * 1000).toISOString());
  assert.equal(lost.metadata.pushName, 'Indrick');
  const big = inbound.delivery({
    msg: msg({ type: 'video', hasMedia: true }),
    phone: '1',
    session: 's',
    text: null,
    location: null,
    media: { objectKey: null, refusal: 'too_large' },
  });
  assert.equal(big.message_kind, 'VIDEO');
  assert.equal(big.metadata.media_refused, 'too_large');
});

class FakeLocation {
  constructor(lat, lng, options) {
    Object.assign(this, { lat, lng, options });
  }
}
class FakeMedia {
  constructor(mimetype, data) {
    Object.assign(this, { mimetype, data });
  }
  static async fromUrl(url) {
    return { url };
  }
}
const kit = { Location: FakeLocation, MessageMedia: FakeMedia };

test('each /send body becomes the matching WhatsApp Web content', async () => {
  const text = await outboundContent({ to: '1', text: 'Bonjour' }, kit);
  assert.equal(text.content, 'Bonjour');
  const photo = await outboundContent({ to: '1', image: 'https://s/x.jpg', caption: 'Studio' }, kit);
  assert.deepEqual(photo.content, { url: 'https://s/x.jpg' });
  assert.equal(photo.options.caption, 'Studio');
  const pin = await outboundContent({ to: '1', location: { latitude: 3.8, longitude: 11.5, label: 'Bastos' } }, kit);
  assert.equal(pin.content.options.name, 'Bastos');
  const card = await outboundContent({ to: '1', contact: { name: 'Paul', phone: '+237 6 99' } }, kit);
  assert.match(card.content, /waid=237699:\+237699/);
  assert.equal(await outboundContent({ to: '1' }, kit), null);
});

test('a refused delivery is not retried; a down platform is', async () => {
  let calls = 0;
  const refuse = createPlatform({
    eventsUrl: 'http://api/events',
    log: quiet,
    pause: async () => {},
    fetchImpl: async () => {
      calls += 1;
      return { ok: false, status: 422, text: async () => 'bad' };
    },
  });
  assert.equal(await refuse.forward({ external_message_id: 'x' }), false);
  assert.equal(calls, 1);
  calls = 0;
  const down = createPlatform({
    eventsUrl: 'http://api/events',
    log: quiet,
    pause: async () => {},
    fetchImpl: async () => {
      calls += 1;
      throw new Error('ECONNREFUSED');
    },
  });
  assert.equal(await down.forward({ external_message_id: 'x' }), false);
  assert.equal(calls, 4);
});

test('media goes to /media and its key comes back', async () => {
  let url;
  const platform = createPlatform({
    eventsUrl: 'http://api/integrations/whatsapp/events',
    log: quiet,
    fetchImpl: async (target) => {
      url = target;
      return { ok: true, json: async () => ({ media_object_key: 'k1' }) };
    },
  });
  const stored = await platform.upload({ externalId: 'x', mimetype: 'image/jpeg', buffer: Buffer.from('a') });
  assert.deepEqual(stored, { objectKey: 'k1', refusal: null });
  assert.equal(url, 'http://api/integrations/whatsapp/media');
});
