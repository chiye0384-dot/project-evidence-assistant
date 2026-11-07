// Deterministic task UI checks with synthetic records. These are not real-model
// quality measurements, browser acceptance, or permission to expose a mock UI.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

class Element {
  constructor() {
    this.children = []; this.dataset = {}; this.attributes = {}; this.listeners = {};
    this.classList = { toggle() {} };
  }
  setAttribute(key, value) { this.attributes[key] = value; }
  removeAttribute(key) { delete this.attributes[key]; }
  addEventListener(name, fn) { (this.listeners[name] ||= []).push(fn); }
  append(...items) { this.children.push(...items); }
  replaceChildren(...items) { this.children = items.flatMap(item => item.fragment ? item.children : [item]); }
  contains() { return false; }
  focus() {}
}

const elements = new Map();
const pending = [];
const timers = new Map();
let timerId = 0;
const document = {
  activeElement: null,
  getElementById(id) { if (!elements.has(id)) elements.set(id, new Element()); return elements.get(id); },
  createElement: () => new Element(),
  createDocumentFragment: () => Object.assign(new Element(), { fragment: true }),
};
const reply = body => ({ status: 200, ok: true, json: async () => body });
const context = vm.createContext({
  document, URL, Intl, AbortController,
  setTimeout(fn, delay) { const id = ++timerId; timers.set(id, { fn, delay }); return id; },
  clearTimeout(id) { timers.delete(id); },
  fetch(url, options = {}) {
    if (url === '/api/health') return Promise.resolve(reply({
      stage: 'M1-engine', model_status: 'not_configured',
      capabilities: { project_registry: true, source_tools: true, structure_facts: true, agent_tasks: true, source_analysis: false, agent_generation: false },
    }));
    if (url === '/api/projects') return Promise.resolve(reply([]));
    return new Promise((resolve, reject) => {
      const request = { url, options, settled: false, aborted: false, resolve: body => resolve(reply(body)) };
      pending.push(request);
      options.signal?.addEventListener('abort', () => {
        request.aborted = true;
        const error = new Error('aborted'); error.name = 'AbortError'; reject(error);
      }, { once: true });
    });
  },
});
vm.runInContext(fs.readFileSync(path.join(__dirname, '../static/app.js'), 'utf8'), context);
const evaluate = code => vm.runInContext(code, context);
const flush = () => new Promise(resolve => setImmediate(resolve));
const click = id => elements.get(id).listeners.click[0]();
const record = (id, projectId, status = 'completed', mode = 'scripted') => ({
  id, project_id: projectId, kind: 'directory', commit: 'a'.repeat(40), status,
  execution_mode: mode, counters: { model_calls: 1, tool_calls: 1 }, events: [], result: null,
  review_status: 'current', source_verified: true, message: `record ${id}`,
});
function nextRequest(url, method = 'GET') {
  const request = pending.find(item => !item.settled && item.url === url && (item.options.method || 'GET') === method);
  assert.ok(request, `Missing request ${method} ${url}`); return request;
}
function resolveRequest(url, body, method = 'GET') {
  const request = nextRequest(url, method); request.settled = true; request.resolve(body); return request;
}
async function finishSelection(id, tasks = []) {
  resolveRequest(`/api/projects/${id}`, { id, name: `${id} project`, source_path: `D:/projects/${id}`, public_url: 'https://github.com/test/public', commit: 'a'.repeat(40), created_at: '2026-10-13T00:00:00Z' });
  await flush();
  resolveRequest(`/api/projects/${id}/source/status`, { status: 'unverified', working_tree_changed: null });
  resolveRequest(`/api/projects/${id}/evidence`, []);
  resolveRequest(`/api/projects/${id}/tasks`, tasks);
}

(async () => {
  await flush();
  assert.equal(elements.get('task-engine-message').textContent, '模型未配置，不能启动新目录任务；已保存目录仍可浏览。');
  const selectingA = evaluate('selectProject("a")'); await finishSelection('a'); await selectingA;
  assert.equal(elements.get('start-task').disabled, false, 'Missing source verification must not hide model configuration feedback');
  const start = click('start-task');
  resolveRequest('/api/projects/a/tasks', { status: 'model_not_configured', message: '模型未配置；没有创建任务。' }, 'POST');
  await start;
  assert.equal(evaluate('tasks.length'), 0);
  assert.equal(evaluate('currentTask'), null);
  assert.equal(elements.get('task-feedback').textContent, '模型未配置；没有创建任务。');
  assert.equal(elements.get('task-directory').hidden, true);
  assert.equal(pending.some(item => item.url.includes('/source/verify')), false);
  console.log('PASS: unconfigured start is enabled without source verification and creates no fake record');

  const oldList = evaluate('loadTasks(sourceContext())');
  const newList = evaluate('loadTasks(sourceContext())');
  const oldRequest = nextRequest('/api/projects/a/tasks'); oldRequest.settled = true;
  const scripted = { ...record('new', 'a'), result: { overview: 'mock directory', chapters: [{ title: 'mock', evidence_ids: [] }] } };
  resolveRequest('/api/projects/a/tasks', [scripted]); await newList;
  oldRequest.resolve([record('old', 'a')]); assert.equal(await oldList, false);
  assert.equal(evaluate('tasks[0].id'), 'new');
  assert.equal(elements.get('task-directory').hidden, true, 'Scripted products are not real learning directories');

  const staleA = evaluate('loadTasks(sourceContext())');
  const staleRequest = nextRequest('/api/projects/a/tasks');
  const selectingB = evaluate('selectProject("b")'); await finishSelection('b'); await selectingB;
  assert.equal(staleRequest.aborted, true);
  staleRequest.settled = true; staleRequest.resolve([record('leak-a', 'a')]); await staleA;
  assert.equal(evaluate('selectedProject.id'), 'b');
  assert.equal(evaluate('tasks.length'), 0);
  assert.equal(elements.get('task-detail').hidden, true);
  console.log('PASS: delayed task lists cannot overwrite new history or cross project boundaries');

  const queued = record('task-b', 'b', 'queued');
  const startQueued = click('start-task'); resolveRequest('/api/projects/b/tasks', queued, 'POST'); await startQueued;
  assert.equal(elements.get('cancel-task').disabled, false);
  assert.equal(timers.size, 1);
  const [id, timer] = [...timers.entries()][0]; assert.equal(timer.delay, 1000); timers.delete(id);
  const polling = timer.fn();
  const stalePoll = nextRequest('/api/projects/b/tasks/task-b');
  const cancel = click('cancel-task'); assert.equal(stalePoll.aborted, true);
  resolveRequest('/api/projects/b/tasks/task-b/cancel', record('task-b', 'b', 'cancelled'), 'POST'); await cancel;
  stalePoll.settled = true; stalePoll.resolve(record('task-b', 'b', 'running')); await polling;
  assert.equal(evaluate('currentTask.status'), 'cancelled');
  assert.equal(elements.get('cancel-task').disabled, true);
  assert.equal(timers.size, 0);
  console.log('PASS: cancelling uses backend status and delayed polling cannot revive a cancelled task');

  const history = {
    ...record('real-history', 'b', 'completed', 'real'), review_status: 'needs_review',
    counters: { model_calls: 2, tool_calls: 3, retries: 0, context_chars: 1600, output_chars: 500, input_tokens: 640, input_tokens_estimated: true, output_tokens: 125, output_tokens_estimated: true, elapsed_seconds: 1.5 },
    events: [{ seq: 1, kind: 'tool_result', tool: 'find_files', status: 'ok', message: '得到文件线索', text: 'full snippet must not render' }, { seq: 2, kind: 'reasoning', message: 'internal thoughts must not render' }],
    result: { kind: 'directory', overview: '<b>plain overview</b>', chapters: [{ title: '<img src=x>', summary: '需要人工核对', modules: ['module'], entries: ['Controller.java'], kind: 'backend', support: 'candidate', evidence_ids: ['e-1'] }], gaps: ['还缺业务证据'] },
  };
  context.history = history; evaluate('rememberTask(history); renderTask(history)');
  assert.equal(elements.get('task-directory').hidden, false);
  assert.equal(elements.get('task-review-note').hidden, false);
  assert.equal(elements.get('directory-overview').textContent, '<b>plain overview</b>');
  assert.equal(elements.get('task-events').children.length, 1);
  assert.equal(elements.get('task-events').children[0].children[1].textContent, '得到文件线索');
  assert.match(elements.get('directory-review-status').textContent, /需复核/);
  assert.equal(elements.get('task-counters').children.some(group => group.children[0].textContent === '估算输出 Token' && group.children[1].textContent === '125'), true);
  assert.equal(elements.get('task-counters').children.some(group => group.children[0].textContent === '耗时（秒）' && group.children[1].textContent === '1.5'), true);
  assert.equal(elements.get('task-counters').children.some(group => group.children[0].textContent === '估算输入 Token' && group.children[1].textContent === '640'), true);
  assert.equal(elements.get('directory-chapters').children[0].children.some(item => typeof item.textContent === 'string' && item.textContent.includes('候选实现')), true);

  context.gapRecord = { ...record('real-gap', 'b', 'gap', 'real'), result: { kind: 'gap', message: '缺少源码依据', gaps: ['SQL 未定位'] } };
  evaluate('renderTask(gapRecord)');
  assert.equal(elements.get('task-directory').hidden, true);
  assert.equal(elements.get('task-outcome').hidden, false);
  assert.equal(elements.get('task-outcome').children[0].textContent, '已记录的证据缺口');
  assert.equal(elements.get('task-outcome').children[2].children[0].textContent, 'SQL 未定位');
  context.clarifyRecord = { ...record('real-clarify', 'b', 'needs_clarification', 'real'), result: { kind: 'clarify', question: '<b>你关注哪个入口？</b>', options: ['查询', '提交'], evidence_ids: ['e-1'] } };
  evaluate('renderTask(clarifyRecord)');
  assert.equal(elements.get('task-outcome').children[1].textContent, '<b>你关注哪个入口？</b>');
  assert.equal(elements.get('task-outcome').children.at(-1).textContent.includes('当前没有续答入口'), true);
  evaluate('renderTask({...gapRecord, execution_mode: "scripted"})');
  assert.equal(elements.get('task-outcome').hidden, true);
  assert.equal(elements.get('task-directory').hidden, true);
  console.log('PASS: real gap/clarification results remain distinct from directories and scripted products');

  const firstDetail = evaluate('selectTask("first-detail")');
  const firstRequest = nextRequest('/api/projects/b/tasks/first-detail');
  const secondDetail = evaluate('selectTask("second-detail")');
  assert.equal(firstRequest.aborted, true);
  resolveRequest('/api/projects/b/tasks/second-detail', record('second-detail', 'b')); await secondDetail;
  firstRequest.settled = true; firstRequest.resolve(record('first-detail', 'b')); await firstDetail;
  assert.equal(evaluate('currentTask.id'), 'second-detail');
  console.log('PASS: safe historical directories keep review markers, omit thoughts, and reject stale detail selections');

  const pageOne = Array.from({ length: 100 }, (_, i) => record(`page-${i}`, 'b'));
  const pageLoad = evaluate('loadTasks(sourceContext(), 0)');
  resolveRequest('/api/projects/b/tasks', pageOne); await pageLoad;
  assert.equal(elements.get('newer-tasks').disabled, true);
  assert.equal(elements.get('older-tasks').disabled, false);
  const older = click('older-tasks');
  assert.equal(elements.get('older-tasks').disabled, true, 'No duplicate page requests while loading');
  resolveRequest('/api/projects/b/tasks?offset=100', [record('old-page', 'b')]); await older;
  assert.equal(evaluate('taskPageOffset'), 100);
  assert.equal(elements.get('task-page').textContent, '第2页');
  assert.equal(elements.get('newer-tasks').disabled, false);
  assert.equal(elements.get('older-tasks').disabled, true);
  const oldPageDetail = evaluate('selectTask("old-page")');
  resolveRequest('/api/projects/b/tasks/old-page', record('old-page', 'b')); await oldPageDetail;
  assert.equal(evaluate('currentTask.id'), 'old-page');
  const stalePage = evaluate('loadTasks(sourceContext(), 100)');
  const stalePageRequest = nextRequest('/api/projects/b/tasks?offset=100');
  const newer = evaluate('loadTasks(sourceContext(), 0)');
  resolveRequest('/api/projects/b/tasks', pageOne); await newer;
  stalePageRequest.settled = true; stalePageRequest.resolve([record('late-old-page', 'b')]); await stalePage;
  assert.equal(evaluate('taskPageOffset'), 0);
  assert.equal(evaluate('tasks[0].id'), 'page-0');
  assert.match(elements.get('task-list-message').textContent, /本页 100 条/);
  console.log('PASS: bounded task pages retain old detail access and reject reversed page responses');

  const browseOldAgain = click('older-tasks');
  resolveRequest('/api/projects/b/tasks?offset=100', [record('old-page', 'b')]); await browseOldAgain;
  const createFromOldPage = click('start-task');
  const newlyCreated = record('new-after-history', 'b');
  resolveRequest('/api/projects/b/tasks', newlyCreated, 'POST'); await flush();
  resolveRequest('/api/projects/b/tasks', [newlyCreated, ...pageOne.slice(0, 99)]); await createFromOldPage;
  assert.equal(evaluate('taskPageOffset'), 0);
  assert.equal(evaluate('tasks[0].id'), 'new-after-history');
  assert.equal(evaluate('tasks.length'), 100);
  assert.equal(evaluate('tasks.some(task => task.id === "old-page")'), false);
  assert.equal(evaluate('currentTask.id'), 'new-after-history');
  console.log('PASS: creating from an older page reloads the latest page without mixing page contents');

  context.activeRecord = record('bounded', 'b', 'running');
  evaluate('rememberTask(activeRecord); renderTask(activeRecord); taskPollAttempts = MAX_TASK_POLLS; scheduleTaskPoll(sourceContext())');
  assert.equal(timers.size, 0);
  assert.match(elements.get('task-poll-status').textContent, /后台任务未因此取消/);
  assert.equal(elements.get('cancel-task').disabled, false);
  evaluate('renderDetail(null)');
  assert.equal(evaluate('tasks.length'), 0);
  assert.equal(elements.get('task-directory').hidden, true);
  assert.equal(timers.size, 0);
  assert.equal(pending.some(item => item.url.includes('/tools/')), false);
  console.log('PASS: polling is bounded and deletion/reset clears this project task view without source reads');

  evaluate('modelStatus = "configured"');
  const costs = evaluate('loadSpending()');
  resolveRequest('/api/model/status', { model_status: 'configured', spending: {
    status: 'ready', trial_limit_microyuan: 5000000, trial_remaining_microyuan: 4972000,
    total_limit_microyuan: 50000000, accounted_upper_bound_microyuan: 28000,
    api_key: 'synthetic-field-that-must-not-render',
  }});
  await costs;
  assert.equal(elements.get('spending-panel').hidden, false);
  assert.match(elements.get('spending-summary').textContent, /0\.028 元/);
  assert.match(elements.get('spending-summary').textContent, /4\.972 元/);
  assert.equal(elements.get('spending-summary').textContent.includes('synthetic-field'), false);
  assert.equal(evaluate('money(undefined)'), '未知');
  const staleCosts = evaluate('loadSpending()');
  evaluate('modelStatus = "not_configured"'); await evaluate('loadSpending()');
  resolveRequest('/api/model/status', { model_status: 'configured', spending: { status: 'ready' } });
  await staleCosts;
  assert.equal(elements.get('spending-panel').hidden, true);
  assert.equal(pending.filter(item => item.url === '/api/model/status').every(item => (item.options.method || 'GET') === 'GET'), true);
  console.log('PASS: fee figures use exact microyuan, unknown is not zero, and stale costs cannot enable a model');

  const againB = evaluate('selectProject("b")'); await finishSelection('b', pageOne); await againB;
  const staleOlder = evaluate('loadTasks(sourceContext(), 100)');
  const staleOlderRequest = nextRequest('/api/projects/b/tasks?offset=100');
  const selectC = evaluate('selectProject("c")'); await finishSelection('c'); await selectC;
  assert.equal(staleOlderRequest.aborted, true);
  staleOlderRequest.settled = true; staleOlderRequest.resolve([record('cross-project-old', 'b')]); await staleOlder;
  assert.equal(evaluate('taskPageOffset'), 0);
  assert.equal(evaluate('tasks.length'), 0);
  assert.equal(elements.get('task-page').textContent, '第1页');
  assert.equal(pending.filter(item => item.url.includes('offset=')).every(item => (item.options.method || 'GET') === 'GET'), true);
  console.log('PASS: project switch resets pagination and drops stale older-page responses without generation');
})().catch(error => { console.error(error); process.exitCode = 1; });
