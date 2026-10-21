'use strict';

/**
 * What a WhatsApp message carries, and how to get it.
 *
 * This module exists because of D3: `kindOf` already recognised IMAGE,
 * DOCUMENT and LOCATION, and `skipReason` then discarded the message anyway
 * because it had no text. The four specifications all name the shared position
 * as the preferred way to say where something is, three of them require
 * photos, and Housing step 9 makes them mandatory — so "no text" was deciding
 * that the best answer a user can give is not an answer.
 *
 * It stays dumb, like the rest of this transport: it names content and hands
 * bytes over. What a photo means, and whether a pin is a usable address, is
 * decided behind the API.
 */

const MAX_UPLOAD_ATTEMPTS = 3;

/**
 * The platform's own ceiling (MAX_MEDIA_BYTES in routers/whatsapp.py). Checked
 * here too, before downloading: a two-minute video is tens of megabytes held
 * in this process's memory for an upload that can only be refused.
 */
const MAX_MEDIA_BYTES = 16 * 1024 * 1024;

/** The mime type each kind falls back to when WhatsApp omits its own. */
const DEFAULT_MIMETYPE = {
  IMAGE: 'image/jpeg',
  VIDEO: 'video/mp4',
  AUDIO: 'audio/ogg',
  DOCUMENT: 'application/pdf',
  STICKER: 'image/webp',
};

const MEDIA_FIELD_BY_KIND = {
  IMAGE: 'imageMessage',
  VIDEO: 'videoMessage',
  AUDIO: 'audioMessage',
  DOCUMENT: 'documentMessage',
  STICKER: 'stickerMessage',
};

function kindOf(message) {
  if (!message) return 'TEXT';
  if (message.imageMessage) return 'IMAGE';
  if (message.videoMessage) return 'VIDEO';
  if (message.audioMessage) return 'AUDIO';
  if (message.documentMessage) return 'DOCUMENT';
  if (message.stickerMessage) return 'STICKER';
  if (message.locationMessage || message.liveLocationMessage) return 'LOCATION';
  if (message.contactMessage || message.contactsArrayMessage) return 'CONTACT';
  return 'TEXT';
}

function extractText(message) {
  if (!message) return null;
  const candidates = [
    message.conversation,
    message.extendedTextMessage && message.extendedTextMessage.text,
    message.imageMessage && message.imageMessage.caption,
    message.videoMessage && message.videoMessage.caption,
    message.documentMessage && message.documentMessage.caption,
    message.buttonsResponseMessage && message.buttonsResponseMessage.selectedDisplayText,
    message.templateButtonReplyMessage &&
      message.templateButtonReplyMessage.selectedDisplayText,
    message.listResponseMessage && message.listResponseMessage.title,
  ];
  const found = candidates.find((value) => typeof value === 'string' && value.trim());
  return found ? found.trim() : null;
}

/**
 * The position the user sent, or null when there is none the platform can use.
 *
 * Refused rather than forwarded: a pin at (0, 0) is what a dropped coordinate
 * field looks like on the wire, and one coordinate without the other locates
 * nothing. Both would publish a listing somewhere nobody lives, and the
 * platform would have no way to tell that from a real address.
 */
function locationOf(message) {
  const pin =
    (message && (message.locationMessage || message.liveLocationMessage)) || null;
  if (!pin) return null;

  const latitude = Number(pin.degreesLatitude);
  const longitude = Number(pin.degreesLongitude);
  if (!Number.isFinite(latitude) || !Number.isFinite(longitude)) return null;
  if (latitude < -90 || latitude > 90) return null;
  if (longitude < -180 || longitude > 180) return null;
  if (latitude === 0 && longitude === 0) return null;

  // The place name leads the address: it is the more specific half, and the
  // platform reads the label as a neighbourhood hint.
  const label =
    [pin.name, pin.address]
      .filter((part) => typeof part === 'string' && part.trim())
      .map((part) => part.trim())
      .join(', ') || null;

  return { latitude, longitude, label };
}

/**
 * The bytes this message carries, named by kind and mime type, or null.
 *
 * A missing mimetype falls back to the kind's usual one instead of refusing:
 * dropping a landlord's photo over an absent header would fail the one step
 * Housing makes mandatory, and the platform re-checks the type on arrival.
 */
function mediaDescriptorOf(message) {
  const kind = kindOf(message);
  const field = MEDIA_FIELD_BY_KIND[kind];
  if (!field || !message || !message[field]) return null;

  const declared = message[field].mimetype;
  const mimetype =
    typeof declared === 'string' && declared.trim()
      ? // `audio/ogg; codecs=opus` is what a voice note announces; the
        // platform matches its allowlist on the bare type.
        declared.split(';')[0].trim().toLowerCase()
      : DEFAULT_MIMETYPE[kind];
  return { kind, mimetype };
}

/**
 * The size WhatsApp announces for a media message, in bytes, or null.
 *
 * `fileLength` arrives as a protobuf Long, a number or a string depending on
 * the path the message took; all three are read.
 */
function declaredSizeOf(message) {
  const field = MEDIA_FIELD_BY_KIND[kindOf(message)];
  const raw = field && message && message[field] ? message[field].fileLength : null;
  if (raw === null || raw === undefined) return null;
  const size = typeof raw === 'object' && typeof raw.toNumber === 'function'
    ? raw.toNumber()
    : Number(raw);
  return Number.isFinite(size) && size > 0 ? size : null;
}

/**
 * Whether this message says anything at all.
 *
 * The rule that replaces `no_text`. Text, bytes or a position each count on
 * their own; a contact card counts as none of them, because the platform has
 * nothing to do with one yet and storing it would be collecting a third
 * party's number nobody asked for.
 */
function carriesContent(message) {
  return Boolean(
    extractText(message) || mediaDescriptorOf(message) || locationOf(message)
  );
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

/**
 * Download the media and hand it to the platform, returning its object key.
 *
 * Uploading *before* the delivery is forwarded is deliberate: a key on the bus
 * must always name bytes that exist, and the alternative — forward first,
 * upload later — leaves the conversation holding a reference to nothing.
 *
 * Returns null when the bytes could not be stored. The caller decides what to
 * do with a message whose media is lost; it must not pretend the key exists.
 */
/**
 * Store one media message and say what became of it.
 *
 * Resolves to `{ objectKey, refusal }`: the stored key, or why it was not
 * stored when the reason is one the user should hear — `too_large` for a
 * video past the platform's ceiling. Both null means a transient failure the
 * user cannot act on. A refusal used to be indistinguishable from that, so
 * the delivery was dropped and the landlord's video met silence.
 */
async function uploadMedia({
  entry,
  descriptor,
  sock,
  mediaUrl,
  apiKey,
  log,
  download,
  declaredBytes = null,
}) {
  const externalId = (entry.key && entry.key.id) || '';
  let lastError = 'not attempted';

  if (declaredBytes !== null && declaredBytes > MAX_MEDIA_BYTES) {
    log.warn(
      { external_message_id: externalId, kind: descriptor.kind, size_bytes: declaredBytes },
      'media refused before download: too large'
    );
    return { objectKey: null, refusal: 'too_large' };
  }

  for (let attempt = 1; attempt <= MAX_UPLOAD_ATTEMPTS; attempt += 1) {
    try {
      const buffer = await download(entry, 'buffer', {}, {
        logger: log,
        // Serves WhatsApp's "re-upload this, I cannot decrypt it" path.
        // Without it an expired media URL is a permanently lost photo.
        reuploadRequest: sock && sock.updateMediaMessage,
      });
      if (!buffer || !buffer.length) throw new Error('media download was empty');
      if (buffer.length > MAX_MEDIA_BYTES) {
        log.warn(
          { external_message_id: externalId, kind: descriptor.kind, size_bytes: buffer.length },
          'media refused: too large'
        );
        return { objectKey: null, refusal: 'too_large' };
      }

      const headers = {
        'content-type': descriptor.mimetype,
        'x-external-message-id': externalId,
      };
      if (apiKey) headers['x-api-key'] = apiKey;

      const response = await fetch(mediaUrl, {
        method: 'POST',
        headers,
        body: buffer,
        signal: AbortSignal.timeout(30000),
      });
      if (response.ok) {
        const body = await response.json();
        log.info(
          {
            external_message_id: externalId,
            kind: descriptor.kind,
            size_bytes: buffer.length,
            object_key: body.media_object_key,
          },
          'media stored'
        );
        return { objectKey: body.media_object_key || null, refusal: null };
      }
      if (response.status === 413) return { objectKey: null, refusal: 'too_large' };

      lastError = `HTTP ${response.status}: ${(await response.text()).slice(0, 300)}`;
      // The platform refuses this body for what it contains — the type, the
      // size, the key. Retrying sends the identical bytes to the identical
      // verdict.
      if (response.status >= 400 && response.status < 500 && response.status !== 429) {
        break;
      }
    } catch (error) {
      lastError = (error && error.message) || String(error);
    }
    if (attempt < MAX_UPLOAD_ATTEMPTS) await sleep(1000 * 2 ** (attempt - 1));
  }

  log.error(
    { external_message_id: externalId, kind: descriptor.kind, error: lastError },
    'media upload failed'
  );
  return { objectKey: null, refusal: null };
}

module.exports = {
  MAX_MEDIA_BYTES,
  MAX_UPLOAD_ATTEMPTS,
  carriesContent,
  declaredSizeOf,
  extractText,
  kindOf,
  locationOf,
  mediaDescriptorOf,
  uploadMedia,
};
