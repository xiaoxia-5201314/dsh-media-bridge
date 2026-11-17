import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'media-bridge-host-test-'));
const moduleDir = path.join(temp, 'node_modules', '@deepseek-ai', 'schemastery');
fs.mkdirSync(moduleDir, { recursive: true });
fs.writeFileSync(path.join(moduleDir, 'index.js'), `const schema = () => ({ volatile() { return this } }); exports.string = exports.object = exports.array = exports.dict = exports.union = schema;`);
process.env.DSH_INSTALL_DIR = temp;
const bridge = await import(pathToFileURL(path.join(import.meta.dirname, 'index.js')).href);

function host(file, state = {}) {
  const handlers = {};
  const refs = Object.fromEntries(['chains', 'models', 'voices', 'baseUrls', 'disabled'].map(k => [k, { get: () => state[k] }]));
  let tool;
  const entry = {};
  const ctx = {
    fiber: { entry },
    loader: { *entries() { yield { options: { id: 'media-bridge-mcp', config: { env: { MEDIA_BRIDGE_CHAINS_FILE: file } } }, fiber: { effectiveConfig: { env: { MEDIA_BRIDGE_CHAINS_FILE: file } } } }; } },
    on(name, cb) { handlers[name] = cb; },
    logger() { return { warn() {} }; },
    tools: { register(value) { tool = value; return () => {}; } },
    effect(fn) { fn(); },
    get() {
      return {
        async edit(_entry, change) {
          const next = change(state);
          // a real editor replaces the config, so a key absent from the result is gone
          for (const key of Object.keys(state)) if (!(key in next)) delete state[key];
          Object.assign(state, next);
          handlers['loader/volatile-update']();
        },
      };
    },
  };
  bridge.apply(ctx, refs);
  return { tool, handlers, state };
}

test('initial and volatile update write atomically and get reads live refs', async () => {
  const file = path.join(temp, 'runtime', 'chains.json');
  const h = host(file);
  assert.deepEqual(JSON.parse(fs.readFileSync(file)).chains.transcribe, bridge.DEFAULTS.chains.transcribe);
  h.state.disabled = ['xiaomi'];
  h.handlers['loader/volatile-update']();
  const result = await h.tool.execute({ action: 'get' });
  assert.equal(result.ok, true);
  assert.ok(!result.config.chains.transcribe.includes('xiaomi'));
  assert.deepEqual(JSON.parse(fs.readFileSync(file)).disabled, ['xiaomi']);
  assert.deepEqual(fs.readdirSync(path.dirname(file)), ['chains.json']);
});

test('invalid URLs and malformed models are rejected before save', async () => {
  const h = host(path.join(temp, 'validate', 'chains.json'));
  for (const config of [{ baseUrls: { xiaomi: 'file:///etc/passwd' } }, { baseUrls: { xiaomi: 'https://u:p@example.com' } }, { models: { xiaomi: { synthesize: 1 } } }]) {
    assert.equal((await h.tool.execute({ action: 'set', config })).ok, false);
  }
});

test('write failure is discoverable and later recovery succeeds', async () => {
  const dir = path.join(temp, 'failure');
  fs.writeFileSync(dir, 'not a directory');
  const h = host(path.join(dir, 'chains.json'));
  assert.equal((await h.tool.execute({ action: 'get' })).ok, false);
  const result = await h.tool.execute({ action: 'set', config: { disabled: ['xiaomi'] } });
  assert.equal(result.saved, true);
  assert.equal(result.ok, false);
  fs.unlinkSync(dir);
  h.handlers['loader/volatile-update']();
  assert.equal((await h.tool.execute({ action: 'get' })).ok, true);
});

test('unverified or relative runtime path fails visibly', async () => {
  const h = host('relative.json');
  assert.equal((await h.tool.execute({ action: 'get' })).ok, false);
});

test('model config accepts legacy string and ordered model arrays', async () => {
  const dir = path.join(temp, 'models');
  const state = { models: { 'qwen-payg': { synthesize: ['m2', 'm1'] }, xiaomi: { synthesize: 'legacy' } } };
  const h = host(path.join(dir, 'chains.json'), state);
  const result = await h.tool.execute({ action: 'get' });
  assert.equal(result.ok, true);
  assert.deepEqual(result.config.models['qwen-payg'].synthesize, ['m2', 'm1']);
  assert.deepEqual(JSON.parse(fs.readFileSync(path.join(dir, 'chains.json'))).models, state.models);
  state.models['qwen-payg'].synthesize = ['m1'];
  h.handlers['loader/volatile-update']();
  assert.deepEqual((await h.tool.execute({ action: 'get' })).config.models['qwen-payg'].synthesize, ['m1']);
  for (const bad of [{ models: { xiaomi: { synthesize: ['ok', 7] } } }, { models: { xiaomi: { synthesize: 5 } } }]) {
    assert.equal((await h.tool.execute({ action: 'set', config: bad })).ok, false);
  }
});
test('null clears a field back to the built-in default', async () => {
  const dir = path.join(temp, 'clear');
  // no disabled providers here: the effective chain filters them out, which is
  // asserted in the first test and would mask what this one is about
  const state = { chains: { synthesize: ['xiaomi'] }, voices: { xiaomi: 'mimo_default' } };
  const h = host(path.join(dir, 'chains.json'), state);
  const result = await h.tool.execute({ action: 'set', config: { chains: null } });
  assert.equal(result.ok, true);
  assert.deepEqual(result.cleared, ['chains']);
  assert.equal(state.chains, undefined, 'the override must be gone, not emptied');
  const file = JSON.parse(fs.readFileSync(path.join(dir, 'chains.json')));
  assert.deepEqual(file.chains, bridge.DEFAULTS.chains);
  assert.deepEqual(file.voices, { xiaomi: 'mimo_default' }, 'clearing one field must not touch the others');
  assert.equal((await h.tool.execute({ action: 'set', config: { chanins: null } })).ok, false);
  assert.equal((await h.tool.execute({ action: 'set', config: { chanins: null, voices: {} } })).ok, false);
});

process.on('exit', () => fs.rmSync(temp, { recursive: true, force: true }));
