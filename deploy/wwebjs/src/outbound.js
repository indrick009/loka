'use strict';

// One POST /send body, as what client.sendMessage takes. Same body as
// deploy/baileys accepts (adapters/whatsapp/baileys.py builds it): text, an
// image with its caption, a pin, or a contact card.

const { digitsOf } = require('./inbound');

function vcardOf(contact) {
  const name = String(contact.name || contact.phone).replace(/[\r\n;]/g, ' ');
  const digits = digitsOf(contact.phone);
  // waid makes WhatsApp show « Message » on the card, opening the chat.
  return (
    'BEGIN:VCARD\nVERSION:3.0\n' +
    `FN:${name}\n` +
    `TEL;type=CELL;type=VOICE;waid=${digits}:+${digits}\n` +
    'END:VCARD'
  );
}

/**
 * { content, options } for one send, or null when the body asks for nothing.
 * `MessageMedia` and `Location` are whatsapp-web.js's, passed in so this stays
 * testable without a browser.
 */
async function outboundContent(body, { MessageMedia, Location }) {
  const { text, image, caption, location, contact } = body || {};
  if (location && Number.isFinite(Number(location.latitude))) {
    const options = {};
    if (location.label) options.name = String(location.label);
    if (location.address) options.address = String(location.address);
    return {
      content: new Location(Number(location.latitude), Number(location.longitude), options),
      options: {},
    };
  }
  if (contact && contact.phone) {
    return { content: vcardOf(contact), options: { parseVCards: true } };
  }
  if (typeof image === 'string' && image.trim()) {
    const media = /^https?:\/\//i.test(image)
      ? await MessageMedia.fromUrl(image, { unsafeMime: true })
      : new MessageMedia('image/jpeg', image);
    return { content: media, options: caption ? { caption: String(caption) } : {} };
  }
  if (typeof text === 'string' && text.trim()) {
    // A URL must not turn into a preview card the platform never asked for.
    return { content: String(text), options: { linkPreview: false } };
  }
  return null;
}

module.exports = { outboundContent, vcardOf };
