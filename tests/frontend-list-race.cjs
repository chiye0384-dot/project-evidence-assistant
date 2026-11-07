// A deterministic request-order regression check. This is not browser acceptance.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

class Element {
  constructor() {
    this.children = [];
    this.dataset = {};
    this.attributes = {};
    this.listeners = {};
    this.classList = { toggle() {} };
  }
  setAttribute(name, value) { this.attributes[name] = value; }
  removeAttribute(name) { delete this.attributes[name]; }
  addEventListener(name, handler) { (this.listeners[name] ||= []).push(handler); }
  append(...elements) { this.children.push(...elements); }
  replaceChildren(...elements) { this.children = elements.flatMap(e => e.fragment ? e.children : [e]); }
  contains() { return false; }
  focus() {}
}

const elements = new Map();
const pending = [];
const servicePending = [];
const document = {
  activeElement: null,
  getElementById(id) {
    if (!elements.has(id)) elements.set(id, new Element());
    return elements.get(id);
  },
  createElement: () => new Element(),
  createDocumentFragment: () => Object.assign(new Element(), { fragment: true }),
};
const reply = payload => ({ status: 200, ok: true, json: async () => payload });
const context = vm.createContext({
  document, URL, Intl,
  fetch(url, options) {
    if (url === '/api/health') return Promise.resolve(reply({
      model_status: 'not_configured', stage: 'M0',
      capabilities: { project_registry: true, source_tools: true, structure_facts: true, source_analysis: false, agent_generation: false },
    }));
    if (url.startsWith('/api/projects/')) {
      return new Promise(resolve => servicePending.push({ url, options, resolve: payload => resolve(reply(payload)), settled: false }));
    }
    assert.equal(url, '/api/projects');
    return new Promise(resolve => pending.push(payload => resolve(reply(payload))));
  },
});
vm.runInContext(fs.readFileSync(path.join(__dirname, '../static/app.js'), 'utf8'), context);
const state = () => vm.runInContext('JSON.stringify(projects)', context);
const flush = () => new Promise(resolve => setImmediate(resolve));
function resolveService(url, payload) {
  const item = servicePending.find(item => item.url === url && !item.settled);
  assert.ok(item, `Expected pending request: ${url}`);
  item.settled = true;
  item.resolve(payload);
}
const project = id => ({ id, name: `${id} project`, source_path: `D:/projects/${id}`, public_url: 'https://github.com/test/public', commit: 'a'.repeat(40), created_at: '2026-10-12T12:00:00Z' });
async function finishSelection(id, source, evidence = []) {
  resolveService(`/api/projects/${id}`, project(id));
  await flush();
  resolveService(`/api/projects/${id}/source/status`, source);
  resolveService(`/api/projects/${id}/evidence`, evidence);
}

(async () => {
  // The initial refresh is delayed; the newest response must win.
  const newest = vm.runInContext('loadProjects()', context);
  pending[1]([{ id: 'new', name: 'New project' }]);
  assert.equal(await newest, true);
  pending[0]([]);
  await flush();
  assert.equal(state(), '[{"id":"new","name":"New project"}]');
  assert.equal(elements.get('project-count').textContent, '1');

  // A stale response must neither resurrect removed records nor clear busy state.
  const stale = vm.runInContext('loadProjects()', context);
  const empty = vm.runInContext('loadProjects()', context);
  pending[2]([{ id: 'deleted', name: 'Removed project' }]);
  assert.equal(await stale, false);
  assert.equal(elements.get('project-list').attributes['aria-busy'], 'true');
  pending[3]([]);
  assert.equal(await empty, true);
  assert.equal(state(), '[]');
  assert.equal(elements.get('project-count').textContent, '0');
  assert.equal(elements.get('project-list').attributes['aria-busy'], 'false');
  console.log('PASS: delayed list responses cannot overwrite newest records or loading state');

  // Selection reads only saved source status and evidence, not source content.
  const selectingA = vm.runInContext('selectProject("a")', context);
  await finishSelection('a', { status: 'verified', working_tree_changed: null });
  assert.equal(await selectingA, true);
  assert.equal(servicePending.some(item => item.url.endsWith('/structure')), false);
  assert.equal(elements.get('source-change-note').textContent.includes('没有检查工作区'), true);

  // A delayed verification may not populate structure after switching to B.
  const verifyA = elements.get('verify-source').listeners.click[0]();
  const selectingB = vm.runInContext('selectProject("b")', context);
  await finishSelection('b', { status: 'unverified', message: 'B needs manual verification', working_tree_changed: null });
  assert.equal(await selectingB, true);
  resolveService('/api/projects/a/source/verify', { status: 'verified', working_tree_changed: null });
  await verifyA;
  assert.equal(vm.runInContext('selectedProject.id', context), 'b');
  assert.equal(elements.get('source-description').textContent, 'B needs manual verification');
  assert.equal(servicePending.some(item => item.url === '/api/projects/a/structure'), false);
  assert.equal(elements.get('find-button').disabled, true);

  // A delayed read from B may not appear in A's evidence viewer.
  vm.runInContext('sourceState = {status: "verified"}; updateSourceControls()', context);
  const readB = vm.runInContext('runTool("read", {path: "B.java", start_line: 1, end_line: 2})', context);
  const nextA = vm.runInContext('selectProject("a")', context);
  await finishSelection('a', { status: 'verified', working_tree_changed: null });
  assert.equal(await nextA, true);
  resolveService('/api/projects/b/tools/read', { status: 'ok', path: 'B.java', start_line: 1, end_line: 2, text: 'B-only text', sha256: 'b'.repeat(64) });
  await readB;
  assert.equal(vm.runInContext('selectedProject.id', context), 'a');
  assert.equal(elements.get('snippet-text').textContent, '');
  assert.equal(elements.get('evidence-count').textContent, '0');
  assert.equal(elements.get('find-button').disabled, false);
  console.log('PASS: stale source verification and read responses cannot cross project boundaries');

  // Empty/out-of-range reads must not show null line numbers or claim a save.
  const emptyRead = vm.runInContext('runTool("read", {path: "Empty.java", start_line: 1, end_line: 2})', context);
  resolveService('/api/projects/a/tools/read', { status: 'no_results', path: 'Empty.java', start_line: null, end_line: null, text: '', message: '没有可读取的行' });
  await emptyRead;
  assert.equal(elements.get('evidence-viewer').hidden, true);
  assert.equal(elements.get('tool-result-status').textContent, '未保存片段：没有可读取的行');
  assert.equal(elements.get('snippet-text').textContent, '');

  // A valid read without a storage identifier is not a persisted evidence card.
  const unstored = vm.runInContext('runTool("read", {path: "A.java", start_line: 1, end_line: 1})', context);
  resolveService('/api/projects/a/tools/read', { status: 'ok', path: 'A.java', start_line: 1, end_line: 1, text: 'class A {}' });
  await unstored;
  assert.equal(elements.get('evidence-viewer').hidden, true);
  assert.match(elements.get('tool-result-status').textContent, /^未保存片段：未收到保存凭据/);

  const saved = { id: 'saved-a', path: 'A.java', start_line: 1, end_line: 1, text: '<script>plain source text</script>', commit: 'a'.repeat(40), sha256: 'c'.repeat(64), status: 'current' };
  const storedRead = vm.runInContext('runTool("read", {path: "A.java", start_line: 1, end_line: 1})', context);
  resolveService('/api/projects/a/tools/read', { ...saved, status: 'ok', evidence_id: 'saved-a' });
  await flush();
  resolveService('/api/projects/a/evidence', [saved]);
  await storedRead;
  assert.equal(elements.get('evidence-viewer').hidden, false);
  assert.equal(elements.get('snippet-text').textContent, saved.text);
  assert.equal(elements.get('evidence-count').textContent, '1');
  assert.match(elements.get('tool-result-status').textContent, /^已保存 A\.java/);
  console.log('PASS: no-result and unpersisted reads cannot be presented as saved evidence');

  // The UI accepts uppercase hex, while PATCH sends canonical lowercase hex.
  elements.get('version-commit').value = 'B'.repeat(40);
  const changedVersion = elements.get('version-form').listeners.submit[0]({ preventDefault() {} });
  const versionRequest = servicePending.find(item => item.url === '/api/projects/a/version' && !item.settled);
  assert.equal(JSON.parse(versionRequest.options.body).commit, 'b'.repeat(40));
  resolveService('/api/projects/a/version', { ...project('a'), commit: 'b'.repeat(40) });
  await flush();
  resolveService('/api/projects/a', { ...project('a'), commit: 'b'.repeat(40) });
  await flush();
  resolveService('/api/projects/a/source/status', { status: 'unverified', working_tree_changed: null });
  resolveService('/api/projects/a/evidence', [{ ...saved, status: 'needs_review' }]);
  await changedVersion;
  assert.equal(elements.get('find-button').disabled, true);
  assert.equal(elements.get('evidence-count').textContent, '1');
  console.log('PASS: version changes normalize commit hex and preserve historical evidence');
})().catch(error => { console.error(error); process.exitCode = 1; });
