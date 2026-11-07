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
      const request = { url, options, settled: false, aborted: false, resolve: body => resolve(reply(body)),
                        resolveResponse: response => resolve(response) };
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
const chapter = {
  id: 'outline-1', project_id: 'a', task_id: 'outline-task', directory_task_id: 'directory-1', chapter_index: 0,
  kind: 'chapter_outline', commit: 'a'.repeat(40), execution_mode: 'real', review_status: 'current',
  evidence: [{ evidence_id: 'e-1', path: 'src/Example.java', start_line: 1, end_line: 2 }],
  content: { title: '查询章', objective: '核对入口', sections: [{ id: 'entry', title: '入口与参数', objective: '阅读参数与边界' }], gaps: ['SQL 未读'] },
};
const body = {
  ...chapter, id: 'section-1', parent_outline_id: chapter.id, kind: 'section_explanation', section_id: 'entry',
  content: { title: '入口与参数', blocks: [{ heading: '<img src=x onerror=alert(1)>', text: '<script>unsafe()</script>',
    support: 'fact', evidence_ids: ['e-1'] }], gaps: ['运行尚未证明'] },
};
const descendants = element => [element, ...element.children.flatMap(descendants)];

(async () => {
  await flush();
  evaluate('selectedProject = {id: "a", commit: "a".repeat(40)}; selectingProject = false; learningAvailable = true; sourceState = {status: "verified"}; learningState = {query_enabled: false, execution_mode: "real"};');
  context.directory = { ...record('directory-1', 'a', 'completed', 'real'), result: { kind: 'directory', overview: 'fixture',
    chapters: [{ title: '查询入口', summary: 'fixture', kind: 'backend', support: 'candidate', entries: [] }], gaps: ['fixture'] } };
  evaluate('renderDirectory(directory)');
  const open = descendants(elements.get('directory-chapters')).find(item => item.textContent === '打开查询章节');
  assert.ok(open && !open.disabled); open.listeners.click[0]();
  assert.equal(evaluate('learningSelection.directory_task_id'), 'directory-1');
  const generate = descendants(elements.get('learning-content')).find(item => item.textContent === '生成查询章结构（调用模型）');
  assert.ok(generate && generate.disabled);
  assert.equal(pending.some(item => item.options.method === 'POST'), false);
  console.log('PASS: chapter opens without model request; directory-only permission leaves generation disabled');

  context.chapter = chapter; context.body = body;
  evaluate('learningRecords = [chapter, body]; renderLearning()');
  const rendered = descendants(elements.get('learning-content'));
  assert.ok(rendered.some(item => item.textContent === '<script>unsafe()</script>'));
  assert.ok(rendered.some(item => item.textContent === '<img src=x onerror=alert(1)>'));
  assert.equal(rendered.some(item => item.innerHTML), false);
  assert.equal(rendered.some(item => /生成本节/.test(item.textContent || '')), false);
  assert.equal(pending.some(item => item.options.method === 'POST'), false);
  evaluate('savedEvidence = [{id: "e-1", path: "src/Example.java", start_line: 1, end_line: 2, text: "class Example {}", commit: "a".repeat(40)}]');
  const citation = rendered.find(item => item.textContent === '已读片段：src/Example.java · 1–2行');
  assert.ok(rendered.some(item => item.textContent === '此记录未保存本块精确选行；下方仅为已读片段范围。'));
  await citation.listeners.click[0]();
  assert.equal(elements.get('snippet-text').textContent, 'class Example {}');
  assert.equal(pending.some(item => item.url.includes('/tools/')), false);
  console.log('PASS: saved body renders text safely and opens saved evidence without source or model calls');

  evaluate('savedEvidence=[]; sourceState={status:"unverified"}');
  const oldCitation=citation.listeners.click[0]();
  resolveRequest('/api/projects/a/evidence/e-1',{id:'e-1',path:'src/Example.java',start_line:1,end_line:2,
    text:'saved historical source',commit:'a'.repeat(40),status:'needs_review'});
  await oldCitation;
  assert.equal(elements.get('snippet-text').textContent,'saved historical source');
  assert.equal(elements.get('snippet-status').textContent,'历史片段 · 需复核');
  assert.equal(evaluate('savedEvidence.length'),1);
  assert.equal(pending.some(item=>item.url==='/api/projects/a/evidence' && !item.settled),false);
  assert.equal(pending.some(item=>item.url.includes('/tools/') || item.options.method==='POST'),false);
  console.log('PASS: old citations use scoped saved-detail GET even with unverified source; no first-page loop or new read/model');

  evaluate('savedEvidence=[]');
  const staleCitation=citation.listeners.click[0]();
  evaluate('selectedProject={id:"b",commit:"b".repeat(40)};sourceGeneration++');
  resolveRequest('/api/projects/a/evidence/e-1',{id:'e-1',text:'must never cross project',commit:'a'.repeat(40)});
  await staleCitation;
  assert.equal(elements.get('snippet-text').textContent,'saved historical source');
  assert.equal(evaluate('savedEvidence.length'),0);
  evaluate('selectedProject={id:"a",commit:"a".repeat(40)};sourceGeneration++;sourceState={status:"verified"};learningRecordId=chapter.id');

  const slowCitation=citation.listeners.click[0]();
  const latestCitation=citation.listeners.click[0]();
  const slowRequest=nextRequest('/api/projects/a/evidence/e-1'); slowRequest.settled=true;
  resolveRequest('/api/projects/a/evidence/e-1',{id:'e-1',path:'src/Example.java',start_line:1,end_line:2,
    text:'newest citation response',commit:'a'.repeat(40)});
  await latestCitation;
  slowRequest.resolve({id:'e-1',text:'stale citation response',commit:'a'.repeat(40)});
  await slowCitation;
  assert.equal(elements.get('snippet-text').textContent,'newest citation response');
  assert.equal(evaluate('savedEvidence.length'),1);
  console.log('PASS: delayed citation details cannot cross projects or replace the latest click');

  evaluate('savedEvidence=[]');
  const legacyCitation=citation.listeners.click[0]();
  const missingDetail=nextRequest('/api/projects/a/evidence/e-1'); missingDetail.settled=true;
  missingDetail.resolveResponse({status:404,ok:false,json:async()=>({detail:'Not Found'})});
  await flush();
  resolveRequest('/api/projects/a/evidence?limit=50&offset=0',Array.from({length:50},(_,index)=>({id:`new-${index}`})));
  await flush();
  resolveRequest('/api/projects/a/evidence?limit=50&offset=50',[{id:'e-1',path:'src/Example.java',start_line:1,end_line:2,
    text:'legacy backend saved citation',commit:'a'.repeat(40),status:'needs_review'}]);
  await legacyCitation;
  assert.equal(elements.get('snippet-text').textContent,'legacy backend saved citation');
  assert.equal(evaluate('savedEvidence.length'),1);
  assert.equal(pending.some(item=>item.url.includes('/tools/') || item.options.method==='POST'),false);
  console.log('PASS: older backends use bounded saved-page fallback without a restart, source reads or paid calls');

  evaluate('readingAvailable=true;readingMarks=[];learningRecords=[chapter,body];learningSelection=null;learningRecordId=chapter.id;sourceState={status:"unverified"};renderLearning()');
  const savePosition=descendants(elements.get('learning-content')).find(item=>item.textContent==='保存阅读位置（免费）');
  assert.ok(savePosition && !savePosition.disabled);
  const savePositionPromise=savePosition.listeners.click[0]();
  const positionRequest=nextRequest('/api/projects/a/learning/progress','PUT');
  assert.equal(JSON.parse(positionRequest.options.body).action,'viewed');
  assert.equal(JSON.parse(positionRequest.options.body).content_id,'section-1');
  const position={project_id:'a',content_id:'section-1',parent_outline_id:'outline-1',section_id:'entry',
    kind:'section_explanation',viewed:true,self_reported_understood:false,independent_acceptance:false,review_status:'current'};
  resolveRequest('/api/projects/a/learning/progress',position,'PUT'); await savePositionPromise;
  assert.equal(evaluate('readingMarks[0].self_reported_understood'),false);
  const resumePosition=descendants(elements.get('learning-records')).find(item=>item.textContent==='继续上次阅读（免费）');
  assert.ok(resumePosition);resumePosition.listeners.click[0]();
  assert.equal(evaluate('learningRecordId'),'outline-1');
  assert.equal(descendants(elements.get('learning-content')).find(item=>item.dataset.sectionId==='entry').open,true);
  const selfReport=descendants(elements.get('learning-content')).find(item=>item.textContent==='标记本人已懂（自报，免费）');
  const selfReportPromise=selfReport.listeners.click[0]();
  resolveRequest('/api/projects/a/learning/progress',{...position,self_reported_understood:true},'PUT'); await selfReportPromise;
  assert.equal(evaluate('readingMarks[0].independent_acceptance'),false);
  assert.ok(descendants(elements.get('learning-content')).some(item=>item.textContent==='本人自报已懂 · 独立验收未完成'));
  assert.equal(pending.some(item=>item.url.includes('/tools/') || item.options.method==='POST'),false);
  console.log('PASS: reading position resumes saved section for free; explicit self-report never becomes independent acceptance');

  const stalePositionLoad=evaluate('loadReadingProgress(sourceContext())');
  evaluate('selectedProject={id:"b",commit:"b".repeat(40)};sourceGeneration++;resetLearningView()');
  resolveRequest('/api/projects/a/learning/progress',[position]); await stalePositionLoad;
  assert.equal(evaluate('readingMarks.length'),0); assert.equal(evaluate('readingAvailable'),false);
  evaluate('selectedProject={id:"a",commit:"a".repeat(40)};sourceGeneration++;learningRecords=[chapter];readingAvailable=true;renderLearning()');
  const oldPositionLoad=evaluate('loadReadingProgress(sourceContext())');
  const newPositionLoad=evaluate('loadReadingProgress(sourceContext())');
  const oldPositionResponse=nextRequest('/api/projects/a/learning/progress');oldPositionResponse.settled=true;
  resolveRequest('/api/projects/a/learning/progress',[position]);await newPositionLoad;
  oldPositionResponse.resolve([]);await oldPositionLoad;
  assert.equal(evaluate('readingMarks.length'),1);
  const staleMark=evaluate('markReading("section-1","entry","viewed")');
  evaluate('selectedProject={id:"b",commit:"b".repeat(40)};sourceGeneration++;resetLearningView()');
  resolveRequest('/api/projects/a/learning/progress',position,'PUT');await staleMark;
  assert.equal(evaluate('readingMarks.length'),0);
  evaluate('selectedProject={id:"a",commit:"a".repeat(40)};sourceGeneration++;sourceState={status:"verified"};learningRecordId=chapter.id');
  console.log('PASS: delayed reading loads and saves cannot cross projects or overwrite the newest position');

  evaluate('learningRecords = [chapter]; learningState = {query_enabled: true, execution_mode: "real"}; tasks = []; taskActionBusy = false; renderLearning()');
  let sectionButton = descendants(elements.get('learning-content')).find(item => /生成本节/.test(item.textContent || ''));
  assert.equal(sectionButton.disabled, false);
  evaluate('sourceState = {status: "unverified"}; updateLearningControls()'); assert.equal(sectionButton.disabled, true);
  evaluate('sourceState = {status: "verified"}; tasks = [{status: "running"}]; updateLearningControls()'); assert.equal(sectionButton.disabled, true);
  evaluate('tasks = []; learningRecords = [{...chapter, review_status: "needs_review"}]; renderLearning()');
  sectionButton = descendants(elements.get('learning-content')).find(item => /生成本节/.test(item.textContent || ''));
  assert.equal(sectionButton.disabled, true);
  console.log('PASS: missing verification, active tasks and old content prevent new paid generation');

  evaluate('learningRecords = []; learningState = null; learningSelection = null; learningRecordId = null');
  const stale = evaluate('loadLearning(sourceContext())');
  evaluate('resetLearningView(); selectedProject = {id: "b", commit: "b".repeat(40)}; sourceGeneration++');
  resolveRequest('/api/projects/a/learning/status', {query_enabled: true, execution_mode: 'real'});
  resolveRequest('/api/projects/a/learning', [chapter]); await stale;
  assert.equal(evaluate('learningRecords.length'), 0); assert.equal(evaluate('learningState'), null);
  console.log('PASS: delayed learning responses cannot cross projects or enable generation');

  evaluate('selectedProject = {id: "a", commit: "a".repeat(40)}; learningRecords = []; learningState = {query_enabled: true, execution_mode: "real"}; sourceState = {status: "verified"}; taskActionBusy = false; tasks = []');
  const loading = evaluate('loadLearning(sourceContext())');
  resolveRequest('/api/projects/a/learning/status', {query_enabled: true, execution_mode: 'real'});
  resolveRequest('/api/projects/a/learning', [{...chapter, execution_mode: 'scripted'}, {...body, project_id: 'b'}]); await loading;
  assert.equal(evaluate('learningRecords.length'), 0);
  console.log('PASS: scripted and foreign products do not appear as learner prose');

  const starting = evaluate('startLearning("outline", {directory_task_id: "directory-1", chapter_index: 0})');
  const submitted = nextRequest('/api/projects/a/learning/outline', 'POST');
  assert.deepEqual(JSON.parse(submitted.options.body), {directory_task_id: 'directory-1', chapter_index: 0});
  evaluate('resetTaskView(); selectedProject = {id: "b", commit: "b".repeat(40)}; sourceGeneration++');
  resolveRequest('/api/projects/a/learning/outline', {...record('late-task', 'a', 'queued', 'real'), kind: 'chapter_outline'}, 'POST');
  await starting; assert.equal(evaluate('tasks.length'), 0); assert.equal(evaluate('currentTask'), null);
  assert.equal(evaluate('taskActionBusy'), false);
  console.log('PASS: late paid-start response cannot overwrite a new project or revive its task');
  evaluate('selectedProject={id:"a",commit:"a".repeat(40)}; selectingProject=false; sourceState={status:"verified"}; learningState={query_enabled:true,submission_enabled:false,execution_mode:"real"}; learningRecords=[chapter]; tasks=[]; taskActionBusy=false; renderDirectory(directory)');
  const submissionOpen=descendants(elements.get('directory-chapters')).find(item=>item.textContent==='打开提交主题（待查证）');
  assert.ok(submissionOpen); submissionOpen.listeners.click[0]();
  assert.equal(evaluate('learningSelection.topic'),'submission');
  const submissionGenerate=descendants(elements.get('learning-content')).find(item=>item.textContent==='生成提交章结构（调用模型）');
  assert.ok(submissionGenerate && submissionGenerate.disabled);
  assert.equal(descendants(elements.get('learning-content')).some(item=>item.textContent===chapter.content.title),false,'Query outline must not substitute for same-index submission topic');
  evaluate('learningState.submission_enabled=true; updateLearningControls()');
  assert.equal(submissionGenerate.disabled,false);
  console.log('PASS: topic discovery is unverified navigation; query permission cannot enable submission or substitute its outline');

  evaluate('learningState={query_enabled:true,submission_enabled:true,consumption_enabled:false,execution_mode:"real"}; learningRecords=[chapter]; renderDirectory(directory)');
  const consumptionOpen=descendants(elements.get('directory-chapters')).find(item=>item.textContent==='打开消息消费主题（待查证）');
  assert.ok(consumptionOpen); consumptionOpen.listeners.click[0]();
  assert.equal(evaluate('learningSelection.topic'),'consumption');
  const consumptionGenerate=descendants(elements.get('learning-content')).find(item=>item.textContent==='生成消息消费章结构（调用模型）');
  assert.ok(consumptionGenerate && consumptionGenerate.disabled);
  assert.equal(descendants(elements.get('learning-content')).some(item=>item.textContent===chapter.content.title),false);
  assert.equal(pending.some(item=>item.options.method==='POST' && !item.settled),false);
  evaluate('learningState.consumption_enabled=true; updateLearningControls()');
  assert.equal(consumptionGenerate.disabled,false);
  evaluate('learningState.consumption_enabled=false; updateLearningControls()');
  assert.equal(consumptionGenerate.disabled,true);
  context.workerDirectory={...context.directory,result:{...context.directory.result,
    chapters:[...context.directory.result.chapters,{title:'判题 Worker 模块',summary:'fixture',kind:'backend',support:'candidate',entries:[]}]}};
  evaluate('renderDirectory(workerDirectory)');
  const workerConsumptionOpen=descendants(elements.get('directory-chapters')).find(item=>item.textContent==='打开消息消费主题（待查证）');
  workerConsumptionOpen.listeners.click[0]();
  assert.equal(evaluate('learningSelection.chapter_index'),1);
  assert.equal(evaluate('learningSelection.topic'),'consumption');
  console.log('PASS: consumption navigation is free; query/submission permissions cannot enable or substitute consumption');

  evaluate('learningState={query_enabled:true,submission_enabled:true,consumption_enabled:true,judging_enabled:false,execution_mode:"real"}; renderDirectory(workerDirectory)');
  const judgingOpen=descendants(elements.get('directory-chapters')).find(item=>item.textContent==='打开判题主题（待查证）');
  assert.ok(judgingOpen); judgingOpen.listeners.click[0]();
  assert.equal(evaluate('learningSelection.chapter_index'),1);
  assert.equal(evaluate('learningSelection.topic'),'judging');
  const judgingGenerate=descendants(elements.get('learning-content')).find(item=>item.textContent==='生成判题章结构（调用模型）');
  assert.ok(judgingGenerate && judgingGenerate.disabled);
  assert.equal(pending.some(item=>item.options.method==='POST' && !item.settled),false);
  evaluate('learningState.judging_enabled=true; updateLearningControls()');
  assert.equal(judgingGenerate.disabled,false);
  evaluate('learningState.judging_enabled=false; updateLearningControls()');
  assert.equal(judgingGenerate.disabled,true);
  const judgingChapter={...chapter,topic:'judging',content:{...chapter.content,topic:'judging'}};
  context.judgingChapter=judgingChapter;
  evaluate('learningRecords=[judgingChapter]; learningSelection=null; learningRecordId=judgingChapter.id; renderLearning()');
  const judgingBodyGenerate=descendants(elements.get('learning-content')).find(item=>item.textContent==='生成本节讲解（调用模型）');
  assert.ok(judgingBodyGenerate && judgingBodyGenerate.disabled);
  evaluate('learningState.judging_enabled=true; updateLearningControls()');
  assert.equal(judgingBodyGenerate.disabled,false);
  console.log('PASS: judging outline/body follow their own purpose; other flags never authorize judging or create a task on navigation');

  evaluate('learningSelection={directory_task_id:"directory-1",chapter_index:0}; learningRecordId=chapter.id; learningRecords=[chapter]; agentTasksAvailable=true; renderLearning()');
  const viewDirectory=descendants(elements.get('learning-content')).find(item=>item.textContent==='查看关联目录（免费）');
  assert.ok(viewDirectory);
  const freeView=viewDirectory.listeners.click[0]();
  resolveRequest('/api/projects/a/tasks/directory-1',context.directory);
  await freeView;
  assert.equal(evaluate('currentTask.id'),'directory-1');
  assert.equal(elements.get('task-directory').hidden,false);
  assert.equal(pending.filter(item=>item.url==='/api/projects/a/tasks/directory-1').every(item=>(item.options.method||'GET')==='GET'),true);
  console.log('PASS: saved chapter opens its older linked directory with GET only, without regenerating');

  evaluate('selectedProject={id:"a",commit:"a".repeat(40)};sourceState={status:"verified"};taskActionBusy=false;tasks=[];learningState={query_enabled:true,submission_enabled:true,consumption_enabled:true,judging_enabled:true,execution_mode:"real"};learningRecordId=chapter.id;learningRecords=[chapter,body];renderLearning()');
  let followupButton=descendants(elements.get('learning-content')).find(item=>item.textContent==='提交本节追问（调用模型）');
  let questionInput=followupButton.questionInput;
  const paidBefore=pending.filter(item=>item.options.method==='POST').length;
  questionInput.value='入口参数为什么需要检查？';questionInput.listeners.input[0]();
  assert.equal(followupButton.disabled,true);
  await followupButton.listeners.click[0]();
  assert.equal(pending.filter(item=>item.options.method==='POST').length,paidBefore);
  evaluate('learningState.followup_enabled=true;updateLearningControls()');
  assert.equal(followupButton.disabled,false);
  evaluate('sourceState={status:"unverified"};updateLearningControls()');assert.equal(followupButton.disabled,true);
  evaluate('sourceState={status:"verified"};renderLearning()');
  followupButton=descendants(elements.get('learning-content')).find(item=>item.textContent==='提交本节追问（调用模型）');
  assert.equal(followupButton.questionInput.value,'入口参数为什么需要检查？');
  const followupStart=followupButton.listeners.click[0]();
  const submittedFollowup=nextRequest('/api/projects/a/learning/followup','POST');
  assert.deepEqual(JSON.parse(submittedFollowup.options.body),{body_id:body.id,question:'入口参数为什么需要检查？'});
  evaluate('resetTaskView();resetLearningView();selectedProject={id:"b",commit:"b".repeat(40)};sourceGeneration++');
  submittedFollowup.settled=true;submittedFollowup.resolve({...record('late-followup','a','running','real'),kind:'follow_up'});
  await followupStart;
  assert.equal(evaluate('tasks.length'),0);assert.equal(evaluate('followupDrafts.size'),0);
  console.log('PASS: composing follow-up is free; all old topic flags remain insufficient; explicit purpose and source gates apply; late start cannot cross projects');

  context.followupAnswer={...body,id:'followup-1',kind:'follow_up',parent_body_id:body.id,question:'<script>display-only</script>',content:{...body.content,kind:'follow_up'}};
  evaluate('selectedProject={id:"a",commit:"a".repeat(40)};sourceGeneration++;learningRecordId=chapter.id;learningRecords=[chapter,body,followupAnswer];learningState={execution_mode:"real",query_enabled:false,followup_enabled:false};renderLearning()');
  assert.ok(descendants(elements.get('learning-content')).some(item=>item.textContent==='已保存追问：<script>display-only</script>'));
  followupButton=descendants(elements.get('learning-content')).find(item=>item.textContent==='提交本节追问（调用模型）');
  assert.equal(followupButton.disabled,true);
  assert.equal(evaluate('learningRecords.find(item=>item.id===body.id).kind'),'section_explanation');
  console.log('PASS: saved follow-up questions and answers render as inert text without paid scope or replacing their original body');

  // Synthetic citation fixtures, never published or counted as model answers.
  const original = JSON.stringify(context.followupAnswer);
  const selectedBlock = {heading:'合成选行展示',text:'仅测试展示，不是源码语义证明。',support:'fact',
    evidence_ids:['e-1'],source_lines:[{evidence_id:'e-1',lines:[5,1,3]}]};
  context.citationFixture = {...body,kind:'follow_up',
    evidence:[{evidence_id:'e-1',path:'src/Example.java',start_line:1,end_line:20,truncated:false}],
    content:{blocks:[selectedBlock,{...selectedBlock,source_lines:[{evidence_id:'e-1',lines:[7]}]}],gaps:[]}};
  const renderCitations = () => {
    evaluate('renderExplanation(document.getElementById("citation-fixture"), citationFixture)');
    return descendants(elements.get('citation-fixture')).map(item=>item.textContent || '');
  };
  let labels = renderCitations();
  assert.ok(labels.includes('本块保存的选行：src/Example.java · 第1、3、5行'));
  assert.ok(labels.includes('本块保存的选行：src/Example.java · 第7行'));
  assert.equal(labels.some(label=>label.includes('第1–5行')),false);
  assert.equal(labels.filter(label=>label==='已读片段：src/Example.java · 1–20行').length,2);
  assert.deepEqual(selectedBlock.source_lines[0].lines,[5,1,3]);
  console.log('PASS: each block displays its own discrete saved lines, separate from the full read range, without mutating selections');

  context.citationFixture.content.blocks=[{...selectedBlock,source_lines:undefined,
    source_spans:[{evidence_id:'e-1',start_line:2,end_line:4}]}];
  elements.get('citation-fixture').replaceChildren();labels=renderCitations();
  assert.ok(labels.includes('本块历史选段：src/Example.java · 第2–4行'));
  assert.equal(labels.some(label=>label.startsWith('本块保存的选行：')),false);
  console.log('PASS: historical spans remain labelled as historical spans, never converted to new selections');

  for (const invalid of [
    [{evidence_id:'e-1',lines:[21]}], [{evidence_id:'e-1',lines:[0]}],
    [{evidence_id:'e-1',lines:['3']}], [{evidence_id:'e-1',lines:[1,1]}],
    [{evidence_id:'e-2',lines:[3]}], [{evidence_id:'e-1',lines:[]}],
    [{evidence_id:'e-1',lines:Array.from({length:21},(_,i)=>i+1)}],
    [null], '1,3', [{evidence_id:'e-1',lines:[3]},{evidence_id:'e-1',lines:[4]}],
  ]) {
    context.citationFixture.content.blocks=[{...selectedBlock,source_lines:invalid}];
    elements.get('citation-fixture').replaceChildren();labels=renderCitations();
    assert.ok(labels.some(label=>label.includes('无法准确展示')));
    assert.equal(labels.some(label=>label.startsWith('本块保存的选行：')),false);
  }
  for (const problem of ['mixed','truncated','missing','duplicate-reference']) {
    context.citationFixture.content.blocks=[{...selectedBlock}];
    context.citationFixture.evidence=[{evidence_id:'e-1',path:'src/Example.java',start_line:1,end_line:20,truncated:problem==='truncated'}];
    if(problem==='mixed') context.citationFixture.content.blocks[0].source_spans=[{evidence_id:'e-1',start_line:1,end_line:3}];
    if(problem==='missing') context.citationFixture.evidence=[];
    if(problem==='duplicate-reference') context.citationFixture.evidence.push({...context.citationFixture.evidence[0]});
    elements.get('citation-fixture').replaceChildren();labels=renderCitations();
    assert.ok(labels.some(label=>label.includes('无法准确展示')));
    assert.equal(labels.some(label=>label.startsWith('本块保存的选行：')),false);
  }
  assert.equal(JSON.stringify(context.followupAnswer),original);
  console.log('PASS: malformed, off-range, mixed, truncated or unmatched selections are not presented as exact citations; old answer unchanged');
})().catch(error => { console.error(error); process.exitCode = 1; });
