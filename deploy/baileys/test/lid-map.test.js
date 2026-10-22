'use strict';

/**
 * The phone → LID map survives a restart.
 *
 * Held in memory only, it was lost when the gateway restarted, and every reply
 * went to the `@s.whatsapp.net` form: the contact's phone showed "waiting for
 * this message" until he happened to write again.
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const os = require('os');
const path = require('path');

const { createLidMap, FILE_NAME } = require('../src/lid-map');

function tempDir() {
  return fs.mkdtempSync(path.join(os.tmpdir(), 'lid-map-'));
}

test('a mapping learned before a restart is known after it', () => {
  const dir = tempDir();
  createLidMap(dir).set('237600000000', '11111111111111@lid');

  const reloaded = createLidMap(dir);

  assert.strictEqual(reloaded.get('237600000000'), '11111111111111@lid');
});

test('a first start with no file knows nothing and does not fail', () => {
  assert.strictEqual(createLidMap(tempDir()).size, 0);
});

test('a corrupt file is ignored rather than crashing the gateway', () => {
  const dir = tempDir();
  fs.writeFileSync(path.join(dir, FILE_NAME), '{not json');
  const warnings = [];

  const map = createLidMap(dir, { log: { warn: (...args) => warnings.push(args) } });

  assert.strictEqual(map.size, 0);
  assert.strictEqual(warnings.length, 1);
});

test('only LIDs are kept from the file', () => {
  const dir = tempDir();
  fs.writeFileSync(
    path.join(dir, FILE_NAME),
    JSON.stringify({ 237600000000: '237600000000@s.whatsapp.net', 237611111111: '22@lid' })
  );

  const map = createLidMap(dir);

  assert.strictEqual(map.get('237600000000'), undefined);
  assert.strictEqual(map.get('237611111111'), '22@lid');
});
