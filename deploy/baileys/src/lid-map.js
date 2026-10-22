'use strict';

// Which LID WhatsApp uses for a phone number, kept across restarts.
//
// WhatsApp addresses a contact by an opaque LID since the multi-device
// migration, while the platform keys conversations on the phone number. The
// mapping is only learned from an inbound message, so an in-memory map lost
// on restart sent every reply to the `@s.whatsapp.net` form until that
// contact wrote again — observed live, each one stuck on "waiting for this
// message". It is written next to the auth state, on the same volume, and
// cleared with it on logout.

const fs = require('fs');
const path = require('path');

const FILE_NAME = 'lid-map.json';

function createLidMap(dir, { log } = {}) {
  const file = path.join(dir, FILE_NAME);
  const map = new Map();
  try {
    const stored = JSON.parse(fs.readFileSync(file, 'utf8'));
    for (const [digits, lid] of Object.entries(stored)) {
      if (typeof lid === 'string' && lid.endsWith('@lid')) map.set(digits, lid);
    }
  } catch (error) {
    if (error.code !== 'ENOENT' && log) log.warn({ error: error.message }, 'lid map unreadable');
  }

  function save() {
    // Written to a temporary file and renamed, so a crash mid-write never
    // leaves a truncated map behind.
    const tmp = `${file}.tmp`;
    fs.writeFileSync(tmp, JSON.stringify(Object.fromEntries(map)));
    fs.renameSync(tmp, file);
  }

  return {
    get(digits) {
      return map.get(digits);
    },
    set(digits, lid) {
      if (!digits || !lid || map.get(digits) === lid) return;
      map.set(digits, lid);
      try {
        save();
      } catch (error) {
        if (log) log.warn({ error: error.message }, 'lid map not saved');
      }
    },
    get size() {
      return map.size;
    },
  };
}

module.exports = { createLidMap, FILE_NAME };
