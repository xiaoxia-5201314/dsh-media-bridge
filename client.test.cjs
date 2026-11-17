const { test } = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');

function setup(initial, remoteExtra) {
  let plugin, controller, section, listener = () => {};
  let snapshot = initial;
  const calls = [];
  const keyCalls = [];
  let mutate = async () => true;
  const form = {
    getSnapshot: () => snapshot,
    subscribe: f => { listener = f; return () => {}; },
    mutate: (...args) => { calls.push(args); return mutate(...args); },
  };
  const locales = [];
  const React = { createElement: (type, props, ...children) => ({ type, props: props || {}, children }), useReducer: () => [0, () => {}], useEffect: () => {}, useState: init => [init, () => {}] };
  const sandbox = { window: { __ModuleLoader__: { load: v => { plugin = v.factory(() => React); } } }, URL };
  vm.runInNewContext(fs.readFileSync(require('node:path').join(__dirname, 'client.js'), 'utf8'), sandbox);
  const ctx = { configForms: { get: () => form }, locale: { register: (_ns, dicts) => { locales.push(dicts); return () => {}; }, bind: () => x => x }, remote: { $on: () => () => {}, ...(remoteExtra || {}) },
    effect: f => f(), slots: { inject: (_s, f) => f(), register: (opts, view) => { controller = opts.inject().mediaBridge; section = view; return () => {}; } } };
  plugin.apply(ctx);
  return { controller, section, calls, keyCalls, locales, sandbox, update: s => { snapshot = s; listener(); }, setMutate: f => { mutate = f; } };
}
const ready = (value, revision = 1) => ({ status: 'ready', value, revision, writable: true });
test('loading render cannot freeze defaults; partial chains append defaults and move consistently', () => {
  const x = setup({ status: 'loading', writable: false });
  x.section({ mediaBridge: x.controller, t: x => x });
  x.update(ready({ chains: { transcribe: ['xiaomi'] } }));
  assert.deepEqual(Array.from(x.controller.draft.chains.transcribe), ['xiaomi', 'qwen-plan', 'deepseek', 'qwen-payg', 'minimax-plan', 'minimax-payg']);
  x.controller.move('transcribe', 'qwen-plan', -1);
  assert.deepEqual(Array.from(x.controller.draft.chains.transcribe).slice(0, 2), ['qwen-plan', 'xiaomi']);
});
test('one atomic mutation uses original revision and saves changed fields', async () => {
  const x = setup(ready({}));
  x.controller.setVoice('xiaomi', 'mimo_default'); x.controller.toggle('synthesize', 'xiaomi');
  await x.controller.save();
  assert.equal(x.calls.length, 1); assert.equal(x.calls[0][1], 1);
  assert.deepEqual(Array.from(x.calls[0][0], op => op.path[0]), ['voices', 'disabled']);
  assert.equal(x.controller.status(), 'saved');
});
test('concurrent snapshot and refused write preserve the draft', async () => {
  const x = setup(ready({})); x.controller.setVoice('xiaomi', 'a'); x.update(ready({}, 2)); await x.controller.save();
  assert.equal(x.calls.length, 0); assert.equal(x.controller.status(), 'conflict'); assert.equal(x.controller.draft.voices.xiaomi, 'a');
  x.controller.discard(); x.controller.setVoice('xiaomi', 'b'); x.setMutate(async () => false); await x.controller.save();
  assert.equal(x.controller.status(), 'failed'); assert.equal(x.controller.draft.voices.xiaomi, 'b');
});
test('in-flight edits and discard cannot erase a submitted draft', async () => {
  const x = setup(ready({})); let finish; x.setMutate(() => new Promise(r => { finish = r; }));
  x.controller.setVoice('xiaomi', 'a'); const saving = x.controller.save();
  x.controller.setVoice('xiaomi', 'b'); x.controller.discard(); assert.equal(x.controller.draft.voices.xiaomi, 'a');
  finish(false); await saving; assert.equal(x.controller.draft.voices.xiaomi, 'a');
});
test('reverted clean draft follows external snapshots and uses the new revision', async () => {
  const x = setup(ready({}));
  x.controller.setVoice('xiaomi', 'temporary');
  x.controller.setVoice('xiaomi', '');
  assert.equal(x.controller.dirty(), false);
  x.update(ready({ voices: { xiaomi: 'new-value' } }, 2));
  assert.equal(x.controller.draft.voices.xiaomi, 'new-value');
  x.controller.setVoice('xiaomi', 'next');
  await x.controller.save();
  assert.equal(x.calls[0][1], 2);
});
test('URL validation rejects userinfo, query, fragments and non-http schemes', async () => {
  for (const url of ['https://u:p@example.com', 'https://example.com/?token=x', 'https://example.com/#x', 'file:///etc/a']) {
    const x = setup(ready({})); x.controller.setBaseUrl('xiaomi', url); await x.controller.save(); assert.equal(x.calls.length, 0); assert.equal(x.controller.status(), 'invalidUrl');
  }
  const x = setup(ready({})); x.controller.setBaseUrl('xiaomi', 'https://example.com/v1'); await x.controller.save(); assert.equal(x.calls.length, 1);
});

test('legacy model string is readable and model list supports add/remove/move', async () => {
  const x = setup(ready({ models: { 'qwen-payg': { synthesize: 'legacy' } } }));
  assert.deepEqual(Array.from(x.controller.models('qwen-payg', 'synthesize')), ['legacy']);
  x.controller.addModel('qwen-payg', 'synthesize', 'new');
  assert.deepEqual(Array.from(x.controller.models('qwen-payg', 'synthesize')), ['legacy', 'new']);
  x.controller.moveModel('qwen-payg', 'synthesize', 1, -1);
  assert.deepEqual(Array.from(x.controller.models('qwen-payg', 'synthesize')), ['new', 'legacy']);
  x.controller.removeModel('qwen-payg', 'synthesize', 0);
  assert.deepEqual(Array.from(x.controller.models('qwen-payg', 'synthesize')), ['legacy']);
  await x.controller.save();
  assert.deepEqual(Array.from(x.calls[0][0], op => op.path[0]), ['models']);
  assert.deepEqual(Array.from(x.calls[0][0][0].value['qwen-payg'].synthesize), ['legacy']);
});

test('drag reorders providers and models only within the same capability', () => {
  const x = setup(ready({}));
  const before = Array.from(x.controller.draft.chains.synthesize);
  x.controller.beginDrag('chain', 'synthesize', null, 0);
  x.controller.dropAt('chain', 'synthesize', null, 2);
  assert.deepEqual(Array.from(x.controller.draft.chains.synthesize), [before[1], before[2], before[0], ...before.slice(3)]);
  const afterChain = Array.from(x.controller.draft.chains.synthesize);
  x.controller.beginDrag('chain', 'transcribe', null, 0);
  x.controller.dropAt('chain', 'synthesize', null, 1);
  assert.deepEqual(Array.from(x.controller.draft.chains.synthesize), afterChain);
  x.controller.addModel('minimax-plan', 'synthesize', 'a');
  x.controller.addModel('minimax-plan', 'synthesize', 'b');
  x.controller.beginDrag('model', 'synthesize', 'minimax-plan', 0);
  x.controller.dropAt('model', 'synthesize', 'minimax-plan', 1);
  assert.deepEqual(Array.from(x.controller.models('minimax-plan', 'synthesize')), ['b', 'a']);
  const beforeModel = Array.from(x.controller.models('minimax-plan', 'synthesize'));
  x.controller.beginDrag('model', 'synthesize', 'minimax-plan', 0);
  x.controller.dropAt('model', 'generate_image', 'minimax-plan', 1);
  assert.deepEqual(Array.from(x.controller.models('minimax-plan', 'synthesize')), beforeModel);
});
test('whole page renders with ready config and legacy model strings', () => {
  const x = setup(ready({ models: { 'qwen-payg': { synthesize: 'legacy' } } }));
  x.controller.toggleProvider('qwen-payg');
  const tree = JSON.stringify(x.section({ mediaBridge: x.controller, t: key => key }), (k, v) => typeof v === 'function' ? 'fn' : v);
  assert.ok(tree.includes('chains'), 'chain section missing');
  assert.ok(tree.includes('models'), 'model section missing');
  assert.ok(tree.includes('addModel'), 'add-model control missing once expanded');
  assert.ok(tree.includes('legacy'), 'legacy model value missing');
});
test('reorder controls render as one merged micro stepper', () => {
  const x = setup(ready({}));
  const tree = JSON.stringify(x.section({ mediaBridge: x.controller, t: key => key }), (k, v) => typeof v === 'function' ? 'fn' : v);
  assert.ok(tree.includes('▴') && tree.includes('▾'), 'stepper glyphs missing');
  assert.ok(!tree.includes("'↑'") && !tree.includes('↑'), 'old arrow buttons still present');
});

test('keys save through the credentials face without touching config', async () => {
  const seen = [];
  let describe = 0;
  const x = setup(ready({}), {
    credentials: {
      describe: async () => ({ ok: true, value: { QIANWEN_API_KEY: { configured: true } } }),
      set: async (ref, value) => { seen.push(['set', ref, value]); return {}; },
      unset: async ref => { seen.push(['unset', ref]); return {}; },
    },
  });
  await x.controller.setCredential('MINIMAX_API_KEY', ' fake-secret ');
  assert.deepEqual(seen[0], ['set', 'MINIMAX_API_KEY', 'fake-secret']);
  assert.equal(x.controller.credStatus()['MINIMAX_API_KEY'], 'keySet');
  assert.equal(x.controller.dirty(), false);
  assert.equal(x.calls.length, 0);
  await x.controller.clearCredential('QIANWEN_API_KEY');
  assert.deepEqual(seen[1], ['unset', 'QIANWEN_API_KEY']);
  assert.equal(x.controller.credStatus()['QIANWEN_API_KEY'], 'keyCleared');
  assert.ok(describe >= 0);
});

test('failed credential write reports failure and keeps no value', async () => {
  const x = setup(ready({}), { credentials: { describe: async () => ({ ok: true, value: {} }), set: async () => { throw new Error('boom'); }, unset: async () => ({}) } });
  await x.controller.setCredential('XIAOMI_API_KEY', 'secret');
  assert.equal(x.controller.credStatus()['XIAOMI_API_KEY'], 'keyFailed');
  assert.equal(x.controller.dirty(), false);
  assert.equal(x.calls.length, 0);
});

test('without a credential writer the page says so and disables inputs', () => {
  const x = setup(ready({}));
  assert.equal(x.controller.keyWritable(), false);
  const tree = JSON.stringify(x.section({ mediaBridge: x.controller, t: key => key }), (k, v) => typeof v === 'function' ? 'fn' : v);
  assert.ok(tree.includes('keyUnavailable'), 'missing unavailable hint');
  assert.ok(tree.includes('true'), 'inputs should carry disabled flag');
});
test('redesigned page uses theme tokens, five cards and one stepper per row', () => {
  const x = setup(ready({}));
  const nodes = [];
  let css = '';
  (function walk(node) {
    if (Array.isArray(node)) { node.forEach(walk); return; }
    if (!node || typeof node !== 'object') return;
    nodes.push(node);
    if (node.props && node.props.dangerouslySetInnerHTML) css = node.props.dangerouslySetInnerHTML.__html;
    if (node.children) walk(node.children);
  })(x.section({ mediaBridge: x.controller, t: key => key }));
  const byClass = name => nodes.filter(n => typeof n.props.className === 'string' && n.props.className.split(' ').includes(name));
  const titles = nodes.filter(n => n.type === 'h3').map(n => String(n.children)).join('|');
  for (const key of ['chains', 'models', 'voices', 'endpoints', 'budget'])
    assert.ok(titles.includes(key), 'missing settings card: ' + key);
  assert.equal(nodes.filter(n => n.type === 'h3').length, 6, 'one card per settings group');
  assert.equal(byClass('mb-foot').length, 1, 'one sticky action bar expected');
  const rows = byClass('mb-item');
  const steps = byClass('mb-step');
  assert.equal(steps.length, rows.length, 'one merged stepper per draggable row');
  for (const s of steps) assert.equal(s.children.filter(c => c && c.type === 'button').length, 2, 'stepper holds exactly two arrows');
  assert.ok(css.includes('var(--dsw-alias-border-l1'), 'theme border token missing');
  assert.ok(css.includes('var(--dsw-alias-brand-primary'), 'theme accent token missing');
  assert.ok(css.includes('var(--dsw-alias-state-success-primary'), 'theme state token missing');
  assert.ok(!/(^|[^,\s])#[0-9a-fA-F]{3}/.test(css), 'literal colours may appear only as token fallbacks');
  assert.ok(!nodes.some(n => n.props.type === 'button' && n.props.style && n.props.style.fontSize), 'buttons must not carry scattered inline sizes');
});
test('every glyph-only control and unlabelled field has an accessible name', () => {
  const x = setup(ready({ models: { 'qwen-payg': { synthesize: ['m1', 'm2'] } } }));
  const bad = [];
  (function walk(node) {
    if (Array.isArray(node)) { node.forEach(walk); return; }
    if (!node || typeof node !== 'object') return;
    const label = node.props['aria-label'];
    const text = (node.children || []).filter(c => typeof c === 'string').join('');
    if (node.type === 'button' && text && !/[\p{L}\p{N}]/u.test(text) && !label) bad.push('button ' + text);
    if ((node.type === 'input' || node.type === 'select') && !label && node.props.type !== 'checkbox') bad.push((node.type + ' ' + (node.props.placeholder || '')));
    if (node.type === 'input' && node.props.type === 'checkbox' && !label) bad.push('checkbox');
    if (node.children) walk(node.children);
  })(x.section({ mediaBridge: x.controller, t: key => key }));
  assert.deepEqual(bad, [], 'unlabelled controls: ' + bad.join(', '));
});
test('models card collapses per provider and expansion never dirties the draft', () => {
  const x = setup(ready({ models: { 'minimax-plan': { synthesize: ['mm-1'] }, xiaomi: { synthesize: ['mi-1'] } } }));
  const flat = () => JSON.stringify(x.section({ mediaBridge: x.controller, t: key => key }), (k, v) => typeof v === 'function' ? 'fn' : v);
  const bodies = tree => (tree.match(/"className":"mb-prov-body"/g) || []).length;
  const steps = tree => (tree.match(/"className":"mb-step"/g) || []).length;

  const closed = flat();
  const baseSteps = steps(closed);
  assert.equal(bodies(closed), 0, 'no provider body may render while collapsed');
  assert.ok(!closed.includes('addModel'), 'collapsed card must not expose add-model rows');
  assert.ok(closed.includes('modelsCount'), 'collapsed header should summarise the model count');

  x.controller.toggleProvider('minimax-plan');
  const one = flat();
  assert.equal(bodies(one), 1, 'only the clicked provider expands');
  assert.ok(one.includes('addModel'), 'expanded provider shows its editing rows');
  assert.equal(steps(one), baseSteps + 1, 'one extra model row appears: minimax-plan has a single configured model');

  x.controller.expandAll(true);
  assert.equal(bodies(flat()), 6, 'expand all opens every provider');
  assert.equal(steps(flat()), baseSteps + 2, 'both configured models become rows');
  assert.equal(x.controller.allExpanded(), true);

  x.controller.expandAll(false);
  assert.equal(bodies(flat()), 0, 'collapse all closes everything again');
  assert.equal(x.controller.allExpanded(), false);

  assert.equal(x.controller.dirty(), false, 'expansion is a view preference, not a config change');
  assert.equal(x.calls.length, 0, 'expansion must not write config');
});



test('voice controls show the official Chinese names and keep free text for Qwen', () => {
  const x = setup(ready({ voices: { 'qwen-plan': 'longanhuan_v3.6', 'xiaomi': '冰糖' } }));
  const nodes = [];
  (function walk(node) {
    if (Array.isArray(node)) { node.forEach(walk); return; }
    if (!node || typeof node !== 'object') return;
    nodes.push(node);
    if (node.children) walk(node.children);
  })(x.section({ mediaBridge: x.controller, t: key => key }));
  const text = JSON.stringify(nodes, (k, v) => typeof v === 'function' ? 'fn' : v);
  // MiniMax lists vendor Chinese names, not raw ids, in the zh dictionary
  assert.ok(text.includes('少女音色'), 'MiniMax Chinese voice name missing');
  assert.ok(text.includes('沉稳高管'), 'MiniMax Mandarin voice name missing');
  assert.ok(text.includes('专业女主持'), 'MiniMax Cantonese voice name missing');
  // MiMo presets are offered with their documented ids
  assert.ok(text.includes('冰糖'), 'MiMo voice list missing');
  assert.ok(text.includes('白桦'), 'MiMo male voice missing');
  // Qwen rows carry both a preset select and a free-text id box
  const selects = nodes.filter(n => n.type === 'select').length;
  const inputs = nodes.filter(n => n.type === 'input' && n.props.type !== 'checkbox' && n.props.type !== 'password').length;
  assert.ok(selects >= 4, 'expected preset dropdowns, got ' + selects);
  assert.ok(inputs >= 2, 'expected typeable voice ids for the Qwen families, got ' + inputs);
});

test('Qwen voice lists follow the configured model family and endpoint', () => {
  const render = snap => {
    const x = setup(ready(snap));
    const text = JSON.stringify(x.section({ mediaBridge: x.controller, t: key => key }), (k, v) => typeof v === 'function' ? 'fn' : v);
    return { x, text };
  };
  const plain = render({ voices: { 'qwen-plan': 'longanlingxin' } });
  assert.ok(plain.text.includes('龙安灵心'), 'the plan endpoint shows its audio-tts system voices');
  assert.ok(plain.text.includes('3.0-tts-plus'), 'each entry names the model it belongs to');
  // payg now defaults to the 3.1 family, whose voice ids differ from 3.0's
  assert.ok(plain.text.includes('longanhuan_v3.1'), 'the 3.1 voices must lead while 3.1 is the default');
  assert.ok(!plain.text.includes('阳光亲切'), 'qwen3-tts voices only appear when that model is selected');

  const qwen3 = render({ models: { 'qwen-payg': { synthesize: ['qwen3-tts-flash'] } } });
  assert.ok(qwen3.text.includes('阳光亲切'), 'selecting qwen3-tts swaps in its own voice list');

  const cosy = render({ models: { 'qwen-payg': { synthesize: ['cosyvoice-v3-flash'] } } });
  assert.ok(cosy.text.includes('龙安洋'), 'cosyvoice voices appear once that model is configured');
  assert.ok(!cosy.text.includes('阳光亲切'), 'the qwen3-tts list steps aside');

  const hints = plain.x.locales.flatMap(d => [d.zh, d.en].filter(Boolean).map(v => v.voiceHint || ''));
  assert.equal(hints.length, 2, 'both dictionaries should carry the voice hint');
  assert.ok(hints.every(h => h.includes('target_model')), 'the model-binding warning must be in every language');
});

test('budget card renders all three tiers and edits emit a budget op', async () => {
  const x = setup(ready({ budget: { daily_limit_cny: 10, provider_limit_cny: {}, model_limit_cny: {} } }));
  const nodes = [];
  (function walk(node) {
    if (Array.isArray(node)) { node.forEach(walk); return; }
    if (!node || typeof node !== 'object') return;
    nodes.push(node);
    if (node.children) walk(node.children);
  })(x.section({ mediaBridge: x.controller, t: key => key }));
  const numbers = nodes.filter(n => n.type === 'input' && n.props.type === 'number');
  assert.ok(numbers.length >= 8, 'expected a daily field plus per-provider and per-model rows, got ' + numbers.length);
  assert.ok(numbers.some(n => n.props.value === '10'), 'the daily cap should show the configured 10');
  x.controller.setBudgetLimit('provider_limit_cny', 'minimax-payg', '2');
  x.controller.setBudgetLimit('model_limit_cny', 'xiaomi:mimo-v2.5-tts', '0');
  x.controller.setBudgetDaily('7.5');
  await x.controller.save();
  assert.equal(x.calls.length, 1);
  const budget = x.calls[0][0].find(op => op.path[0] === 'budget');
  assert.ok(budget, 'a changed cap must be submitted');
  assert.equal(budget.value.daily_limit_cny, 7.5);
  assert.equal(budget.value.provider_limit_cny['minimax-payg'], 2);
  assert.equal(budget.value.model_limit_cny['xiaomi:mimo-v2.5-tts'], 0, '0 must survive as "closed"');
  // blank means unlimited: the key disappears from the draft rather than becoming 0
  x.controller.setBudgetLimit('provider_limit_cny', 'minimax-payg', '');
  assert.equal('minimax-payg' in x.controller.draft.budget.provider_limit_cny, false,
    'clearing a cap must remove it, not store zero (zero means closed)');
});
