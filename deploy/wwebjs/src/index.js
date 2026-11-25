'use strict';

/**
 * WhatsApp gateway on WhatsApp Web (whatsapp-web.js).
 *
 * Why it exists next to deploy/baileys: once a number is linked to the
 * WhatsApp Business Platform (coexistence), messages Baileys encrypts reach
 * the recipient as « waiting for this message » (Baileys #1686, unfixed).
 * WhatsApp Web encrypts with WhatsApp's own code, and Meta supports it as a
 * companion of a coexistence number. The HTTP contract is deploy/baileys's,
 * so the platform switches transport with WHATSAPP__GATEWAY_URL alone.
 *
 * Like Baileys, it is the dumbest part of the platform: pair, forward, send.
 *
 *   GET  /health  liveness plus pairing state
 *   GET  /qr      the pairing QR code
 *   POST /send    {to, text} | {to, image, caption} | {to, location} | {to, contact}
 *   POST /logout  unlink, so a new QR can be scanned
 */

const express = require('express');
const QRCode = require('qrcode');
const pino = require('pino');
const { Client, LocalAuth, Location, MessageMedia } = require('whatsapp-web.js');
const inbound = require('./inbound');
const { outboundContent } = require('./outbound');
const { MAX_MEDIA_BYTES, createPlatform } = require('./platform');

const PORT = Number(process.env.PORT || 3000);
const AUTH_DIR = process.env.AUTH_DIR || '/app/auth';
const SESSION_NAME = process.env.SESSION_NAME || 'kora-main';
const PLATFORM_URL = process.env.PLATFORM_URL || 'http://api:8000/integrations/whatsapp/events';
const RESTART_DELAY_MS = Number(process.env.RESTART_DELAY_MS || 5000);

const log = pino({ level: (process.env.LOG_LEVEL || 'info').toLowerCase() });
const platform = createPlatform({ eventsUrl: PLATFORM_URL, apiKey: process.env.PLATFORM_API_KEY || '', log });

const gateway = { client: null, qr: null, connected: false, starting: false };
// Phone number -> the chat id WhatsApp knows it by (a @lid one, nowadays).
const chatIds = new Map();

function newClient() {
  return new Client({
    authStrategy: new LocalAuth({ dataPath: AUTH_DIR, clientId: SESSION_NAME }),
    puppeteer: {
      headless: true,
      executablePath: process.env.CHROMIUM_PATH || undefined,
      // No sandbox inside the container (no user namespaces); /dev/shm is
      // 64 MB there and Chromium crashes on it.
      args: ['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage', '--disable-gpu'],
    },
  });
}

async function start() {
  if (gateway.starting) return;
  gateway.starting = true;
  const client = newClient();
  gateway.client = client;

  client.on('qr', (qr) => {
    gateway.qr = qr;
    log.info('scan the QR code at /qr with WhatsApp > Linked devices');
  });
  client.on('authenticated', () => log.info('whatsapp authenticated'));
  client.on('auth_failure', (message) => log.warn({ message }, 'whatsapp authentication failed'));
  client.on('ready', () => {
    gateway.connected = true;
    gateway.qr = null;
    log.info({ jid: client.info && client.info.wid && client.info.wid._serialized }, 'whatsapp connected');
  });
  client.on('disconnected', (reason) => {
    gateway.connected = false;
    log.warn({ reason }, 'whatsapp disconnected');
    restart();
  });
  client.on('message', (msg) => {
    handleIncoming(msg).catch((error) =>
      log.error({ error: error.message, id: msg.id && msg.id.id }, 'delivery handling failed')
    );
  });

  try {
    await client.initialize();
  } catch (error) {
    log.error({ error: error.message }, 'whatsapp web did not start');
    gateway.starting = false;
    restart();
    return;
  }
  gateway.starting = false;
}

let restartTimer = null;
function restart() {
  if (restartTimer) return;
  restartTimer = setTimeout(async () => {
    restartTimer = null;
    const old = gateway.client;
    gateway.client = null;
    gateway.starting = false;
    try {
      if (old) await old.destroy();
    } catch (error) {
      log.warn({ error: error.message }, 'previous browser could not be closed');
    }
    start().catch((error) => log.error({ error: error.message }, 'restart failed'));
  }, RESTART_DELAY_MS);
}

async function handleIncoming(msg) {
  const reason = inbound.skipReason(msg);
  const id = msg.id && msg.id.id;
  if (reason) {
    log.info({ external_message_id: id, from: msg.from, type: msg.type, reason }, 'delivery skipped');
    return;
  }
  const phone = await inbound.senderPhone(msg, (ids) => gateway.client.getContactLidAndPhone(ids));
  if (!phone) {
    log.warn({ external_message_id: id, from: msg.from }, 'delivery skipped: no phone number');
    return;
  }
  chatIds.set(phone, msg.from);

  const text = inbound.textOf(msg);
  const location = inbound.locationOf(msg);
  const media = await storeMedia(msg, id);
  if (inbound.MEDIA_KINDS.has(inbound.kindOf(msg)) && !media.objectKey && !media.refusal && !text) {
    log.error({ external_message_id: id }, 'delivery skipped: media could not be stored and nothing else was sent');
    return;
  }
  log.info({ external_message_id: id, from: msg.from, kind: inbound.kindOf(msg), sender_phone: phone }, 'delivery accepted');
  const quoted = await inbound.quotedOf(msg);
  await platform.forward(
    inbound.delivery({ msg, phone, session: SESSION_NAME, text, location, media, quoted }),
  );
}

async function storeMedia(msg, id) {
  const kind = inbound.kindOf(msg);
  if (!inbound.MEDIA_KINDS.has(kind) || !msg.hasMedia) return { objectKey: null, refusal: null };
  const declared = Number(msg._data && msg._data.size);
  if (Number.isFinite(declared) && declared > MAX_MEDIA_BYTES) return { objectKey: null, refusal: 'too_large' };
  try {
    const file = await msg.downloadMedia();
    if (!file || !file.data) throw new Error('media download was empty');
    return await platform.upload({
      externalId: id,
      mimetype: inbound.mimetypeOf(file, kind),
      buffer: Buffer.from(file.data, 'base64'),
    });
  } catch (error) {
    log.error({ external_message_id: id, error: error.message }, 'media download failed');
    return { objectKey: null, refusal: null };
  }
}

async function chatIdOf(to) {
  if (String(to).includes('@')) return String(to);
  const digits = inbound.digitsOf(to);
  if (chatIds.has(digits)) return chatIds.get(digits);
  const found = await gateway.client.getNumberId(digits);
  const id = found ? found._serialized : `${digits}@c.us`;
  chatIds.set(digits, id);
  return id;
}

const app = express();
app.use(express.json({ limit: '256kb' }));

app.get('/health', (_req, res) => {
  res.status(200).json({
    status: 'ok',
    connected: gateway.connected,
    has_qr: Boolean(gateway.qr),
    session: SESSION_NAME,
    platform_url: PLATFORM_URL,
    transport: 'wwebjs',
  });
});

const page = (title, body, refresh = '') =>
  '<!doctype html><meta charset="utf-8"><title>idia</title>' +
  (refresh ? `<meta http-equiv="refresh" content="${refresh}">` : '') +
  `<body style="font-family:sans-serif;padding:2rem"><h1>${title}</h1>${body}</body>`;

app.get('/qr', async (_req, res) => {
  if (gateway.connected) {
    res.status(200).send(page('Connecté', '<p>Le compte WhatsApp est lié.</p>'));
    return;
  }
  if (!gateway.qr) {
    res.status(503).send(page('En attente du QR code…', '', '2'));
    return;
  }
  const dataUrl = await QRCode.toDataURL(gateway.qr, { margin: 1, width: 380 });
  res
    .status(200)
    .send(page('Scanne avec WhatsApp', `<p>Appareils liés → Lier un appareil</p><img src="${dataUrl}" alt="QR">`, '20'));
});

app.post('/send', async (req, res) => {
  const body = req.body || {};
  if (!body.to) {
    res.status(400).json({ error: 'to is required' });
    return;
  }
  if (!gateway.connected || !gateway.client) {
    res.status(503).json({ error: 'whatsapp is not connected' });
    return;
  }
  let message;
  try {
    message = await outboundContent(body, { MessageMedia, Location });
  } catch (error) {
    // The image URL could not be fetched: the platform retries.
    res.status(502).json({ error: `image unavailable: ${error.message}` });
    return;
  }
  if (!message) {
    res.status(400).json({ error: 'one of text, image, location or contact is required' });
    return;
  }
  try {
    const chatId = await chatIdOf(body.to);
    const sent = await gateway.client.sendMessage(chatId, message.content, message.options);
    res.status(200).json({ ok: true, id: sent && sent.id ? sent.id.id : null });
  } catch (error) {
    log.error({ error: error.message, to: body.to }, 'send failed');
    // 502: the platform retries; a transient failure is not a bad address.
    res.status(502).json({ error: error.message });
  }
});

app.post('/logout', async (_req, res) => {
  try {
    if (gateway.client) await gateway.client.logout();
  } catch (error) {
    log.warn({ error: error.message }, 'logout failed');
  }
  gateway.connected = false;
  chatIds.clear();
  restart();
  res.status(200).json({ ok: true });
});

app.use((error, _req, res, _next) => {
  log.error({ error: error.message }, 'gateway request failed');
  res.status(500).json({ error: 'internal_error' });
});

app.listen(PORT, '0.0.0.0', () => {
  log.info({ port: PORT, platform: PLATFORM_URL }, 'gateway listening');
  start().catch((error) => log.error({ error: error.message }, 'start failed'));
});
