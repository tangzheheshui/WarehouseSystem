const state = {
  token: localStorage.getItem("wmsToken") || "",
  user: null,
  page: "dashboard",
  importResult: {},
  inventoryRows: [],
  pickingDetail: null,
  templateType: "inbound",
  templates: [],
};

const titles = {
  dashboard: "工作台",
  inbound: "入库管理",
  outbound: "出库管理",
  inventory: "库存查询",
  picking: "拣货单",
  users: "用户管理",
  templates: "模板管理",
  logs: "操作日志",
  password: "修改密码",
};

const pages = [
  { id: "dashboard", label: "工作台", admin: false },
  { id: "inbound", label: "入库管理", admin: false },
  { id: "outbound", label: "出库管理", admin: false },
  { id: "inventory", label: "库存查询", admin: false },
  { id: "picking", label: "拣货单", admin: false },
  { id: "users", label: "用户管理", admin: true },
  { id: "templates", label: "模板管理", admin: true },
  { id: "logs", label: "操作日志", admin: true },
];

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, char => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[char]);
}

function formatTime(value) {
  if (!value) return "-";
  return String(value).replace("T", " ").slice(0, 19);
}

function toast(message, error = false) {
  const node = document.getElementById("toast");
  node.textContent = message;
  node.className = error ? "toast error" : "toast";
  setTimeout(() => node.classList.add("hidden"), 3600);
}

async function api(path, options = {}) {
  const settings = { ...options, headers: { ...(options.headers || {}) } };
  if (state.token) settings.headers.Authorization = `Bearer ${state.token}`;
  if (settings.body && !(settings.body instanceof FormData)) {
    settings.headers["Content-Type"] = "application/json";
    settings.body = JSON.stringify(settings.body);
  }
  const response = await fetch(path, settings);
  let data = {};
  try { data = await response.json(); } catch (_) {}
  if (!response.ok) {
    const error = new Error(data.error || "请求失败");
    error.details = data.details || [];
    error.status = response.status;
    throw error;
  }
  return data;
}

async function download(path, fallbackName) {
  const response = await fetch(path, { headers: { Authorization: `Bearer ${state.token}` } });
  if (!response.ok) {
    let data = {};
    try { data = await response.json(); } catch (_) {}
    throw new Error(data.error || "下载失败");
  }
  const disposition = response.headers.get("Content-Disposition") || "";
  const match = disposition.match(/filename="([^"]+)"/);
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = match ? match[1] : fallbackName;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

function renderNav() {
  const nav = document.getElementById("nav");
  const available = pages.filter(item => !item.admin || state.user.role === "admin");
  nav.innerHTML = available.map(item => `
    <button data-page="${item.id}" class="${state.page === item.id ? "active" : ""}">${item.label}</button>
  `).join("");
  nav.querySelectorAll("button").forEach(button => {
    button.onclick = () => location.hash = `#${button.dataset.page}`;
  });
  document.getElementById("userPhone").textContent = state.user.phone;
  document.getElementById("userRole").textContent = state.user.role === "admin" ? "管理员" : "仓库人员";
}

function showApp() {
  document.getElementById("loginView").classList.add("hidden");
  document.getElementById("appView").classList.remove("hidden");
  renderNav();
  if (state.user.mustChangePassword) {
    state.page = "password";
    location.hash = "#password";
  }
  renderPage();
}

function setStatus(value) {
  if (!value) return "-";
  const labels = {
    pending: "待拣货", completed: "已完成", pending_picking: "待拣货",
    active: "正常", disabled: "禁用",
  };
  return `<span class="badge ${esc(value)}">${labels[value] || esc(value)}</span>`;
}

function metric(label, value) {
  return `<div class="metric"><span>${label}</span><strong>${esc(value)}</strong></div>`;
}

async function renderDashboard() {
  document.getElementById("page").innerHTML = `<div class="panel">加载中...</div>`;
  const data = await api("/api/dashboard");
  document.getElementById("page").innerHTML = `
    <div class="metrics">
      ${metric("商品数", data.products)}
      ${metric("库存批次", data.lots)}
      ${metric("库存总量", data.quantity)}
      ${metric("待拣货", data.pendingPicking)}
      ${metric("启用账号", data.activeUsers)}
    </div>
    <div class="panel">
      <h3>最近单据</h3>
      <div id="recentOrders"></div>
    </div>`;
  const orders = await api("/api/orders");
  document.getElementById("recentOrders").innerHTML = orderTable(orders.slice(0, 10));
}

function orderTable(rows) {
  if (!rows.length) return `<div class="empty">暂无单据</div>`;
  return `<div class="table-wrap"><table>
    <thead><tr><th>单号</th><th>类型</th><th>状态</th><th>操作人</th><th>时间</th></tr></thead>
    <tbody>${rows.map(row => `<tr>
      <td class="mono">${esc(row.order_no)}</td>
      <td>${row.order_type === "inbound" ? "入库" : "出库"}</td>
      <td>${setStatus(row.status)}</td>
      <td>${esc(row.operator_name)}</td>
      <td class="nowrap">${formatTime(row.created_at)}</td>
    </tr>`).join("")}</tbody></table></div>`;
}

function importResultHtml(result, type) {
  if (!result || !result.visible) return "";
  if (result.ok) {
    return `<div class="result-box">
      <div class="result-title"><span>${type === "inbound" ? "入库成功" : "出库成功"}</span><span class="mono">${esc(result.orderNo)}</span></div>
      <div>${result.aggregated.map(item => {
        const name = item.productId;
        const detail = item.batchNo ? ` / ${item.batchNo}` : "";
        return `${esc(name)}${esc(detail)}：${esc(item.quantity)}件`;
      }).join("；")}</div>
      ${type === "outbound" && result.pickNo ? `<div>拣货单：${esc(result.pickNo)}</div>` : ""}
    </div>`;
  }
  return `<div class="result-box error">
    <div class="result-title"><span>${esc(result.error)}</span><button class="btn small" id="downloadErrors">下载错误明细</button></div>
    <div class="table-wrap"><table><thead><tr><th>行号</th><th>列名</th><th>错误</th></tr></thead>
    <tbody>${result.details.map(item => `<tr><td>${esc(item.row)}</td><td>${esc(item.column)}</td><td>${esc(item.message)}</td></tr>`).join("")}</tbody></table></div>
  </div>`;
}

async function renderImportPage(type) {
  const label = type === "inbound" ? "入库" : "出库";
  const result = state.importResult[type] || {};
  document.getElementById("page").innerHTML = `
    <div class="panel">
      <div class="panel-header"><h3>${label}导入</h3>
        <button class="btn" id="downloadTemplate">下载模板</button></div>
      <form id="importForm" class="fields-row">
        <label>Excel文件<input type="file" name="file" accept=".xlsx" required></label>
        <button class="btn primary" type="submit">上传校验并${label}</button>
      </form>
    </div>
    ${importResultHtml(result, type)}
    <div class="panel">
      <h3>${label}单</h3>
      <form id="orderFilter" class="fields-row" style="margin-bottom:12px">
        <label>单号/操作人<input name="keyword"></label>
        <label>开始日期<input name="startDate" type="date"></label>
        <label>结束日期<input name="endDate" type="date"></label>
        <button class="btn" type="submit">查询</button>
      </form>
      <div id="orderList"></div>
    </div>`;
  document.getElementById("downloadTemplate").onclick = () =>
    download(`/api/templates/${type}/download`, `${type}-template.xlsx`);
  document.getElementById("importForm").onsubmit = async event => {
    event.preventDefault();
    const button = event.target.querySelector("button[type=submit]");
    button.disabled = true;
    button.textContent = "处理中...";
    try {
      const body = new FormData(event.target);
      const data = await api(`/api/${type}/upload`, { method: "POST", body });
      state.importResult[type] = {
        visible: true, ok: true, orderNo: data.orderNo,
        pickNo: data.pickingOrder?.pick_no, aggregated: data.aggregated || [],
      };
      await renderImportPage(type);
      toast(`${label}成功：${data.orderNo}`);
    } catch (error) {
      state.importResult[type] = {
        visible: true, ok: false, error: error.message, details: error.details || [],
      };
      await renderImportPage(type);
      toast(error.message, true);
    }
  };
  const errorButton = document.getElementById("downloadErrors");
  if (errorButton) errorButton.onclick = () => {
    const details = state.importResult[type].details;
    postDownload("/api/import-errors/export", { details }, "import-errors.xlsx");
  };
  document.getElementById("orderFilter").onsubmit = event => {
    event.preventDefault();
    loadImportOrders(type, new FormData(event.target));
  };
  await loadImportOrders(type, new FormData(document.getElementById("orderFilter")));
}

async function loadImportOrders(type, form) {
  const query = new URLSearchParams(form);
  query.set("type", type);
  const orders = await api(`/api/orders?${query}`);
  document.getElementById("orderList").innerHTML = orderTable(orders);
}

async function renderInventory() {
  document.getElementById("page").innerHTML = `
    <div class="panel">
      <h3>查询库存</h3>
      <form id="inventoryForm" class="fields-row">
        <label>商品ID<input name="productId"></label>
        <label>商品名称<input name="productName"></label>
        <label>批次号<input name="batchNo"></label>
        <label>库位<input name="location"></label>
        <button class="btn primary" type="submit">查询</button>
      </form>
    </div>
    <div class="panel">
      <h3>商品汇总</h3><div id="inventorySummary"></div>
    </div>
    <div class="panel">
      <div class="panel-header"><h3>批次明细</h3><button class="btn" id="exportInventory">导出Excel</button></div>
      <div id="inventoryTable"></div>
    </div>
    <div class="panel">
      <h3>库存流水</h3>
      <div id="stockLogTable"></div>
    </div>`;
  const form = document.getElementById("inventoryForm");
  form.onsubmit = event => { event.preventDefault(); loadInventory(new FormData(form)); };
  document.getElementById("exportInventory").onclick = () => {
    const query = new URLSearchParams(new FormData(form)).toString();
    download(`/api/inventory/export?${query}`, "inventory.xlsx");
  };
  await loadInventory(new FormData(form));
}

async function loadInventory(form) {
  const query = new URLSearchParams(form).toString();
  const rows = await api(`/api/inventory?${query}`);
  state.inventoryRows = rows;
  const summary = Object.values(rows.reduce((result, row) => {
    if (!result[row.product_id]) {
      result[row.product_id] = {
        product_id: row.product_id, product_name: row.product_name,
        spec: row.spec, quantity: 0, locations: new Set(),
      };
    }
    result[row.product_id].quantity += row.quantity;
    if (row.quantity > 0) result[row.product_id].locations.add(row.location);
    return result;
  }, {}));
  document.getElementById("inventorySummary").innerHTML = summary.length ? `
    <div class="table-wrap"><table><thead><tr><th>商品ID</th><th>名称</th><th>规格</th><th>库存总量</th><th>库位列表</th></tr></thead>
    <tbody>${summary.map(row => `<tr><td class="mono">${esc(row.product_id)}</td><td>${esc(row.product_name)}</td><td>${esc(row.spec || "-")}</td><td>${row.quantity}</td><td>${Array.from(row.locations).join("、") || "-"}</td></tr>`).join("")}</tbody></table></div>` : `<div class="empty">暂无汇总</div>`;
  document.getElementById("inventoryTable").innerHTML = rows.length ? `
    <div class="table-wrap"><table><thead><tr><th>商品ID</th><th>名称</th><th>规格</th><th>批次</th><th>库位</th><th>数量</th><th>最近入库</th></tr></thead>
    <tbody>${rows.map(row => `<tr>
      <td class="mono">${esc(row.product_id)}</td><td>${esc(row.product_name)}</td><td>${esc(row.spec || "-")}</td>
      <td>${esc(row.batch_no)}</td><td>${esc(row.location)}</td><td>${esc(row.quantity)}</td><td class="nowrap">${formatTime(row.last_inbound_at)}</td>
    </tr>`).join("")}</tbody></table></div>` : `<div class="empty">暂无库存</div>`;
  const productId = form.get("productId") || "";
  const logs = await api(`/api/stock-logs${productId ? `?productId=${encodeURIComponent(productId)}` : ""}`);
  const flowNode = document.getElementById("stockLogTable");
  if (flowNode) {
    flowNode.innerHTML = logs.length ? `
      <div class="table-wrap"><table><thead><tr><th>时间</th><th>单号</th><th>商品ID</th><th>批次</th><th>库位</th><th>变动</th><th>操作人</th></tr></thead>
      <tbody>${logs.map(row => `<tr>
        <td class="nowrap">${formatTime(row.created_at)}</td><td class="mono">${esc(row.order_no)}</td>
        <td class="mono">${esc(row.product_id)}</td><td>${esc(row.batch_no)}</td><td>${esc(row.location)}</td>
        <td>${row.change_quantity > 0 ? `+${row.change_quantity}` : row.change_quantity}</td><td>${esc(row.operator_name)}</td>
      </tr>`).join("")}</tbody></table></div>` : `<div class="empty">暂无库存流水</div>`;
  }
}

async function postDownload(path, body, fallbackName) {
  const response = await fetch(path, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${state.token}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    let data = {};
    try { data = await response.json(); } catch (_) {}
    throw new Error(data.error || "下载失败");
  }
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = fallbackName;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

async function renderPicking() {
  document.getElementById("page").innerHTML = `
    <div class="panel">
      <div class="panel-header"><h3>拣货单</h3>
        <select id="pickStatus" style="width:130px">
          <option value="">全部状态</option><option value="pending">待拣货</option><option value="completed">已完成</option>
        </select>
        <button class="btn" id="refreshPicking">刷新</button></div>
      <div id="pickList"></div>
    </div>
    <div class="panel"><div class="panel-header"><h3>拣货明细</h3>
      <div class="actions" id="pickActions"></div></div><div id="pickDetail"></div></div>`;
  document.getElementById("pickStatus").onchange = loadPicking;
  document.getElementById("refreshPicking").onclick = loadPicking;
  await loadPicking();
}

async function loadPicking() {
  const status = document.getElementById("pickStatus").value;
  const query = status ? `?status=${status}` : "";
  const rows = await api(`/api/picking-orders${query}`);
  document.getElementById("pickList").innerHTML = rows.length ? `
    <div class="table-wrap"><table><thead><tr><th>拣货单号</th><th>出库单号</th><th>状态</th><th>生成时间</th><th>操作</th></tr></thead>
    <tbody>${rows.map(row => `<tr>
      <td class="mono">${esc(row.pick_no)}</td><td class="mono">${esc(row.outbound_order_no)}</td>
      <td>${setStatus(row.status)}</td><td class="nowrap">${formatTime(row.generated_at)}</td>
      <td><button class="btn small" data-id="${row.id}">查看</button></td>
    </tr>`).join("")}</tbody></table></div>` : `<div class="empty">暂无拣货单</div>`;
  document.querySelectorAll("#pickList button[data-id]").forEach(button => {
    button.onclick = () => loadPickingDetail(Number(button.dataset.id));
  });
  if (rows.length && (!state.pickingDetail || !rows.some(item => item.id === state.pickingDetail.order.id))) {
    await loadPickingDetail(rows[0].id);
  } else if (!rows.length) {
    state.pickingDetail = null;
    document.getElementById("pickDetail").innerHTML = `<div class="empty">暂无明细</div>`;
    document.getElementById("pickActions").innerHTML = "";
  }
}

async function loadPickingDetail(id) {
  state.pickingDetail = await api(`/api/picking-orders/${id}`);
  const order = state.pickingDetail.order;
  const items = state.pickingDetail.items;
  document.getElementById("pickActions").innerHTML = `
    <button class="btn" id="printPick">打印A4</button>
    <button class="btn" id="exportPick">导出Excel</button>
    ${order.status === "pending" ? `<button class="btn success" id="completePick">拣货完成</button>` : ""}`;
  document.getElementById("pickDetail").innerHTML = `
    <div class="pick-detail">
      <div class="pick-head">
        <div><span class="muted">拣货单号</span><br><strong class="mono">${esc(order.pick_no)}</strong></div>
        <div><span class="muted">出库单号</span><br><strong class="mono">${esc(order.outbound_order_no)}</strong></div>
        <div><span class="muted">状态</span><br>${setStatus(order.status)}</div>
        <div><span class="muted">生成时间</span><br><strong>${formatTime(order.generated_at)}</strong></div>
      </div>
      ${items.length ? `<div class="table-wrap"><table><thead><tr><th>顺序</th><th>库位</th><th>商品ID</th><th>名称</th><th>规格</th><th>批次</th><th>数量</th></tr></thead>
      <tbody>${items.map(item => `<tr><td>${item.sequence}</td><td>${esc(item.location)}</td><td class="mono">${esc(item.product_id)}</td><td>${esc(item.product_name)}</td><td>${esc(item.spec || "-")}</td><td>${esc(item.batch_no)}</td><td>${item.quantity}</td></tr>`).join("")}</tbody></table></div>` : `<div class="empty">暂无明细</div>`}
    </div>`;
  document.getElementById("printPick").onclick = () => printPicking(state.pickingDetail);
  document.getElementById("exportPick").onclick = () =>
    download(`/api/picking-orders/${id}/export`, `picking-${order.pick_no}.xlsx`);
  const completeButton = document.getElementById("completePick");
  if (completeButton) completeButton.onclick = async () => {
    try {
      await api(`/api/picking-orders/${id}/complete`, { method: "POST", body: {} });
      toast("拣货完成");
      await loadPicking();
    } catch (error) { toast(error.message, true); }
  };
}

function printPicking(detail) {
  const order = detail.order;
  document.getElementById("printArea").innerHTML = `
    <h1>拣货单</h1>
    <div class="meta">拣货单号：${esc(order.pick_no)}　出库单号：${esc(order.outbound_order_no)}　生成时间：${formatTime(order.generated_at)}</div>
    <table><thead><tr><th>顺序</th><th>库位</th><th>商品ID</th><th>名称</th><th>规格</th><th>批次</th><th>数量</th></tr></thead>
    <tbody>${detail.items.map(item => `<tr><td>${item.sequence}</td><td>${esc(item.location)}</td><td>${esc(item.product_id)}</td><td>${esc(item.product_name)}</td><td>${esc(item.spec || "-")}</td><td>${esc(item.batch_no)}</td><td>${item.quantity}</td></tr>`).join("")}</tbody></table>`;
  window.print();
}

async function renderUsers() {
  document.getElementById("page").innerHTML = `
    <div class="panel">
      <h3>新增用户</h3>
      <form id="userForm" class="fields-row">
        <label>手机号<input name="phone" required></label>
        <label>角色<select name="role"><option value="worker">仓库人员</option><option value="admin">管理员</option></select></label>
        <label>初始密码<input name="password" type="password" required></label>
        <button class="btn primary" type="submit">创建</button>
      </form>
    </div>
    <div class="panel"><h3>账号列表</h3><div id="userTable"></div></div>`;
  document.getElementById("userForm").onsubmit = async event => {
    event.preventDefault();
    try {
      await api("/api/users", { method: "POST", body: Object.fromEntries(new FormData(event.target)) });
      toast("用户创建成功");
      await renderUsers();
    } catch (error) { toast(error.message, true); }
  };
  const users = await api("/api/users");
  document.getElementById("userTable").innerHTML = `
    <div class="table-wrap"><table><thead><tr><th>ID</th><th>手机号</th><th>角色</th><th>状态</th><th>创建时间</th><th>操作</th></tr></thead>
    <tbody>${users.map(user => `<tr>
      <td>${user.id}</td><td class="mono">${esc(user.phone)}</td>
      <td>${user.role === "admin" ? "管理员" : "仓库人员"}</td><td>${setStatus(user.status)}</td>
      <td class="nowrap">${formatTime(user.createdAt)}</td>
      <td><div class="actions">
        <button class="btn small" data-action="reset" data-id="${user.id}">重置密码</button>
        <button class="btn small ${user.status === "active" ? "danger" : "success"}" data-action="status" data-id="${user.id}" data-status="${user.status === "active" ? "disabled" : "active"}">
          ${user.status === "active" ? "禁用" : "启用"}
        </button>
      </div></td>
    </tr>`).join("")}</tbody></table></div>`;
  document.querySelectorAll("#userTable button[data-action]").forEach(button => {
    button.onclick = async () => {
      const id = Number(button.dataset.id);
      try {
        if (button.dataset.action === "reset") {
          const password = prompt("请输入新初始密码（8-20位，含字母和数字）");
          if (!password) return;
          await api(`/api/users/${id}/reset-password`, { method: "POST", body: { password } });
          toast("密码已重置");
        } else {
          await api(`/api/users/${id}/status`, {
            method: "POST", body: { status: button.dataset.status },
          });
          toast("状态已更新");
        }
        await renderUsers();
      } catch (error) { toast(error.message, true); }
    };
  });
}

async function renderTemplates() {
  if (!state.templates.length) state.templates = await api("/api/templates");
  const current = state.templates.find(item => item.type === state.templateType);
  document.getElementById("page").innerHTML = `
    <div class="panel">
      <div class="panel-header"><h3>模板列配置</h3>
        <button class="btn" id="downloadTemplate">下载当前模板</button></div>
      <div class="template-tabs">
        <button class="segment ${state.templateType === "inbound" ? "active" : ""}" data-type="inbound">入库模板</button>
        <button class="segment ${state.templateType === "outbound" ? "active" : ""}" data-type="outbound">出库模板</button>
      </div>
      <div class="table-wrap"><table><thead><tr><th>字段</th><th>Excel列名</th><th>类型</th><th>必填</th></tr></thead>
      <tbody>${current.columns.map((column, index) => `<tr>
        <td class="mono">${esc(column.key)}</td>
        <td><input data-index="${index}" data-field="name" value="${esc(column.name)}"></td>
        <td>${column.type}</td>
        <td><input type="checkbox" data-index="${index}" data-field="required" ${column.required ? "checked" : ""}></td>
      </tr>`).join("")}</tbody></table></div>
      <div class="actions" style="margin-top:12px">
        <button class="btn primary" id="saveTemplate">保存并生成新版本</button>
        <button class="btn" id="toggleTemplate">${current.status === "active" ? "停用模板" : "启用模板"}</button>
        <span class="muted">当前版本 v${current.version}</span>
        <span class="muted">状态：${current.status === "active" ? "启用" : "停用"}</span>
      </div>
    </div>`;
  document.querySelectorAll(".segment").forEach(button => {
    button.onclick = () => { state.templateType = button.dataset.type; renderTemplates(); };
  });
  document.getElementById("downloadTemplate").onclick = () =>
    download(`/api/templates/${state.templateType}/download`, `${state.templateType}-template.xlsx`);
  document.getElementById("saveTemplate").onclick = async () => {
    const columns = current.columns.map((column, index) => ({
      ...column,
      name: document.querySelector(`[data-index="${index}"][data-field="name"]`).value.trim(),
      required: document.querySelector(`[data-index="${index}"][data-field="required"]`).checked,
    }));
    try {
      const updated = await api(`/api/templates/${state.templateType}`, {
        method: "PUT", body: { columns },
      });
      state.templates = await api("/api/templates");
      toast(`模板已更新到 v${updated.version}`);
      await renderTemplates();
    } catch (error) { toast(error.message, true); }
  };

  document.getElementById("toggleTemplate").onclick = async () => {
    try {
      const status = current.status === "active" ? "disabled" : "active";
      await api(`/api/templates/${state.templateType}/status`, {
        method: "POST",
        body: { status },
      });
      state.templates = await api("/api/templates");
      toast(status === "active" ? "模板已启用" : "模板已停用");
      await renderTemplates();
    } catch (error) { toast(error.message, true); }
  };
}

async function renderLogs() {
  document.getElementById("page").innerHTML = `
    <div class="panel"><h3>操作日志</h3><div id="logTable"></div></div>`;
  const rows = await api("/api/audit-logs");
  document.getElementById("logTable").innerHTML = rows.length ? `
    <div class="table-wrap"><table><thead><tr><th>时间</th><th>操作人</th><th>动作</th><th>内容</th></tr></thead>
    <tbody>${rows.map(row => `<tr><td class="nowrap">${formatTime(row.created_at)}</td><td>${esc(row.user_name)}</td><td class="mono">${esc(row.action)}</td><td>${esc(row.detail || "-")}</td></tr>`).join("")}</tbody></table></div>` : `<div class="empty">暂无日志</div>`;
}

function renderPassword() {
  document.getElementById("page").innerHTML = `
    <div class="panel" style="max-width:520px">
      <h3>修改密码</h3>
      <form id="passwordForm" class="fields">
        <label>原密码<input name="oldPassword" type="password" required></label>
        <label>新密码<input name="newPassword" type="password" required></label>
        <label>确认新密码<input name="confirmPassword" type="password" required></label>
        <button class="btn primary" type="submit">保存</button>
        <p id="passwordError" class="form-error"></p>
      </form>
    </div>`;
  document.getElementById("passwordForm").onsubmit = async event => {
    event.preventDefault();
    const data = Object.fromEntries(new FormData(event.target));
    const errorNode = document.getElementById("passwordError");
    errorNode.textContent = "";
    if (data.newPassword !== data.confirmPassword) {
      errorNode.textContent = "两次输入的新密码不一致";
      return;
    }
    try {
      await api("/api/change-password", {
        method: "POST",
        body: { oldPassword: data.oldPassword, newPassword: data.newPassword },
      });
      state.user.mustChangePassword = false;
      toast("密码已修改");
      location.hash = "#dashboard";
    } catch (error) { errorNode.textContent = error.message; }
  };
}

async function renderPage() {
  const page = state.user.mustChangePassword && state.page !== "password" ? "password" : state.page;
  document.getElementById("pageTitle").textContent = titles[page] || titles.dashboard;
  renderNav();
  try {
    if (page === "dashboard") await renderDashboard();
    else if (page === "inbound") await renderImportPage("inbound");
    else if (page === "outbound") await renderImportPage("outbound");
    else if (page === "inventory") await renderInventory();
    else if (page === "picking") await renderPicking();
    else if (page === "users") await renderUsers();
    else if (page === "templates") await renderTemplates();
    else if (page === "logs") await renderLogs();
    else if (page === "password") renderPassword();
  } catch (error) {
    document.getElementById("page").innerHTML = `<div class="panel result-box error">${esc(error.message)}</div>`;
  }
}

document.getElementById("loginForm").onsubmit = async event => {
  event.preventDefault();
  const errorNode = document.getElementById("loginError");
  errorNode.textContent = "";
  try {
    const data = Object.fromEntries(new FormData(event.target));
    const result = await api("/api/login", { method: "POST", body: data });
    state.token = result.token;
    state.user = result.user;
    localStorage.setItem("wmsToken", result.token);
    showApp();
  } catch (error) { errorNode.textContent = error.message; }
};

document.getElementById("logoutButton").onclick = async () => {
  try { await api("/api/logout", { method: "POST", body: {} }); } catch (_) {}
  state.token = "";
  state.user = null;
  localStorage.removeItem("wmsToken");
  document.getElementById("appView").classList.add("hidden");
  document.getElementById("loginView").classList.remove("hidden");
};

document.querySelector(".topbar button[data-page]").onclick = () => location.hash = "#password";

window.addEventListener("hashchange", () => {
  const page = location.hash.slice(1) || "dashboard";
  const allowed = pages.some(item => item.id === page && (!item.admin || state.user?.role === "admin"));
  state.page = allowed ? page : "dashboard";
  if (state.user) renderPage();
});

(async function initialize() {
  if (!state.token) {
    document.getElementById("loginView").classList.remove("hidden");
    return;
  }
  try {
    state.user = await api("/api/me");
    showApp();
  } catch (_) {
    state.token = "";
    localStorage.removeItem("wmsToken");
    document.getElementById("loginView").classList.remove("hidden");
  }
})();
