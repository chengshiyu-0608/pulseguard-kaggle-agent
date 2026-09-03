const state = {
  users: [],
  selected: null,
  tier: "全部",
  summary: null,
};

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];

function pct(value, digits = 1) {
  return `${(value * 100).toFixed(digits)}%`;
}

function riskClass(tier) {
  return tier === "高风险" ? "high" : tier === "中风险" ? "medium" : "low";
}

async function loadSummary() {
  const response = await fetch("/api/summary");
  state.summary = await response.json();
  const { overview, evaluation } = state.summary;
  $("#metricHighRisk").textContent = overview.risk_distribution["高风险"].toLocaleString();
  $("#metricAccuracy").textContent = pct(overview.risk_distribution["高风险"] / overview.latest_users);

  $("#evalRows").textContent = evaluation.test.rows.toLocaleString();
  $("#evalAuc").textContent = evaluation.test.roc_auc.toFixed(3);
  $("#evalAccuracy").textContent = pct(evaluation.test.accuracy);
  $("#evalRecall").textContent = pct(evaluation.test.recall);
  $("#evalF1").textContent = evaluation.test.f1.toFixed(3);
  $("#evalBrier").textContent = evaluation.test.brier_score.toFixed(3);
  $("#labelDefinition").textContent = evaluation.label_definition;
  $("#trainPeriod").textContent = evaluation.training_period;
  $("#testPeriod").textContent = evaluation.test_period;
  $("#limitationList").innerHTML = evaluation.limitations.map((item) => `<li>${item}</li>`).join("");
}

async function loadUsers() {
  const query = encodeURIComponent($("#userSearch").value.trim());
  const tier = state.tier === "全部" ? "" : encodeURIComponent(state.tier);
  const response = await fetch(`/api/users?tier=${tier}&q=${query}&limit=80`);
  if (!response.ok) throw new Error(`用户列表请求失败（${response.status}）`);
  const payload = await response.json();
  state.users = Array.isArray(payload.users) ? payload.users : [];
  $("#userCount").textContent = `${payload.total.toLocaleString()} 名用户`;
  renderUsers();
  if (!state.selected && state.users.length) selectUser(state.users[0]);
}

function renderUsers() {
  const list = $("#userList");
  if (!state.users.length) {
    list.innerHTML = `<div class="empty-state" style="height:160px">没有匹配用户</div>`;
    return;
  }
  list.innerHTML = state.users.map((user) => `
    <div class="user-row ${state.selected?.user_id === user.user_id ? "active" : ""}" data-user="${user.user_id}" role="button" tabindex="0">
      <span class="user-id">${user.user_id}</span>
      <span class="user-month">${user.month}</span>
      <span class="score ${riskClass(user.risk_tier)}">${pct(user.risk_score, 0)}</span>
      <span>${user.risk_type || (user.active_days <= 2 ? "活跃下降型" : "参与动量不足")}</span>
      <span class="status ${user.status === "已处理" || user.status === "已触达" ? "done" : ""}">${user.status || "待处理"}</span>
      <span><button type="button" class="view-btn">查看</button></span>
    </div>
  `).join("");
  $$(".user-row").forEach((row) => {
    row.addEventListener("click", () => selectUser(state.users.find((user) => user.user_id === row.dataset.user)));
  });
}

function selectUser(user) {
  if (!user) return;
  state.selected = user;
  renderUsers();
  $("#selectedUser").textContent = user.user_id;
  $("#drawerRisk").textContent = pct(user.risk_score);
  const badge = $("#riskBadge");
  badge.className = `risk-badge ${riskClass(user.risk_tier)}`;
  badge.textContent = `${user.risk_tier} · ${pct(user.risk_score)}`;
  $("#profileSubmissions").textContent = user.submissions ?? "--";
  $("#profileDays").textContent = user.active_days ?? "--";
  $("#profileCompetitions").textContent = user.active_competitions ?? "--";
  $("#profileAverage").textContent = user.historical_average_submissions?.toFixed?.(1) ?? "--";
  $("#factorList").classList.remove("empty-state");
  const factors = Array.isArray(user.factors) ? user.factors : [];
  if (!factors.length) {
    $("#factorList").innerHTML = `<div class="empty-state">暂无可用因素解释</div>`;
  } else {
    const maxContribution = Math.max(...factors.map((factor) => Math.abs(factor.contribution)), 0.01);
    $("#factorList").innerHTML = factors.map((factor) => {
    const isRisk = factor.contribution > 0;
    const width = Math.max(8, Math.abs(factor.contribution) / maxContribution * 100);
    return `
      <div class="factor-row">
        <div class="factor-row-top"><strong>${factor.label}</strong><span>${isRisk ? "+" : ""}${factor.contribution.toFixed(2)} · ${factor.direction}</span></div>
        <div class="factor-bar"><div class="factor-fill ${isRisk ? "risk" : "protect"}" style="width:${width}%"></div></div>
      </div>`;
    }).join("");
  }
  $("#actionList").innerHTML = "<li>点击“运行诊断”生成干预建议</li>";
  $("#agentAnswer").textContent = "用户已加载，等待运行Agent诊断。";
  $("#toolTrace").innerHTML = "";
  $("#runAgent").disabled = false;
  $("#sendTouch").disabled = false;
  $("#markHandled").disabled = false;
  $("#actionStatus").textContent = "";
  $("#actionHint").textContent = "建议：先生成运营建议，再选择触达渠道";
}

async function runAgent() {
  if (!state.selected) return;
  const button = $("#runAgent");
  button.disabled = true;
  button.textContent = "诊断中";
  const response = await fetch("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      user_id: state.selected.user_id,
      question: $("#agentQuestion").value,
    }),
  });
  const result = await response.json();
  $("#agentAnswer").textContent = result.answer || result.error;
  $("#actionList").innerHTML = (result.recommendation?.actions || []).map((action) => `<li>${action}</li>`).join("");
  $("#actionHint").textContent = result.recommendation?.actions?.[0] || "建议已生成，请人工复核";
  $("#toolTrace").innerHTML = (result.tool_trace || []).map((item) => `
    <div class="tool-item"><strong>${item.tool}</strong><span>${item.summary}</span></div>
  `).join("");
  button.disabled = false;
  button.textContent = "重新诊断";
}

async function applyAction(action) {
  if (!state.selected) return;
  const response = await fetch("/api/action", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      user_id: state.selected.user_id,
      action,
      channel: $("#touchChannel").value,
    }),
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || "操作失败");
  $("#actionStatus").textContent = `${result.action}：${result.status}`;
  await loadUsers();
}

function setupNavigation() {
  $$(".nav-item").forEach((button) => {
    button.addEventListener("click", () => {
      $$(".nav-item").forEach((item) => item.classList.remove("active"));
      button.classList.add("active");
      $$(".view").forEach((view) => view.classList.remove("active"));
      $(`#${button.dataset.view}View`).classList.add("active");
    });
  });
}

function setupFilters() {
  $$(".segments button").forEach((button) => {
    button.addEventListener("click", () => {
      $$(".segments button").forEach((item) => item.classList.remove("active"));
      button.classList.add("active");
      state.tier = button.dataset.tier;
      state.selected = null;
      loadUsers();
    });
  });
  let searchTimer;
  $("#userSearch").addEventListener("input", () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(loadUsers, 180);
  });
  $("#runAgent").addEventListener("click", runAgent);
  $("#sendTouch").addEventListener("click", () => applyAction("send").catch(showActionError));
  $("#markHandled").addEventListener("click", () => applyAction("handled").catch(showActionError));
}

function showActionError(error) {
  $("#actionStatus").textContent = `操作失败：${error.message}`;
}

async function init() {
  setupNavigation();
  setupFilters();
  await loadSummary();
  await loadUsers();
}

init().catch((error) => {
  $("#agentAnswer").textContent = `加载失败：${error.message}`;
  $("#userList").innerHTML = `<div class="empty-state" style="height:160px">列表加载失败，请点击刷新重试</div>`;
});
