"use strict";

const ui = {
  list: document.getElementById("project-list"),
  count: document.getElementById("project-count"),
  refresh: document.getElementById("refresh-button"),
  form: document.getElementById("register-form"),
  submit: document.getElementById("submit-button"),
  feedback: document.getElementById("feedback"),
  connection: document.getElementById("connection-status"),
  model: document.getElementById("model-status"),
  registryStatus: document.getElementById("registry-status"),
  learningStatus: document.getElementById("learning-status"),
  learningDescription: document.getElementById("learning-description"),
  detailTitle: document.getElementById("detail-title"),
  detailEmpty: document.getElementById("detail-empty"),
  detailContent: document.getElementById("detail-content"),
  analysisStatus: document.getElementById("analysis-status"),
  path: document.getElementById("detail-path"),
  url: document.getElementById("detail-url"),
  repositoryLink: document.getElementById("repository-link"),
  commit: document.getElementById("detail-commit"),
  created: document.getElementById("detail-created"),
  deleteButton: document.getElementById("delete-button"),
  dialog: document.getElementById("delete-dialog"),
  deleteName: document.getElementById("delete-project-name"),
  cancelDelete: document.getElementById("cancel-delete"),
  confirmDelete: document.getElementById("confirm-delete"),
  stage: document.getElementById("stage-label"),
  footerStage: document.getElementById("footer-stage"),
  detailSourceStatus: document.getElementById("detail-source-status"),
  sourceStatus: document.getElementById("source-status"),
  sourceWorkspace: document.getElementById("source-workspace"),
  sourceDescription: document.getElementById("source-description"),
  verify: document.getElementById("verify-source"),
  sourceMetadata: document.getElementById("source-metadata"),
  sourceFileCount: document.getElementById("source-file-count"),
  sourceVerifiedAt: document.getElementById("source-verified-at"),
  sourceHead: document.getElementById("source-head"),
  sourceLicense: document.getElementById("source-license"),
  sourceLicenseNote: document.getElementById("source-license-note"),
  sourceChangeNote: document.getElementById("source-change-note"),
  sourceLimitations: document.getElementById("source-limitations"),
  versionForm: document.getElementById("version-form"),
  versionCommit: document.getElementById("version-commit"),
  versionButton: document.getElementById("version-button"),
  structureMessage: document.getElementById("structure-message"),
  structureFacts: document.getElementById("structure-facts"),
  structureLimitations: document.getElementById("structure-limitations"),
  toolsStatus: document.getElementById("tools-status"),
  findForm: document.getElementById("find-form"),
  findPattern: document.getElementById("find-pattern"),
  findButton: document.getElementById("find-button"),
  searchForm: document.getElementById("search-form"),
  searchQuery: document.getElementById("search-query"),
  searchGlob: document.getElementById("search-glob"),
  searchButton: document.getElementById("search-button"),
  readForm: document.getElementById("read-form"),
  readPath: document.getElementById("read-path"),
  readStart: document.getElementById("read-start"),
  readEnd: document.getElementById("read-end"),
  readButton: document.getElementById("read-button"),
  toolResults: document.getElementById("tool-results"),
  toolResultStatus: document.getElementById("tool-result-status"),
  evidenceList: document.getElementById("evidence-list"),
  evidenceMessage: document.getElementById("evidence-message"),
  evidenceCount: document.getElementById("evidence-count"),
  evidenceViewer: document.getElementById("evidence-viewer"),
  snippetPath: document.getElementById("snippet-path"),
  snippetStatus: document.getElementById("snippet-status"),
  snippetLocation: document.getElementById("snippet-location"),
  snippetLines: document.getElementById("snippet-lines"),
  snippetText: document.getElementById("snippet-text"),
  snippetCommit: document.getElementById("snippet-commit"),
  snippetSha: document.getElementById("snippet-sha"),
  taskPanel: document.getElementById("task-panel"),
  taskEngineStatus: document.getElementById("task-engine-status"),
  taskEngineMessage: document.getElementById("task-engine-message"),
  spendingPanel: document.getElementById("spending-panel"),
  spendingSummary: document.getElementById("spending-summary"),
  spendingNotice: document.getElementById("spending-notice"),
  spendingRefresh: document.getElementById("refresh-spending"),
  taskStart: document.getElementById("start-task"),
  taskRefresh: document.getElementById("refresh-tasks"),
  taskFeedback: document.getElementById("task-feedback"),
  taskList: document.getElementById("task-list"),
  taskCount: document.getElementById("task-count"),
  taskListMessage: document.getElementById("task-list-message"),
  taskNewer: document.getElementById("newer-tasks"),
  taskOlder: document.getElementById("older-tasks"),
  taskPage: document.getElementById("task-page"),
  taskDetail: document.getElementById("task-detail"),
  taskDetailTitle: document.getElementById("task-detail-title"),
  taskStatus: document.getElementById("task-status"),
  taskMode: document.getElementById("task-mode"),
  taskMessage: document.getElementById("task-message"),
  taskReviewNote: document.getElementById("task-review-note"),
  taskOutcome: document.getElementById("task-outcome"),
  taskCounters: document.getElementById("task-counters"),
  taskPollStatus: document.getElementById("task-poll-status"),
  taskCancel: document.getElementById("cancel-task"),
  taskEvents: document.getElementById("task-events"),
  taskEventsMessage: document.getElementById("task-events-message"),
  taskDirectory: document.getElementById("task-directory"),
  directoryReview: document.getElementById("directory-review-status"),
  directoryOverview: document.getElementById("directory-overview"),
  directoryChapters: document.getElementById("directory-chapters"),
  directoryGaps: document.getElementById("directory-gaps"),
  reader: document.getElementById("learning-reader"),
  readerStatus: document.getElementById("reader-status"),
  readerMessage: document.getElementById("reader-message"),
  readerFeedback: document.getElementById("reader-feedback"),
  learningRefresh: document.getElementById("refresh-learning"),
  learningRecords: document.getElementById("learning-records"),
  learningContent: document.getElementById("learning-content"),
};

let projects = [];
let selectedProject = null;
let detailRequest = 0;
let listRequest = 0;
let deleteTarget = null;
let deleting = false;
let registryAvailable = false;
let saving = false;
let sourceToolsAvailable = false;
let structureAvailable = false;
let sourceGeneration = 0;
let citationRequest = 0;
let sourceState = null;
let selectingProject = false;
let sourceBusy = false;
let toolBusy = false;
let savedEvidence = [];
let activeEvidence = null;
let agentTasksAvailable = false;
let agentEngineSeen = false;
let modelStatus = "unknown";
let tasks = [];
let taskPageOffset = 0;
let taskPageLoading = false;
let currentTask = null;
let selectedTaskId = null;
let taskListRequest = 0;
let taskDetailRequest = 0;
let taskActionRequest = 0;
let taskActionBusy = false;
let taskPollTimer = null;
let taskPollAttempts = 0;
let taskPollFailures = 0;
let taskDetailController = null;
const taskControllers = new Set();
const MAX_TASK_POLLS = 120;
let learningAvailable = false;
let learningState = null;
let learningRecords = [];
let learningSelection = null;
let learningRecordId = null;
let learningRequest = 0;
let learningButtons = [];
let readingMarks = [];
let readingAvailable = false;
let readingRequest = 0;
let readingActionRequest = 0;
let followupDrafts = new Map();

function showFeedback(message, isError = false) {
  ui.feedback.textContent = message;
  ui.feedback.classList.toggle("error", isError);
  ui.feedback.setAttribute("role", isError ? "alert" : "status");
  ui.feedback.setAttribute("aria-live", isError ? "assertive" : "polite");
  ui.feedback.hidden = false;
}

function errorMessage(payload, status) {
  if (typeof payload?.detail === "string") return payload.detail;
  if (typeof payload?.detail?.message === "string") return payload.detail.message;
  if (typeof payload?.detail?.reason === "string") return payload.detail.reason;
  if (Array.isArray(payload?.detail)) {
    return payload.detail.map((item) => typeof item?.msg === "string" ? item.msg : "登记内容格式有误").join("；");
  }
  if (typeof payload?.message === "string") return payload.message;
  if (status === 404) return "这条记录已不存在，请刷新项目列表。";
  if (status === 409) return "项目登记发生冲突，请核对后重试。";
  return "操作未完成，请稍后重试。";
}

async function request(path, options = {}) {
  let response;
  try {
    response = await fetch(path, { ...options, headers: { "Accept": "application/json", ...options.headers }, cache: "no-store" });
  } catch {
    if (options.signal?.aborted) {
      const error = new Error("请求已取消");
      error.name = "AbortError";
      throw error;
    }
    throw new Error("无法连接本机后台，请确认服务正在运行后重试。");
  }
  if (response.status === 204) return null;
  const payload = await response.json().catch(() => null);
  if (!response.ok) {
    const error = new Error(errorMessage(payload, response.status));
    error.httpStatus = response.status;
    error.code = payload?.detail?.code || payload?.code || payload?.status;
    throw error;
  }
  if (payload === null) throw new Error("后台返回了无法读取的结果，请重试。");
  return payload;
}

function listMessage(message) {
  const element = document.createElement("p");
  element.className = "list-message";
  element.textContent = message;
  ui.list.replaceChildren(element);
}

function renderList() {
  const activeProjectId = ui.list.contains(document.activeElement) ? document.activeElement.dataset.projectId : null;
  ui.count.textContent = String(projects.length);
  if (!projects.length) {
    listMessage("这里还没有项目，先登记一个学习对象。");
    return;
  }
  const fragment = document.createDocumentFragment();
  for (const project of projects) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "project-item";
    button.dataset.projectId = project.id;
    button.setAttribute("aria-pressed", String(selectedProject?.id === project.id));
    const icon = document.createElement("span");
    icon.className = "project-item-icon";
    icon.setAttribute("aria-hidden", "true");
    icon.textContent = "▤";
    const copy = document.createElement("span");
    copy.className = "project-item-copy";
    const name = document.createElement("span");
    name.className = "project-item-name";
    name.textContent = project.name;
    const status = document.createElement("span");
    status.className = "project-item-status";
    status.textContent = project.source_status === "verified" ? "公开版本已核对 · 查看学习记录" : "已登记 · 待核对公开版本";
    copy.append(name, status);
    button.append(icon, copy);
    button.addEventListener("click", () => selectProject(project.id));
    fragment.append(button);
  }
  ui.list.replaceChildren(fragment);
  if (activeProjectId) {
    const activeButton = Array.from(ui.list.children).find((button) => button.dataset.projectId === activeProjectId);
    activeButton?.focus();
  }
}

function repositoryUrl(value) {
  try {
    const url = new URL(value);
    return url.protocol === "https:" && url.hostname === "github.com" && !url.username && !url.password && !url.port ? url.href : null;
  } catch { return null; }
}

function formatDate(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "登记时间不可用";
  return new Intl.DateTimeFormat("zh-CN", { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }).format(date);
}

function renderDetail(project) {
  selectedProject = project;
  ui.detailEmpty.hidden = Boolean(project);
  ui.detailContent.hidden = !project;
  ui.analysisStatus.hidden = !project;
  ui.detailTitle.textContent = project ? project.name : "选择你的学习对象";
  ui.sourceWorkspace.hidden = !project;
  if (project) {
    ui.path.textContent = project.source_path;
    ui.url.textContent = project.public_url;
    ui.commit.textContent = project.commit;
    ui.created.textContent = formatDate(project.created_at);
    const url = repositoryUrl(project.public_url);
    ui.repositoryLink.hidden = !url;
    if (url) ui.repositoryLink.href = url;
    else ui.repositoryLink.removeAttribute("href");
  }
  if (!project) resetSourceView("请选择项目，再核对公开版本。");
  renderList();
}

async function selectProject(id) {
  const currentRequest = ++detailRequest;
  const generation = ++sourceGeneration;
  selectingProject = true;
  resetSourceView("正在切换项目并读取已保存状态…");
  ui.detailContent.setAttribute("aria-busy", "true");
  try {
    const project = await request(`/api/projects/${encodeURIComponent(id)}`);
    if (currentRequest !== detailRequest) return false;
    renderDetail(project);
    selectingProject = false;
    updateSourceControls();
    await Promise.all([loadSourceStatus({ id, generation }), loadEvidence({ id, generation }), agentTasksAvailable ? loadTasks({ id, generation }) : Promise.resolve(false), learningAvailable ? loadLearning({ id, generation }) : Promise.resolve(false)]);
    return true;
  } catch (error) {
    if (currentRequest === detailRequest) showFeedback(error.message, true);
    return false;
  } finally {
    if (currentRequest === detailRequest) {
      selectingProject = false;
      ui.detailContent.setAttribute("aria-busy", "false");
      updateSourceControls();
    }
  }
}

function updateRegistryControls() {
  ui.submit.disabled = !registryAvailable || saving;
  ui.deleteButton.disabled = !registryAvailable;
  updateSourceControls();
  updateTaskControls();
}

async function loadHealth() {
  try {
    const health = await request("/api/health");
    ui.connection.textContent = "后台已连接";
    ui.connection.className = "connection connected";
    ui.model.textContent = ({ not_configured: "模型未配置", scripted_test: "编排测试模式 · 无真实模型", configured: "模型已配置 · 结果待评测" })[health.model_status] || "模型状态未确认";
    modelStatus = health.model_status || "unknown";
    agentTasksAvailable = health.capabilities?.agent_tasks === true;
    learningAvailable = health.capabilities?.learning_records === true;
    ui.reader.hidden = !learningAvailable;
    agentEngineSeen = agentEngineSeen || agentTasksAvailable;
    ui.taskPanel.hidden = !agentEngineSeen;
    registryAvailable = health.capabilities?.project_registry === true;
    sourceToolsAvailable = health.capabilities?.source_tools === true;
    structureAvailable = health.capabilities?.structure_facts === true;
    const stage = learningAvailable ? "M2 · 后端章节工程" : health.stage === "M1-engine" ? "M1 · 调用循环" : health.stage === "M0" ? "M0 · 公开源码查阅" : health.stage === "M-1" ? "M-1 · 项目登记" : "阶段未确认";
    ui.stage.textContent = stage;
    ui.footerStage.textContent = `当前阶段：${stage}`;
    ui.registryStatus.textContent = registryAvailable ? "登记信息只保存在本机" : "后台未开放项目登记";
    const sourceAnalysis = health.capabilities?.source_analysis;
    const agentGeneration = health.capabilities?.agent_generation;
    if (sourceAnalysis === false && agentGeneration === false) {
      ui.learningStatus.textContent = agentTasksAvailable && modelStatus === "not_configured" ? "模型未配置" : "尚未开放";
      ui.learningDescription.textContent = agentTasksAvailable && modelStatus === "not_configured" ? "模型未配置，不能启动新生成任务；已有目录和正文仍可浏览。结构事实与源码片段不是智能正文。" : sourceToolsAvailable ? "当前可手动核对并查阅公开源码；生成内容请查看任务及章节记录。结构事实与源码片段不是智能正文。" : "当前版本只管理项目登记。尚未扫描源码，也未生成项目地图、章节或讲解。";
    } else {
      ui.learningStatus.textContent = learningAvailable ? "查看后端章节" : "查看目录任务";
      ui.learningDescription.textContent = learningAvailable ? "目录中选择主题，先查看小节结构，再逐节查证。保存内容与追问结果可免费阅读；新的正文追问需要本机独立启用，填写问题不会自动调用模型。" : "已开放受控源码查阅与目录任务。生成结果见任务记录；按章节阅读、正文与追问尚待实现。";
    }
    updateRegistryControls();
    void loadSpending();
    return true;
  } catch {
    ui.connection.textContent = "后台未连接";
    ui.connection.className = "connection disconnected";
    ui.model.textContent = "模型状态未获取";
    registryAvailable = false;
    agentTasksAvailable = false;
    learningAvailable = false;
    learningState = null;
    modelStatus = "unknown";
    sourceToolsAvailable = false;
    structureAvailable = false;
    ui.stage.textContent = "阶段未获取";
    ui.footerStage.textContent = "当前阶段未获取";
    ui.registryStatus.textContent = "请连接本机后台后刷新";
    ui.learningStatus.textContent = "状态未获取";
    ui.learningDescription.textContent = "暂时无法确认后台能力。请检查本机服务并刷新；这里尚未加载学习内容。";
    updateRegistryControls();
    void loadSpending();
    return false;
  }
}

async function loadProjects() {
  const currentRequest = ++listRequest;
  ui.list.setAttribute("aria-busy", "true");
  try {
    const result = await request("/api/projects");
    if (currentRequest !== listRequest) return false;
    if (!Array.isArray(result)) throw new Error("项目列表格式异常，请稍后重试。");
    projects = result;
    renderList();
    const retained = projects.find((project) => project.id === selectedProject?.id);
    if (selectedProject && !retained) {
      ++detailRequest;
      ++sourceGeneration;
      renderDetail(null);
    }
    if (retained && !await selectProject(retained.id)) return false;
    return true;
  } catch (error) {
    if (currentRequest !== listRequest) return false;
    listMessage("暂时无法读取项目记录，请点击刷新重试。");
    showFeedback(error.message, true);
    return false;
  } finally {
    if (currentRequest === listRequest) ui.list.setAttribute("aria-busy", "false");
  }
}

async function refresh(showMessage = false) {
  ui.refresh.disabled = true;
  try {
    const [healthy, loaded] = await Promise.all([loadHealth(), loadProjects()]);
    if (showMessage && healthy && loaded) showFeedback("本机项目记录与后台状态已刷新。");
  } finally { ui.refresh.disabled = false; }
}

ui.refresh.addEventListener("click", () => refresh(true));

ui.form.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!registryAvailable || saving) return;
  const formData = new FormData(ui.form);
  const payload = Object.fromEntries(["name", "source_path", "public_url", "commit"].map((key) => [key, String(formData.get(key) || "").trim()]));
  if (!payload.name || !payload.source_path) {
    showFeedback("请填写项目名称与本地项目路径。", true);
    return;
  }
  if (!/^https:\/\/github\.com\/[^\s/?#]+\/[^\s/?#]+\/?$/i.test(payload.public_url)) {
    showFeedback("请填写公开 GitHub 仓库地址，例如 https://github.com/用户名/仓库名。", true);
    document.getElementById("public-url").focus();
    return;
  }
  if (!/^[a-fA-F0-9]{40}$/.test(payload.commit)) {
    showFeedback("请填写由数字和 a–f 组成的 40 位完整提交编号。", true);
    document.getElementById("commit").focus();
    return;
  }
  saving = true;
  updateRegistryControls();
  ui.submit.textContent = "正在保存…";
  ui.form.setAttribute("aria-busy", "true");
  try {
    const project = await request("/api/projects", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
    ++listRequest;
    ++detailRequest;
    ++sourceGeneration;
    ui.form.reset();
    const loaded = await loadProjects();
    if (loaded) {
      const selected = await selectProject(project.id);
      if (selected) showFeedback(`已保存「${project.name}」的登记信息。源码尚未分析。`);
      else showFeedback(`「${project.name}」已保存，但详情暂时无法读取，请刷新后重试。`, true);
    } else showFeedback(`「${project.name}」已保存，但列表暂未刷新成功，请点击刷新。`, true);
  } catch (error) { showFeedback(error.message, true); }
  finally {
    saving = false;
    updateRegistryControls();
    ui.submit.textContent = "保存登记 →";
    ui.form.setAttribute("aria-busy", "false");
  }
});

ui.deleteButton.addEventListener("click", () => {
  if (!selectedProject) return;
  deleteTarget = selectedProject;
  ui.deleteName.textContent = selectedProject.name;
  ui.dialog.showModal();
  ui.cancelDelete.focus();
});

ui.cancelDelete.addEventListener("click", () => ui.dialog.close());
ui.dialog.addEventListener("cancel", (event) => { if (deleting) event.preventDefault(); });
ui.dialog.addEventListener("close", () => { deleteTarget = null; });

ui.confirmDelete.addEventListener("click", async () => {
  if (!deleteTarget || deleting) return;
  const project = deleteTarget;
  deleting = true;
  ui.confirmDelete.disabled = true;
  ui.cancelDelete.disabled = true;
  ui.confirmDelete.textContent = "正在删除…";
  try {
    await request(`/api/projects/${encodeURIComponent(project.id)}`, { method: "DELETE" });
    ++listRequest;
    ++detailRequest;
    ++sourceGeneration;
    renderDetail(null);
    ui.dialog.close();
    const loaded = await loadProjects();
    if (loaded) showFeedback(`已删除「${project.name}」的助手记录，原项目源码保持不变。`);
    else showFeedback("助手记录已删除，但列表暂未刷新成功，请点击刷新。", true);
    ui.refresh.focus();
  } catch (error) {
    ui.dialog.close();
    showFeedback(error.message, true);
    ui.deleteButton.focus();
  } finally {
    deleting = false;
    ui.confirmDelete.disabled = false;
    ui.cancelDelete.disabled = false;
    ui.confirmDelete.textContent = "删除助手记录";
  }
});

function sourceContext() {
  return { id: selectedProject?.id, generation: sourceGeneration };
}

function isCurrentSource(context) {
  return Boolean(context.id) && context.id === selectedProject?.id && context.generation === sourceGeneration && !selectingProject;
}

function projectApi(context, suffix) {
  return `/api/projects/${encodeURIComponent(context.id)}/${suffix}`;
}

function updateSourceControls() {
  const selected = Boolean(selectedProject) && !selectingProject;
  const idle = !sourceBusy && !toolBusy;
  const verified = selected && sourceToolsAvailable && sourceState?.status === "verified";
  ui.verify.disabled = !selected || !sourceToolsAvailable || !idle;
  ui.versionButton.disabled = !selected || !registryAvailable || !idle;
  ui.versionCommit.disabled = !selected || !registryAvailable || !idle;
  for (const element of [ui.findPattern, ui.searchQuery, ui.searchGlob, ui.readPath, ui.readStart, ui.readEnd, ui.findButton, ui.searchButton, ui.readButton]) {
    element.disabled = !verified || !idle;
  }
  if (!selected) ui.toolsStatus.textContent = selectingProject ? "正在切换项目，源码工具暂不可用。" : "请选择项目，再手动核对公开版本。";
  else if (!sourceToolsAvailable) ui.toolsStatus.textContent = "后台尚未开放源码工具，当前不能读取。";
  else if (!verified) ui.toolsStatus.textContent = sourceState?.message || "尚未核对公开版本，源码工具不可用。已保存的历史证据仍可查看。";
  else if (sourceBusy) ui.toolsStatus.textContent = "正在核对版本并读取结构，请稍候。";
  else if (toolBusy) ui.toolsStatus.textContent = "正在查阅公开版本的源码，请稍候。";
  else ui.toolsStatus.textContent = "核对已通过，可按需查找、搜索和保存获准公开版本的片段。";
}

function setLimitations(element, values) {
  element.replaceChildren();
  const limitations = Array.isArray(values) ? values.filter((value) => typeof value === "string") : [];
  element.hidden = !limitations.length;
  for (const text of limitations) {
    const item = document.createElement("li");
    item.textContent = text;
    element.append(item);
  }
}

function resetSourceView(message) {
  resetTaskView();
  sourceState = null;
  sourceBusy = false;
  toolBusy = false;
  savedEvidence = [];
  activeEvidence = null;
  ui.sourceStatus.textContent = selectingProject ? "读取状态中" : "待核对";
  ui.sourceDescription.textContent = message;
  ui.detailSourceStatus.textContent = "公开版本尚未核对";
  ui.sourceMetadata.hidden = true;
  ui.sourceLicenseNote.hidden = true;
  ui.sourceChangeNote.hidden = true;
  ui.verify.textContent = "核对公开版本并读取结构";
  ui.versionCommit.value = "";
  setLimitations(ui.sourceLimitations, []);
  ui.structureFacts.replaceChildren();
  ui.structureMessage.textContent = "手动核对公开版本后，读取结构事实。";
  setLimitations(ui.structureLimitations, []);
  ui.toolResults.replaceChildren();
  ui.toolResultStatus.hidden = true;
  ui.readPath.value = "";
  ui.readStart.value = "1";
  ui.readEnd.value = "80";
  ui.evidenceList.replaceChildren();
  ui.evidenceCount.textContent = "0";
  ui.evidenceMessage.textContent = selectingProject ? "正在读取本项目已保存的证据…" : "选择项目后查看已保存证据。";
  ui.evidenceViewer.hidden = true;
  ui.snippetText.textContent = "";
  ui.snippetLines.textContent = "";
  updateSourceControls();
}

function applySourceState(source) {
  sourceState = source;
  const verified = source?.status === "verified";
  ui.sourceStatus.textContent = verified ? "公开版本已核对" : "待重新核对";
  ui.sourceDescription.textContent = source?.message || (verified ? "公开固定版本已核对。结构与工具只读取允许范围内的内容。核对会过期，届时需再次手动核对。" : "尚未完成有效核对。请点击按钮核对公开版本；不会自动联网或读取源码。");
  ui.detailSourceStatus.textContent = verified ? "公开固定版本已核对，正文尚未生成" : "公开版本核对未生效，源码工具不可用";
  ui.sourceMetadata.hidden = !verified;
  ui.sourceFileCount.textContent = Number.isInteger(source?.file_count) ? `${source.file_count} 个` : "未提供数量";
  ui.sourceVerifiedAt.textContent = source?.verified_at ? formatDate(source.verified_at) : "尚未核对";
  ui.sourceHead.textContent = source?.head_commit || "未读取";
  ui.sourceLicense.textContent = source?.license_spdx || "未提供";
  ui.sourceLicenseNote.hidden = !source?.license_path || !source?.license_sha256;
  ui.sourceLicenseNote.textContent = source?.license_path && source?.license_sha256 ? `固定版许可来源已在本机保留：${source.license_path}（含内容摘要）。上方声明来自 GitHub 当前仓库元数据，不代表固定提交的许可已经相同。` : "";
  ui.sourceChangeNote.hidden = !source;
  ui.sourceChangeNote.textContent = source?.working_tree_changed === true ? "本地当前提交或暂存记录与固定公开版本不同。工具只对应公开固定提交；未提交改动不读取，历史证据需复核。" : "工具只对应公开固定提交；未提交改动不读取。当前没有检查工作区文件，不能据此判断本地是否无修改。";
  // Source verification is independent of the model configuration shown by health.
  const sourceLimitations = Array.isArray(source?.limitations) ? source.limitations.map(value =>
    value === "不读取未提交或未跟踪文件；未配置模型。" ? "不读取未提交或未跟踪文件。" : value) : [];
  setLimitations(ui.sourceLimitations, sourceLimitations);
  if (selectedProject) {
    projects = projects.map((project) => project.id === selectedProject.id ? { ...project, source_status: source?.status || "unverified" } : project);
    renderList();
  }
  updateSourceControls();
}

async function loadSourceStatus(context) {
  try {
    const source = await request(projectApi(context, "source/status"));
    if (!isCurrentSource(context)) return false;
    applySourceState(source);
    return true;
  } catch (error) {
    if (!isCurrentSource(context)) return false;
    sourceState = null;
    ui.sourceStatus.textContent = "核对状态未获取";
    ui.sourceDescription.textContent = error.message;
    ui.detailSourceStatus.textContent = "核对状态未获取，源码工具不可用";
    updateSourceControls();
    return false;
  }
}

function chooseReadPath(path, line = 1) {
  ui.readPath.value = path;
  const start = Math.max(1, Number(line) - 3);
  ui.readStart.value = String(start);
  ui.readEnd.value = String(start + 79);
  ui.readPath.focus();
}

function renderStructure(result) {
  ui.structureFacts.replaceChildren();
  const facts = Array.isArray(result.facts) ? result.facts : [];
  ui.structureMessage.textContent = facts.length ? `取得 ${facts.length} 条结构事实${result.truncated ? "，结果已截断" : ""}。点击来源可填入片段路径，不会自动读取。` : "没有提取到结构事实。请查看限制说明，或使用查找工具定位文件。";
  for (const fact of facts) {
    const item = document.createElement("div");
    item.className = "structure-fact";
    const title = document.createElement("h3");
    const labels = { pom: "Maven 配置", module_name: "模块名称候选", module_declaration: "模块声明", boot_entry: "启动入口候选", controller_mapping: "接口映射候选" };
    title.textContent = fact.label || `${labels[fact.kind] || "结构线索"}${typeof fact.value === "string" ? ` · ${fact.value}` : ""}`;
    const startLine = fact.start_line || fact.line || 1;
    const endLine = fact.end_line || startLine;
    const location = document.createElement("button");
    location.type = "button";
    location.className = "fact-location mono";
    location.textContent = `${fact.path} · L${startLine}–${endLine}`;
    location.addEventListener("click", () => chooseReadPath(fact.path, startLine));
    item.append(title, location);
    const text = fact.text ?? fact.excerpt;
    if (typeof text === "string" && text) {
      const details = document.createElement("details");
      const summary = document.createElement("summary");
      summary.textContent = "查看事实对应的源码片段";
      const code = document.createElement("pre");
      code.className = "fact-code mono";
      code.textContent = text;
      details.append(summary, code);
      item.append(details);
    }
    ui.structureFacts.append(item);
  }
  setLimitations(ui.structureLimitations, result.limitations);
}

ui.verify.addEventListener("click", async () => {
  const context = sourceContext();
  if (!isCurrentSource(context) || !sourceToolsAvailable || sourceBusy || toolBusy) return;
  sourceBusy = true;
  ui.verify.textContent = "正在核对并读取…";
  ui.sourceDescription.textContent = "正在核对 GitHub 的公开仓库与固定提交元数据，不上传本地源码。";
  updateSourceControls();
  try {
    const source = await request(projectApi(context, "source/verify"), { method: "POST" });
    if (!isCurrentSource(context)) return;
    applySourceState(source);
    if (source.status !== "verified") {
      showFeedback(source.message || "公开版本核对未通过，源码工具不可用。", true);
      return;
    }
    if (structureAvailable) {
      ui.structureMessage.textContent = "版本已核对，正在从本机读取获准结构…";
      const structure = await request(projectApi(context, "structure"));
      if (!isCurrentSource(context)) return;
      if (structure.status !== "ok") throw new Error(structure.message || "结构事实读取未完成。");
      renderStructure(structure);
    } else ui.structureMessage.textContent = "公开版本已核对，但后台尚未开放结构读取。";
    await loadEvidence(context);
    if (isCurrentSource(context)) showFeedback(structureAvailable ? "公开版本核对与结构读取已完成。尚未生成学习目录或正文。" : "公开版本已核对。结构读取暂未开放，尚未生成学习目录或正文。");
  } catch (error) {
    if (!isCurrentSource(context)) return;
    ui.structureMessage.textContent = "本次核对或结构读取未完成，不能视为结构分析成功。";
    showFeedback(error.message, true);
    await Promise.all([loadSourceStatus(context), loadEvidence(context)]);
  } finally {
    if (isCurrentSource(context)) {
      sourceBusy = false;
      ui.verify.textContent = "核对公开版本并读取结构";
      updateSourceControls();
    }
  }
});

ui.versionForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const context = sourceContext();
  if (!isCurrentSource(context) || !registryAvailable || sourceBusy || toolBusy) return;
  const commit = ui.versionCommit.value.trim().toLowerCase();
  if (!/^[a-fA-F0-9]{40}$/.test(commit)) return showFeedback("新版本需要 40 位完整公开提交编号。", true);
  if (commit.toLowerCase() === selectedProject.commit.toLowerCase()) return showFeedback("这已经是助手选定的版本，无需更换。");
  sourceBusy = true;
  updateSourceControls();
  try {
    await request(projectApi(context, "version"), { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ commit }) });
    if (!isCurrentSource(context)) return;
    const selected = await selectProject(context.id);
    if (selected && selectedProject?.id === context.id && !selectingProject) showFeedback("已更换助手选定版本。原项目没有修改；历史证据保留并需复核，请重新手动核对公开版本。");
  } catch (error) {
    if (isCurrentSource(context)) showFeedback(error.message, true);
  } finally {
    if (isCurrentSource(context)) { sourceBusy = false; updateSourceControls(); }
  }
});

function renderToolResults(result, kind) {
  ui.toolResults.replaceChildren();
  ui.toolResultStatus.hidden = false;
  const items = Array.isArray(result.items) ? result.items : [];
  ui.toolResultStatus.textContent = `${items.length ? `找到 ${items.length} 项；点击结果填入读取位置` : "未找到匹配结果"}${result.truncated ? "。返回数量已截断，请缩小查找范围。" : "。"}`;
  for (const item of items) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "tool-result";
    const path = document.createElement("span");
    path.className = "mono result-path";
    path.textContent = `${item.path}${kind === "search" ? ` · L${item.line}` : ""}`;
    button.append(path);
    if (kind === "search") {
      const text = document.createElement("span");
      text.className = "result-text mono";
      text.textContent = item.text;
      button.append(text);
    }
    button.addEventListener("click", () => chooseReadPath(item.path, item.line || 1));
    ui.toolResults.append(button);
  }
}

async function runTool(kind, payload) {
  const context = sourceContext();
  if (!isCurrentSource(context) || !sourceToolsAvailable || sourceState?.status !== "verified" || sourceBusy || toolBusy) return;
  toolBusy = true;
  ui.toolResultStatus.hidden = false;
  ui.toolResultStatus.textContent = "正在查阅获准公开源码…";
  ui.toolResults.replaceChildren();
  updateSourceControls();
  try {
    const result = await request(projectApi(context, `tools/${kind}`), { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
    if (!isCurrentSource(context)) return;
    if (!["ok", "no_results"].includes(result.status)) {
      const error = new Error(result.message || "查阅未完成，请核对输入或重新核对版本。");
      error.code = result.status;
      throw error;
    }
    if (kind === "read") {
      const text = result.text ?? result.excerpt;
      const hasSnippet = typeof text === "string" && text.length > 0 && Number.isInteger(result.start_line) && Number.isInteger(result.end_line) && result.start_line > 0 && result.end_line >= result.start_line;
      if (result.status !== "ok" || !result.evidence_id || !hasSnippet) {
        ui.toolResultStatus.textContent = `未保存片段：${result.message || (!hasSnippet ? "没有取得有效行范围的内容，文件可能为空或起始行超过文件末尾。" : "未收到保存凭据，不能确认本次片段已保存。")}`;
        return;
      }
      renderSnippet({ ...result, id: result.evidence_id, text, status: "current" });
      ui.toolResultStatus.textContent = `已保存 ${result.path} 的第 ${result.start_line}–${result.end_line} 行${result.truncated ? "，内容已截断" : ""}。`;
      await loadEvidence(context);
    } else renderToolResults(result, kind);
  } catch (error) {
    if (!isCurrentSource(context)) return;
    ui.toolResultStatus.textContent = error.message;
    showFeedback(error.message, true);
    await Promise.all([loadSourceStatus(context), loadEvidence(context)]);
  } finally {
    if (isCurrentSource(context)) { toolBusy = false; updateSourceControls(); }
  }
}

ui.findForm.addEventListener("submit", (event) => {
  event.preventDefault();
  const pattern = ui.findPattern.value.trim();
  if (pattern) runTool("find", { pattern, limit: 30 });
});
ui.searchForm.addEventListener("submit", (event) => {
  event.preventDefault();
  const query = ui.searchQuery.value.trim();
  if (query) runTool("search", { query, path_glob: ui.searchGlob.value.trim() || "**/*", limit: 30 });
});
ui.readForm.addEventListener("submit", (event) => {
  event.preventDefault();
  const start = Number(ui.readStart.value);
  const end = Number(ui.readEnd.value);
  if (!Number.isInteger(start) || !Number.isInteger(end) || start < 1 || end < start || end - start >= 80) {
    return showFeedback("请指定正确行号，结束行不能早于起始行，单次最多读取 80 行。", true);
  }
  const path = ui.readPath.value.trim();
  if (path) runTool("read", { path, start_line: start, end_line: end });
});

function renderSnippet(evidence) {
  activeEvidence = evidence.id || null;
  ui.evidenceViewer.hidden = false;
  ui.snippetPath.textContent = evidence.path;
  ui.snippetStatus.textContent = evidence.status === "needs_review" ? "历史片段 · 需复核" : "已保存片段";
  ui.snippetStatus.className = evidence.status === "needs_review" ? "status-pill review" : "status-pill";
  ui.snippetLocation.textContent = `第 ${evidence.start_line}–${evidence.end_line} 行 · ${evidence.public_url || "公开仓库未提供"}${evidence.created_at ? ` · 保存于 ${formatDate(evidence.created_at)}` : ""}`;
  const text = evidence.text ?? evidence.excerpt ?? "";
  ui.snippetText.textContent = text;
  const textLineCount = typeof text === "string" ? text.split("\n").length : 0;
  const lineCount = Math.max(0, Math.min(evidence.end_line - evidence.start_line + 1, textLineCount));
  ui.snippetLines.textContent = Array.from({ length: lineCount }, (_, index) => evidence.start_line + index).join("\n");
  ui.snippetCommit.textContent = evidence.commit || "版本未提供";
  ui.snippetSha.textContent = evidence.sha256 || "摘要未提供";
}

function renderEvidence() {
  ui.evidenceList.replaceChildren();
  ui.evidenceCount.textContent = String(savedEvidence.length);
  const reviewCount = savedEvidence.filter((item) => item.status === "needs_review").length;
  ui.evidenceMessage.textContent = savedEvidence.length ? `${savedEvidence.length} 条本机证据${reviewCount ? `，其中 ${reviewCount} 条需复核` : ""}。点击查看当时保存的片段，不会重新读取源码。` : "还没有保存源码片段。使用上方工具读取后，会保留来源、行号和公开版本。";
  for (const evidence of savedEvidence) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "evidence-card";
    button.setAttribute("aria-pressed", String(activeEvidence === evidence.id));
    const path = document.createElement("span");
    path.className = "mono evidence-path";
    path.textContent = evidence.path;
    const meta = document.createElement("span");
    meta.className = "evidence-card-meta";
    meta.textContent = `L${evidence.start_line}–${evidence.end_line} · ${String(evidence.commit || "").slice(0, 8)}`;
    const status = document.createElement("span");
    status.className = evidence.status === "needs_review" ? "status-pill review" : "status-pill";
    status.textContent = evidence.status === "needs_review" ? "需复核 · 历史保留" : "当前版本";
    button.append(path, meta, status);
    button.addEventListener("click", () => { renderSnippet(evidence); renderEvidence(); });
    ui.evidenceList.append(button);
  }
}

async function loadEvidence(context) {
  try {
    const evidence = await request(projectApi(context, "evidence"));
    if (!isCurrentSource(context)) return false;
    if (!Array.isArray(evidence)) throw new Error("证据列表格式异常，请刷新后重试。");
    savedEvidence = evidence;
    if (activeEvidence) {
      const retained = evidence.find((item) => item.id === activeEvidence);
      if (retained) renderSnippet(retained);
      else { activeEvidence = null; ui.evidenceViewer.hidden = true; }
    }
    renderEvidence();
    return true;
  } catch (error) {
    if (!isCurrentSource(context)) return false;
    ui.evidenceMessage.textContent = `暂时无法读取本机证据：${error.message}`;
    return false;
  }
}

function taskIsActive(task) {
  return ["queued", "running"].includes(task?.status);
}

function taskStatusText(status) {
  return ({ queued: "排队中", running: "运行中", completed: "已结束 · 有结果", gap: "证据有缺口", needs_clarification: "需要澄清", denied: "已拒绝", no_progress: "无进展，已停止", cancelled: "已取消", budget_exhausted: "达到任务上限", timeout: "超过任务时限", failed: "任务失败", source_changed: "来源变化，已停止", unverified: "源码未核对", interrupted: "运行中断" })[status] || "状态未确认";
}

function updateTaskControls() {
  const selected = Boolean(selectedProject) && !selectingProject;
  ui.taskStart.disabled = !agentTasksAvailable || !selected || taskActionBusy || tasks.some(taskIsActive);
  ui.taskRefresh.disabled = !agentTasksAvailable || !selected || taskActionBusy;
  ui.taskNewer.disabled = !agentTasksAvailable || !selected || taskActionBusy || taskPageLoading || taskPageOffset === 0;
  ui.taskOlder.disabled = !agentTasksAvailable || !selected || taskActionBusy || taskPageLoading || tasks.length < 100 || taskPageOffset >= 900;
  ui.taskCancel.disabled = !agentTasksAvailable || !selected || taskActionBusy || !taskIsActive(currentTask);
  ui.taskEngineStatus.textContent = !agentTasksAvailable ? "状态未获取" : modelStatus === "not_configured" ? "模型未配置" : modelStatus === "scripted_test" ? "编排测试模式" : "调用循环入口";
  ui.taskEngineMessage.textContent = !agentTasksAvailable ? "无法确认目录任务入口，请连接本机后台后刷新。历史记录不会因此成为新生成内容。" : modelStatus === "not_configured" ? "模型未配置，不能启动新目录任务；已保存目录仍可浏览。" : modelStatus === "scripted_test" ? "调用循环处于编排检查模式；脚本结果不能作为真实模型目录或质量证据。" : "调用循环入口已开放；是否生成有效学习目录，以任务记录和后续评测为准。";
  if (!agentTasksAvailable) stopTaskPolling();
  updateLearningControls();
}

function showTaskFeedback(message, isError = false) {
  ui.taskFeedback.textContent = message;
  ui.taskFeedback.classList.toggle("error", isError);
  ui.taskFeedback.setAttribute("role", isError ? "alert" : "status");
  ui.taskFeedback.hidden = false;
}

function stopTaskPolling() {
  if (taskPollTimer !== null && typeof clearTimeout === "function") clearTimeout(taskPollTimer);
  taskPollTimer = null;
}

function resetTaskView() {
  resetLearningView();
  ++taskListRequest;
  ++taskDetailRequest;
  ++taskActionRequest;
  stopTaskPolling();
  for (const controller of taskControllers) controller.abort();
  taskControllers.clear();
  taskDetailController = null;
  tasks = [];
  taskPageOffset = 0;
  taskPageLoading = false;
  currentTask = null;
  selectedTaskId = null;
  taskActionBusy = false;
  taskPollAttempts = 0;
  taskPollFailures = 0;
  ui.taskList.replaceChildren();
  ui.taskEvents.replaceChildren();
  ui.directoryChapters.replaceChildren();
  ui.taskCount.textContent = "0";
  ui.taskPage.textContent = "第1页";
  ui.taskFeedback.hidden = true;
  ui.taskDetail.hidden = true;
  ui.taskOutcome.hidden = true;
  ui.taskOutcome.replaceChildren();
  ui.taskDirectory.hidden = true;
  ui.taskPollStatus.textContent = "";
  ui.taskListMessage.textContent = selectingProject ? "正在读取这个项目的本机任务记录…" : "选择项目后查看历史任务。";
  updateTaskControls();
}

async function taskRequest(path, options = {}, ownedController = null) {
  const controller = ownedController || (typeof AbortController === "function" ? new AbortController() : null);
  if (controller) taskControllers.add(controller);
  try { return await request(path, { ...options, ...(controller ? { signal: controller.signal } : {}) }); }
  finally { if (controller) taskControllers.delete(controller); }
}

function renderTaskList() {
  ui.taskList.replaceChildren();
  ui.taskCount.textContent = String(tasks.length);
  ui.taskPage.textContent = `第${Math.floor(taskPageOffset / 100) + 1}页`;
  ui.taskListMessage.textContent = tasks.length ? `本页 ${tasks.length} 条本机任务，每页最多100条。可翻页查看历史目录和行动记录，不会重新生成内容。` : taskPageOffset ? "这一页没有任务记录，请返回较新记录。" : "没有历史任务。模型未配置时，点击开始只会收到配置状态，不创建模拟任务。";
  for (const task of tasks) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "task-record";
    button.setAttribute("aria-pressed", String(selectedTaskId === task.id));
    button.disabled = taskActionBusy;
    const title = document.createElement("span");
    title.className = "task-record-title";
    title.textContent = `${taskKindText(task.kind, task.result?.topic || task.inputs?.topic)} · ${String(task.id).slice(0, 8)}`;
    const status = document.createElement("span");
    status.className = "task-record-status";
    status.textContent = `${taskStatusText(task.status)}${task.review_status === "needs_review" ? " · 需复核" : ""}`;
    const mode = document.createElement("span");
    mode.className = "field-help";
    mode.textContent = task.execution_mode === "scripted" ? "编排检查记录 · 不是真实模型" : task.execution_mode === "real" ? "真实模型任务记录 · 质量待评测" : "未配置模式记录";
    button.append(title, status, mode);
    button.addEventListener("click", () => selectTask(task.id));
    ui.taskList.append(button);
  }
}

function renderDirectory(task) {
  ui.taskDirectory.hidden = true;
  ui.directoryChapters.replaceChildren();
  setLimitations(ui.directoryGaps, []);
  // Scripted test products never become a product learning directory.
  if (task.execution_mode !== "real" || task.status !== "completed" || task.result?.kind !== "directory" || !Array.isArray(task.result.chapters)) return;
  ui.taskDirectory.hidden = false;
  ui.directoryReview.textContent = task.review_status === "needs_review" ? "历史版本 · 需复核" : "真实模型记录 · 待评测";
  ui.directoryOverview.textContent = typeof task.result.overview === "string" ? task.result.overview : "这条记录没有概览说明。";
  for (const [chapterIndex, chapter] of task.result.chapters.entries()) {
    const card = document.createElement("div");
    card.className = "directory-chapter";
    const title = document.createElement("h4");
    title.textContent = chapter.title || "未命名章节";
    const summary = document.createElement("p");
    summary.textContent = typeof chapter.summary === "string" ? chapter.summary : "未提供说明";
    card.append(title, summary);
    for (const [label, values] of [["相关模块", chapter.modules], ["入口线索", chapter.entries], ["关联证据编号", chapter.evidence_ids]]) {
      const textValues = Array.isArray(values) ? values.filter(value => typeof value === "string") : [];
      if (!textValues.length) continue;
      const meta = document.createElement("p");
      meta.className = label === "关联证据编号" ? "directory-evidence mono" : "directory-meta";
      meta.textContent = `${label}：${textValues.join("、")}`;
      card.append(meta);
    }
    if (typeof chapter.support === "string") {
      const support = document.createElement("p");
      support.className = "field-help";
      const supports = { fact: "源码事实", documentation: "文档说明", inference: "解释性推断", candidate: "候选实现", gap: "证据缺口" };
      support.textContent = `记录的支持状态：${supports[chapter.support] || "未识别的状态"}（尚未人工核对语义）`;
      card.append(support);
    }
    if (learningAvailable && chapter.kind === "backend" && /查询|详情/.test(chapter.title || "")) {
      const button = document.createElement("button");
      button.type = "button"; button.className = "secondary-button";
      button.textContent = "打开查询章节";
      button.disabled = task.review_status === "needs_review";
      button.addEventListener("click", () => {
        learningSelection = { directory_task_id: task.id, chapter_index: chapterIndex };
        learningRecordId = null;
        renderLearning();
        ui.reader.scrollIntoView?.({ behavior: "smooth", block: "start" });
      });
      card.append(button);
    }
    ui.directoryChapters.append(card);
  }
  if (learningAvailable && task.review_status !== "needs_review") {
    for (const [topic, matcher] of [["submission", /提交/], ["consumption", /消息|消费|Outbox|Worker/i], ["judging", /判题|沙箱|Sandbox|Worker/i]]) {
      let backendIndex = task.result.chapters.findIndex(chapter => chapter.kind === "backend" && matcher.test(chapter.title || ""));
      if (backendIndex < 0) backendIndex = task.result.chapters.findIndex(chapter => chapter.kind === "backend");
      if (backendIndex < 0) continue;
      const button = document.createElement("button"); button.type = "button"; button.className = "secondary-button";
      button.textContent = `打开${learningTopicName(topic)}主题（待查证）`;
      button.addEventListener("click", () => {
        learningSelection = {directory_task_id: task.id, chapter_index: backendIndex, topic};
        learningRecordId = null; renderLearning(); ui.reader.scrollIntoView?.({behavior: "smooth", block: "start"});
      });
      ui.directoryChapters.append(button);
    }
  }

  setLimitations(ui.directoryGaps, task.result.gaps);
}

function renderTaskOutcome(task) {
  ui.taskOutcome.hidden = true;
  ui.taskOutcome.replaceChildren();
  const result = task.result;
  if (task.execution_mode !== "real" || !result || !["gap", "clarify"].includes(result.kind)) return;
  ui.taskOutcome.hidden = false;
  const title = document.createElement("h4");
  title.textContent = result.kind === "gap" ? "已记录的证据缺口" : "已记录的澄清问题";
  const message = document.createElement("p");
  message.textContent = result.kind === "gap" ? result.message || "未提供缺口说明。" : result.question || "未提供澄清问题。";
  ui.taskOutcome.append(title, message);
  const items = result.kind === "gap" ? result.gaps : result.options;
  if (Array.isArray(items) && items.some(item => typeof item === "string")) {
    const list = document.createElement("ul");
    for (const text of items.filter(item => typeof item === "string")) {
      const item = document.createElement("li");
      item.textContent = text;
      list.append(item);
    }
    ui.taskOutcome.append(list);
  }
  if (Array.isArray(result.evidence_ids) && result.evidence_ids.length) {
    const evidence = document.createElement("p");
    evidence.className = "field-help mono";
    evidence.textContent = `关联证据编号：${result.evidence_ids.filter(id => typeof id === "string").join("、")}。引用支持关系仍需核对。`;
    ui.taskOutcome.append(evidence);
  }
  const note = document.createElement("p");
  note.className = "field-help";
  note.textContent = result.kind === "gap" ? "这是证据不足的任务结果，没有生成学习目录。" : "这里只回查当时的问题与选项；当前没有续答入口，不会自动继续调用模型。";
  ui.taskOutcome.append(note);
}

function renderTask(task) {
  currentTask = task;
  selectedTaskId = task.id;
  ui.taskDetail.hidden = false;
  ui.taskDetailTitle.textContent = `${taskKindText(task.kind, task.result?.topic || task.inputs?.topic)} · ${String(task.id).slice(0, 8)}`;
  ui.taskStatus.textContent = taskStatusText(task.status);
  ui.taskMode.textContent = `${task.execution_mode === "scripted" ? "编排检查，不代表真实模型质量" : task.execution_mode === "real" ? "真实模型任务记录，正确性待评测" : "模型未配置模式"} · 固定版本 ${task.commit || "未提供"}`;
  ui.taskMessage.textContent = typeof task.message === "string" ? task.message : "没有附加状态说明。";
  ui.taskReviewNote.hidden = task.review_status !== "needs_review";
  ui.taskReviewNote.textContent = "这是旧版本的任务记录，结果与行动轨迹仍保留。新版本需要复核，浏览本记录不会重新调用模型。";
  renderTaskOutcome(task);
  ui.taskCounters.replaceChildren();
  const counters = task.counters || {};
  for (const [key, label] of [["model_calls", "模型请求"], ["tool_calls", "工具调用"], ["retries", "重试次数"], ["context_chars", "上下文字符"], ["output_chars", "输出字符"], ["input_tokens", counters.input_tokens_estimated ? "估算输入 Token" : "输入 Token"], ["output_tokens", counters.output_tokens_estimated ? "估算输出 Token" : "输出 Token"], ["elapsed_seconds", "耗时（秒）"]]) {
    if (typeof counters[key] !== "number" || !Number.isFinite(counters[key])) continue;
    const group = document.createElement("div");
    const term = document.createElement("dt");
    const value = document.createElement("dd");
    term.textContent = label;
    value.textContent = String(counters[key]);
    group.append(term, value);
    ui.taskCounters.append(group);
  }
  ui.taskEvents.replaceChildren();
  const events = Array.isArray(task.events) ? task.events : [];
  const visibleEvents = events.filter(event => !["thought", "reasoning", "analysis", "chain_of_thought", "model_thought"].includes(event.kind));
  for (const event of visibleEvents.slice(-50)) {
    const item = document.createElement("li");
    const meta = document.createElement("span");
    meta.className = "task-event-meta";
    const labels = { task_started: "开始", tool_call: "工具调用", tool_result: "工具反馈", retry: "有限重试", cancelled: "取消", completed: "结束", stopped: "停止", model_response: "模型响应" };
    meta.textContent = `#${event.seq ?? "?"} · ${labels[event.kind] || "行动记录"}${typeof event.tool === "string" ? ` · ${event.tool}` : ""}${typeof event.status === "string" ? ` · ${event.status}` : ""}`;
    const message = document.createElement("p");
    message.textContent = typeof event.message === "string" ? event.message.slice(0, 350) : "未提供简要说明";
    item.append(meta, message);
    ui.taskEvents.append(item);
  }
  ui.taskEventsMessage.textContent = !visibleEvents.length ? "尚无可展示的工具或状态行动记录。" : visibleEvents.length > 50 ? "仅展示最近 50 条简要行动记录，完整持久记录保留在本机。" : "显示简要行动记录，不能据此认定讲解正确。";
  if (!taskIsActive(task)) {
    stopTaskPolling();
    ui.taskPollStatus.textContent = "任务已停止自动刷新。终态按后台记录显示。";
    if (learningAvailable && ["chapter_outline", "section_explanation", "follow_up"].includes(task.kind)) void loadLearning(sourceContext());
  }
  renderDirectory(task);
  renderTaskList();
  updateTaskControls();
}

function rememberTask(task) {
  const index = tasks.findIndex(item => item.id === task.id);
  if (index >= 0) tasks[index] = task;
  else if (taskPageOffset === 0) tasks = [task, ...tasks].slice(0, 100);
}

async function loadTasks(context, offset = taskPageOffset) {
  if (!agentTasksAvailable) return false;
  const generation = ++taskListRequest;
  taskPageLoading = true;
  updateTaskControls();
  ui.taskList.setAttribute("aria-busy", "true");
  try {
    const records = await taskRequest(projectApi(context, offset ? `tasks?offset=${offset}` : "tasks"));
    if (!isCurrentSource(context) || generation !== taskListRequest) return false;
    if (!Array.isArray(records)) throw new Error("任务记录格式异常，请刷新后重试。");
    tasks = records.filter(task => task.project_id === context.id).slice(0, 100);
    taskPageOffset = offset;
    const retained = tasks.find(task => task.id === selectedTaskId);
    if (retained) renderTask(retained);
    else if (!currentTask && tasks.length) renderTask(tasks[0]);
    else renderTaskList();
    updateTaskControls();
    scheduleTaskPoll(context);
    return true;
  } catch (error) {
    if (!isCurrentSource(context) || generation !== taskListRequest || error.name === "AbortError") return false;
    ui.taskListMessage.textContent = `任务记录暂时无法读取：${error.message}`;
    return false;
  } finally {
    if (isCurrentSource(context) && generation === taskListRequest) {
      taskPageLoading = false;
      ui.taskList.setAttribute("aria-busy", "false");
      updateTaskControls();
    }
  }
}

async function readTaskRecord(context, taskId, generation) {
  if (taskDetailController) taskDetailController.abort();
  const controller = typeof AbortController === "function" ? new AbortController() : null;
  taskDetailController = controller;
  try {
    const task = await taskRequest(projectApi(context, `tasks/${encodeURIComponent(taskId)}`), {}, controller);
    if (!isCurrentSource(context) || generation !== taskDetailRequest || selectedTaskId !== taskId) return false;
    if (task.project_id !== context.id || task.id !== taskId) throw new Error("任务不属于当前项目，未显示返回内容。");
    rememberTask(task);
    renderTask(task);
    taskPollFailures = 0;
    return true;
  } catch (error) {
    if (!isCurrentSource(context) || generation !== taskDetailRequest || error.name === "AbortError") return false;
    ++taskPollFailures;
    showTaskFeedback(`任务状态暂时无法获取：${error.message}`, true);
    return false;
  } finally { if (taskDetailController === controller) taskDetailController = null; }
}

async function selectTask(taskId) {
  const context = sourceContext();
  if (!isCurrentSource(context) || !agentTasksAvailable || taskActionBusy) return false;
  stopTaskPolling();
  ++taskListRequest;
  ui.taskList.setAttribute("aria-busy", "false");
  taskPollAttempts = 0;
  taskPollFailures = 0;
  selectedTaskId = taskId;
  currentTask = null;
  ui.taskDetail.hidden = true;
  ui.taskDirectory.hidden = true;
  renderTaskList();
  updateTaskControls();
  const generation = ++taskDetailRequest;
  const loaded = await readTaskRecord(context, taskId, generation);
  if (loaded) scheduleTaskPoll(context);
  return loaded;
}

function scheduleTaskPoll(context) {
  if (!isCurrentSource(context) || !agentTasksAvailable || !taskIsActive(currentTask) || taskPollTimer !== null || taskActionBusy) return;
  if (taskPollAttempts >= MAX_TASK_POLLS || taskPollFailures >= 3) {
    ui.taskPollStatus.textContent = "已停止自动刷新；后台任务未因此取消。可手动刷新记录，或取消任务。";
    return;
  }
  if (typeof setTimeout !== "function") return;
  ui.taskPollStatus.textContent = "每秒刷新一次运行状态，最多 120 次；只读取当前任务记录。";
  const taskId = currentTask.id;
  const generation = taskDetailRequest;
  taskPollTimer = setTimeout(async () => {
    taskPollTimer = null;
    if (!isCurrentSource(context) || selectedTaskId !== taskId || generation !== taskDetailRequest || !taskIsActive(currentTask) || taskActionBusy) return;
    ++taskPollAttempts;
    await readTaskRecord(context, taskId, generation);
    if (isCurrentSource(context) && generation === taskDetailRequest) scheduleTaskPoll(context);
  }, 1000);
}

ui.taskRefresh.addEventListener("click", async () => {
  const context = sourceContext();
  if (!isCurrentSource(context) || taskActionBusy) return;
  stopTaskPolling();
  taskPollAttempts = 0;
  taskPollFailures = 0;
  await loadTasks(context);
});

for (const [button, delta] of [[ui.taskNewer, -100], [ui.taskOlder, 100]]) {
  button.addEventListener("click", async () => {
    const context = sourceContext();
    if (!isCurrentSource(context) || button.disabled || taskPageLoading || taskActionBusy) return;
    await loadTasks(context, taskPageOffset + delta);
  });
}

ui.taskStart.addEventListener("click", async () => {
  const context = sourceContext();
  if (!isCurrentSource(context) || !agentTasksAvailable || taskActionBusy || tasks.some(taskIsActive)) return;
  const generation = ++taskActionRequest;
  ++taskListRequest;
  taskPageLoading = false;
  ++taskDetailRequest;
  ui.taskList.setAttribute("aria-busy", "false");
  stopTaskPolling();
  if (taskDetailController) taskDetailController.abort();
  taskActionBusy = true;
  updateTaskControls();
  renderTaskList();
  showTaskFeedback("正在确认后台模型与任务条件…");
  try {
    const task = await taskRequest(projectApi(context, "tasks"), { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
    if (!isCurrentSource(context) || generation !== taskActionRequest) return;
    if (task.status === "model_not_configured" && !task.id) {
      showTaskFeedback(task.message || "模型未配置，尚未生成学习目录；没有创建任务记录。");
      return;
    }
    if (!task.id || task.project_id !== context.id) throw new Error(task.message || "后台未返回有效任务记录，没有显示任务或目录。");
    ++taskDetailRequest;
    taskPollAttempts = 0;
    taskPollFailures = 0;
    const refreshFirstPage = taskPageOffset !== 0;
    if (refreshFirstPage) { taskPageOffset = 0; tasks = []; }
    rememberTask(task);
    renderTask(task);
    showTaskFeedback(task.message || "任务已登记，请查看实际状态与行动记录。");
    if (refreshFirstPage) await loadTasks(context, 0);
  } catch (error) {
    if (isCurrentSource(context) && generation === taskActionRequest && error.name !== "AbortError") showTaskFeedback(error.message, true);
  } finally {
    if (isCurrentSource(context) && generation === taskActionRequest) {
      taskActionBusy = false;
      renderTaskList();
      updateTaskControls();
      scheduleTaskPoll(context);
    }
  }
});

ui.taskCancel.addEventListener("click", async () => {
  const context = sourceContext();
  if (!isCurrentSource(context) || !agentTasksAvailable || taskActionBusy || !taskIsActive(currentTask)) return;
  const taskId = currentTask.id;
  const generation = ++taskActionRequest;
  ++taskListRequest;
  ui.taskList.setAttribute("aria-busy", "false");
  ++taskDetailRequest;
  stopTaskPolling();
  if (taskDetailController) taskDetailController.abort();
  taskActionBusy = true;
  updateTaskControls();
  renderTaskList();
  showTaskFeedback("正在提交取消请求，以后台最终状态为准…");
  try {
    const task = await taskRequest(projectApi(context, `tasks/${encodeURIComponent(taskId)}/cancel`), { method: "POST" });
    if (!isCurrentSource(context) || generation !== taskActionRequest || selectedTaskId !== taskId) return;
    if (task.id !== taskId || task.project_id !== context.id) throw new Error("取消结果不属于当前任务，未更改显示状态。");
    rememberTask(task);
    renderTask(task);
    showTaskFeedback(task.message || `取消请求已返回：${taskStatusText(task.status)}。`);
  } catch (error) {
    if (isCurrentSource(context) && generation === taskActionRequest && error.name !== "AbortError") showTaskFeedback(error.message, true);
  } finally {
    if (isCurrentSource(context) && generation === taskActionRequest) {
      taskActionBusy = false;
      renderTaskList();
      updateTaskControls();
      scheduleTaskPoll(context);
    }
  }
});

let spendingRequest = 0;

function money(value) {
  return Number.isSafeInteger(value) && value >= 0 ? (value / 1000000).toFixed(6).replace(/0+$/, "").replace(/\.$/, "") : "未知";
}

async function loadSpending() {
  const generation = ++spendingRequest;
  ui.spendingPanel.hidden = modelStatus !== "configured";
  if (modelStatus !== "configured") return;
  ui.spendingSummary.textContent = "正在读取本机费用记录…";
  try {
    const result = await request("/api/model/status");
    if (generation !== spendingRequest || modelStatus !== "configured") return;
    const spending = result.spending || {};
    if (result.model_status !== "configured" || spending.status === "not_enabled") throw new Error("费用门禁尚未启用");
    ui.spendingSummary.textContent = spending.trial_limit_microyuan === spending.total_limit_microyuan
      ? `项目累计上限 ${money(spending.total_limit_microyuan)} 元 · 剩余 ${money(spending.total_remaining_microyuan)} 元。已核回执的费用上限 ${money(spending.accounted_upper_bound_microyuan)} 元。`
      : `首轮上限 ${money(spending.trial_limit_microyuan)} 元 · 剩余 ${money(spending.trial_remaining_microyuan)} 元；项目总上限 ${money(spending.total_limit_microyuan)} 元。已核回执的费用上限 ${money(spending.accounted_upper_bound_microyuan)} 元。`;
    ui.spendingNotice.textContent = `${spending.status === "cost_uncertain" ? "存在待核对的在途费用，已停止新请求。" : spending.status === "price_expired" ? "核定价格已过期，需要重新核价。" : "发送前预留，费用不明时停止。"} 金额包含保守预留；按高峰价计上限，供应商账单可能更低。此处不显示密钥，也不会调用模型。`;
  } catch (error) {
    if (generation === spendingRequest) ui.spendingSummary.textContent = "费用状态暂不可用，请刷新核对；不能据此认定费用为零。";
  }
}

ui.spendingRefresh.addEventListener("click", loadSpending);

function learningTopicName(topic) {
  return ({query: "查询", submission: "提交", consumption: "消息消费", judging: "判题"})[topic] || "未知主题";
}

function taskKindText(kind, topic = null) {
  const name = topic ? learningTopicName(topic) : "后端";
  return ({ directory: "目录任务", chapter_outline: `${name}章结构`, section_explanation: `${name}小节正文`, follow_up: `${name}正文追问` })[kind] || "目录任务";
}

function resetLearningView() {
  ++learningRequest;
  learningState = null; learningRecords = []; learningSelection = null; learningRecordId = null; learningButtons = [];
  ++readingRequest; ++readingActionRequest; readingMarks = []; readingAvailable = false;
  followupDrafts = new Map();
  ui.learningRecords.replaceChildren(); ui.learningContent.replaceChildren();
  ui.learningContent.hidden = true; ui.readerFeedback.hidden = true;
  updateLearningControls();
}

function learningCanGenerate(topic = "query") {
  return learningAvailable && learningState?.[`${topic}_enabled`] === true && learningState?.execution_mode === "real"
    && Boolean(selectedProject) && !selectingProject && sourceState?.status === "verified"
    && !taskActionBusy && !tasks.some(taskIsActive);
}

function updateLearningControls() {
  ui.learningRefresh.disabled = !learningAvailable || !selectedProject || selectingProject;
  const activeTopic = learningSelection?.topic || "query";
  ui.readerStatus.textContent = !learningAvailable ? "入口未开放" : learningState?.[`${activeTopic}_enabled`] === true ? "按节查证" : "所选主题生成尚未启用";
  for (const button of learningButtons) button.disabled = !learningCanGenerate(button.dataset.topic || "query") || button.dataset.stale === "true"
    || (button.dataset.followup === "true" && (learningState?.followup_enabled !== true || !button.questionInput?.value?.trim()));
}

function readerFeedback(message, error = false) {
  ui.readerFeedback.textContent = message; ui.readerFeedback.hidden = false;
  ui.readerFeedback.classList.toggle("error", error);
}

async function loadLearning(context) {
  if (!learningAvailable || !isCurrentSource(context)) return false;
  const generation = ++learningRequest;
  try {
    const [state, records] = await Promise.all([
      taskRequest(projectApi(context, "learning/status")), taskRequest(projectApi(context, "learning"))]);
    if (!isCurrentSource(context) || generation !== learningRequest) return false;
    if (!Array.isArray(records)) throw new Error("学习记录格式异常。");
    learningState = state;
    // Scripted products remain accessible to tests via API, never learner prose.
    learningRecords = records.filter(item => item.project_id === context.id && item.execution_mode === "real");
    renderLearning(); void loadReadingProgress(context); return true;
  } catch (error) {
    if (isCurrentSource(context) && generation === learningRequest && error.name !== "AbortError") {
      learningState = null; updateLearningControls(); readerFeedback(`学习记录读取失败：${error.message}`, true);
    }
    return false;
  }
}

async function loadReadingProgress(context) {
  const sequence = ++readingRequest;
  try {
    const marks = await request(projectApi(context, "learning/progress"));
    if (!isCurrentSource(context) || sequence !== readingRequest) return;
    if (!Array.isArray(marks) || marks.length > 100) throw new Error("Invalid reading records");
    readingMarks = marks.filter(item => item.project_id === context.id);
    readingAvailable = true; renderLearning();
  } catch (error) {
    if (isCurrentSource(context) && sequence === readingRequest) { readingAvailable = false; renderLearning(); }
  }
}

async function markReading(contentId, sectionId, action) {
  const context = sourceContext();
  if (!readingAvailable || !isCurrentSource(context)) return;
  const sequence = ++readingActionRequest;
  try {
    const mark = await request(projectApi(context, "learning/progress"), {method: "PUT",
      headers: {"Content-Type": "application/json"}, body: JSON.stringify({content_id: contentId, section_id: sectionId, action})});
    if (!isCurrentSource(context) || sequence !== readingActionRequest) return;
    if (mark.project_id !== context.id || mark.content_id !== contentId) throw new Error("Reading identity mismatch");
    readingMarks = [mark, ...readingMarks.filter(item => item.content_id !== contentId)];
    renderLearning();
    const details = Array.from(ui.learningContent.children).find(item => item.dataset.sectionId === sectionId);
    if (details) details.open = true;
    readerFeedback(action === "viewed" ? "阅读位置已保存；这不代表已理解。" : "本人自报标记已更新；独立定位与修改验收仍未完成。");
  } catch (error) {
    if (isCurrentSource(context) && sequence === readingActionRequest) readerFeedback("暂时无法保存阅读标记，请重试。没有发起模型请求。", true);
  }
}

function readingControls(parent, content, sectionId, body = false) {
  const mark = readingMarks.find(item => item.content_id === content.id);
  const info = mark?.self_reported_understood ? "本人自报已懂 · 独立验收未完成" : mark?.viewed ? "已保存浏览位置 · 理解情况未自报" : "浏览位置与本人自报分别记录，不自动判断掌握。";
  learningText(parent, "p", readingAvailable ? info : "阅读标记保存暂不可用；已有内容可继续查看。", "field-help");
  const save = learningText(parent, "button", "保存阅读位置（免费）", "secondary-button");
  save.type = "button"; save.disabled = !readingAvailable;
  save.addEventListener("click", () => markReading(content.id, sectionId, "viewed"));
  if (body) {
    const label = mark?.self_reported_understood ? "撤销本人已懂标记（免费）" : "标记本人已懂（自报，免费）";
    const understand = learningText(parent, "button", label, "secondary-button");
    understand.type = "button";
    understand.disabled = !readingAvailable || (!mark?.self_reported_understood && content.review_status !== "current");
    understand.addEventListener("click", () => markReading(content.id, sectionId, mark?.self_reported_understood ? "clear_understood" : "self_understood"));
  }
}

function resumeReading(mark) {
  const content = learningRecords.find(item => item.id === mark.content_id);
  const outline = content?.kind === "section_explanation" ? learningRecords.find(item => item.id === content.parent_outline_id) : content;
  if (!outline) return readerFeedback("相关保存章节暂不可用，请刷新学习记录。", true);
  learningSelection = null; learningRecordId = outline.id; renderLearning();
  const details = Array.from(ui.learningContent.children).find(item => item.dataset.sectionId === mark.section_id);
  if (details) details.open = true;
  ui.reader.scrollIntoView?.({behavior: "smooth", block: "start"});
}

function learningText(parent, tag, text, className = "") {
  const element = document.createElement(tag); element.textContent = text; element.className = className;
  parent.append(element); return element;
}

function learningGenerateButton(parent, label, endpoint, selection, stale = false, topic = "query") {
  const button = learningText(parent, "button", label, "primary-button"); button.type = "button";
  button.dataset.stale = String(stale); button.dataset.topic = topic; learningButtons.push(button);
  button.addEventListener("click", () => startLearning(endpoint, selection, topic));
  return button;
}

async function savedCitation(context, evidenceId, sequence) {
  try {
    return await request(projectApi(context, `evidence/${encodeURIComponent(evidenceId)}`));
  } catch (error) {
    if (error.httpStatus !== 404) throw error;
    // Older running backends already expose bounded saved-record pagination.
    // A missing detail route never grants access to source tools or other projects.
    for (let offset = 0; offset < 1000; offset += 50) {
      if (!isCurrentSource(context) || sequence !== citationRequest) return null;
      const page = await request(projectApi(context, `evidence?limit=50&offset=${offset}`));
      if (!isCurrentSource(context) || sequence !== citationRequest) return null;
      if (!Array.isArray(page) || page.length > 50) throw new Error("Invalid evidence page");
      const card = page.find(item => item.id === evidenceId);
      if (card) return card;
      if (page.length < 50) break;
    }
    throw new Error("Saved evidence not found");
  }
}

function blockCitationSelection(parent, block, references) {
  const lines = block.source_lines;
  const spans = block.source_spans;
  const hasLines = lines != null && (!Array.isArray(lines) || lines.length > 0);
  const hasSpans = spans != null && (!Array.isArray(spans) || spans.length > 0);
  if (!hasLines && !hasSpans) {
    if (references.length) learningText(parent, "p", "此记录未保存本块精确选行；下方仅为已读片段范围。", "field-help");
    return;
  }
  const ids = block.evidence_ids;
  const entries = hasLines ? lines : spans;
  const validLine = n => Number.isInteger(n) && n >= 1 && n <= 1000000;
  // Display persisted coordinates only. Never infer selections from the prose,
  // neighbouring blocks or the complete excerpt, even for historical records.
  const valid = !(hasLines && hasSpans) && Array.isArray(entries) && entries.length <= 16 &&
    Array.isArray(ids) && ids.length > 0 && ids.length <= 16 && new Set(ids).size === ids.length &&
    new Set(entries.map(item => item?.evidence_id)).size === ids.length &&
    (!hasLines || entries.length === ids.length) && entries.every(item => {
      const matches = references.filter(ref => ref.evidence_id === item?.evidence_id);
      if (!ids.includes(item?.evidence_id) || matches.length !== 1) return false;
      const ref = matches[0];
      if (!validLine(ref.start_line) || !validLine(ref.end_line) || ref.start_line > ref.end_line || ref.truncated) return false;
      const inRange = n => validLine(n) && n >= ref.start_line && n <= ref.end_line;
      return hasLines
        ? Array.isArray(item.lines) && item.lines.length > 0 && item.lines.length <= 20 &&
          new Set(item.lines).size === item.lines.length && item.lines.every(inRange)
        : inRange(item.start_line) && inRange(item.end_line) &&
          item.end_line >= item.start_line && item.end_line - item.start_line < 20;
    });
  if (!valid) {
    learningText(parent, "p", "本块保存的选行信息不完整或不一致，无法准确展示；下方仅为已读片段范围。", "field-help");
    return;
  }
  for (const item of entries) {
    const ref = references.find(ref => ref.evidence_id === item.evidence_id);
    const selection = hasLines ? [...item.lines].sort((a, b) => a - b).join("、") : `${item.start_line}–${item.end_line}`;
    learningText(parent, "p", `${hasLines ? "本块保存的选行" : "本块历史选段"}：${ref.path} · 第${selection}行`, "field-help");
  }
}

function learningEvidence(parent, references, block = null) {
  if (block) blockCitationSelection(parent, block, references || []);
  for (const reference of references || []) {
    const button = learningText(parent, "button", `${block ? "已读片段：" : ""}${reference.path} · ${reference.start_line}–${reference.end_line}行`, "text-button learning-citation");
    button.type = "button";
    button.addEventListener("click", async () => {
      const context = sourceContext();
      const sequence = ++citationRequest;
      let card = savedEvidence.find(item => item.id === reference.evidence_id);
      if (!card) {
        try {
          card = await savedCitation(context, reference.evidence_id, sequence);
          if (!isCurrentSource(context) || sequence !== citationRequest) return;
          if (!card || card.id !== reference.evidence_id) throw new Error("Evidence identity mismatch");
          savedEvidence = [card, ...savedEvidence.filter(item => item.id !== card.id)].slice(0, 1000);
        } catch (error) {
          if (isCurrentSource(context) && sequence === citationRequest) {
            readerFeedback("暂时无法打开这条已保存依据，请重试。此操作不会重新读取源码或调用模型。", true);
          }
          return;
        }
      }
      if (!isCurrentSource(context) || sequence !== citationRequest) return;
      renderSnippet(card); renderEvidence();
      ui.evidenceViewer.scrollIntoView?.({ behavior: "smooth", block: "start" });
    });
  }
}

function renderExplanation(parent, content) {
  for (const block of content.content.blocks || []) {
    learningText(parent, "h4", block.heading);
    learningText(parent, "p", block.text, "learning-prose");
    const labels = {fact: "源码事实", documentation: "文档说明", inference: "解释性推断", candidate: "候选", gap: "缺口"};
    learningText(parent, "p", labels[block.support] || "未识别依据", "field-help");
    learningEvidence(parent, (content.evidence || []).filter(ref => (block.evidence_ids || []).includes(ref.evidence_id)), block);
  }
  const gaps = document.createElement("ul"); setLimitations(gaps, content.content.gaps); parent.append(gaps);
}

function followupControls(parent, body, topic, stale) {
  const group = document.createElement("div"); group.className = "followup-controls";
  const label = learningText(group, "label", "针对这一份正文提问", "field-label");
  const question = document.createElement("textarea"); question.rows = 3; question.maxLength = 500;
  question.placeholder = "只填写当前小节的问题；不要填写密钥、本机路径或个人隐私。";
  question.value = followupDrafts.get(body.id) || ""; label.append(question);
  learningText(group, "p", learningState?.followup_enabled === true
    ? "仅点击下方按钮才调用模型。旧讲解会作为待查证摘录，回答重新查证并保留独立引用，不改写原正文。"
    : "追问调用尚未在本机启用。可以填写问题或免费阅读已保存回答，填写不会产生模型费用。", "field-help");
  const button = learningText(group, "button", "提交本节追问（调用模型）", "primary-button");
  button.type = "button"; button.dataset.topic = topic; button.dataset.followup = "true";
  button.dataset.stale = String(stale || body.review_status !== "current" || body.commit !== selectedProject?.commit);
  button.questionInput = question; learningButtons.push(button);
  question.addEventListener("input", () => { followupDrafts.set(body.id, question.value); updateLearningControls(); });
  button.addEventListener("click", () => startLearning("followup", {body_id: body.id, question: question.value}, topic));
  parent.append(group);
  for (const answer of learningRecords.filter(item => item.kind === "follow_up" && item.parent_body_id === body.id)) {
    const saved = document.createElement("details"); saved.className = "learning-section";
    learningText(saved, "summary", `已保存追问：${answer.question}`);
    learningText(saved, "p", answer.review_status === "current" ? "已保存回答 · 语义待核对" : "历史回答 · 需复核", "field-help");
    renderExplanation(saved, answer); parent.append(saved);
  }
}

function renderLearning() {
  ui.learningRecords.replaceChildren(); ui.learningContent.replaceChildren(); learningButtons = [];
  const outlines = learningRecords.filter(item => item.kind === "chapter_outline");
  for (const record of outlines) {
    const button = learningText(ui.learningRecords, "button", `${record.content.title} · ${record.review_status === "needs_review" ? "旧版本，需复核" : "已保存，语义待核对"}`, "secondary-button");
    button.type = "button"; button.setAttribute("aria-pressed", String(record.id === learningRecordId));
    button.addEventListener("click", () => { learningRecordId = record.id; learningSelection = { directory_task_id: record.directory_task_id, chapter_index: record.chapter_index, topic: record.content.topic || "query" }; renderLearning(); });
  }
  if (readingAvailable && readingMarks.length) {
    const resume = learningText(ui.learningRecords, "button", "继续上次阅读（免费）", "secondary-button");
    resume.type = "button"; resume.addEventListener("click", () => resumeReading(readingMarks[0]));
  }
  const selected = outlines.find(item => item.id === learningRecordId) || outlines.find(item => item.directory_task_id === learningSelection?.directory_task_id && item.chapter_index === learningSelection?.chapter_index && (item.content.topic || "query") === (learningSelection?.topic || "query") && item.review_status === "current");
  const topic = selected?.content.topic || learningSelection?.topic || "query";
  ui.learningContent.hidden = !learningSelection && !selected;
  ui.readerMessage.textContent = !learningState?.[`${topic}_enabled`] ? "可以浏览已保存内容。所选主题的结构与正文生成尚未启用；不会自动调用模型。" : "先在目录选择后端主题，生成小节结构，再按节生成或阅读。主题线索不代表已查证，需要当前公开版本核对通过。";
  if (!selected) {
    if (learningSelection) {
      learningText(ui.learningContent, "p", "所选章节尚无保存的小节结构。先生成结构，再按小节展开正文。");
      learningGenerateButton(ui.learningContent, `生成${learningTopicName(topic)}章结构（调用模型）`, "outline", learningSelection, false, topic);
    }
    updateLearningControls(); return;
  }
  learningRecordId = selected.id;
  const stale = selected.review_status !== "current" || selected.commit !== selectedProject?.commit;
  const viewDirectory = learningText(ui.learningContent, "button", "查看关联目录（免费）", "secondary-button");
  viewDirectory.type = "button";
  viewDirectory.addEventListener("click", async () => {
    await selectTask(selected.directory_task_id);
    ui.taskDirectory.scrollIntoView?.({behavior: "smooth", block: "start"});
  });
  learningText(ui.learningContent, "h3", selected.content.title);
  learningText(ui.learningContent, "p", selected.content.objective, "section-intro");
  learningText(ui.learningContent, "p", `保存版本 ${selected.commit} · ${stale ? "需复核，只查看历史内容" : "结构通过，语义仍需比照源码"}`, "field-help mono");
  learningEvidence(ui.learningContent, selected.evidence);
  for (const section of selected.content.sections || []) {
    const card = document.createElement("details"); card.className = "learning-section"; card.dataset.sectionId = section.id;
    learningText(card, "summary", section.title);
    learningText(card, "p", section.objective, "section-intro");
    const body = learningRecords.find(item => item.kind === "section_explanation" && item.parent_outline_id === selected.id && item.section_id === section.id);
    readingControls(card, body || selected, section.id, Boolean(body));
    if (body) {
      learningText(card, "p", body.review_status === "current" ? "已保存正文 · 语义待核对" : "历史正文 · 需复核", "field-help");
      renderExplanation(card, body); followupControls(card, body, topic, stale);
    } else {
      const initialSection = ["submission", "consumption", "judging"].includes(topic) || /入口|参数|查询与响应组装/.test(section.title || "");
      learningText(card, "p", initialSection ? "这一节尚未生成。展开结构不会自动产生费用。" : "这一节尚未启用生成；当前先验收入口与参数、查询与响应组装。展开结构不会自动产生费用。", "field-help");
      learningGenerateButton(card, "生成本节讲解（调用模型）", "section", { outline_id: selected.id, section_id: section.id }, stale || !initialSection, topic);
    }
    ui.learningContent.append(card);
  }
  const gaps = document.createElement("ul"); setLimitations(gaps, selected.content.gaps); ui.learningContent.append(gaps);
  updateLearningControls();
}

async function startLearning(endpoint, selection, topic = "query") {
  const context = sourceContext();
  if (!isCurrentSource(context) || !learningCanGenerate(topic)) return;
  if (endpoint === "followup") {
    if (learningState?.followup_enabled !== true) return;
    const body = learningRecords.find(item => item.id === selection.body_id && item.kind === "section_explanation");
    if (!body || body.review_status !== "current" || body.commit !== selectedProject?.commit) return;
    if (typeof selection.question !== "string" || !selection.question.trim() || selection.question.length > 500) {
      readerFeedback("请填写不超过500字的当前小节问题。没有发起模型请求。", true); return;
    }
  }
  const generation = ++taskActionRequest; ++taskDetailRequest; ++taskListRequest;
  taskPageLoading = false;
  stopTaskPolling(); if (taskDetailController) taskDetailController.abort();
  taskActionBusy = true; updateTaskControls(); renderTaskList();
  readerFeedback("正在核对所选范围、公开版本与费用门禁…");
  try {
    const task = await taskRequest(projectApi(context, `learning/${endpoint}`), { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(selection) });
    if (!isCurrentSource(context) || generation !== taskActionRequest) return;
    if (!task.id || task.project_id !== context.id) throw new Error(task.message || "未启动有效学习任务。");
    taskPollAttempts = 0; taskPollFailures = 0;
    const refreshFirstPage = taskPageOffset !== 0;
    if (refreshFirstPage) { taskPageOffset = 0; tasks = []; }
    rememberTask(task); renderTask(task); readerFeedback(task.message || "任务已启动，请查看行动记录。");
    if (refreshFirstPage) await loadTasks(context, 0);
  } catch (error) {
    if (isCurrentSource(context) && generation === taskActionRequest && error.name !== "AbortError") readerFeedback(error.message, true);
  } finally {
    if (isCurrentSource(context) && generation === taskActionRequest) {
      taskActionBusy = false; updateTaskControls(); renderTaskList(); scheduleTaskPoll(context);
      void loadSpending();
    }
  }
}

ui.learningRefresh.addEventListener("click", () => loadLearning(sourceContext()));
refresh();
