'use strict';

/**
 * WhatsApp gateway.
 *
 * Baileys is deliberately the dumbest component in the platform: it pairs an
 * account, turns inbound deliveries into HTTP posts, and turns outbound HTTP
 * calls into WhatsApp messages. It owns no conversation state, no de-duplication
 * and no business rule — every one of those lives behind the API, because a
 * transport that decides anything is a transport that can decide wrongly with
 * nobody able to correct it.
 *
 * Endpoints:
 *   GET  /health  liveness plus pairing state
 *   GET  /qr      page showing the pairing QR code
 *   POST /send    {"to": "6xxxxxxxxx", "text": "..."}
 *                 {"to": "...", "image": "<base64|url>", "caption": "..."}
 *                 {"to": "...", "location": {"latitude": .., "longitude": ..}}
 *   POST /logout  unlinks the session so a new QR can be scanned
 */

const fs = require('fs');
const path = require('path');
const express = require('express');
const QRCode = require('qrcode');
const qrcodeTerminal = require('qrcode-terminal');
const pino = require('pino');
const makeWASocket = require('@whiskeysockets/baileys').default;
const {
  useMultiFileAuthState,
  fetchLatestBaileysVersion,
  makeCacheableSignalKeyStore,
  downloadMediaMessage,
  DisconnectReason,
} = require('@whiskeysockets/baileys');
const {
  carriesContent,
  extractText,
  kindOf,
  locationOf,
  mediaDescriptorOf,
  uploadMedia,
} = require('./media');

const PORT = Number(process.env.PORT || 3000);
const AUTH_DIR = process.env.AUTH_DIR || path.join(process.cwd(), 'auth');
const SESSION_NAME = process.env.SESSION_NAME || 'loka-main';
const PLATFORM_URL =
  process.env.PLATFORM_URL || 'http://api:8000/integrations/whatsapp/events';
const PLATFORM_API_KEY = process.env.PLATFORM_API_KEY || '';
// Derived rather than configured separately: two URLs that can disagree is one
// more way for media to go somewhere the events never mention.
const PLATFORM_MEDIA_URL =
  process.env.PLATFORM_MEDIA_URL || PLATFORM_URL.replace(/\/events$/, '/media');
const FORWARD_ATTEMPTS = Number(process.env.FORWARD_ATTEMPTS || 4);
const RECONNECT_DELAY_MS = Number(process.env.RECONNECT_DELAY_MS || 3000);
const MAX_RECONNECT_DELAY_MS = Number(process.env.MAX_RECONNECT_DELAY_MS || 60000);

const log = pino({ level: process.env.LOG_LEVEL || 'info' });
// Baileys is chatty and its own logs are the only place a pairing rejection
// (401, bad QR, unsupported client) explains itself, so they stay at warn
// instead of being silenced.
const baileysLogger = pino({ level: process.env.BAILEYS_LOG_LEVEL || 'warn' });

const app = express();
app.use(express.json({ limit: '256kb' }));

const gateway = {
  sock: null,
  qr: null,
  connected: false,
  jid: null,
  reconnectDelay: RECONNECT_DELAY_MS,
  reconnectTimer: null,
  restarts: 0,
};

// WhatsApp asks us to re-send a message when the recipient could not decrypt it
// (a "retry receipt"). Serving that request means handing Baileys the original
// message back through `getMessage`; without it the recipient stays stuck on
// "waiting for this message" for ever. A bounded cache of what we sent is all
// the state this transport is allowed to keep.
const RETRY_CACHE_LIMIT = 200;
const retryCache = new Map();

function rememberForRetry(message) {
  if (!message || !message.key || !message.key.id) return;
  retryCache.set(message.key.id, message);
  if (retryCache.size > RETRY_CACHE_LIMIT) {
    retryCache.delete(retryCache.keys().next().value);
  }
}

// WhatsApp addresses a contact by an opaque LID since the multi-device
// migration, while the platform keys conversations on the phone number. The
// inbound delivery carries both, so the mapping is learned here and reused to
// answer on the same address WhatsApp used. Replying to the `@s.whatsapp.net`
// form when the chat is LID-addressed is what leaves the recipient on
// "waiting for this message".
const lidByPhone = new Map();

function rememberLid(phone, key) {
  if (!phone || !key) return;
  const lid =
    key.senderLid || (key.remoteJid && String(key.remoteJid).endsWith('@lid') ? key.remoteJid : null);
  if (lid) lidByPhone.set(digitsOf(phone), lid);
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

/**
 * Forget the pairing state.
 *
 * AUTH_DIR is a mounted volume, so removing the directory itself is removing
 * the mount point: the kernel answers EBUSY, the throw escaped the socket
 * event handler and killed the process on every reconnect. Only the contents
 * are removed, one entry at a time, and a locked file is logged rather than
 * fatal — a leftover credential is retried on the next attempt, a dead
 * gateway never presents a QR again.
 */
function clearAuthState(reason) {
  try {
    for (const entry of fs.readdirSync(AUTH_DIR)) {
      try {
        fs.rmSync(path.join(AUTH_DIR, entry), { recursive: true, force: true });
      } catch (error) {
        log.error({ entry, reason, error: error.message }, 'auth file could not be removed');
      }
    }
    log.warn({ reason }, 'auth state cleared, a new QR code will be requested');
  } catch (error) {
    log.error({ reason, error: error.message }, 'auth state could not be read');
  }
}

const digitsOf = (value) => String(value || '').replace(/\D/g, '');

function jidDigits(jid) {
  return digitsOf(String(jid || '').split('@')[0].split(':')[0]);
}

/**
 * The phone number of a sender, or null when WhatsApp does not expose it.
 *
 * Since the multi-device migration a conversation is addressed by an opaque
 * LID (`94480740917312@lid`) whose digits are not a phone number; the platform
 * keys every conversation on the number, so the PN carried alongside the key is
 * the only usable identity. Falling back to the LID digits would send an
 * unmatchable number downstream, which is why there is no fallback at all.
 */
function senderPhone(key) {
  const jid = key.senderPn || key.participantPn || null;
  if (jid && String(jid).endsWith('@s.whatsapp.net')) return jidDigits(jid);
  if (key.remoteJid && String(key.remoteJid).endsWith('@s.whatsapp.net')) {
    return jidDigits(key.remoteJid);
  }
  return null;
}

/**
 * Why an entry is not forwarded, or null when it is.
 *
 * Returning the reason instead of a boolean is what makes a message that
 * vanishes between WhatsApp and the platform explainable from the logs: every
 * filter is a place where a real conversation can disappear without a trace,
 * and the trace is the only way to tell a wrong filter from a lost socket.
 */
function skipReason(message, key) {
  const jid = key.remoteJid || '';
  if (key.fromMe) return 'from_me';
  // One-to-one conversations only: a group delivery has no single sender, no
  // single session and no business rule that maps to it yet.
  if (jid.endsWith('@g.us') || jid.endsWith('@broadcast') || jid.endsWith('@newsletter')) {
    return 'not_one_to_one';
  }
  if (!jid.endsWith('@s.whatsapp.net') && !jid.endsWith('@lid')) return 'not_a_user_jid';
  if (message && message.protocolMessage) return 'protocol';
  if (message && message.reactionMessage) return 'reaction';
  // Was `no_text`, which discarded every photo and every shared position —
  // the preferred answer in all four specifications — while `kindOf` right
  // above had already recognised them. Text, bytes or a pin each count.
  if (!carriesContent(message)) return 'empty';
  return null;
}

async function forward(delivery) {
  const headers = { 'content-type': 'application/json' };
  if (PLATFORM_API_KEY) headers['x-api-key'] = PLATFORM_API_KEY;

  let lastError = 'not attempted';
  for (let attempt = 1; attempt <= FORWARD_ATTEMPTS; attempt += 1) {
    try {
      const response = await fetch(PLATFORM_URL, {
        method: 'POST',
        headers,
        body: JSON.stringify(delivery),
        signal: AbortSignal.timeout(10000),
      });
      if (response.ok) {
        log.info(
          { external_message_id: delivery.external_message_id, attempt },
          'delivery forwarded'
        );
        return true;
      }
      const detail = (await response.text()).slice(0, 300);
      lastError = `HTTP ${response.status}: ${detail}`;
      // A rejected body will be rejected identically on every attempt: the
      // platform refuses it because of what it contains, not when it arrives.
      const permanent = response.status >= 400 && response.status < 500 && response.status !== 429;
      if (permanent) break;
    } catch (error) {
      lastError = error && error.message ? error.message : String(error);
    }
    if (attempt < FORWARD_ATTEMPTS) await sleep(1000 * 2 ** (attempt - 1));
  }
  log.error({ error: lastError, attempt: FORWARD_ATTEMPTS }, 'delivery forwarding failed');
  return false;
}

async function handleIncoming(messages, upsertType) {
  log.info({ count: messages.length, upsert_type: upsertType }, 'messages.upsert received');
  for (const entry of messages) {
    try {
      await handleOne(entry, upsertType);
    } catch (error) {
      // One malformed entry must not abandon the rest of the batch.
      log.error(
        { external_message_id: entry.key && entry.key.id, error: error.message },
        'delivery handling failed'
      );
    }
  }
}

async function handleOne(entry, upsertType) {
  const message = entry.message;
  const key = entry.key || {};
  const reason = skipReason(message, key);
  if (reason) {
    log.info(
      {
        external_message_id: key.id,
        remote_jid: key.remoteJid || null,
        from_me: Boolean(key.fromMe),
        upsert_type: upsertType,
        kind: kindOf(message),
        reason,
      },
      'delivery skipped'
    );
    return;
  }

  const text = extractText(message);
  const location = locationOf(message);
  const descriptor = mediaDescriptorOf(message);
  const senderJid = key.participant || key.remoteJid;
  const timestamp = Number(entry.messageTimestamp || Math.floor(Date.now() / 1000));
  const phone = senderPhone(key);

  log.info(
    {
      external_message_id: key.id,
      remote_jid: key.remoteJid || null,
      sender_jid: senderJid,
      upsert_type: upsertType,
      kind: kindOf(message),
      has_media: Boolean(descriptor),
      has_location: Boolean(location),
      sender_phone: phone,
      sender_pn: key.senderPn || null,
      sender_lid: key.senderLid || null,
      participant_pn: key.participantPn || null,
      participant_lid: key.participantLid || null,
    },
    'delivery accepted'
  );

  // A message with no phone number cannot be attributed to any session, and
  // the log above is where to look when one disappears.
  if (!phone) {
    log.warn(
      { external_message_id: key.id, remote_jid: key.remoteJid || null },
      'delivery skipped'
    );
    return;
  }

  rememberLid(phone, key);

  // The bytes go first, so the key on the delivery always names an object that
  // exists. A photo that could not be stored is reported and the message is
  // still forwarded when it carries a caption or a pin — losing the photo is
  // bad, losing the sentence the landlord wrote with it is worse.
  let mediaObjectKey = null;
  if (descriptor) {
    mediaObjectKey = await uploadMedia({
      entry,
      descriptor,
      sock: gateway.sock,
      mediaUrl: PLATFORM_MEDIA_URL,
      apiKey: PLATFORM_API_KEY,
      log,
      download: downloadMediaMessage,
    });
    if (!mediaObjectKey && !text && !location) {
      log.error(
        { external_message_id: key.id, kind: descriptor.kind },
        'delivery skipped: media could not be stored and nothing else was sent'
      );
      return;
    }
  }

  await forward({
    provider: 'baileys',
    external_message_id: key.id,
    conversation_ref: key.remoteJid,
    sender_phone: phone,
    // The stored kind follows what actually survived: an IMAGE whose bytes
    // were lost but whose caption arrived is a text message, and claiming
    // otherwise promises the pipeline a photo it can never read.
    message_kind: mediaObjectKey || !descriptor ? kindOf(message) : 'TEXT',
    provider_timestamp: new Date(timestamp * 1000).toISOString(),
    text,
    media_object_key: mediaObjectKey,
    location,
    metadata: {
      pushName: entry.pushName || null,
      session_name: SESSION_NAME,
    },
  });
}

async function showQr(qr) {
  gateway.qr = qr;
  log.info('scan the QR code below with WhatsApp > Linked devices');
  qrcodeTerminal.generate(qr, { small: true });
}

function scheduleReconnect() {
  const delay = Math.min(
    gateway.reconnectDelay * Math.max(1, gateway.restarts),
    MAX_RECONNECT_DELAY_MS
  );
  gateway.reconnectTimer = setTimeout(() => {
    startSocket().catch((error) => {
      log.error({ error: error.message }, 'reconnect failed');
    });
  }, delay);
}

async function handleConnectionUpdate(sock, update) {
  const { connection, lastDisconnect, qr } = update;

  if (qr) await showQr(qr);

  if (connection === 'open') {
    gateway.connected = true;
    gateway.qr = null;
    gateway.reconnectDelay = RECONNECT_DELAY_MS;
    gateway.restarts = 0;
    gateway.jid = sock.user && sock.user.id ? sock.user.id : null;
    log.info({ jid: gateway.jid }, 'whatsapp connected');
    return;
  }

  if (connection !== 'close') return;

  gateway.connected = false;
  const error = lastDisconnect && lastDisconnect.error;
  const statusCode =
    error && error.output && error.output.statusCode ? error.output.statusCode : undefined;
  const loggedOut = statusCode === DisconnectReason.loggedOut;

  // Without this message a rejected pairing and a dropped wifi look identical
  // from the outside, and both end in the same reconnect.
  log.warn(
    { statusCode, reason: error && error.message ? error.message : null },
    loggedOut ? 'session logged out' : 'connection closed'
  );

  if (loggedOut) {
    // Either the linked device was removed from the phone or WhatsApp refused
    // the pairing (expired QR, unsupported client). Keeping the rejected state
    // would make every reconnect fail instantly, so it goes and a fresh QR is
    // requested.
    clearAuthState('logged_out');
    gateway.restarts += 1;
  } else {
    gateway.restarts += 1;
  }

  scheduleReconnect();
}

async function startSocket() {
  if (gateway.reconnectTimer) {
    clearTimeout(gateway.reconnectTimer);
    gateway.reconnectTimer = null;
  }
  if (gateway.sock) {
    // Two live sockets sharing one credential set makes WhatsApp invalidate
    // the session, which looks exactly like a refused pairing.
    try {
      gateway.sock.ev.removeAllListeners();
      gateway.sock.end(undefined);
    } catch (error) {
      log.warn({ error: error.message }, 'previous socket could not be closed');
    }
    gateway.sock = null;
  }

  fs.mkdirSync(AUTH_DIR, { recursive: true });
  const { state, saveCreds } = await useMultiFileAuthState(AUTH_DIR);
  let version;
  try {
    ({ version } = await fetchLatestBaileysVersion());
  } catch (error) {
    log.warn({ error: error.message }, 'baileys version lookup failed, using bundled');
  }

  const sock = makeWASocket({
    auth: {
      creds: state.creds,
      // The cacheable store serialises key lookups and writes. Without it two
      // concurrent decryptions race on the same signal key and corrupt the
      // ratchet, which surfaces as "Bad MAC" and unreadable messages.
      keys: makeCacheableSignalKeyStore(state.keys, baileysLogger),
    },
    version,
    logger: baileysLogger,
    printQRInTerminal: false,
    browser: [SESSION_NAME, 'chrome', '1.0.0'],
    markOnlineOnConnect: false,
    syncFullHistory: false,
    getMessage: async (key) => {
      const cached = key && key.id ? retryCache.get(key.id) : undefined;
      return cached ? cached.message : undefined;
    },
  });

  gateway.sock = sock;

  sock.ev.on('creds.update', saveCreds);

  sock.ev.on('connection.update', (update) => {
    handleConnectionUpdate(sock, update).catch((error) => {
      // An unhandled rejection here would take the whole gateway down instead
      // of costing one reconnect.
      log.error({ error: error.message }, 'connection update failed');
    });
  });

  sock.ev.on('messages.upsert', async ({ messages, type }) => {
    // 'notify' is a live delivery; 'append' is either history being backfilled
    // or a message that reached the server while this device was offline. The
    // two are told apart by age, not by type: an 'append' from a minute ago is
    // a real message the user is waiting for, one from last week is history
    // the platform has already answered.
    try {
      await handleIncoming(messages, type);
    } catch (error) {
      log.error({ error: error.message }, 'inbound handling failed');
    }
  });
}

app.get('/health', (_req, res) => {
  res.status(200).json({
    status: 'ok',
    connected: gateway.connected,
    has_qr: Boolean(gateway.qr),
    session: SESSION_NAME,
    platform_url: PLATFORM_URL,
  });
});

// Temporary: WhatsApp addresses contacts by an opaque LID since the multi-device
// migration, and the platform keys every conversation on the phone number. This
// endpoint exists to observe how WhatsApp answers a LID query before the
// mapping is wired into the inbound path.
app.get('/debug/resolve', async (req, res) => {
  if (!gateway.sock) {
    res.status(503).json({ error: 'not connected' });
    return;
  }
  const jid = String(req.query.jid || '');
  if (!jid) {
    res.status(400).json({ error: 'jid is required' });
    return;
  }
  try {
    const variant = String(req.query.variant || 'a');
    const userNodes = {
      a: { tag: 'user', attrs: { jid }, content: [{ tag: 'contact', attrs: {} }] },
      b: { tag: 'user', attrs: { jid: jid.split('@')[0] }, content: [{ tag: 'contact', attrs: {} }] },
      c: {
        tag: 'user',
        attrs: {},
        content: [{ tag: 'contact', attrs: {}, content: `+${jid.split('@')[0]}` }],
      },
      d: {
        tag: 'user',
        attrs: {},
        content: [
          { tag: 'lid', attrs: {}, content: jid.split('@')[0] },
          { tag: 'contact', attrs: {} },
        ],
      },
    };
    const started = Date.now();
    const answer = await gateway.sock.query(
      {
        tag: 'iq',
        attrs: { to: 's.whatsapp.net', type: 'get', xmlns: 'usync' },
        content: [
          {
            tag: 'usync',
            attrs: { context: 'interactive', mode: 'query', sid: variant, last: 'true', index: '0' },
            content: [
              {
                tag: 'query',
                attrs: {},
                content: [{ tag: 'contact', attrs: {} }, { tag: 'lid', attrs: {} }],
              },
              { tag: 'list', attrs: {}, content: [userNodes[variant] || userNodes.a] },
            ],
          },
        ],
      },
      15000
    );
    res.status(200).json({ jid, variant, elapsed_ms: Date.now() - started, answer });
  } catch (error) {
    res.status(502).json({ error: error.message });
  }
});

app.get('/qr', async (_req, res) => {
  if (gateway.connected) {
    res.status(200).send(
      '<!doctype html><meta charset="utf-8"><title>Loka</title>' +
        '<body style="font-family:sans-serif;padding:2rem">' +
        '<h1>Connecte</h1><p>Le compte WhatsApp est lie.</p></body>'
    );
    return;
  }
  if (!gateway.qr) {
    res.status(503).send(
      '<!doctype html><meta charset="utf-8"><title>Loka</title>' +
        '<head><meta http-equiv="refresh" content="2"></head>' +
        '<body style="font-family:sans-serif;padding:2rem">' +
        '<h1>En attente du QR code...</h1></body>'
    );
    return;
  }
  const dataUrl = await QRCode.toDataURL(gateway.qr, { margin: 1, width: 380 });
  res.status(200).send(
    '<!doctype html><meta charset="utf-8"><title>Loka - QR WhatsApp</title>' +
      '<head><meta http-equiv="refresh" content="5"></head>' +
      '<body style="font-family:sans-serif;padding:2rem;text-align:center">' +
      '<h1>Scannez ce code</h1>' +
      '<p>WhatsApp &gt; Appareils lies &gt; Relier un appareil</p>' +
      `<img src="${dataUrl}" alt="QR code" style="width:380px;height:380px">` +
      '</body>'
  );
});

app.post('/send', async (req, res) => {
  const { to, text, image, caption, location } = req.body || {};
  if (!to) {
    res.status(400).json({ error: 'to is required' });
    return;
  }
  // D3's outbound half: this accepted only {to, text}, so the platform could
  // never answer with a listing photo or with the lodging's position — which
  // is the thing the 1 000 FCFA unlock pays for.
  const content = outboundContent({ text, image, caption, location });
  if (!content) {
    res.status(400).json({ error: 'one of text, image or location is required' });
    return;
  }
  if (!gateway.connected || !gateway.sock) {
    res.status(503).json({ error: 'whatsapp is not connected' });
    return;
  }
  const digits = digitsOf(to);
  const jid = String(to).includes('@')
    ? String(to)
    : lidByPhone.get(digits) || `${digits}@s.whatsapp.net`;
  try {
    const sent = await gateway.sock.sendMessage(jid, content);
    rememberForRetry(sent);
    res.status(200).json({ ok: true, id: sent.key.id });
  } catch (error) {
    log.error({ error: error.message, jid }, 'send failed');
    // 502: the platform retries, because a transient WhatsApp failure is not
    // the same as a destination that will never exist.
    res.status(502).json({ error: error.message });
  }
});

/**
 * What to hand Baileys for one outbound message, or null when nothing was asked.
 *
 * An image may be a URL the platform pre-signed or base64 bytes; both are
 * accepted because the caller knows which it has and this transport should not
 * make it fetch its own object twice.
 */
function outboundContent({ text, image, caption, location }) {
  if (location && Number.isFinite(Number(location.latitude))) {
    return {
      location: {
        degreesLatitude: Number(location.latitude),
        degreesLongitude: Number(location.longitude),
        name: location.label ? String(location.label) : undefined,
      },
    };
  }
  if (typeof image === 'string' && image.trim()) {
    const body = /^https?:\/\//i.test(image)
      ? { url: image }
      : Buffer.from(image, 'base64');
    return { image: body, caption: caption ? String(caption) : undefined };
  }
  if (typeof text === 'string' && text.trim()) {
    return { text: String(text) };
  }
  return null;
}

app.post('/logout', async (_req, res) => {
  try {
    if (gateway.sock) await gateway.sock.logout();
  } catch (error) {
    log.warn({ error: error.message }, 'logout failed, clearing local state anyway');
  }
  clearAuthState('logout');
  res.status(200).json({ ok: true });
});

app.use((error, _req, res, _next) => {
  log.error({ error: error.message }, 'gateway request failed');
  res.status(500).json({ error: 'internal_error' });
});

app.listen(PORT, '0.0.0.0', () => {
  log.info({ port: PORT, platform: PLATFORM_URL }, 'gateway listening');
  startSocket().catch((error) => {
    log.error({ error: error.message }, 'socket start failed');
  });
});
