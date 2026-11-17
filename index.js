/**
 * Host half of the media-bridge bundle: owns the `media-bridge-settings` row.
 *
 * The effective upstream plan (fallback chains, model ids, voices, endpoints)
 * lives in this row's Config, declared with Schemastery volatile fields. The
 * settings domain only treats an entry as a settings namespace when its Config
 * declares volatile fields, so that declaration is what makes the page appear
 * under Settings.
 *
 * Three surfaces edit it — the Settings page (via the settings domain), this
 * row's YAML in `cordis.patch.yml`, and the `media_bridge_settings` agent tool
 * (via the config editor). The listener attempts to rewrite the JSON file on
 * each change. Failures are logged and exposed through the settings tool;
 * a Settings page save alone does not establish that synchronization worked.
 *
 * Schemastery lives in the DSH installation, not next to this linked bundle, so
 * it is resolved from the running installation instead of being imported.
 */
import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';
import { randomUUID } from 'node:crypto';

export const PROVIDERS = {
  'minimax-plan': { label: 'MiniMax M Plan', caps: ['synthesize', 'generate_image'] },
  'minimax-payg': { label: 'MiniMax pay-as-you-go', caps: ['synthesize', 'generate_image', 'generate_video'] },
  'qwen-plan': { label: 'Qwen Token Plan', caps: ['transcribe', 'synthesize', 'generate_image', 'generate_video'] },
  'qwen-payg': { label: 'Qwen pay-as-you-go', caps: ['transcribe', 'synthesize', 'generate_image', 'generate_video'] },
  'xiaomi': { label: 'Xiaomi MiMo (free TTS)', caps: ['transcribe', 'synthesize'] },
  'deepseek': { label: 'DeepSeek pay-as-you-go', caps: ['transcribe', 'synthesize'] },
};

export const CAPABILITIES = ['transcribe', 'synthesize', 'generate_image', 'generate_video'];

export const DEFAULTS = {
  chains: {
    transcribe: ['qwen-plan', 'xiaomi', 'deepseek', 'qwen-payg', 'minimax-plan', 'minimax-payg'],
    synthesize: ['minimax-plan', 'qwen-plan', 'xiaomi', 'deepseek', 'minimax-payg', 'qwen-payg'],
    generate_image: ['minimax-plan', 'qwen-plan', 'minimax-payg', 'qwen-payg'],
    generate_video: ['qwen-plan', 'minimax-payg', 'qwen-payg'],
  },
  models: {},
  voices: {},
  baseUrls: {},
  disabled: [],
};

/** Locate Schemastery inside the running DSH installation. */
function loadSchemastery() {
  const roots = [];
  const add = (base) => { if (base && !roots.includes(base)) roots.push(base); };
  if (process.env.DSH_INSTALL_DIR) add(path.join(process.env.DSH_INSTALL_DIR, 'node_modules'));
  if (process.resourcesPath) add(path.join(process.resourcesPath, 'app.asar', 'dsh', 'node_modules'));
  // Walk up from the entry script and the executable: an npm-global install
  // keeps the package under <prefix>/lib/node_modules/@deepseek-ai/dsh, and the
  // entry script may be a symlink, so resolve it first.
  const realpath = (p) => { try { return fs.realpathSync(p); } catch { return p; } };
  for (const start of [process.argv[1], process.execPath]) {
    if (!start) continue;
    let dir = path.dirname(realpath(start));
    for (let depth = 0; depth < 7; depth++) {
      add(path.join(dir, 'node_modules'));
      add(path.join(dir, 'node_modules', '@deepseek-ai', 'dsh', 'node_modules'));
      const parent = path.dirname(dir);
      if (parent === dir) break;
      dir = parent;
    }
  }
  add('/usr/local/lib/node_modules');
  add('/usr/lib/node_modules');
  for (const root of roots) {
    try {
      const req = createRequire(path.join(root, 'noop.js'));
      return req('@deepseek-ai/schemastery');
    } catch {}
  }
  throw new Error(`media-bridge: cannot resolve @deepseek-ai/schemastery from ${roots.join(', ') || '(no candidate)'}`);
}

const z = loadSchemastery();

const providerId = z.string();
const chainsSchema = z.object({
  transcribe: z.array(providerId),
  synthesize: z.array(providerId),
  generate_image: z.array(providerId),
  generate_video: z.array(providerId),
});

/**
 * The row's Config. Every field is volatile, so the settings domain publishes
 * this entry as an editable namespace and a write lands without a remount.
 */
export const Config = z.object({
  chains: chainsSchema.volatile(),
  models: z.dict(z.dict(z.union([z.string(), z.array(z.string())]))).volatile(),
  voices: z.dict(z.string()).volatile(),
  baseUrls: z.dict(z.string()).volatile(),
  disabled: z.array(z.string()).volatile(),
});

const FIELDS = ['chains', 'models', 'voices', 'baseUrls', 'disabled'];

function isPlainObject(value) {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

/** Unwrap a Schemastery volatile reference to its plain value. */
function plain(value) {
  if (value !== null && typeof value === 'object' && typeof value.get === 'function') {
    return value.get();
  }
  return value;
}

/** Validate one candidate config; returns issue strings (empty = accepted). */
export function validateConfig(value) {
  const issues = [];
  if (value !== undefined && !isPlainObject(value)) { issues.push('config must be an object'); return issues; }
  const config = value ?? {};
  for (const key of Object.keys(config)) if (!FIELDS.includes(key)) issues.push(`unknown field "${key}" (allowed: ${FIELDS.join(', ')})`);
  if (config.chains !== undefined) {
    if (!isPlainObject(config.chains)) issues.push('chains must be an object');
    else for (const [cap, order] of Object.entries(config.chains)) {
      if (!CAPABILITIES.includes(cap)) issues.push(`chains: unknown capability "${cap}"`);
      if (!Array.isArray(order)) issues.push(`chains.${cap} must be an array of provider ids`);
      else for (const p of order) if (!PROVIDERS[p]) issues.push(`chains.${cap}: unknown provider "${p}"`);
    }
  }
  for (const field of ['models', 'voices', 'baseUrls']) {
    if (config[field] === undefined) continue;
    if (!isPlainObject(config[field])) { issues.push(`${field} must be an object`); continue; }
    for (const [p, v] of Object.entries(config[field])) {
      if (!PROVIDERS[p]) issues.push(`${field}: unknown provider "${p}"`);
      if (field === 'models') {
        if (!isPlainObject(v)) issues.push(`models.${p} must be an object of capability -> model id`);
        else for (const [cap, model] of Object.entries(v)) {
          if (!CAPABILITIES.includes(cap)) issues.push(`models.${p}: unknown capability "${cap}"`);
          if (typeof model === 'string') continue;
          if (Array.isArray(model)) {
            if (model.some(item => typeof item !== 'string')) issues.push(`models.${p}.${cap} must be a string or an ordered array of strings`);
            continue;
          }
          issues.push(`models.${p}.${cap} must be a string or an ordered array of strings`);
        }
      }
      if ((field === 'voices' || field === 'baseUrls') && typeof v !== 'string') issues.push(`${field}.${p} must be a string`);
      if (field === 'baseUrls' && typeof v === 'string') {
        try {
          const url = new URL(v);
          if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password) throw new Error();
        } catch { issues.push(`baseUrls.${p} must be an absolute HTTP(S) URL without credentials`); }
      }
    }
  }
  if (config.disabled !== undefined) {
    if (!Array.isArray(config.disabled)) issues.push('disabled must be an array');
    else for (const p of config.disabled) if (!PROVIDERS[p]) issues.push(`disabled: unknown provider "${p}"`);
  }
  return issues;
}

function mergeConfig(value) {
  const config = value ?? {};
  const raw = plain(config.chains) ?? {};
  const chains = {};
  for (const cap of CAPABILITIES) {
    const order = (Array.isArray(raw[cap]) ? raw[cap] : DEFAULTS.chains[cap]).filter(p => PROVIDERS[p]);
    for (const p of DEFAULTS.chains[cap]) if (!order.includes(p)) order.push(p);
    chains[cap] = order;
  }
  const disabled = Array.isArray(plain(config.disabled)) ? plain(config.disabled) : [];
  for (const cap of CAPABILITIES) chains[cap] = chains[cap].filter(p => !disabled.includes(p));
  return {
    chains,
    models: plain(config.models) ?? {},
    voices: plain(config.voices) ?? {},
    baseUrls: plain(config.baseUrls) ?? {},
    disabled,
  };
}

function chainFilePath(ctx) {
  // Match the MCP row's explicit path, without resolving or reading its credentials.
  const entries = ctx.loader?.entries ? [...ctx.loader.entries()] : [];
  const rows = entries.filter(entry => entry.options?.id === 'media-bridge-mcp');
  if (rows.length > 1) throw new Error('multiple media-bridge MCP rows; chain file path is ambiguous');
  // Read only this non-secret key from the composed raw row. Expressions and
  // implicit child-process defaults cannot be verified safely here.
  const candidate = rows[0]?.options?.config?.env?.MEDIA_BRIDGE_CHAINS_FILE;
  const effective = rows[0]?.fiber?.effectiveConfig?.env?.MEDIA_BRIDGE_CHAINS_FILE;
  if (typeof candidate !== 'string' || !path.isAbsolute(candidate) || candidate.includes('${')) {
    throw new Error('MCP MEDIA_BRIDGE_CHAINS_FILE must be an explicit absolute, literal path');
  }
  if (effective !== candidate) throw new Error('MCP effective chain file path is unavailable or differs from its configured path');
  return { file: path.normalize(candidate), pathVerified: true };
}

export const inject = ['tools', 'loader'];

export function apply(ctx, config) {
  let syncError = null;
  let lastFile = null;
  function sync() {
    let temporary;
    try {
      const { file, pathVerified } = chainFilePath(ctx);
      lastFile = file;
      if (!pathVerified) throw new Error('MCP row not found; cannot verify chain file path');
      const raw = Object.fromEntries(FIELDS.map(field => [field, plain(config?.[field])]));
      const issues = validateConfig(raw);
      if (issues.length) throw new Error(`invalid config: ${issues.join('; ')}`);
      const merged = mergeConfig(raw);
      fs.mkdirSync(path.dirname(file), { recursive: true });
      temporary = `${file}.${process.pid}.${randomUUID()}.tmp`;
      fs.writeFileSync(temporary, JSON.stringify({ version: 1, writtenAt: new Date().toISOString(), ...merged }, null, 2), { encoding: 'utf8', mode: 0o600, flag: 'wx' });
      fs.renameSync(temporary, file);
      syncError = null;
      return merged;
    } catch (error) {
      if (temporary) try { fs.unlinkSync(temporary); } catch {}
      syncError = String(error);
      ctx.logger('media-bridge').warn(`chain file sync failed: ${syncError}`);
      return null;
    }
  }
  sync();
  ctx.on('loader/volatile-update', () => { sync(); });
  // The MCP row can finish starting after this row. Re-check after reconciliation.
  ctx.on('app-boot/config-reload', () => { sync(); });

  // One operation, two callers: the agent path mirrors what the Settings page
  // writes, through the same config editor the settings domain uses.
  const tool = {
    name: 'media_bridge_settings',
    description: 'Read or update the media bridge upstream plan: fallback chain order per capability, per-provider model ids, voices, endpoints, and disabled providers. get returns the effective config; set takes a partial or full config object and validates it. Updates are shown on the Settings page; get reports whether the runtime file was synchronized.',
    parameters: {
      action: { type: 'string', required: true, description: '"get" to read, "set" to update.' },
      config: { type: 'object', additionalProperties: true, description: 'For action=set: object with any of chains, models, voices, baseUrls, disabled. Pass null for a field to clear it and fall back to the built-in default.' },
    },
    output: {
      schema: { type: 'object', additionalProperties: true },
      render: (_args, value) => [{ type: 'text', text: JSON.stringify(value, null, 2) }],
    },
    async execute(args) {
      if (args.action === 'get') {
        const merged = sync();
        return merged ? { ok: true, file: lastFile, config: merged } : { ok: false, file: lastFile, error: syncError };
      }
      if (args.action !== 'set') return { ok: false, error: 'action must be "get" or "set"' };
      const candidate = args.config;
      if (!isPlainObject(candidate)) return { ok: false, error: 'config must be an object' };
      // null clears a field. Without it set only ever merges, so an override could
      // not be undone except by hand on the Settings page.
      const cleared = Object.keys(candidate).filter(key => candidate[key] === null);
      const unknown = Object.keys(candidate).filter(key => !FIELDS.includes(key));
      if (unknown.length > 0) {
        return { ok: false, error: `unknown field "${unknown.join('", "')}" (allowed: ${FIELDS.join(', ')})` };
      }
      const updates = Object.fromEntries(Object.entries(candidate).filter(([, value]) => value !== null));
      const issues = validateConfig(updates);
      if (issues.length > 0) return { ok: false, error: issues.join('; ') };
      const editor = ctx.get('configEditor');
      const entry = ctx.fiber.entry;
      if (editor === undefined || entry === undefined) return { ok: false, error: 'Configuration cannot be saved in this profile' };
      try {
        await editor.edit(entry, current => {
          const next = { ...current, ...updates };
          for (const key of cleared) delete next[key];
          return next;
        });
      } catch (error) { return { ok: false, error: `configuration was not saved: ${String(error)}` }; }
      const merged = sync();
      if (!merged) return { ok: false, saved: true, file: lastFile, cleared, error: `configuration saved but chain file sync failed: ${syncError}` };
      return { ok: true, saved: true, file: lastFile, cleared, config: merged };
    },
  };
  ctx.effect(() => ctx.tools.register(tool), 'media-bridge:settings-tool');
}
