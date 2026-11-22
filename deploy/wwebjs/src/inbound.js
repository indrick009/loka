'use strict';

// A WhatsApp Web message, as the platform's GatewayMessage
// (routers/whatsapp.py). Kinds, filters and the refusal of a (0, 0) pin are
// the ones deploy/baileys applies, so both transports feed idia the same way.

const KIND_BY_TYPE = {
  chat: 'TEXT',
  image: 'IMAGE',
  video: 'VIDEO',
  audio: 'AUDIO',
  ptt: 'AUDIO',
  document: 'DOCUMENT',
  sticker: 'STICKER',
  location: 'LOCATION',
  vcard: 'CONTACT',
  multi_vcard: 'CONTACT',
  buttons_response: 'TEXT',
  list_response: 'TEXT',
};
const MEDIA_KINDS = new Set(['IMAGE', 'VIDEO', 'AUDIO', 'DOCUMENT', 'STICKER']);
const DEFAULT_MIMETYPE = {
  IMAGE: 'image/jpeg',
  VIDEO: 'video/mp4',
  AUDIO: 'audio/ogg',
  DOCUMENT: 'application/pdf',
  STICKER: 'image/webp',
};

const digitsOf = (value) => String(value || '').replace(/\D/g, '');
const userOf = (jid) => digitsOf(String(jid || '').split('@')[0].split(':')[0]);

const kindOf = (msg) => KIND_BY_TYPE[msg.type] || 'UNKNOWN';

function textOf(msg) {
  // A location's body is a thumbnail, not words the user wrote.
  if (msg.type === 'location') return null;
  const body = typeof msg.body === 'string' ? msg.body.trim() : '';
  return body || null;
}

/** The pin the user sent, or null when it locates nothing. */
function locationOf(msg) {
  if (msg.type !== 'location' || !msg.location) return null;
  const latitude = Number(msg.location.latitude);
  const longitude = Number(msg.location.longitude);
  if (!Number.isFinite(latitude) || !Number.isFinite(longitude)) return null;
  if (Math.abs(latitude) > 90 || Math.abs(longitude) > 180) return null;
  if (latitude === 0 && longitude === 0) return null;
  const { name, address, description } = msg.location;
  const label =
    [name, address, description]
      .filter((part) => typeof part === 'string' && part.trim())
      .map((part) => part.trim().split('\n')[0])
      .join(', ') || null;
  return { latitude, longitude, label };
}

/** Why this message is not forwarded, or null when it is. */
function skipReason(msg) {
  if (msg.fromMe) return 'from_me';
  const from = String(msg.from || '');
  if (msg.isStatus || from.endsWith('@g.us') || from.endsWith('@broadcast') || from.endsWith('@newsletter')) {
    return 'not_one_to_one';
  }
  if (!from.endsWith('@c.us') && !from.endsWith('@lid')) return 'not_a_user_jid';
  const kind = kindOf(msg);
  if (kind === 'UNKNOWN' || kind === 'CONTACT') return 'unsupported_type';
  if (!textOf(msg) && !locationOf(msg) && !(MEDIA_KINDS.has(kind) && msg.hasMedia)) return 'empty';
  return null;
}

/**
 * The sender's phone number. A @c.us address carries it; a @lid one does not,
 * and WhatsApp Web is asked which number stands behind it.
 */
async function senderPhone(msg, lookup) {
  const from = String(msg.from || '');
  if (from.endsWith('@c.us')) return userOf(from);
  try {
    const [pair] = await lookup([from]);
    const pn = pair && pair.pn ? userOf(pair.pn) : '';
    return pn || null;
  } catch (_error) {
    return null;
  }
}

function mimetypeOf(media, kind) {
  const declared = media && typeof media.mimetype === 'string' ? media.mimetype : '';
  return declared.split(';')[0].trim().toLowerCase() || DEFAULT_MIMETYPE[kind];
}

function delivery({ msg, phone, session, text, location, media }) {
  const kind = kindOf(msg);
  return {
    provider: 'wwebjs',
    external_message_id: (msg.id && msg.id.id) || String(msg.id && msg.id._serialized),
    conversation_ref: String(msg.from).slice(0, 96),
    sender_phone: phone,
    // A photo whose bytes were lost but whose caption arrived is a text.
    message_kind: MEDIA_KINDS.has(kind) && !media.objectKey && !media.refusal ? 'TEXT' : kind,
    provider_timestamp: new Date(Number(msg.timestamp || Date.now() / 1000) * 1000).toISOString(),
    text,
    media_object_key: media.objectKey || null,
    location,
    metadata: {
      pushName: (msg._data && msg._data.notifyName) || null,
      session_name: session,
      ...(media.refusal ? { media_refused: media.refusal } : {}),
    },
  };
}

module.exports = {
  MEDIA_KINDS,
  delivery,
  digitsOf,
  kindOf,
  locationOf,
  mimetypeOf,
  senderPhone,
  skipReason,
  textOf,
  userOf,
};
