'use strict';

// The two calls this gateway makes to the platform: hand over a delivery, and
// store the bytes of a photo first so the delivery can name them. Same
// contract as deploy/baileys (routers/whatsapp.py): the API cannot tell which
// transport sent it.

// Long enough to outlast an API restart during a deploy (20 to 40 s): with 4
// attempts over 7 s, Laure's message to Morel was lost (26 Nov 2026, 10:48).
// 1, 2, 4… seconds, capped at a minute: about four minutes in all.
const FORWARD_ATTEMPTS = 10;
const MAX_PAUSE_MS = 60000;
const UPLOAD_ATTEMPTS = 3;
// MAX_MEDIA_BYTES in routers/whatsapp.py.
const MAX_MEDIA_BYTES = 16 * 1024 * 1024;

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

// A body the platform refuses for what it contains is refused identically on
// every attempt.
const permanent = (status) => status >= 400 && status < 500 && status !== 429;

function createPlatform({ eventsUrl, apiKey, log, fetchImpl = fetch, pause = sleep }) {
  const mediaUrl = eventsUrl.replace(/\/events$/, '/media');
  const auth = apiKey ? { 'x-api-key': apiKey } : {};

  async function forward(delivery) {
    let lastError = 'not attempted';
    for (let attempt = 1; attempt <= FORWARD_ATTEMPTS; attempt += 1) {
      try {
        const response = await fetchImpl(eventsUrl, {
          method: 'POST',
          headers: { 'content-type': 'application/json', ...auth },
          body: JSON.stringify(delivery),
          signal: AbortSignal.timeout(10000),
        });
        if (response.ok) {
          log.info({ external_message_id: delivery.external_message_id, attempt }, 'delivery forwarded');
          return true;
        }
        lastError = `HTTP ${response.status}: ${(await response.text()).slice(0, 300)}`;
        if (permanent(response.status)) break;
      } catch (error) {
        lastError = (error && error.message) || String(error);
      }
      if (attempt < FORWARD_ATTEMPTS) await pause(Math.min(1000 * 2 ** (attempt - 1), MAX_PAUSE_MS));
    }
    log.error({ error: lastError, external_message_id: delivery.external_message_id }, 'delivery forwarding failed');
    return false;
  }

  /**
   * Store one media file; resolves to { objectKey, refusal }. `too_large` is a
   * refusal the user is told about; both null is a failure he cannot act on.
   */
  async function upload({ externalId, mimetype, buffer }) {
    if (buffer.length > MAX_MEDIA_BYTES) return { objectKey: null, refusal: 'too_large' };
    let lastError = 'not attempted';
    for (let attempt = 1; attempt <= UPLOAD_ATTEMPTS; attempt += 1) {
      try {
        const response = await fetchImpl(mediaUrl, {
          method: 'POST',
          headers: { 'content-type': mimetype, 'x-external-message-id': externalId, ...auth },
          body: buffer,
          signal: AbortSignal.timeout(30000),
        });
        if (response.ok) {
          const body = await response.json();
          return { objectKey: body.media_object_key || null, refusal: null };
        }
        if (response.status === 413) return { objectKey: null, refusal: 'too_large' };
        lastError = `HTTP ${response.status}: ${(await response.text()).slice(0, 300)}`;
        if (permanent(response.status)) break;
      } catch (error) {
        lastError = (error && error.message) || String(error);
      }
      if (attempt < UPLOAD_ATTEMPTS) await pause(1000 * 2 ** (attempt - 1));
    }
    log.error({ external_message_id: externalId, error: lastError }, 'media upload failed');
    return { objectKey: null, refusal: null };
  }

  return { forward, upload };
}

module.exports = { MAX_MEDIA_BYTES, createPlatform };
