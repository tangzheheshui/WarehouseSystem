const BASE_PATH = location.pathname === "/"
  ? ""
  : location.pathname
    .replace(/\/index\.html$/, "")
    .replace(/\/?$/, "/");
const TOKEN_KEY = `wmsToken:${BASE_PATH}`;
const PROJECT_KEY = `wmsProject:${BASE_PATH}`;
const INVENTORY_VIEW_KEY = `wmsInventoryView:${BASE_PATH}`;
const INVENTORY_COLUMNS_KEY = `wmsInventoryColumns:${BASE_PATH}`;

const state = {
  token: localStorage.getItem(TOKEN_KEY) || "",
  branding: { systemName: "万顺WMS仓库管理系统", companyName: "", subscription: null },
  user: null,
  projects: [],
  projectId: localStorage.getItem(PROJECT_KEY) || "",
  page: "dashboard",
  importResult: {},
  pickingDetail: null,
  templateType: "inbound",
  templates: [],
  templateProjects: [],
  templateProjectId: "",
  editingProduct: null,
  editingProject: null,
  editingUser: null,
  historyProjectId: null,
  inventoryOptions: null,
  inventoryRows: [],
  inventoryPage: 1,
  inventoryPageSize: 20,
  inventorySelectedIds: new Set(),
  inventorySelectedOnly: false,
  inventorySort: { key: null, direction: "asc" },
  inventoryView: localStorage.getItem(INVENTORY_VIEW_KEY) || "detail",
  inventoryVisibleColumns: new Set(
    JSON.parse(localStorage.getItem(INVENTORY_COLUMNS_KEY) || "[]"),
  ),
  pickSort: "location",
};

const titles = {
  dashboard: "看板",
  inbound: "入库管理",
  outbound: "出库管理",
  move: "移库管理",
  inventory: "库存查询",
  invAdjust: "库存调整",
  picking: "拣货单",
  warehouses: "仓库信息",
  orders: "单据管理",
  projects: "项目管理",
  users: "用户管理",
  settings: "系统设置",
  templates: "模板管理",
  logs: "操作日志",
  password: "修改密码",
};

const adminPages = [
  { id: "dashboard", label: "看板", group: "总览", tone: "blue" },
  { id: "warehouses", label: "仓库信息", group: "主数据", tone: "violet" },
  { id: "orders", label: "单据管理", group: "配置权限", tone: "amber" },
  { id: "projects", label: "项目管理", group: "配置权限", tone: "teal" },
  { id: "users", label: "用户管理", group: "配置权限", tone: "teal" },
  { id: "templates", label: "模板管理", group: "配置权限", tone: "violet" },
  { id: "settings", label: "系统设置", group: "配置权限", tone: "slate" },
];

const projectPages = [
  { id: "dashboard", label: "看板", group: "总览", tone: "blue" },
  { id: "inbound", label: "入库管理", group: "仓库作业", tone: "green" },
  { id: "outbound", label: "出库管理", group: "仓库作业", tone: "amber" },
  { id: "move", label: "移库管理", group: "仓库作业", tone: "cyan" },
  { id: "inventory", label: "库存查询", group: "库存中心", tone: "blue" },
  { id: "invAdjust", label: "库存调整", group: "库存中心", tone: "amber" },
  { id: "picking", label: "拣货单", group: "库存中心", tone: "teal" },
];

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, char => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[char]);
}

function formatTime(value) {
  return value ? String(value).replace("T", " ").slice(0, 19) : "-";
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
  if (state.projectId) settings.headers["X-Project-Id"] = state.projectId;
  if (settings.body && !(settings.body instanceof FormData)) {
    settings.headers["Content-Type"] = "application/json";
    settings.body = JSON.stringify(settings.body);
  }
  const response = await fetch(`${BASE_PATH}api/${path}`, settings);
  let data = {};
  try { data = await response.json(); } catch (_) {}
  if (response.status === 401 && state.token) {
    clearSession();
    location.reload();
  }
  if (!response.ok) {
    const error = new Error(data.error || "请求失败");
    error.details = data.details || [];
    error.status = response.status;
    throw error;
  }
  return data;
}

async function download(path, fallbackName) {
  const response = await fetch(`${BASE_PATH}api/${path}`, {
    headers: {
      Authorization: `Bearer ${state.token}`,
      ...(state.projectId ? { "X-Project-Id": state.projectId } : {}),
    },
  });
  if (!response.ok) {
    let data = {};
    try { data = await response.json(); } catch (_) {}
    throw new Error(data.error || "下载失败");
  }
  await saveBlob(response, fallbackName);
}

async function postDownload(path, body, fallbackName) {
  const response = await fetch(`${BASE_PATH}api/${path}`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${state.token}`,
      "Content-Type": "application/json",
      ...(state.projectId ? { "X-Project-Id": state.projectId } : {}),
    },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    let data = {};
    try { data = await response.json(); } catch (_) {}
    throw new Error(data.error || "下载失败");
  }
  await saveBlob(response, fallbackName);
}

async function saveBlob(response, fallbackName) {
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

function clearSession() {
  state.token = "";
  state.user = null;
  state.projects = [];
  state.projectId = "";
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(PROJECT_KEY);
}

function applyBranding() {
  const node = document.getElementById("loginCompanyName");
  const name = (state.branding.companyName || "").trim();
  node.textContent = name;
  node.classList.toggle("hidden", !name);
  // 订阅徽标：常显到期日，临期（≤30天）变警告色；永久/单公司部署不显示
  const badge = document.getElementById("subscriptionBadge");
  const subscription = state.branding.subscription;
  if (subscription && subscription.state !== "perpetual") {
    badge.textContent = subscription.state === "expiring"
      ? `订阅剩 ${subscription.daysLeft} 天`
      : `订阅到期 ${subscription.expiresAt}`;
    badge.title = `订阅到期日 ${subscription.expiresAt}`;
    badge.className = subscription.state === "expiring"
      ? "badge warning" : "badge";
  } else {
    badge.className = "badge hidden";
  }
}

async function loadBranding() {
  try {
    state.branding = await api("branding");
    applyBranding();
  } catch (_) {}
}

function allowedPages() {
  return state.user.role === "admin" ? adminPages : projectPages;
}

function renderNav() {
  const nav = document.getElementById("nav");
  const pages = allowedPages();
  const groups = [...new Set(pages.map(item => item.group))];
  nav.innerHTML = groups.map(group => `
    <div class="nav-group">
      <div class="nav-title">${esc(group)}</div>
      ${pages.filter(item => item.group === group).map(item => `
        <button data-page="${item.id}" data-tone="${item.tone || "blue"}"
          class="${state.page === item.id ? "active" : ""}">
          <span class="nav-dot"></span><span>${esc(item.label)}</span>
        </button>`).join("")}
    </div>
  `).join("");
  nav.querySelectorAll("button").forEach(button => {
    button.onclick = () => {
      // 离开项目信息页时退出商品管理视图，下次进入回到项目列表
      location.hash = `#${button.dataset.page}`;
    };
  });
  document.getElementById("userName").textContent = state.user.displayName;
  document.getElementById("userRole").textContent = state.user.role === "admin"
    ? "系统管理员" : "项目管理员";
  document.getElementById("userAvatar").textContent = String(
    state.user.displayName || "?"
  ).slice(0, 1).toUpperCase();
}

function renderProjectSelect() {
  const button = document.getElementById("projectSelect");
  const menu = document.getElementById("projectMenu");
  const nameNode = document.getElementById("projectSwitcherName");
  const wrapper = document.getElementById("projectSelectWrap");
  if (state.user.role !== "project_admin") {
    button.classList.add("hidden");
    menu.classList.add("hidden");
    return;
  }
  button.classList.remove("hidden");

  const current = state.projects.find(project =>
    String(project.id) === String(state.projectId)
  );
  nameNode.textContent = current?.project_name || "请选择项目";
  button.classList.toggle("empty", !state.projects.length);
  button.setAttribute("aria-expanded", "false");
  menu.classList.add("hidden");
  document.getElementById("projectCount").textContent = `${state.projects.length} 个`;
  const list = document.getElementById("projectMenuList");
  list.innerHTML = state.projects.map(project => {
    const active = String(project.id) === String(state.projectId);
    return `<button type="button" class="project-option ${active ? "active" : ""}"
      role="option" aria-selected="${active}" data-project-id="${project.id}">
      <span class="project-option-name">${esc(project.project_name)}</span>
      <span class="project-option-check">✓</span>
    </button>`;
  }).join("");

  button.onclick = event => {
    event.stopPropagation();
    const willOpen = menu.classList.contains("hidden");
    menu.classList.toggle("hidden", !willOpen);
    button.setAttribute("aria-expanded", String(willOpen));
  };
  list.querySelectorAll("[data-project-id]").forEach(option => {
    option.onclick = event => {
      event.stopPropagation();
      state.projectId = option.dataset.projectId;
      localStorage.setItem(PROJECT_KEY, state.projectId);
      state.templates = [];
      menu.classList.add("hidden");
      renderPage();
    };
  });
}

function showApp() {
  document.getElementById("loginView").classList.add("hidden");
  document.getElementById("appView").classList.remove("hidden");
  renderNav();
  renderProjectSelect();
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
    reversed: "已撤销", active: "启用", disabled: "停用",
  };
  return `<span class="badge ${esc(value)}">${labels[value] || esc(value)}</span>`;
}

function metric(label, value, tone = "blue") {
  return `<div class="metric tone-${tone}"><span>${label}</span><strong>${esc(value)}</strong></div>`;
}

async function renderDashboard() {
  document.getElementById("page").innerHTML = `<div class="panel">加载中...</div>`;
  if (state.user.role === "admin") {
    const [data, projects, users] = await Promise.all([
      api("dashboard"), api("projects"), api("users"),
    ]);
    state.projects = projects;
    document.getElementById("page").innerHTML = `
      <div class="metrics">
        ${metric("项目管理员", data.users, "violet")}
        ${metric("有效项目", data.projects, "teal")}
        ${metric("有效商品", data.products, "blue")}
        ${metric("有效仓库", data.warehouses, "amber")}
      </div>
      <div class="panel">
        <div class="panel-header"><h3>项目列表</h3></div>
        ${simpleTable(
          ["项目", "状态", "项目管理员", "库存批次", "说明"],
          projects.map(row => [esc(row.project_name), setStatus(row.status),
            row.user_count, row.lot_count, esc(row.remark || "-")]),
        )}
      </div>
      <div class="panel">
        <div class="panel-header"><h3>用户列表</h3></div>
        ${simpleTable(
          ["姓名", "姓名全拼", "角色", "状态", "授权项目"],
          users.map(row => [esc(row.display_name), esc(row.username),
            row.role === "admin" ? "管理员" : "项目管理员", setStatus(row.status),
            projectNames(row.projectIds)]),
        )}
      </div>`;
  } else {
    const [data, orders, logs] = await Promise.all([
      api("dashboard"), api("orders"), api("audit-logs"),
    ]);
    document.getElementById("page").innerHTML = `
      <div class="metrics">
        ${metric("库存批次", data.lots, "blue")}
        ${metric("库存总数", data.quantity, "teal")}
        ${metric("今日单据", data.todayOrders, "violet")}
        ${metric("待拣货", data.pendingPicking, "amber")}
      </div>
      <div class="panel"><h3>最近单据</h3><div id="recentOrders"></div></div>
      <div class="panel"><h3>最近操作</h3>${simpleTable(
        ["时间", "操作人", "操作", "内容"],
        logs.slice(0, 10).map(row => [
          `<span class="nowrap">${formatTime(row.created_at)}</span>`,
          esc(row.user_name), actionLabel(row.action), esc(row.detail || "-"),
        ]))}</div>`;
    document.getElementById("recentOrders").innerHTML = orderTable(orders.slice(0, 10));
  }
  document.querySelectorAll("[data-page]").forEach(button => {
    if (button.dataset.page) button.onclick = () => location.hash = `#${button.dataset.page}`;
  });
}

function projectNames(ids) {
  const names = state.projects.filter(project => ids.includes(project.id));
  return names.length ? names.map(project => esc(project.project_name)).join("、") : "-";
}

function simpleTable(headers, rows) {
  if (!rows.length) return `<div class="empty">暂无数据</div>`;
  return `<div class="table-wrap"><table>
    <thead><tr>${headers.map(item => `<th>${item}</th>`).join("")}</tr></thead>
    <tbody>${rows.map(row => `<tr>${row.map(item => `<td>${item}</td>`).join("")}</tr>`).join("")}</tbody>
  </table></div>`;
}

function orderTable(rows) {
  if (!rows.length) return `<div class="empty">暂无单据</div>`;
  return `<div class="table-wrap"><table>
    <thead><tr><th>单号</th><th>类型</th><th>状态</th><th>操作人</th><th>时间</th></tr></thead>
    <tbody>${rows.map(row => `<tr>
      <td class="mono">${esc(row.orderNo)}</td>
      <td>${orderType(row.type)}</td>
      <td>${setStatus(row.status)}</td>
      <td>${esc(row.operatorName)}</td>
      <td class="nowrap">${formatTime(row.createdAt)}</td>
    </tr>`).join("")}</tbody></table></div>`;
}

function orderType(value) {
  return {
    inbound: "入库", outbound: "出库", move: "移库", history_import: "历史导入",
  }[value] || value;
}

function importResultHtml(result, type) {
  if (!result || !result.visible) return "";
  if (result.ok) {
    return `<div class="result-box">
      <div class="result-title">
        <span>${IMPORT_LABELS[type] || type}成功</span>
        <span class="mono">${esc(result.orderNo)}</span>
      </div>
      <div>${result.aggregated.map(item => {
        const detail = item.batchNo ? ` / ${item.batchNo}` : "";
        const lane = item.from ? `：${esc(item.from)} → ${esc(item.to)}，` : "：";
        return `${esc(item.productCode)}${esc(detail)}${lane}${esc(item.quantity)}`;
      }).join("；")}</div>
      ${type === "outbound" && result.pickNo ? `<div>拣货单：${esc(result.pickNo)}</div>` : ""}
    </div>`;
  }
  return `<div class="result-box error">
    <div class="result-title"><span>${esc(result.error)}</span>
      <button class="btn small" id="downloadErrors">下载错误明细</button></div>
    ${simpleTable(["行号", "列名", "错误"], result.details.map(item => [
      item.row, esc(item.column), esc(item.message),
    ]))}
  </div>`;
}

const IMPORT_LABELS = { inbound: "入库", outbound: "出库", move: "移库" };

async function renderImportPage(type) {
  const label = IMPORT_LABELS[type] || type;
  const result = state.importResult[type] || {};
  const manualFields = type === "inbound"
    ? [
        ["orderNo", "入库通知单号", "text"],
        ["orderDate", "入库日期", "date"],
        ["productCode", "货品编号", "text"],
        ["qualityStatus", "品质标记", "quality"],
        ["batchNo", "生产批号", "text"],
        ["productionDate", "生产日期", "date"],
        ["expiryDate", "到期日期", "date"],
        ["quantity", "数量", "number"],
        ["unit", "单位", "text"],
        ["locationName", "货位", "text"],
        ["purchaseOrderNo", "采购单号", "text"],
        ["note", "备注", "text"],
      ]
    : type === "move"
    ? [
        ["orderNo", "移库单号", "text"],
        ["orderDate", "移库日期", "date"],
        ["productCode", "货品编号", "text", "moveProductList"],
        ["batchNo", "生产批号", "text", "moveBatchList"],
        ["quantity", "数量", "number"],
        ["unit", "单位", "text"],
        ["fromLocation", "源货位", "text", "moveLocationList"],
        ["toLocation", "目标货位", "text", "moveLocationList"],
        ["note", "备注", "text"],
      ]
    : [
        ["orderNo", "配货单号", "text"],
        ["orderDate", "出库日期", "date"],
        ["productCode", "货品编号", "text"],
        ["qualityStatus", "品质", "quality"],
        ["batchNo", "生产批号", "text"],
        ["productionDate", "生产日期", "date"],
        ["quantity", "数量", "number"],
        ["unit", "单位", "text"],
        ["locationName", "指定货位", "text"],
        ["recipient", "收货方", "text"],
        ["note", "备注", "text"],
      ];
  document.getElementById("page").innerHTML = `
    ${type !== "move" ? `
    <div class="panel">
      <div class="panel-header"><h3>${label}导入</h3>
        <button class="btn" id="downloadTemplate">下载模板</button></div>
      <form id="importForm" class="fields-row wide">
        <label>Excel文件<input type="file" name="file" accept=".xlsx" required></label>
        <button class="btn primary" type="submit">上传校验并${label}</button>
      </form>
    </div>` : ""}
    ${importResultHtml(result, type)}
    ${type === "move" ? `
    <div class="panel">
      <div class="panel-header"><h3>筛选库存批量移库</h3>
        <button class="btn primary" id="openMoveBulk">从库存筛选批次</button></div>
      <p class="form-hint" style="padding:0 16px 12px">按货品、批次、货位、品质筛选库存，勾选后一次生成一张移库单；不依赖模板，所选批次各自占一行。</p>
    </div>` : ""}
    ${type !== "move" ? `
    <div class="panel">
      <div class="panel-header"><h3>手动新增${label}</h3></div>
      <form id="manualForm" class="fields-row wide">
        ${manualFields.map(([name, fieldLabel, kind, listId]) => {
          if (kind === "quality") {
            return `<label>${fieldLabel}<select name="${name}">
              <option value="良品">良品</option><option value="扣留">扣留</option>
            </select></label>`;
          }
          const attrs = kind === "date" ? " type=\"date\"" : kind === "number" ? " type=\"number\" min=\"1\" step=\"1\"" : "";
          const listAttr = listId ? ` list="${listId}"` : "";
          return `<label>${fieldLabel}<input name="${name}"${attrs}${listAttr}></label>`;
        }).join("")}
        <button class="btn primary" type="submit">提交</button>
      </form>
    </div>` : ""}
    <div class="panel">
      <h3>${label}单${type === "move" ? "（按时间倒序，暂不提供筛选）" : ""}</h3>
      ${type !== "move" ? `
      <form id="orderFilter" class="fields-row">
        <label>单号/操作人<input name="keyword"></label>
        <label>开始日期<input name="startDate" type="date"></label>
        <label>结束日期<input name="endDate" type="date"></label>
        <button class="btn" type="submit">查询</button>
      </form>` : ""}
      <div id="orderList" class="table-top"></div>
    </div>`;
  if (document.getElementById("downloadTemplate")) {
    document.getElementById("downloadTemplate").onclick = () =>
      download(`templates/${type}/download`, `${type}-template.xlsx`);
  }
  if (type === "move") {
    document.getElementById("openMoveBulk").onclick = openMoveBulkPick;
  }
  if (document.getElementById("importForm")) document.getElementById("importForm").onsubmit = async event => {
    event.preventDefault();
    const button = event.target.querySelector("button[type=submit]");
    button.disabled = true;
    button.textContent = "处理中...";
    try {
      const data = await api(`${type}/upload`, {
        method: "POST",
        body: new FormData(event.target),
      });
      state.importResult[type] = {
        visible: true, ok: true, orderNo: data.orderNo,
        pickNo: data.pickingOrder?.pick_no, aggregated: data.aggregated || [],
      };
      await renderImportPage(type);
      toast(`${label}成功：${data.orderNo}`);
    } catch (error) {
      state.importResult[type] = {
        visible: true, ok: false, error: error.message,
        details: error.details || [],
      };
      await renderImportPage(type);
      toast(error.message, true);
    }
  };
  if (document.getElementById("manualForm")) document.getElementById("manualForm").onsubmit = async event => {
    event.preventDefault();
    const button = event.target.querySelector("button[type=submit]");
    button.disabled = true;
    button.textContent = "处理中...";
    const body = Object.fromEntries(new FormData(event.target));
    if (body.quantity) body.quantity = Number(body.quantity);
    try {
      const data = await api(`${type}/manual`, { method: "POST", body });
      state.importResult[type] = {
        visible: true, ok: true, orderNo: data.orderNo,
        pickNo: data.pickNo || data.pickingOrder?.pick_no,
        aggregated: data.aggregated || [],
      };
      await renderImportPage(type);
      toast(`${label}成功：${data.orderNo}`);
    } catch (error) {
      state.importResult[type] = {
        visible: true, ok: false, error: error.message,
        details: error.details || [],
      };
      await renderImportPage(type);
      toast(error.message, true);
    }
  };
  const errorButton = document.getElementById("downloadErrors");
  if (errorButton) errorButton.onclick = () => {
    postDownload("import-errors/export", {
      details: state.importResult[type].details,
    }, "import-errors.xlsx");
  };
  if (document.getElementById("orderFilter")) document.getElementById("orderFilter").onsubmit = event => {
    event.preventDefault();
    loadImportOrders(type, new FormData(event.target));
  };
  await loadImportOrders(type, document.getElementById("orderFilter")
    ? new FormData(document.getElementById("orderFilter")) : new FormData());
  if (type === "move" && document.getElementById("moveProductList")) attachMoveDatalists();
}

async function loadImportOrders(type, form) {
  const query = new URLSearchParams(form);
  query.set("type", type);
  const orders = await api(`orders?${query}`);
  document.getElementById("orderList").innerHTML = orderTable(orders);
}

async function attachMoveDatalists() {
  let options;
  try {
    options = await api("inventory/filters");
  } catch (_) {
    return;
  }
  const locationList = document.getElementById("moveLocationList");
  const productList = document.getElementById("moveProductList");
  const batchList = document.getElementById("moveBatchList");
  if (!locationList || !productList || !batchList) return;
  const locations = new Set();
  const products = new Map();
  const batchesByProduct = {};
  for (const record of options.records || []) {
    locations.add(record.location_name);
    if (record.cargo_area) {
      locations.add(`${record.cargo_area}/${record.location_name}`);
    }
    if (!products.has(record.product_id)) {
      products.set(
        record.product_id,
        `${record.product_id} ${record.product_name || ""}`.trim(),
      );
    }
    if (record.batch_no) {
      if (!batchesByProduct[record.product_id]) {
        batchesByProduct[record.product_id] = new Set();
      }
      batchesByProduct[record.product_id].add(record.batch_no);
    }
  }
  locationList.innerHTML = [...locations].sort().map(value =>
    `<option value="${esc(value)}"></option>`).join("");
  productList.innerHTML = [...products].map(([code, text]) =>
    `<option value="${esc(code)}">${esc(text)}</option>`).join("");
  const codeInput = document.querySelector("#manualForm input[name=productCode]");
  const refreshBatches = () => {
    const code = codeInput ? codeInput.value.trim() : "";
    const known = batchesByProduct[code]
      || new Set(Object.keys(batchesByProduct).flatMap(
        key => [...batchesByProduct[key]]));
    batchList.innerHTML = [...known].sort().map(value =>
      `<option value="${esc(value)}"></option>`).join("");
  };
  refreshBatches();
  if (codeInput) codeInput.addEventListener("input", refreshBatches);
}

// ---- 库存调整：独立页面，筛选 → 勾选批次 → 批次修正/品质/禁出 ----
async function renderInvAdjust() {
  if (state.user.role === "admin") {
    if (!state.invProjects?.length) state.invProjects = await api("projects");
    if (!state.invProjects.some(project =>
        String(project.id) === String(state.invProjectId))) {
      state.invProjectId = state.invProjects[0]?.id || "";
    }
  }
  if (!state.invAdjustSelected) state.invAdjustSelected = new Set();
  const projectQuery = state.user.role === "admin"
    ? `?projectId=${state.invProjectId}` : "";
  state.inventoryOptions = await api(`inventory/filters${projectQuery}`);
  const records = state.inventoryOptions.records || [];
  const uniq = key => [...new Set(records.map(r => r[key]).filter(Boolean))].sort();
  const f = state.invAdjustFilters || {};
  const chip = (key, label, values) => `
    <label>${label}<select name="${key}"><option value="">全部</option>${values.map(value =>
      `<option ${f[key] === value ? "selected" : ""}>${esc(value)}</option>`).join("")}</select></label>`;
  document.getElementById("page").innerHTML = `
    <div class="panel">
      <div class="panel-header"><h3>库存调整</h3>
        ${state.user.role === "admin" ? `
          <select class="input" style="width:auto;min-width:180px"
            onchange="state.invProjectId=Number(this.value);state.invAdjustFilters={};state.invAdjustSelected.clear();renderInvAdjust()">
            ${state.invProjects.map(project => `
              <option value="${project.id}" ${
                String(project.id) === String(state.invProjectId) ? "selected" : ""
              }>${esc(project.project_name)}</option>`).join("")}
          </select>` : ""}</div>
      <form id="invAdjustFilter" class="fields-row" style="padding:12px 16px 0">
        ${chip("productCode", "货品", uniq("product_id"))}
        ${chip("batchNo", "批次", uniq("batch_no"))}
        ${chip("locationName", "货位", uniq("location_name"))}
        ${chip("qualityStatus", "品质", uniq("quality_status"))}
        <button class="btn primary" type="submit">查询</button>
      </form>
      <div class="actions" style="padding:10px 16px">
        <span class="chip tone-violet">已选 <span id="invAdjustCount">0</span></span>
        <button class="btn small" id="invAdjustClear" disabled>清空勾选</button>
        <button class="btn small primary" id="invAdjustBtn" disabled>批次修正</button>
        <button class="btn small primary" id="invQualityBtn" disabled>品质调整</button>
        <button class="btn small primary" id="invProhibitBtn" disabled>禁出设置</button>
        <span class="form-hint" style="align-self:center">数量不在调整范围：录错了用「单据管理」撤销重录</span>
      </div>
      <div id="invAdjustResult"><div class="empty" style="padding:20px">点击「查询」加载库存批次</div></div>
    </div>`;
  document.getElementById("invAdjustFilter").onsubmit = async event => {
    event.preventDefault();
    state.invAdjustFilters = Object.fromEntries(new FormData(event.target));
    await loadInvAdjustRows();
  };
  document.getElementById("invAdjustClear").onclick = () => {
    state.invAdjustSelected.clear();
    renderInvAdjustTable();
  };
  document.getElementById("invAdjustBtn").onclick = openInventoryAdjust;
  document.getElementById("invQualityBtn").onclick = openInventoryQuality;
  document.getElementById("invProhibitBtn").onclick = openInventoryProhibit;
}

async function loadInvAdjustRows() {
  const f = state.invAdjustFilters || {};
  const params = new URLSearchParams();
  Object.entries({
    product_code: f.productCode, batch_no: f.batchNo,
    location_name: f.locationName, quality_status: f.qualityStatus,
  }).forEach(([key, value]) => { if (value) params.set(key, value); });
  if (state.user.role === "admin") params.set("projectId", state.invProjectId);
  state.inventoryRows = await api(`inventory?${params.toString()}`);
  state.invAdjustSelected.clear();
  renderInvAdjustTable();
}

function renderInvAdjustTable() {
  const host = document.getElementById("invAdjustResult");
  if (!host) return;
  const selected = state.invAdjustSelected;
  const rows = state.inventoryRows;
  const count = document.getElementById("invAdjustCount");
  if (count) count.textContent = selected.size;
  ["invAdjustBtn", "invQualityBtn", "invProhibitBtn", "invAdjustClear"]
    .forEach(id => { const node = document.getElementById(id); if (node) node.disabled = !selected.size; });
  if (!rows.length) {
    host.innerHTML = `<div class="empty" style="padding:20px">没有符合条件的库存批次</div>`;
    return;
  }
  host.innerHTML = `
    <div class="table-wrap"><table class="data-table">
      <thead><tr>
        <th class="center" style="width:40px"><input type="checkbox" class="table-check"
          onchange="invAdjustCheckAll(this)"></th>
        <th>货品</th><th>批次</th><th>货位</th><th>生产日期</th>
        <th>到期日期</th><th>品质</th><th>禁出</th><th>可用</th>
      </tr></thead>
      <tbody>${rows.map(row => `
        <tr>
          <td class="center"><input type="checkbox" class="table-check"
            data-inv-id="${row.id}" ${selected.has(String(row.id)) ? "checked" : ""}
            onchange="invAdjustCheck(this)"></td>
          <td><span class="mono">${esc(row.product_id)}</span> ${esc(row.product_name)}</td>
          <td>${esc(row.batch_no)}</td>
          <td>${esc(row.location_name)}</td>
          <td>${esc(row.production_date || "-")}</td>
          <td>${esc(row.expiry_date || "-")}</td>
          <td>${esc(row.quality_status)}</td>
          <td>${row.prohibit_outbound ? "是" : "否"}</td>
          <td>${row.available_quantity}</td>
        </tr>`).join("")}</tbody>
    </table></div>`;
}

function invAdjustCheck(box) {
  box.checked ? state.invAdjustSelected.add(box.dataset.invId)
              : state.invAdjustSelected.delete(box.dataset.invId);
  renderInvAdjustTable();
}
function invAdjustCheckAll(box) {
  document.querySelectorAll("#invAdjustResult tbody input[type=checkbox]").forEach(cb => {
    cb.checked = box.checked;
    box.checked ? state.invAdjustSelected.add(cb.dataset.invId)
                : state.invAdjustSelected.delete(cb.dataset.invId);
  });
  renderInvAdjustTable();
}

async function renderInventory() {
  if (state.user.role === "admin") {
    if (!state.invProjects?.length) state.invProjects = await api("projects");
    if (!state.invProjects.some(project =>
        String(project.id) === String(state.invProjectId))) {
      state.invProjectId = state.invProjects[0]?.id || "";
    }
  }
  const projectQuery = state.user.role === "admin"
    ? `?projectId=${state.invProjectId}` : "";
  document.getElementById("page").innerHTML = `<div class="panel">正在准备查询条件...</div>`;
  state.inventoryOptions = await api(`inventory/filters${projectQuery}`);
  document.getElementById("page").innerHTML = `
    <div class="panel filter-panel">
      ${state.user.role === "admin" ? `
        <div class="fields-row" style="padding:12px 16px 0">
          <label>项目（管理员可查看/操作各项目库存）<select
            onchange="state.invProjectId=Number(this.value);renderInventory()">
            ${state.invProjects.map(project => `
              <option value="${project.id}" ${
                String(project.id) === String(state.invProjectId) ? "selected" : ""
              }>${esc(project.project_name)}</option>`).join("")}
          </select></label>
        </div>` : ""}
      <form id="inventoryForm" class="compact-filter">
        <div class="filter-grid primary-filter-grid">
          <label>仓库<select name="warehouseName" data-option></select></label>
          <label>库区<select name="storageArea" data-option></select></label>
          <label>货区<select name="cargoArea" data-option></select></label>
          <label>货位<select name="locationName" data-option></select></label>
          <label>货品<select name="productCode" data-option></select></label>
          <label>生产批号<select name="batchNo" data-option></select></label>
        </div>
        <div class="filter-actions">
          <details id="advancedFilter" class="filter-popover">
            <summary>更多筛选</summary>
            <div class="filter-popover-panel">
              <label>品质标记<select name="qualityStatus" data-option></select></label>
              <label>禁止出库<select name="prohibitOutbound">
                <option value="">全部</option><option value="1">仅禁止</option><option value="0">仅允许</option>
              </select></label>
              <label>生产起<input type="date" name="productionStartDate"></label>
              <label>生产止<input type="date" name="productionEndDate"></label>
              <label>到期起<input type="date" name="expiryStartDate"></label>
              <label>到期止<input type="date" name="expiryEndDate"></label>
              <label>入库起<input type="date" name="inboundStartDate"></label>
              <label>入库止<input type="date" name="inboundEndDate"></label>
            </div>
          </details>
          <button class="btn primary" type="submit">查询</button>
          <button class="btn" type="button" id="resetInventory">重置</button>
        </div>
      </form>
    </div>
    <div class="panel result-panel">
      <div class="panel-header">
        <div>
          <div class="view-toggle" id="inventoryViewToggle">
            <button type="button" data-view="detail"
              class="${state.inventoryView === "detail" ? "active" : ""}">库存明细</button>
            <button type="button" data-view="summary"
              class="${state.inventoryView === "summary" ? "active" : ""}">库存汇总</button>
          </div>
          <div id="inventoryStats" class="table-stats"></div>
        </div>
        <div class="actions">
          <details id="columnSettings" class="filter-popover">
            <summary>显示列</summary>
            <div class="filter-popover-panel column-panel">
              ${inventoryColumns.map(column => `
                <label class="check-line"><input type="checkbox" data-column="${column.key}"
                  ${state.inventoryVisibleColumns.size === 0 || state.inventoryVisibleColumns.has(column.key) ? "checked" : ""}>
                  ${column.label}</label>`).join("")}
            </div>
          </details>
          <button class="btn" id="exportInventory">导出Excel</button>
        </div>
      </div>
      <div id="inventoryTable"></div>
    </div>`;

  const form = document.getElementById("inventoryForm");
  populateInventoryOptions(form);
  form.querySelectorAll("[data-option]").forEach(select => {
    select.onchange = () => populateInventoryOptions(form);
  });
  form.onsubmit = event => {
    event.preventDefault();
    document.getElementById("advancedFilter")?.removeAttribute("open");
    loadInventory(new FormData(form));
  };
  document.getElementById("resetInventory").onclick = () => {
    form.reset();
    populateInventoryOptions(form);
    document.getElementById("advancedFilter")?.removeAttribute("open");
    loadInventory(new FormData(form));
  };
  document.querySelectorAll("#inventoryViewToggle button[data-view]").forEach(button => {
    button.onclick = () => {
      state.inventoryView = button.dataset.view;
      localStorage.setItem(INVENTORY_VIEW_KEY, state.inventoryView);
      renderInventory();
    };
  });
  document.querySelectorAll("#columnSettings input[data-column]").forEach(checkbox => {
    checkbox.onchange = () => {
      if (checkbox.checked) state.inventoryVisibleColumns.add(checkbox.dataset.column);
      else state.inventoryVisibleColumns.delete(checkbox.dataset.column);
      localStorage.setItem(
        INVENTORY_COLUMNS_KEY,
        JSON.stringify([...state.inventoryVisibleColumns]),
      );
      renderInventoryTable();
    };
  });
  document.addEventListener("click", event => {
    const popover = document.getElementById("advancedFilter");
    if (popover?.open && !popover.contains(event.target)) popover.removeAttribute("open");
  });
  document.getElementById("exportInventory").onclick = () => {
    let query = new URLSearchParams(new FormData(form)).toString();
    if (state.inventoryView === "summary") query = `${query}&summary=1`;
    if (state.user.role === "admin") query = `projectId=${state.invProjectId}&${query}`;
    download(`inventory/export?${query}`, "库存查询结果.xlsx");
  };
  await loadInventory(new FormData(form));
}

// ---- 批次修正 / 品质调整 / 禁出开关（不改数量，全部留痕） ----
function invAdjustSelection() {
  const selected = state.page === "invAdjust"
    ? state.invAdjustSelected : state.inventorySelectedIds;
  return state.inventoryRows.filter(row => selected.has(String(row.id)));
}

function invAdjustProjectId() {
  return state.user.role === "admin" ? state.invProjectId : undefined;
}

function refreshInventoryAfterAdjust() {
  if (state.page === "invAdjust") { loadInvAdjustRows(); return; }
  const form = document.getElementById("inventoryForm");
  if (form) loadInventory(new FormData(form));
  else renderInventory();
}

function openInventoryAdjust() {
  const picked = invAdjustSelection();
  if (!picked.length) { toast("请先勾选要修正的批次", true); return; }
  const single = picked.length === 1 ? picked[0] : null;
  showModal("批次修正（" + picked.length + " 批）", `
    <p class="form-hint">数量不在修正范围内；留空的字段表示不修改。${single ? "" : "多选时按统一值应用。"}</p>
    <div class="field"><label>生产日期</label>
      <input type="date" id="fAdjProd" value="${esc(single?.production_date || "")}"></div>
    <div class="field"><label>到期日期</label>
      <input type="date" id="fAdjExp" value="${esc(single?.expiry_date || "")}"></div>
    <div class="field"><label>采购单号（清空请留空并勾选下方）</label>
      <input id="fAdjPo" value="${esc(single?.purchase_order_no || "")}"
        placeholder="${single?.purchase_order_no ? "留空表示不改" : "可留空"}">
      <label class="check-line" style="margin-top:4px">
        <input type="checkbox" id="fAdjPoClear"> 清空采购单号</label></div>`,
    `<button class="btn" onclick="closeModal()">取消</button>
     <button class="btn primary" id="fAdjSave">保存修正</button>`);
  document.getElementById("fAdjSave").onclick = async () => {
    const body = { ids: picked.map(row => row.id) };
    if (fAdjProd.value) body.productionDate = fAdjProd.value;
    if (fAdjExp.value) body.expiryDate = fAdjExp.value;
    if (document.getElementById("fAdjPoClear").checked) body.purchaseOrderNo = "";
    else if (fAdjPo.value !== (single?.purchase_order_no || ""))
      body.purchaseOrderNo = fAdjPo.value;
    try {
      const result = await api("inventory/adjust", { method: "POST", body: {
        ...body, projectId: invAdjustProjectId(),
      } });
      closeModal();
      toast(`已修正 ${result.adjusted} 个批次`);
      refreshInventoryAfterAdjust();
    } catch (error) { toast(error.message, true); }
  };
}

function openInventoryQuality() {
  const picked = invAdjustSelection();
  if (!picked.length) { toast("请先勾选要调整的批次", true); return; }
  showModal("品质调整（" + picked.length + " 批）", `
    <div class="field"><label>品质标记</label>
      <select id="fQuality"><option value="良品">良品</option>
        <option value="扣留">扣留</option></select></div>
    <p class="form-hint">品质调整会记入操作日志；数量不变。</p>`,
    `<button class="btn" onclick="closeModal()">取消</button>
     <button class="btn primary" id="fQualitySave">确认调整</button>`);
  document.getElementById("fQualitySave").onclick = async () => {
    try {
      const result = await api("inventory/quality", { method: "POST", body: {
        ids: picked.map(row => row.id), qualityStatus: fQuality.value,
        projectId: invAdjustProjectId(),
      } });
      closeModal();
      toast(`已调整 ${result.adjusted} 个批次`);
      refreshInventoryAfterAdjust();
    } catch (error) { toast(error.message, true); }
  };
}

function openInventoryProhibit() {
  const picked = invAdjustSelection();
  if (!picked.length) { toast("请先勾选要设置的批次", true); return; }
  showModal("禁止出库设置（" + picked.length + " 批）", `
    <div class="field"><label>出库状态</label>
      <select id="fProhibit"><option value="1">禁止出库</option>
        <option value="0">允许出库</option></select></div>
    <p class="form-hint">禁止出库的批次不可出库，仍可移库；调整记入操作日志。</p>`,
    `<button class="btn" onclick="closeModal()">取消</button>
     <button class="btn primary" id="fProhibitSave">确认设置</button>`);
  document.getElementById("fProhibitSave").onclick = async () => {
    try {
      const result = await api("inventory/prohibit-outbound", {
        method: "POST",
        body: { ids: picked.map(row => row.id),
          prohibit: Number(fProhibit.value),
          projectId: invAdjustProjectId() },
      });
      closeModal();
      toast(`已设置 ${result.adjusted} 个批次`);
      refreshInventoryAfterAdjust();
    } catch (error) { toast(error.message, true); }
  };
}

// ---- 筛选式批量移库：移库页内筛选库存 → 勾选 → 移库清单 → 一张移库单 ----
function openMoveBulkPick() {
  state.moveBulkSelected = new Set();
  state.moveBulkFilters = {};
  renderMoveBulkPick();
}

async function renderMoveBulkPick() {
  if (!state.moveBulkOptions) {
    state.moveBulkOptions = await api("inventory/filters");
  }
  const records = state.moveBulkOptions.records || [];
  const uniq = key => [...new Set(records.map(r => r[key]).filter(Boolean))].sort();
  const f = state.moveBulkFilters || {};
  const option = (key, values) => `
    <select name="${key}"><option value="">全部</option>${values.map(value =>
      `<option ${f[key] === value ? "selected" : ""}>${esc(value)}</option>`).join("")}</select>`;
  document.getElementById("page").innerHTML = `
    <div class="panel">
      <div class="panel-header"><h3>筛选库存批量移库</h3>
        <button class="btn" onclick="renderImportPage('move')">返回移库管理</button></div>
      <form id="moveBulkFilter" class="fields-row" style="padding:12px 16px 0">
        <label>货品${option("productCode", uniq("product_id"))}</label>
        <label>批次${option("batchNo", uniq("batch_no"))}</label>
        <label>货位${option("locationName", uniq("location_name"))}</label>
        <label>品质${option("qualityStatus", uniq("quality_status"))}</label>
        <button class="btn primary" type="submit">查询</button>
      </form>
      <div id="moveBulkResult"></div>
    </div>`;
  document.getElementById("moveBulkFilter").onsubmit = async event => {
    event.preventDefault();
    state.moveBulkFilters = Object.fromEntries(new FormData(event.target));
    await loadMoveBulkRows();
  };
  await loadMoveBulkRows();
}

async function loadMoveBulkRows() {
  const f = state.moveBulkFilters || {};
  const params = new URLSearchParams();
  Object.entries({
    product_code: f.productCode, batch_no: f.batchNo,
    location_name: f.locationName, quality_status: f.qualityStatus,
  }).forEach(([key, value]) => { if (value) params.set(key, value); });
  state.moveBulkRows = await api(`inventory?${params.toString()}`);
  renderMoveBulkTable();
}

function renderMoveBulkTable() {
  const rows = state.moveBulkRows.filter(row => row.available_quantity > 0);
  const host = document.getElementById("moveBulkResult");
  if (!host) return;
  if (!rows.length) {
    host.innerHTML = `<div class="empty" style="padding:20px">没有可用库存批次</div>`;
    return;
  }
  host.innerHTML = `
    <div class="table-top" style="padding:10px 16px 0">
      <span class="chip">可用批次 ${rows.length}</span>
      <span class="chip tone-violet">已选 <span id="moveBulkSelectedCount">${state.moveBulkSelected.size}</span></span>
      <button class="btn small primary" id="moveBulkNext"
        ${state.moveBulkSelected.size ? "" : "disabled"}>下一步：填写移库清单</button>
    </div>
    <div class="table-wrap"><table class="data-table">
      <thead><tr>
        <th class="center" style="width:40px"><input type="checkbox" class="table-check"
          onchange="moveBulkCheckAll(this)" ${rows.every(r => state.moveBulkSelected.has(String(r.id))) ? "checked" : ""}></th>
        <th>货品</th><th>批次</th><th>货位</th>
        <th>生产日期</th><th>到期日期</th><th>品质</th><th>可用</th>
      </tr></thead>
      <tbody>${rows.map(row => `
        <tr>
          <td class="center"><input type="checkbox" class="table-check"
            data-inv-id="${row.id}" ${state.moveBulkSelected.has(String(row.id)) ? "checked" : ""}
            onchange="moveBulkCheck(this)"></td>
          <td><span class="mono">${esc(row.product_id)}</span> ${esc(row.product_name)}</td>
          <td>${esc(row.batch_no)}</td>
          <td>${esc(row.location_name)}</td>
          <td>${esc(row.production_date || "-")}</td>
          <td>${esc(row.expiry_date || "-")}</td>
          <td>${esc(row.quality_status)}</td>
          <td>${row.available_quantity}</td>
        </tr>`).join("")}</tbody>
    </table></div>`;
  const next = document.getElementById("moveBulkNext");
  if (next) next.onclick = moveBulkToDraft;
}

function moveBulkCheck(box) {
  box.checked ? state.moveBulkSelected.add(box.dataset.invId)
              : state.moveBulkSelected.delete(box.dataset.invId);
  moveBulkSyncBar();
}
function moveBulkCheckAll(box) {
  document.querySelectorAll("#moveBulkResult tbody input[type=checkbox]").forEach(cb => {
    cb.checked = box.checked;
    box.checked ? state.moveBulkSelected.add(cb.dataset.invId)
                : state.moveBulkSelected.delete(cb.dataset.invId);
  });
  moveBulkSyncBar();
}
function moveBulkSyncBar() {
  const count = document.getElementById("moveBulkSelectedCount");
  if (count) count.textContent = state.moveBulkSelected.size;
  const next = document.getElementById("moveBulkNext");
  if (next) next.disabled = !state.moveBulkSelected.size;
}

function moveBulkToDraft() {
  const rows = state.moveBulkRows.filter(row =>
    state.moveBulkSelected.has(String(row.id)) && row.available_quantity > 0);
  if (!rows.length) { toast("请先勾选要移库的批次", true); return; }
  state.bulkMoveRows = rows.map(row => ({
    inventoryId: row.id, productId: row.product_id,
    productName: row.product_name, batchNo: row.batch_no,
    fromLocationId: row.location_id, fromLocation: row.location_name,
    unit: row.unit || "件", available: row.available_quantity,
    quantity: row.available_quantity, toLocation: "",
  }));
  state.bulkMoveSkipped = 0;
  renderBulkMoveDraft();
}

function renderBulkMoveDraft() {
  const rows = state.bulkMoveRows;
  const optionSource = state.moveBulkOptions || state.inventoryOptions;
  const locations = [...new Set((optionSource?.records || [])
    .map(record => record.location_name).filter(Boolean))];
  document.getElementById("page").innerHTML = `
    <div class="panel">
      <div class="panel-header">
        <h3>批量移库（已选 ${rows.length} 批${
          state.bulkMoveSkipped ? `，另有 ${state.bulkMoveSkipped} 批无可用库存已跳过` : ""}）</h3>
        <button class="btn" onclick="renderMoveBulkPick()">返回筛选结果</button>
      </div>
      <div class="fields-row" style="padding:12px 16px 0">
        <label>统一移到货位<input id="bulkTargetAll" list="bulkTargetList"
          placeholder="填写后应用到所有行，也可逐行改" oninput="applyBulkTargetAll(this.value)"></label>
      </div>
      <datalist id="bulkTargetList">${locations.map(name =>
        `<option value="${esc(name)}">`).join("")}</datalist>
      ${simpleTable(
        ["货品", "批次", "源货位", "可用", "移库数量", "目标货位"],
        rows.map((row, index) => [
          `<span class="mono">${esc(row.productId)}</span> ${esc(row.productName)}`,
          esc(row.batchNo), esc(row.fromLocation), row.available,
          `<input type="number" min="1" max="${row.available}" value="${row.quantity}"
            data-idx="${index}" data-field="quantity" style="width:90px">`,
          `<input list="bulkTargetList" data-idx="${index}" data-field="toLocation"
            value="${esc(row.toLocation)}" placeholder="目标货位">`,
        ]))}
      <div class="actions" style="padding:0 16px 16px">
        <button class="btn primary" id="submitBulkMove">生成移库单</button>
        <span class="form-hint">提交后走移库校验（批次一致性、目标货位须在项目库区），生成一张移库单</span>
      </div>
    </div>`;
  document.querySelectorAll("#page [data-idx]").forEach(input => {
    input.oninput = () => {
      const row = state.bulkMoveRows[Number(input.dataset.idx)];
      if (input.dataset.field === "quantity") row.quantity = Number(input.value);
      else row.toLocation = input.value.trim();
    };
  });
  document.getElementById("submitBulkMove").onclick = submitBulkMove;
}

function applyBulkTargetAll(value) {
  const target = value.trim();
  state.bulkMoveRows.forEach(row => { row.toLocation = target; });
  document.querySelectorAll('#page [data-field="toLocation"]').forEach(input => {
    input.value = target;
  });
}

async function submitBulkMove() {
  for (const row of state.bulkMoveRows) {
    if (!row.toLocation) {
      toast(`请填写 ${row.productId}（批次 ${row.batchNo}）的目标货位`, true);
      return;
    }
    if (!(row.quantity >= 1) || row.quantity > row.available) {
      toast(`${row.productId} 批次 ${row.batchNo} 移库数量须为 1~${row.available}`, true);
      return;
    }
  }
  try {
    const result = await api("move/bulk", {
      method: "POST",
      body: {
        projectId: state.user.role === "admin" ? state.invProjectId : undefined,
        items: state.bulkMoveRows.map(row => ({
          productId: row.productId, batchNo: row.batchNo,
          fromLocationId: row.fromLocationId, fromLocation: row.fromLocation,
          toLocation: row.toLocation, quantity: row.quantity, unit: row.unit,
        })),
      },
    });
    toast(`移库单 ${result.orderNo} 已生成，${state.bulkMoveRows.length} 批已移库`);
    state.moveBulkSelected = new Set();
    state.moveBulkRows = null;
    renderImportPage("move");
  } catch (error) { toast(error.message, true); }
}

function inventoryOptionRecords(form, currentField = "") {
  const values = Object.fromEntries(new FormData(form));
  const keys = {
    warehouseName: "warehouse_name",
    storageArea: "storage_area",
    cargoArea: "cargo_area",
    locationName: "location_name",
    productCode: "product_id",
    batchNo: "batch_no",
    qualityStatus: "quality_status",
  };
  const records = state.inventoryOptions?.records || [];
  return records.filter(record => Object.entries(keys).every(([field, key]) => {
    return field === currentField || !values[field] || String(record[key]) === values[field];
  }));
}

function setSelectOptions(select, options, placeholder) {
  const selected = select.value;
  select.innerHTML = `<option value="">${esc(placeholder)}</option>` + options.map(option =>
    `<option value="${esc(option.value)}" ${option.value === selected ? "selected" : ""}>${esc(option.label)}</option>`
  ).join("");
}

function populateInventoryOptions(form) {
  const definitions = [
    { name: "warehouseName", key: "warehouse_name", placeholder: "全部仓库" },
    { name: "storageArea", key: "storage_area", placeholder: "全部库区" },
    { name: "cargoArea", key: "cargo_area", placeholder: "全部货区" },
    { name: "locationName", key: "location_name", placeholder: "全部货位" },
    { name: "batchNo", key: "batch_no", placeholder: "全部批次" },
    { name: "qualityStatus", key: "quality_status", placeholder: "全部品质" },
  ];
  definitions.forEach(item => {
    const select = form.elements[item.name];
    const values = [...new Set(inventoryOptionRecords(form, item.name).map(row => row[item.key]))]
      .filter(value => value !== null && value !== undefined && value !== "")
      .sort((a, b) => String(a).localeCompare(String(b), "zh-CN"));
    setSelectOptions(select, values.map(value => ({ value, label: value })), item.placeholder);
  });

  const productSelect = form.elements.productCode;
  const products = new Map();
  inventoryOptionRecords(form, "productCode").forEach(row => {
    if (!products.has(row.product_id)) {
      products.set(row.product_id, `${row.product_id} · ${row.product_name}${row.spec ? ` / ${row.spec}` : ""}`);
    }
  });
  setSelectOptions(
    productSelect,
    [...products.entries()].map(([value, label]) => ({ value, label })),
    "全部货品",
  );
}

async function loadInventory(form) {
  const tableNode = document.getElementById("inventoryTable");
  if (tableNode) tableNode.innerHTML = `<div class="empty">正在查询库存...</div>`;
  let query = new URLSearchParams(form).toString();
  if (state.inventoryView === "summary") query = `${query}&summary=1`;
  if (state.user.role === "admin") query = `projectId=${state.invProjectId}&${query}`;
  state.inventoryRows = await api(`inventory?${query}`);
  state.inventoryPage = 1;
  if (!state.keepInventorySelection) {
    state.inventorySelectedIds.clear();
    state.inventorySelectedOnly = false;
  }
  state.keepInventorySelection = false;
  state.inventorySort = { key: null, direction: "asc" };
  renderInventoryTable();
}

function visibleInventoryColumns() {
  if (!state.inventoryVisibleColumns.size) return inventoryColumns;
  return inventoryColumns.filter(column =>
    state.inventoryVisibleColumns.has(column.key),
  );
}

function renderInventorySummaryTable(rows) {
  const columns = [
    { key: "product_id", label: "货品编号" },
    { key: "product_name", label: "货品名称" },
    { key: "total_quantity", label: "库存总数量" },
    { key: "available_quantity", label: "可用库存" },
    { key: "unit", label: "单位" },
    { key: "warehouse_name", label: "所属仓库" },
  ];
  const sorted = [...rows].sort((a, b) =>
    String(a.product_id).localeCompare(String(b.product_id), "zh-CN"),
  );
  const totalQuantity = sorted.reduce((sum, row) => sum + row.total_quantity, 0);
  const available = sorted.reduce((sum, row) => sum + row.available_quantity, 0);
  const stats = document.getElementById("inventoryStats");
  if (stats) stats.innerHTML = `
    <span class="chip">货品 ${sorted.length}</span>
    <span class="chip tone-blue">库存 ${totalQuantity}</span>
    <span class="chip tone-green">可用 ${available}</span>`;
  document.getElementById("inventoryTable").innerHTML = !sorted.length
    ? `<div class="empty">没有符合条件的库存</div>`
    : `<div class="inventory-table-wrap">
        <table class="data-table">
          <thead><tr>${columns.map(column =>
            `<th>${column.label}</th>`).join("")}</tr></thead>
          <tbody>${sorted.map(row => `<tr>
            <td class="mono product-code">${esc(row.product_id)}</td>
            <td>${esc(row.product_name)}</td>
            <td>${row.total_quantity}</td>
            <td>${row.available_quantity}</td>
            <td>${esc(row.unit || "-")}</td>
            <td>${esc(row.warehouse_name || "-")}</td>
          </tr>`).join("")}</tbody>
        </table>
      </div>`;
}

function inventoryExpiry(value) {
  if (!value) return { label: "-", className: "" };
  const expiry = new Date(`${String(value).slice(0, 10)}T00:00:00`);
  const days = Math.floor((expiry - new Date()) / 86400000);
  if (days < 0) return { label: `${value} · 已过期`, className: "danger" };
  if (days <= 30) return { label: `${value} · ${days}天`, className: "warning" };
  return { label: `${value} · ${days}天`, className: "success" };
}

const inventoryColumns = [
  { key: "warehouse_name", label: "仓库" },
  { key: "storage_area", label: "库区" },
  { key: "cargo_area", label: "货区" },
  { key: "location_name", label: "货位" },
  { key: "product_id", label: "货品编号" },
  { key: "product_name", label: "货品名称" },
  { key: "batch_no", label: "生产批号" },
  { key: "production_date", label: "生产日期", type: "date" },
  { key: "expiry_date", label: "到期日期", type: "date" },
  { key: "total_quantity", label: "库存总数", type: "number" },
  { key: "picking_reserved", label: "分拣占用", type: "number" },
  { key: "moving_reserved", label: "移动占用", type: "number" },
  { key: "available_quantity", label: "可用库存", type: "number" },
  { key: "quality_status", label: "品质标记" },
  { key: "prohibit_outbound", label: "禁止出库" },
  { key: "inbound_date", label: "入库日", type: "date" },
];

function inventoryCellValue(row, key) {
  if (key === "prohibit_outbound") return row.prohibit_outbound ? "是" : "否";
  return row[key];
}

function inventorySortValue(row, key) {
  const value = inventoryCellValue(row, key);
  if (value === null || value === undefined || value === "") return "";
  return value;
}

function sortedInventoryRows() {
  const { key, direction } = state.inventorySort;
  if (!key) return [...state.inventoryRows];
  const column = inventoryColumns.find(item => item.key === key);
  const factor = direction === "desc" ? -1 : 1;
  return [...state.inventoryRows].sort((a, b) => {
    const left = inventorySortValue(a, key);
    const right = inventorySortValue(b, key);
    let result;
    if (column?.type === "number") {
      result = (Number(left) || 0) - (Number(right) || 0);
    } else {
      result = String(left).localeCompare(String(right), "zh-CN", { numeric: true });
    }
    if (result === 0) result = Number(a.id) - Number(b.id);
    return result * factor;
  });
}

function inventoryTableCell(row, column) {
  const value = inventoryCellValue(row, column.key);
  if (column.type === "number") {
    return `<td class="numeric ${column.key === "available_quantity" ? "available" : ""}">${value}</td>`;
  }
  if (column.type === "date") {
    const expiry = column.key === "expiry_date" ? inventoryExpiry(row.expiry_date) : null;
    return `<td class="nowrap ${expiry ? expiry.className : ""}">${esc(value || "-")}${expiry ? `<small>${esc(expiry.label.split(" · ").slice(1).join(""))}</small>` : ""}</td>`;
  }
  if (column.key === "product_id") {
    return `<td class="mono product-code">${esc(value)}</td>`;
  }
  if (column.key === "quality_status") {
    return `<td><span class="badge ${value === "良品" ? "completed" : "warning"}">${esc(value)}</span></td>`;
  }
  if (column.key === "prohibit_outbound") {
    return `<td><span class="badge ${row.prohibit_outbound ? "disabled" : "active"}">${value ? "是" : "否"}</span></td>`;
  }
  return `<td>${esc(value || "-")}</td>`;
}

function renderInventoryTable() {
  if (state.inventoryView === "summary") {
    renderInventorySummaryTable(state.inventoryRows);
    return;
  }
  const rows = sortedInventoryRows();
  const pageSize = state.inventoryPageSize;
  const totalPages = Math.max(1, Math.ceil(rows.length / pageSize));
  state.inventoryPage = Math.min(Math.max(1, state.inventoryPage), totalPages);
  const currentPage = state.inventoryPage;
  const pageStart = (currentPage - 1) * pageSize;
  const pageRows = rows.slice(pageStart, pageStart + pageSize);
  const totalQuantity = rows.reduce((sum, row) => sum + row.total_quantity, 0);
  const available = rows.reduce((sum, row) => sum + row.available_quantity, 0);
  const reserved = rows.reduce((sum, row) => sum + row.picking_reserved + row.moving_reserved, 0);

  const stats = document.getElementById("inventoryStats");
  if (stats) stats.innerHTML = `
    <span class="chip">批次 ${rows.length}</span>
    <span class="chip tone-blue">库存 ${totalQuantity}</span>
    <span class="chip tone-green">可用 ${available}</span>
    <span class="chip tone-amber">占用 ${reserved}</span>`;



  const table = !rows.length ? `<div class="empty">没有符合条件的库存批次</div>` : `
    <div class="inventory-table-wrap">
      <table class="data-table inventory-table">
        <thead><tr>
          <th class="index-cell">#</th>
          ${visibleInventoryColumns().map(column => {
            const active = state.inventorySort.key === column.key;
            const mark = active ? (state.inventorySort.direction === "asc" ? "▲" : "▼") : "↕";
            return `<th class="sortable ${active ? "sort-active" : ""}" data-sort-key="${column.key}"
              title="按${column.label}排序">${column.label}<span class="sort-mark">${mark}</span></th>`;
          }).join("")}
        </tr></thead>
        <tbody>${pageRows.map((row, index) => {
          const expiry = inventoryExpiry(row.expiry_date);
          const selected = state.inventorySelectedIds.has(String(row.id));
          return `<tr class="${row.available_quantity <= 0 ? "row-muted" : ""}">
            <td class="index-cell">${pageStart + index + 1}</td>
            ${visibleInventoryColumns().map(column => inventoryTableCell(row, column)).join("")}
          </tr>`;
        }).join("")}</tbody>
      </table>
    </div>`;

  const start = rows.length ? pageStart + 1 : 0;
  const end = Math.min(rows.length, pageStart + pageSize);
  const pageButtons = [];
  for (let page = 1; page <= totalPages; page += 1) {
    if (page === 1 || page === totalPages || Math.abs(page - currentPage) <= 1) {
      pageButtons.push(page);
    } else if (pageButtons[pageButtons.length - 1] !== "…") {
      pageButtons.push("…");
    }
  }
  document.getElementById("inventoryTable").innerHTML = `${table}
    <div class="pagination">
      <span>第 ${start}-${end} 条 / 共 ${rows.length} 条</span>
      <div class="pagination-tools">
        <select id="inventoryPageSize" aria-label="每页条数">
          ${[10, 20, 50, 100].map(size => `<option value="${size}" ${size === pageSize ? "selected" : ""}>${size} 条/页</option>`).join("")}
        </select>
        <button class="btn small" data-page-action="first" ${currentPage === 1 ? "disabled" : ""}>首页</button>
        <button class="btn small" id="inventoryPrev" ${currentPage === 1 ? "disabled" : ""}>上一页</button>
        ${pageButtons.map(page => page === "…" ? '<span class="page-ellipsis">…</span>' : `<button class="btn small page-number ${page === currentPage ? "primary" : ""}" data-page-number="${page}">${page}</button>`).join("")}
        <button class="btn small" id="inventoryNext" ${currentPage === totalPages ? "disabled" : ""}>下一页</button>
        <button class="btn small" data-page-action="last" ${currentPage === totalPages ? "disabled" : ""}>末页</button>
      </div>
    </div>`;

  const pageSizeSelect = document.getElementById("inventoryPageSize");
  if (pageSizeSelect) pageSizeSelect.onchange = () => {
    state.inventoryPageSize = Number(pageSizeSelect.value);
    state.inventoryPage = 1;
    renderInventoryTable();
  };
  const prev = document.getElementById("inventoryPrev");
  const next = document.getElementById("inventoryNext");
  if (prev) prev.onclick = () => { state.inventoryPage -= 1; renderInventoryTable(); };
  if (next) next.onclick = () => { state.inventoryPage += 1; renderInventoryTable(); };
  document.querySelectorAll("[data-page-action]").forEach(button => {
    button.onclick = () => {
      if (!button.disabled) {
        state.inventoryPage = button.dataset.pageAction === "first" ? 1 : totalPages;
        renderInventoryTable();
      }
    };
  });
  document.querySelectorAll("[data-page-number]").forEach(button => {
    button.onclick = () => {
      state.inventoryPage = Number(button.dataset.pageNumber);
      renderInventoryTable();
    };
  });
  document.querySelectorAll(".inventory-table th[data-sort-key]").forEach(header => {
    header.onclick = () => {
      const key = header.dataset.sortKey;
      if (state.inventorySort.key === key) {
        state.inventorySort.direction = state.inventorySort.direction === "asc" ? "desc" : "asc";
      } else {
        state.inventorySort = { key, direction: "asc" };
      }
      state.inventoryPage = 1;
      renderInventoryTable();
    };
  });
}

async function renderPicking() {
  document.getElementById("page").innerHTML = `
    <div class="panel">
      <div class="panel-header"><h3>拣货单</h3>
        <div class="actions">
          <select id="pickStatus"><option value="">全部状态</option>
            <option value="pending">待拣货</option><option value="completed">已完成</option></select>
        </div></div>
      <div id="pickList"></div>
    </div>
    <div class="panel">
      <div class="panel-header"><h3>拣货明细</h3><div class="actions" id="pickActions"></div></div>
      <div id="pickDetail"></div>
    </div>`;
  document.getElementById("pickStatus").onchange = loadPicking;
  await loadPicking();
}

async function loadPicking() {
  const status = document.getElementById("pickStatus").value;
  const rows = await api(`picking-orders${status ? `?status=${status}` : ""}`);
  document.getElementById("pickList").innerHTML = simpleTable(
    ["分拣组号", "配货单号", "状态", "生成时间", "操作"],
    rows.map(row => [
      `<span class="mono">${esc(row.pick_no)}</span>`,
      `<span class="mono">${esc(row.outbound_order_no)}</span>`,
      setStatus(row.status), `<span class="nowrap">${formatTime(row.generated_at)}</span>`,
      `<button class="btn small" data-id="${row.id}">查看</button>`,
    ]),
  );
  document.querySelectorAll("#pickList button[data-id]").forEach(button => {
    button.onclick = () => loadPickingDetail(Number(button.dataset.id));
  });
  if (rows.length && (!state.pickingDetail || !rows.some(item =>
    item.id === state.pickingDetail.order.id))) {
    await loadPickingDetail(rows[0].id);
  } else if (!rows.length) {
    state.pickingDetail = null;
    document.getElementById("pickDetail").innerHTML = `<div class="empty">暂无明细</div>`;
    document.getElementById("pickActions").innerHTML = "";
  }
}

async function loadPickingDetail(id) {
  state.pickingDetail = await api(`picking-orders/${id}`);
  const order = state.pickingDetail.order;
  const items = state.pickingDetail.items;
  document.getElementById("pickActions").innerHTML = `
    <button class="btn" id="printPick">打印A4</button>
    ${order.status === "pending" ? `<button class="btn success" id="completePick">拣货完成</button>` : ""}`;
  const sortedItems = sortedPickItems(items);
  document.getElementById("pickDetail").innerHTML = `
    <div class="pick-detail">
      <div class="pick-head">
        <div><span class="muted">分拣组号</span><br><strong class="mono">${esc(order.pick_no)}</strong></div>
        <div><span class="muted">出库通知单号</span><br><strong class="mono">${esc(order.outbound_order_no)}</strong></div>
        <div><span class="muted">出货日期</span><br><strong>${esc(order.outbound_date || "-")}</strong></div>
        <div><span class="muted">收货方</span><br><strong>${esc(order.recipient || "-")}</strong></div>
        <div><span class="muted">状态</span><br>${setStatus(order.status)}</div>
        <div><span class="muted">生成时间</span><br><strong>${formatTime(order.generated_at)}</strong></div>
      </div>
      ${simpleTable(["序号", "货品编号", "货品名称", "生产日期", "生产批号", "品质", "货位", "分拣总数", "单位"],
        sortedItems.map((item, index) => [index + 1,
          `<span class="mono">${esc(item.product_id)}</span>`, esc(item.product_name),
          esc(item.production_date || "-"), esc(item.batch_no),
          esc(item.quality_status || "良品"), esc(item.location.full_name),
          item.quantity, esc(item.unit || "-")]))}
    </div>`;
  document.getElementById("printPick").onclick = () => printPicking(state.pickingDetail);
  const completeButton = document.getElementById("completePick");
  if (completeButton) completeButton.onclick = async () => {
    try {
      await api(`picking-orders/${id}/complete`, { method: "POST", body: {} });
      toast("拣货完成");
      await loadPicking();
    } catch (error) { toast(error.message, true); }
  };
}

function sortedPickItems(items) {
  return [...items].sort((a, b) => {
    const product = String(a.product_id).localeCompare(String(b.product_id), "zh-CN")
      || String(a.location.full_name).localeCompare(String(b.location.full_name), "zh-CN")
      || String(a.batch_no).localeCompare(String(b.batch_no), "zh-CN");
    if (state.pickSort === "product") return product;
    return String(a.location.full_name).localeCompare(String(b.location.full_name), "zh-CN")
      || String(a.product_id).localeCompare(String(b.product_id), "zh-CN")
      || String(a.batch_no).localeCompare(String(b.batch_no), "zh-CN");
  });
}

function printPicking(detail) {
  const order = detail.order;
  const items = sortedPickItems(detail.items);
  const pageSize = 18;
  const pages = [];
  for (let start = 0; start < items.length; start += pageSize) {
    pages.push(items.slice(start, start + pageSize));
  }
  const total = Math.max(1, pages.length);
  document.getElementById("printArea").innerHTML = `
    <h1>拣货单</h1>
    ${pages.map((pageItems, pageIndex) => `
      <div class="print-page">
        <div class="meta">
          <span>分拣组号：${esc(order.pick_no)}</span>
          <span>出库通知单号：${esc(order.outbound_order_no)}</span>
          <span>出货日期：${esc(order.outbound_date || "-")}</span>
          <span>收货方：${esc(order.recipient || "-")}</span>
        </div>
        <table>
          <thead><tr>
            <th>序号</th><th>货品编号</th><th>货品名称</th><th>生产日期</th>
            <th>生产批号</th><th>品质</th><th>货位</th><th>分拣总数</th><th>单位</th>
          </tr></thead>
          <tbody>
            ${pageItems.map((item, index) => `<tr>
              <td>${pageIndex * pageSize + index + 1}</td>
              <td>${esc(item.product_id)}</td>
              <td>${esc(item.product_name)}</td>
              <td>${esc(item.production_date || "-")}</td>
              <td>${esc(item.batch_no)}</td>
              <td>${esc(item.quality_status || "良品")}</td>
              <td>${esc(item.location.full_name)}</td>
              <td>${item.quantity}</td>
              <td>${esc(item.unit || "-")}</td>
            </tr>`).join("")}
          </tbody>
        </table>
        <div class="print-footer">1-${pageIndex + 1} · 第${pageIndex + 1}页/共${total}页</div>
      </div>`).join("")}`;
  window.print();
}

// 商品归属项目：在公司级页面删除独立商品页，改为项目详情内管理
async function renderWarehouses() {
  const warehouses = await api("warehouses");
  state.whCache = warehouses;
  state.whChecked = state.whChecked || new Set();
  if (!state.whSel || !warehouses.some(item => item.id === state.whSel.whId)) {
    state.whSel = {
      whId: warehouses[0]?.id,
      area: warehouses[0]?.areas[0]?.name || null,
      cargo: null,
    };
  }
  renderWhPage();
}

function curWh() { return state.whCache.find(item => item.id === state.whSel.whId); }
function curArea(wh = curWh()) {
  return wh?.areas.find(area => area.name === state.whSel.area);
}
function curCargo(wh = curWh()) {
  if (!state.whSel.cargo) return null;
  return wh?.cargoAreas.find(cargo =>
    cargo.storage_area_id === curArea(wh)?.id && cargo.name === state.whSel.cargo);
}
function whLocs(wh, areaName = null, cargoName = null) {
  return wh.locations.filter(loc =>
    (!areaName || loc.storage_area === areaName)
    && (!cargoName || loc.cargo_area === cargoName));
}

function renderWhPage() {
  const wh = curWh();
  if (!wh) {
    document.getElementById("page").innerHTML = `
      <div class="panel"><div class="empty" style="padding:40px;text-align:center;color:var(--muted)">
        还没有仓库
        <div style="margin-top:12px">
          <button class="btn primary" onclick="openWarehouseForm()">＋ 新增仓库</button>
        </div>
      </div></div>`;
    return;
  }
  const whStock = wh.locations.filter(loc =>
    loc.stock_quantity > 0 && loc.status === "active").length;
  const cargoList = wh.cargoAreas.filter(c => c.storage_area_id === curArea(wh)?.id);
  const locs = whLocs(wh, state.whSel.area, state.whSel.cargo);
  const keyword = (state.whKeyword || "").trim().toUpperCase();
  const shownLocs = keyword
    ? locs.filter(loc => loc.location_name.toUpperCase().includes(keyword))
    : locs;
  const showCargoCol = !state.whSel.cargo;

  document.getElementById("page").innerHTML = `
    <div class="panel wh-cascade">
      <div class="wh-row">
        <span class="wh-row-label">仓库</span>
        <span class="wh-chipbar">${state.whCache.map(item => `
          <span class="wh-chip ${item.id === wh.id ? "active" : ""}"
            onclick="selectWhNode(${item.id})">${esc(item.warehouse_name)}</span>`).join("")}
        </span>
        <span class="wh-row-actions">
          <button class="btn" onclick="openWarehouseForm(${wh.id})">修改仓库</button>
          <button class="btn outline-danger" onclick="toggleWarehouseStatus(${wh.id})">
            ${wh.status === "active" ? "停用" : "启用"}</button>
          <button class="btn primary" onclick="openWarehouseForm()">＋ 新仓库</button>
        </span>
        <span class="wh-row-sub">${esc(wh.address || "无地址")} · 联系人 ${esc(wh.contact_name || "-")} / ${esc(wh.contact_phone || "-")}
          · ${wh.areas.length} 个库区 · ${wh.locations.length} 个货位${whStock ? ` · ${whStock} 个有货` : ""}</span>
      </div>

      <div class="wh-row">
        <span class="wh-row-label">库区</span>
        <span class="wh-chipbar" id="whAreaChips">${wh.areas.map(area => `
          <span class="wh-chip ${state.whSel.area === area.name ? "active" : ""}"
            onclick="selectWhArea(${wh.id}, '${esc(area.name)}')">${esc(area.name)}</span>`).join("")}
        </span>
        <span class="wh-row-actions">
          <button class="btn primary" onclick="whInlineCreate('area')">＋ 建库区</button>
          <button class="btn" onclick="openRenameForm('area')">改名</button>
          <button class="btn outline-danger" onclick="deleteArea(${curArea(wh)?.id ?? 0})"
            ${state.whSel.area ? "" : "disabled"}>删除</button>
        </span>
      </div>

      <div class="wh-row">
        <span class="wh-row-label">货区</span>
        <span class="wh-chipbar" id="whCargoChips">
          <span class="wh-chip ${!state.whSel.cargo ? "active" : ""}"
            onclick="selectWhCargoAll()">全部</span>
          ${cargoList.map(cargo => `
            <span class="wh-chip ${state.whSel.cargo === cargo.name ? "active" : ""}"
              onclick="selectWhCargo(${wh.id}, '${esc(state.whSel.area)}', '${esc(cargo.name)}')">${esc(cargo.name)}</span>`).join("")}
        </span>
        <span class="wh-row-actions">
          <button class="btn primary" onclick="whInlineCreate('cargo')">＋ 建货区</button>
          ${state.whSel.cargo ? `
            <button class="btn" onclick="openRenameForm('cargo')">改名</button>
            <button class="btn outline-danger" onclick="deleteCargo(${curCargo(wh).id})">删除</button>` : ""}
        </span>
      </div>

      <div class="wh-row">
        <span class="wh-row-label">货位</span>
        <input class="input" style="width:260px" placeholder="搜索货位号…"
          value="${esc(state.whKeyword || "")}" oninput="whSearch(this.value)">
        <span class="form-hint">${shownLocs.length} 个货位 · ${
          shownLocs.filter(loc => loc.stock_quantity > 0 && loc.status === "active").length} 个有货</span>
        <span class="wh-row-actions">
          <button class="btn dashed" onclick="openGenForm('${esc(state.whSel.area || "")}', '${esc(state.whSel.cargo || "")}')">范围生成</button>
          <button class="btn primary" onclick="openLocForm('${esc(state.whSel.area || "")}', '${esc(state.whSel.cargo || "")}')">＋ 建货位</button>
        </span>
      </div>

      ${whLocTable(shownLocs, showCargoCol)}
      <div id="whBatchSlot"></div>
    </div>`;
  renderWhBatchBar();
}

function whSearch(value) {
  state.whKeyword = value;
  const table = whLocTable(
    (() => {
      const wh = curWh();
      const locs = whLocs(wh, state.whSel.area, state.whSel.cargo);
      const kw = value.trim().toUpperCase();
      return kw ? locs.filter(loc => loc.location_name.toUpperCase().includes(kw)) : locs;
    })(), !state.whSel.cargo);
  const old = document.querySelector(".wh-cascade .table-wrap");
  if (old) old.outerHTML = table;
}

/* 行内新建库区/货区：点「＋」原位变输入框，回车保存 */
function whInlineCreate(type) {
  if (type === "cargo" && !curArea()) {
    toast("请先创建库区,再在库区下建货区", true);
    return;
  }
  const bar = type === "area"
    ? document.getElementById("whAreaChips") : document.getElementById("whCargoChips");
  if (bar.querySelector(".wh-inline-box")) return;
  const box = document.createElement("span");
  box.className = "wh-inline-box";
  box.innerHTML = `
    <input class="input" style="width:130px" placeholder="${type === "area" ? "库区名" : "货区名"}">
    <button class="btn small primary">存</button>`;
  bar.appendChild(box);
  const input = box.querySelector("input");
  input.focus();
  const save = async () => {
    const name = input.value.trim();
    if (!name) { box.remove(); return; }
    try {
      if (type === "area") {
        await api(`warehouses/${state.whSel.whId}/areas`, {
          method: "POST", body: { name },
        });
        state.whSel.area = name;
      } else {
        await api(`areas/${curArea().id}/cargo-areas`, {
          method: "POST", body: { name },
        });
        state.whSel.cargo = name;
      }
      renderWhPage();
      toast(`${type === "area" ? "库区" : "货区"}「${name}」已创建`);
    } catch (error) { toast(error.message, true); }
  };
  box.querySelector("button").onclick = save;
  input.onkeydown = event => {
    if (event.key === "Enter") save();
    if (event.key === "Escape") box.remove();
  };
}

function selectWhNode(id) {
  const wh = state.whCache.find(item => item.id === id);
  state.whSel = { whId: id, area: wh.areas[0]?.name || null, cargo: null };
  state.whChecked.clear(); state.whKeyword = "";
  renderWhPage();
}
function selectWhArea(id, area) {
  if (state.whSel.area === area) return;
  state.whSel = { whId: id, area, cargo: null };
  state.whChecked.clear(); state.whKeyword = "";
  renderWhPage();
}
function selectWhCargo(id, area, cargo) {
  state.whSel.cargo = cargo;
  state.whChecked.clear(); state.whKeyword = "";
  renderWhPage();
}
function selectWhCargoAll() {
  state.whSel.cargo = null;
  state.whChecked.clear(); state.whKeyword = "";
  renderWhPage();
}

function whLocTable(locs, withCargoCol) {
  const checked = state.whChecked;
  const allOn = locs.filter(loc => loc.status === "active");
  const colN = 4 + (withCargoCol ? 1 : 0);
  return `
    <div class="table-wrap"><table>
      <thead><tr>
        <th class="center" style="width:40px"><input type="checkbox" ${allOn.length && allOn.every(loc => checked.has(loc.id)) ? "checked" : ""}
          onchange="whToggleAll(this.checked)"></th>
        <th>货位</th>
        ${withCargoCol ? "<th>所属货区</th>" : ""}
        <th>状态</th><th>库存</th><th style="width:120px">操作</th>
      </tr></thead>
      <tbody>${locs.length ? locs.map(loc => `
        <tr>
          <td class="center">${loc.status === "active"
            ? `<input type="checkbox" ${checked.has(loc.id) ? "checked" : ""}
              onchange="whCheck(${loc.id}, this.checked)">` : ""}</td>
          <td><b>${esc(loc.location_name)}</b></td>
          ${withCargoCol ? `<td>${esc(loc.cargo_area)}</td>` : ""}
          <td>${setStatus(loc.status)}</td>
          <td>${loc.stock_quantity > 0 ? `${loc.stock_quantity} 件` : '<span class="muted">-</span>'}</td>
          <td>
            <a class="link-act" onclick="whDisableOne(${loc.id})">${loc.status === "active" ? "停用" : "启用"}</a>
            <a class="link-act danger" onclick="whDeleteOne(${loc.id})">删除</a>
          </td>
        </tr>`).join("") : `<tr><td colspan="${colN}" class="empty">该货区还没有货位</td></tr>`}
      </tbody></table></div>`;
}

function whToggleAll(on) {
  const wh = curWh();
  const locs = state.whSel.cargo
    ? whLocs(wh, state.whSel.area, state.whSel.cargo)
    : whLocs(wh, state.whSel.area);
  locs.filter(loc => loc.status === "active")
    .forEach(loc => on ? whCheckedAdd(loc.id) : state.whChecked.delete(loc.id));
  renderWhPage();
}
function whCheckedAdd(id) { state.whChecked.add(id); renderWhBatchBar(); }
function whCheck(id, on) {
  on ? state.whChecked.add(id) : state.whChecked.delete(id);
  renderWhBatchBar();
}
function renderWhBatchBar() {
  const slot = document.getElementById("whBatchSlot");
  if (!slot) return;
  const wh = curWh();
  const picked = wh.locations.filter(loc => state.whChecked.has(loc.id));
  if (!picked.length) { slot.innerHTML = ""; return; }
  const withStock = picked.filter(loc => loc.stock_quantity > 0 && loc.status === "active");
  slot.innerHTML = `
    <div class="wh-batchbar">
      已选 ${picked.length} 个货位${withStock.length
        ? `(其中 <b style="color:#ffd591">${withStock.length} 个有库存</b>)` : ""}
      <span style="flex:1"></span>
      <button class="btn small" onclick="whBatchStatus('active')">启用</button>
      <button class="btn small danger" onclick="whBatchDisable()">停用</button>
      <button class="btn small danger" onclick="whBatchDelete()">删除</button>
      <button class="btn small" onclick="state.whChecked.clear();renderWhDetail()">取消选择</button>
    </div>`;
}

function whConfirm(title, bodyHtml, buttons) {
  showModal(title, bodyHtml, buttons.map((btn, idx) =>
    `<button class="btn ${btn.danger ? "danger" : ""}" data-idx="${idx}"
      ${btn.primary ? 'data-primary="1"' : ""}>${esc(btn.label)}</button>`).join(""));
  document.querySelectorAll("#modalFoot .btn").forEach(node => {
    node.onclick = () => {
      closeModal();
      buttons[Number(node.dataset.idx)].onClick?.();
    };
  });
}

/* 单个/批量停用共用:有库存弹确认,可仅停空货位 */
async function applyDisable(targets, { clearChecked = false } = {}) {
  for (const loc of targets) {
    try {
      await api(`warehouse-locations/${loc.id}/status`, {
        method: "POST", body: { status: "disabled" },
      });
    } catch (error) { toast(`${loc.location_name}: ${error.message}`, true); }
  }
  if (clearChecked) state.whChecked.clear();
  await renderWarehouses();
  toast(`已停用 ${targets.length} 个货位`);
}

function whDisableOne(id) {
  const wh = curWh();
  const loc = wh.locations.find(item => item.id === id);
  if (!loc) return;
  if (loc.status === "disabled") {
    applyEnable([loc]);
    return;
  }
  const free = [];
  const inUse = loc.stock_quantity > 0 ? [loc] : [];
  if (inUse.length) {
    confirmDisable(inUse, free, mode => {
      applyDisable(mode === "free" ? free : pickedAll(inUse, free));
    });
  } else {
    applyDisable([loc]);
  }
}
function pickedAll(inUse, free) { return [...inUse, ...free]; }

function applyEnable(targets) {
  whBatchStatusTo(targets, "active", "已启用");
}

async function whBatchDisable() {
  const wh = curWh();
  const picked = wh.locations.filter(loc =>
    state.whChecked.has(loc.id) && loc.status === "active");
  if (!picked.length) { toast("请先勾选要停用的货位", true); return; }
  const inUse = picked.filter(loc => loc.stock_quantity > 0);
  const free = picked.filter(loc => !loc.stock_quantity);
  const run = mode => {
    const targets = mode === "free" ? free : picked;
    applyDisable(targets, { clearChecked: true });
  };
  if (inUse.length) {
    confirmDisable(inUse, free, run, picked.length);
  } else {
    run("all");
  }
}

function confirmDisable(inUse, free, onConfirm, total) {
  whConfirm("停用确认 — 有货位正在使用", `
    <div class="wh-impact">
      选中的货位中 <b>${inUse.length} 个有库存</b>(${inUse.map(loc =>
      `${loc.location_name} ${loc.stock_quantity} 件`).join("、")}):
    </div>
    <p class="form-hint">停用后这些货位不能再收发货,库存不受影响,可随时重新启用。</p>
    ${free.length ? `<p class="form-hint">另有 ${free.length} 个空货位将一并停用。</p>` : ""}`,
    [
      ...(free.length && inUse.length
        ? [{ label: `仅停用空货位(${free.length})`, onClick: () => onConfirm("free") }]
        : []),
      { label: `全部停用(${total ?? pickedAll(inUse, free).length})`, danger: true,
        onClick: () => onConfirm("all") },
    ]);
}

async function whBatchStatusTo(targets, status, label) {
  for (const loc of targets) {
    try {
      await api(`warehouse-locations/${loc.id}/status`, {
        method: "POST", body: { status },
      });
    } catch (error) { toast(`${loc.location_name}: ${error.message}`, true); }
  }
  state.whChecked.clear();
  await renderWarehouses();
  toast(`${label} ${targets.length} 个货位`);
}

function whBatchEnable() {
  const wh = curWh();
  const picked = wh.locations.filter(loc =>
    state.whChecked.has(loc.id) && loc.status === "disabled");
  if (!picked.length) { toast("请先勾选要启用的货位(已启用的不在范围内)", true); return; }
  whBatchStatusTo(picked, "active", "已启用");
}

function whDeleteOne(id) {
  const wh = curWh();
  const loc = wh.locations.find(item => item.id === id);
  if (!loc) return;
  if (loc.stock_quantity > 0) {
    toast(`「${loc.location_name}」有 ${loc.stock_quantity} 件库存,不能删除,请改用停用`, true);
    return;
  }
  whConfirm("删除确认",
    `<p>确认删除货位 ${esc(loc.storage_area)} / ${esc(loc.cargo_area)} / <b>${esc(loc.location_name)}</b>?删除后不可恢复。</p>`,
    [
      { label: "取消" },
      { label: "删除", danger: true, onClick: async () => {
        try {
          const result = await api("warehouse-locations/delete", {
            method: "POST", body: { ids: [id] },
          });
          if (result.blocked.length) {
            toast(result.blocked[0].reason, true);
          } else {
            toast("货位已删除");
          }
          await renderWarehouses();
        } catch (error) { toast(error.message, true); }
      } },
    ]);
}

function whBatchDelete() {
  const wh = curWh();
  const picked = wh.locations.filter(loc => state.whChecked.has(loc.id));
  if (!picked.length) { toast("请先勾选要删除的货位", true); return; }
  const blocked = picked.filter(loc => loc.stock_quantity > 0);
  if (blocked.length) {
    toast(`有库存的货位不能删除(请先移出或改用停用):${
      blocked.map(loc => loc.location_name).join("、")}`, true);
    return;
  }
  whConfirm("删除确认", `<p>确认删除 ${picked.length} 个空货位?删除后不可恢复。</p>`, [
    { label: "取消" },
    { label: `删除(${picked.length})`, danger: true, onClick: async () => {
      try {
        const result = await api("warehouse-locations/delete", {
          method: "POST", body: { ids: picked.map(loc => loc.id) },
        });
        state.whChecked.clear();
        await renderWarehouses();
        if (result.blocked.length) {
          toast(`已删 ${result.deleted.length} 个;${result.blocked.length} 个未删(${
            result.blocked.map(item => `${item.name || item.id}:${item.reason}`).join(";")})`, true);
        } else {
          toast(`已删除 ${result.deleted.length} 个货位`);
        }
      } catch (error) { toast(error.message, true); }
    } },
  ]);
}

async function toggleWarehouseStatus(id) {
  const wh = state.whCache.find(item => item.id === id);
  const next = wh.status === "active" ? "disabled" : "active";
  if (next === "disabled") {
    const stockN = wh.locations.filter(loc =>
      loc.stock_quantity > 0 && loc.status === "active").length;
    if (stockN && !confirm(`「${wh.warehouse_name}」还有 ${stockN} 个货位有库存,停用后相关项目将无法作业。确认停用?`)) return;
  }
  try {
    await api(`warehouses/${id}/status`, { method: "POST", body: { status: next } });
    toast(next === "active" ? "仓库已启用" : "仓库已停用");
    await renderWarehouses();
  } catch (error) { toast(error.message, true); }
}

function deleteArea(areaId) {
  whConfirm("删除库区", `<p>确认删除该库区?仅能删除没有货位的空库区。</p>`, [
    { label: "取消" },
    { label: "删除", danger: true, onClick: async () => {
      try {
        await api(`areas/${areaId}`, { method: "DELETE" });
        state.whSel.area = null; state.whSel.cargo = null;
        await renderWarehouses();
        toast("库区已删除");
      } catch (error) { toast(error.message, true); }
    } },
  ]);
}

function deleteCargo(cargoId) {
  whConfirm("删除货区", `<p>确认删除该货区?仅能删除没有货位的空货区。</p>`, [
    { label: "取消" },
    { label: "删除", danger: true, onClick: async () => {
      try {
        await api(`cargo-areas/${cargoId}`, { method: "DELETE" });
        state.whSel.cargo = null;
        await renderWarehouses();
        toast("货区已删除");
      } catch (error) { toast(error.message, true); }
    } },
  ]);
}

/* ---- 仓库页弹窗 ---- */
function showModal(title, bodyHtml, footHtml) {
  let host = document.getElementById("modalHost");
  if (!host) {
    host = document.createElement("div");
    host.id = "modalHost";
    host.className = "wh-mask";
    document.body.appendChild(host);
  }
  host.innerHTML = `
    <div class="wh-modal">
      <div class="wh-modal-head"><span>${esc(title)}</span>
        <button class="btn small" onclick="closeModal()">✕</button></div>
      <div class="wh-modal-body">${bodyHtml}</div>
      <div class="wh-modal-foot" id="modalFoot">${footHtml || ""}</div>
    </div>`;
  host.classList.remove("hidden");
  host.onclick = event => { if (event.target === host) closeModal(); };
}
function closeModal() {
  document.getElementById("modalHost")?.classList.add("hidden");
}

function openWarehouseForm(id) {
  const wh = id ? state.whCache.find(item => item.id === id) : null;
  showModal(wh ? "修改仓库" : "新增仓库", `
    <div class="field"><label>仓库名称</label><input class="input" id="fWhName" value="${esc(wh?.warehouse_name || "")}"></div>
    <div class="field"><label>地址</label><input class="input" id="fWhAddr" value="${esc(wh?.address || "")}"></div>
    <div class="field" style="display:flex;gap:12px">
      <div style="flex:1"><label>联系人</label><input class="input" id="fWhContact" value="${esc(wh?.contact_name || "")}"></div>
      <div style="flex:1"><label>联系电话</label><input class="input" id="fWhPhone" value="${esc(wh?.contact_phone || "")}"></div>
    </div>`,
    `<button class="btn" onclick="closeModal()">取消</button>
     <button class="btn primary" id="fWhSave">保存</button>`);
  document.getElementById("fWhSave").onclick = async () => {
    const body = {
      warehouseName: fWhName.value.trim(), address: fWhAddr.value.trim(),
      contactName: fWhContact.value.trim(), contactPhone: fWhPhone.value.trim(),
    };
    try {
      if (wh) await api(`warehouses/${wh.id}`, { method: "PUT", body });
      else await api("warehouses", { method: "POST", body });
      closeModal();
      if (!wh) state.whSel = null;
      await renderWarehouses();
      toast(wh ? "仓库已保存" : "仓库已创建");
    } catch (error) { toast(error.message, true); }
  };
}

function openRenameForm(mode) {
  const wh = curWh();
  const isArea = mode === "area";
  showModal(isArea ? "库区改名" : "货区改名", `
    <div class="field"><label>${isArea ? "新库区名" : "新货区名"}</label>
      <input class="input" id="fRenameValue" value="${esc(isArea ? state.whSel.area : state.whSel.cargo)}"></div>
    <p class="form-hint">改名会同步作用于该层下所有货位,出入库单据里的旧名称不再自动带出。</p>`,
    `<button class="btn" onclick="closeModal()">取消</button>
     <button class="btn primary" id="fRenameSave">确认改名</button>`);
  document.getElementById("fRenameSave").onclick = async () => {
    try {
      if (isArea) await api(`areas/${curArea(wh).id}`, {
        method: "PUT", body: { name: fRenameValue.value.trim() },
      });
      else await api(`cargo-areas/${curCargo(wh).id}`, {
        method: "PUT", body: { name: fRenameValue.value.trim() },
      });
      closeModal();
      await renderWarehouses();
      toast("已改名,层级下货位同步更新");
    } catch (error) { toast(error.message, true); }
  };
}

function openLocForm(area = "", cargo = "") {
  const wh = curWh();
  area = area || state.whSel.area || "";
  cargo = cargo || state.whSel.cargo || "";
  showModal("建货位", `
    <div class="field" style="display:flex;gap:12px">
      <div style="flex:1"><label>库区(填新名自动建层)</label>
        <input class="input" id="fLocArea" value="${esc(area)}"></div>
      <div style="flex:1"><label>货区(填新名自动建层)</label>
        <input class="input" id="fLocCargo" value="${esc(cargo)}"></div>
    </div>
    <div class="field"><label>货位</label>
      <input class="input" id="fLocName" placeholder="例如 A-01"></div>`,
    `<button class="btn" onclick="closeModal()">取消</button>
     <button class="btn primary" id="fLocSave">创建</button>`);
  document.getElementById("fLocSave").onclick = async () => {
    try {
      await api("warehouse-locations", {
        method: "POST",
        body: {
          warehouseId: wh.id, storageArea: fLocArea.value.trim(),
          cargoArea: fLocCargo.value.trim(), locationName: fLocName.value.trim(),
        },
      });
      closeModal();
      state.whSel.area = fLocArea.value.trim();
      state.whSel.cargo = fLocCargo.value.trim();
      await renderWarehouses();
      toast("货位已创建");
    } catch (error) { toast(error.message, true); }
  };
}

function openGenForm(area = "", cargo = "") {
  const wh = curWh();
  area = area || state.whSel.area || "";
  cargo = cargo || state.whSel.cargo || "";
  showModal("范围生成货位", `
    <div class="field" style="display:flex;gap:12px">
      <div style="flex:1"><label>库区(填新名自动建层)</label>
        <input class="input" id="fGenArea" value="${esc(area)}"></div>
      <div style="flex:1"><label>货区(填新名自动建层)</label>
        <input class="input" id="fGenCargo" value="${esc(cargo)}"></div>
    </div>
    <div class="field" style="display:flex;gap:12px">
      <div style="flex:1"><label>货位前缀</label><input class="input" id="fGenPrefix" placeholder="例如 A-"></div>
      <div style="flex:1"><label>起始号</label><input class="input" id="fGenStart" type="number" value="1"></div>
      <div style="flex:1"><label>结束号</label><input class="input" id="fGenEnd" type="number" value="10"></div>
    </div>
    <p class="form-hint">按前缀+起止号批量生成,编号自动补零对齐;已存在的自动跳过。</p>
    <div id="fGenResult"></div>`,
    `<button class="btn" onclick="closeModal()">关闭</button>
     <button class="btn primary" id="fGenSave">生成</button>`);
  document.getElementById("fGenSave").onclick = async () => {
    const prefix = fGenPrefix.value.trim();
    const start = Number(fGenStart.value), end = Number(fGenEnd.value);
    if (!fGenArea.value.trim() || !fGenCargo.value.trim() || !prefix || !(end >= start)) {
      fGenResult.innerHTML = '<div class="wh-result warn">请完整填写生成条件</div>';
      return;
    }
    const width = String(end).length;
    let added = 0, skipped = 0;
    for (let i = start; i <= end; i++) {
      const name = prefix + String(i).padStart(width, "0");
      try {
        await api("warehouse-locations", {
          method: "POST",
          body: {
            warehouseId: wh.id, storageArea: fGenArea.value.trim(),
            cargoArea: fGenCargo.value.trim(), locationName: name,
          },
        });
        added++;
      } catch (_) { skipped++; }
    }
    fGenResult.innerHTML =
      `<div class="wh-result ok">已生成 ${added} 个货位${skipped ? `,跳过 ${skipped} 个(已存在)` : ""}</div>`;
    state.whSel.area = fGenArea.value.trim();
    state.whSel.cargo = fGenCargo.value.trim();
    await renderWarehouses();
  };
}

async function loadProjectProducts(pid) {
  state.projectProducts = state.projectProducts || {};
  state.projectProducts[pid] = await api(`products?projectId=${pid}`);
}

// 项目内商品：创建/编辑统一弹窗（数量与库存不在此处，走单据）
function openProjectProductModal(project, product) {
  const editing = Boolean(product);
  showModal(editing ? `修改商品 — ${project.project_name}` : `新建商品 — ${project.project_name}`, `
    <div class="field" style="display:flex;gap:12px">
      <div style="flex:1"><label>货品编号</label>
        <input id="fPdCode" ${editing ? "readonly" : ""} value="${esc(product?.product_id || "")}"></div>
      <div style="flex:1"><label>货品名称</label>
        <input id="fPdName" value="${esc(product?.product_name || "")}"></div>
    </div>
    <div class="field" style="display:flex;gap:12px">
      <div style="flex:1"><label>分类</label><input id="fPdCat" value="${esc(product?.category || "")}"></div>
      <div style="flex:1"><label>规格</label><input id="fPdSpec" value="${esc(product?.spec || "")}"></div>
      <div style="flex:1"><label>单位</label><input id="fPdUnit" value="${esc(product?.unit || "")}"></div>
    </div>
    <div class="field" style="display:flex;gap:12px">
      <div style="flex:1"><label>单件重量(kg)</label><input id="fPdWeight" type="number" min="0" step="any" value="${product?.unit_weight ?? ""}"></div>
      <div style="flex:1"><label>体积(m³)</label><input id="fPdVolume" type="number" min="0" step="any" value="${product?.volume ?? ""}"></div>
      <div style="flex:1"><label>保质天数</label><input id="fPdShelf" type="number" min="1" step="1" value="${product?.shelf_life_days ?? ""}"></div>
      ${editing ? `<div style="flex:1"><label>状态</label>
        <select id="fPdStatus"><option value="active">启用</option>
          <option value="disabled">停用</option></select></div>` : ""}
    </div>
    <p class="form-hint">商品只归属本项目；入库/出库时按模板填写，数量走单据不在这里改。</p>`,
    `<button class="btn" onclick="closeModal()">取消</button>
     <button class="btn primary" id="fPdSave">保存</button>`);
  if (editing) document.getElementById("fPdStatus").value = product.status;
  document.getElementById("fPdSave").onclick = async () => {
    const body = {
      productName: fPdName.value.trim(), projectId: project.id,
      category: fPdCat.value.trim(), spec: fPdSpec.value.trim(),
      unit: fPdUnit.value.trim(),
    };
    ["Weight", "Volume", "Shelf"].forEach(key => {
      const node = document.getElementById(`fPd${key}`);
      const map = { Weight: "unitWeight", Volume: "volume", Shelf: "shelfLifeDays" };
      if (node.value !== "") body[map[key]] = Number(node.value);
    });
    try {
      if (editing) {
        body.status = document.getElementById("fPdStatus").value;
        await api(`products/${encodeURIComponent(fPdCode.value.trim())}`, {
          method: "PUT", body,
        });
      } else {
        body.productCode = fPdCode.value.trim();
        await api("products", { method: "POST", body });
      }
      closeModal();
      await loadProjectProducts(project.id);
      state.projects = await api("projects");
      renderProjects();
      toast(editing ? "商品已保存" : "商品已创建");
    } catch (error) { toast(error.message, true); }
  };
}

async function renderProjects() {
  if (!state.projects.length || state.user.role !== "admin") {
    state.projects = await api("projects");
  }
  const warehouses = await api("warehouses");
  const projects = state.user.role === "admin"
    ? state.projects : state.projects;
  const editing = state.editingProject;
  const storageAreas = editing
    ? (warehouses.find(item => String(item.id) === String(editing.warehouse_id))?.locations || [])
        .map(location => location.storage_area)
    : [];
  document.getElementById("page").innerHTML = `
    <div class="panel">
      <div class="panel-header"><h3>${editing ? "修改项目" : "创建项目"}</h3>
        ${editing ? `<button class="btn" id="cancelProject">取消修改</button>` : ""}</div>
      <form id="projectForm" class="fields-row">
        <label>项目名称<input name="projectName" required></label>
        <label>绑定仓库<select name="warehouseId" required>
          <option value="">请选择仓库</option>
          ${warehouses.filter(item => item.status === "active").map(item =>
            `<option value="${item.id}" ${editing && String(editing.warehouse_id) === String(item.id) ? "selected" : ""}>${esc(item.warehouse_name)}</option>`
          ).join("")}
        </select></label>
        <label>绑定库区<select name="storageArea" required>
          <option value="">请选择库区</option>
          ${[...new Set(storageAreas)].map(area =>
            `<option value="${esc(area)}" ${editing && editing.storage_area === area ? "selected" : ""}>${esc(area)}</option>`
          ).join("")}
        </select></label>
        <label>项目说明<input name="remark"></label>
        <label>状态<select name="status"><option value="active">启用</option>
          <option value="disabled">停用</option></select></label>
        <button class="btn primary" type="submit">${editing ? "保存" : "创建"}</button>
      </form>
    </div>
    <div class="panel">
      <div class="panel-header"><h3>项目列表</h3><input id="historyFile" type="file" accept=".csv" class="hidden"></div>
      <div id="projectTable"></div>
    </div>`;
  if (editing) {
    const form = document.getElementById("projectForm");
    form.projectName.value = editing.project_name;
    form.remark.value = editing.remark || "";
    form.status.value = editing.status;
    if (editing.warehouse_id) {
      const select = form.warehouseId;
      select.value = String(editing.warehouse_id);
      const areas = [...new Set(
        (warehouses.find(item => String(item.id) === String(editing.warehouse_id))?.locations || [])
          .map(location => location.storage_area),
      )];
      form.storageArea.innerHTML = `<option value="">请选择库区</option>` + areas.map(area =>
        `<option value="${esc(area)}" ${editing.storage_area === area ? "selected" : ""}>${esc(area)}</option>`,
      ).join("");
    }
    document.getElementById("cancelProject").onclick = () => {
      state.editingProject = null;
      renderProjects();
    };
  }
  document.getElementById("projectForm").warehouseId.onchange = event => {
    const warehouse = warehouses.find(item => String(item.id) === event.target.value);
    const areas = [...new Set((warehouse?.locations || []).map(location => location.storage_area))];
    document.getElementById("projectForm").storageArea.innerHTML =
      `<option value="">请选择库区</option>` + areas.map(area =>
        `<option value="${esc(area)}">${esc(area)}</option>`,
      ).join("");
  };
  document.getElementById("projectForm").onsubmit = async event => {
    event.preventDefault();
    const data = Object.fromEntries(new FormData(event.target));
    try {
      if (editing) {
        await api(`projects/${editing.id}`, {
          method: "PUT",
          body: { ...data, warehouseId: Number(data.warehouseId) },
        });
      } else {
        await api("projects", {
          method: "POST",
          body: { ...data, warehouseId: Number(data.warehouseId) },
        });
      }
      state.editingProject = null;
      state.projects = [];
      toast("项目已保存");
      await renderProjects();
    } catch (error) { toast(error.message, true); }
  };
  // 可展开结构：项目行展开后内嵌该项目的商品列表，列表尾部「＋」弹窗创建
  state.expandedProjects = state.expandedProjects || new Set();
  document.getElementById("projectTable").innerHTML = `
    <div class="table-wrap"><table class="data-table">
      <thead><tr>
        <th style="width:36px"></th>
        <th>项目</th><th>仓库 / 库区</th><th>状态</th><th>项目管理员</th>
        <th>商品</th><th>库存批次</th><th>说明</th><th>操作</th>
      </tr></thead>
      <tbody>${projects.map(project => {
        const expanded = state.expandedProjects.has(project.id);
        const mainRow = `
          <tr>
            <td class="center"><button class="btn small ghost" data-expand="${project.id}"
              title="展开/收起商品">${expanded ? "▾" : "▸"}</button></td>
            <td><b>${esc(project.project_name)}</b></td>
            <td>${esc(project.warehouse_name || "-")} / ${esc(project.storage_area || "-")}</td>
            <td>${setStatus(project.status)}</td>
            <td>${project.user_count || 0}</td>
            <td>${project.product_count ?? 0}</td>
            <td>${project.lot_count ?? 0}</td>
            <td>${esc(project.remark || "-")}</td>
            <td><div class="actions">
              <button class="btn small" data-action="edit" data-id="${project.id}">修改</button>
              <button class="btn small" data-action="status" data-id="${project.id}"
                data-status="${project.status === "active" ? "disabled" : "active"}">
                ${project.status === "active" ? "停用" : "启用"}</button>
              ${!project.lot_count ? `<button class="btn small" data-action="history" data-id="${project.id}">导入历史库存</button>` : ""}
            </div></td>
          </tr>`;
        if (!expanded) return mainRow;
        const products = state.projectProducts?.[project.id];
        const productRows = !products
          ? `<tr class="expand-row"><td></td><td colspan="8" class="muted">加载商品中…</td></tr>`
          : products.length ? products.map(product => `
              <tr class="expand-row">
                <td></td>
                <td colspan="8" style="padding-left:36px">
                  <span class="mono">${esc(product.product_id)}</span>
                  ${esc(product.product_name)}
                  <span class="muted">${esc(product.spec || "-")} / ${esc(product.unit || "-")}
                    · 保质 ${product.shelf_life_days ?? "-"} 天
                    · ${product.status === "active" ? "启用" : "停用"}</span>
                  <button class="btn small" style="margin-left:10px"
                    data-product-edit="${esc(product.product_id)}"
                    data-project="${project.id}">修改</button>
                </td>
              </tr>`).join("")
            : `<tr class="expand-row"><td></td><td colspan="8" class="muted">该项目还没有商品</td></tr>`;
        const addRow = `
          <tr class="expand-row">
            <td></td>
            <td colspan="8" style="padding-left:36px">
              <button class="btn small primary" data-product-add="${project.id}">＋ 新建商品</button>
            </td>
          </tr>`;
        return mainRow + productRows + addRow;
      }).join("")}</tbody>
    </table></div>`;
  document.querySelectorAll("#projectTable button[data-expand]").forEach(button => {
    button.onclick = async () => {
      const pid = Number(button.dataset.expand);
      state.expandedProjects.has(pid)
        ? state.expandedProjects.delete(pid)
        : state.expandedProjects.add(pid);
      if (state.expandedProjects.has(pid)) await loadProjectProducts(pid);
      renderProjects();
    };
  });
  document.querySelectorAll("#projectTable button[data-product-add]").forEach(button => {
    button.onclick = () => {
      const project = projects.find(item => item.id === Number(button.dataset.productAdd));
      openProjectProductModal(project, null);
    };
  });
  document.querySelectorAll("#projectTable button[data-product-edit]").forEach(button => {
    button.onclick = () => {
      const pid = Number(button.dataset.project);
      const product = (state.projectProducts?.[pid] || [])
        .find(item => item.product_id === button.dataset.productEdit);
      const project = projects.find(item => item.id === pid);
      openProjectProductModal(project, product);
    };
  });
  for (const pid of state.expandedProjects) {
    if (!state.projectProducts?.[pid]) await loadProjectProducts(pid);
  }
  document.querySelectorAll("#projectTable button[data-action]").forEach(button => {
    button.onclick = async () => {
      const project = projects.find(item => String(item.id) === button.dataset.id);
      if (button.dataset.action === "edit") {
        state.editingProject = project;
        await renderProjects();
      } else if (button.dataset.action === "status") {
        try {
          await api(`projects/${project.id}`, {
            method: "PUT",
            body: {
              projectName: project.project_name, remark: project.remark,
              status: button.dataset.status,
            },
          });
          state.projects = [];
          toast("项目状态已更新");
          await renderProjects();
        } catch (error) { toast(error.message, true); }
      } else {
        state.historyProjectId = project.id;
        document.getElementById("historyFile").click();
      }
    };
  });
  document.getElementById("historyFile").onchange = async event => {
    const file = event.target.files[0];
    if (!file) return;
    const body = new FormData();
    body.append("file", file);
    try {
      const result = await api(`projects/${state.historyProjectId}/history-import`, {
        method: "POST", body,
      });
      toast(`历史库存已导入：${result.orderNo}`);
      state.projects = [];
      await renderProjects();
    } catch (error) { toast(error.message, true); }
    event.target.value = "";
  };
}

async function renderUsers() {
  if (state.user.role === "admin" && !state.projects.length) {
    state.projects = await api("projects");
  }
  const users = await api("users");
  const editing = state.editingUser;
  document.getElementById("page").innerHTML = `
    <div class="panel">
      <div class="panel-header"><h3>${editing ? "修改用户" : "创建项目管理员"}</h3>
        ${editing ? `<button class="btn" id="cancelUser">取消修改</button>` : ""}</div>
      <form id="userForm" class="fields-row wide">
        <label>姓名<input name="displayName" required></label>
        <label>姓名全拼<input name="username" required></label>
        <label>手机号<input name="contactPhone"></label>
        <label>角色<select name="role">
          <option value="project_admin">项目管理员</option><option value="admin">管理员</option>
        </select></label>
        <label>初始密码<input name="password" type="password"
          placeholder="${editing ? "留空表示不修改" : "8-20位，含字母和数字"}"></label>
        <button class="btn primary" type="submit">${editing ? "保存" : "创建"}</button>
      </form>
      <div class="assignment"><span>授权项目</span><div id="projectChecks"></div></div>
    </div>
    <div class="panel"><h3>用户列表</h3><div id="userTable"></div></div>`;
  const checks = document.getElementById("projectChecks");
  checks.innerHTML = state.projects.map(project => `
    <label class="check"><input type="checkbox" name="projectIds" value="${project.id}"
      ${editing && editing.projectIds.includes(project.id) ? "checked" : ""}>
      ${esc(project.project_name)}</label>`).join("");
  const roleSelect = document.querySelector("#userForm [name=role]");
  const syncProjects = () => checks.classList.toggle("disabled",
    roleSelect.value !== "project_admin");
  roleSelect.onchange = syncProjects;
  syncProjects();
  if (editing) {
    const form = document.getElementById("userForm");
    form.displayName.value = editing.display_name;
    form.username.value = editing.username;
    form.contactPhone.value = editing.contact_phone || "";
    form.role.value = editing.role;
    form.password.required = false;
    syncProjects();
    document.getElementById("cancelUser").onclick = () => {
      state.editingUser = null;
      renderUsers();
    };
  }
  document.getElementById("userForm").onsubmit = async event => {
    event.preventDefault();
    const data = Object.fromEntries(new FormData(event.target));
    data.projectIds = Array.from(document.querySelectorAll(
      "#projectChecks input[name=projectIds]:checked"
    )).map(input => Number(input.value));
    try {
      if (editing) {
        const password = data.password;
        delete data.password;
        await api(`users/${editing.id}`, { method: "PUT", body: data });
        if (password) {
          await api(`users/${editing.id}/reset-password`, {
            method: "POST", body: { password },
          });
        }
      } else {
        await api("users", { method: "POST", body: data });
      }
      state.editingUser = null;
      toast("用户已保存");
      await renderUsers();
    } catch (error) { toast(error.message, true); }
  };
  document.getElementById("userTable").innerHTML = simpleTable(
    ["姓名", "姓名全拼", "手机号", "角色", "状态", "授权项目", "操作"],
    users.map(user => [
      esc(user.display_name), `<span class="mono">${esc(user.username)}</span>`,
      esc(user.contact_phone || "-"), user.role === "admin" ? "管理员" : "项目管理员",
      setStatus(user.status), projectNames(user.projectIds),
      `<div class="actions">
        <button class="btn small" data-action="edit" data-id="${user.id}">修改</button>
        <button class="btn small" data-action="reset" data-id="${user.id}">重置密码</button>
        <button class="btn small ${user.status === "active" ? "danger" : "success"}"
          data-action="status" data-id="${user.id}"
          data-status="${user.status === "active" ? "disabled" : "active"}">
          ${user.status === "active" ? "停用" : "启用"}</button>
      </div>`,
    ]),
  );
  document.querySelectorAll("#userTable button[data-action]").forEach(button => {
    button.onclick = async () => {
      const user = users.find(item => item.id === Number(button.dataset.id));
      try {
        if (button.dataset.action === "edit") {
          state.editingUser = user;
          await renderUsers();
        } else if (button.dataset.action === "reset") {
          const password = prompt("请输入新初始密码（8-20位，含字母和数字）");
          if (!password) return;
          await api(`users/${user.id}/reset-password`, {
            method: "POST", body: { password },
          });
          toast("密码已重置");
        } else {
          await api(`users/${user.id}`, {
            method: "PUT",
            body: {
              username: user.username, displayName: user.display_name,
              contactPhone: user.contact_phone, role: user.role,
              status: button.dataset.status, projectIds: user.projectIds,
            },
          });
          toast("用户状态已更新");
        }
        if (button.dataset.action !== "edit") await renderUsers();
      } catch (error) { toast(error.message, true); }
    };
  });
}

async function renderTemplates() {
  // 模板归管理员管；项目管理员默认自己的项目，管理员用页面内项目选择器
  if (state.user.role === "admin") {
    if (!state.templateProjects?.length) {
      state.templateProjects = await api("projects");
    }
    if (!state.templateProjects.some(project =>
        String(project.id) === String(state.templateProjectId))) {
      state.templateProjectId = state.templateProjects[0]?.id || "";
    }
  }
  const projectQuery = state.user.role === "admin"
    ? `?projectId=${state.templateProjectId}` : "";
  state.templates = await api(`templates${projectQuery}`);
  const current = state.templates.find(item => item.type === state.templateType);
  document.getElementById("page").innerHTML = `
    <div class="panel">
      <div class="panel-header"><h3>模板列配置</h3>
        <button class="btn" id="downloadTemplate">下载当前模板</button></div>
      ${state.user.role === "admin" ? `
        <div class="fields-row" style="padding:12px 16px 0">
          <label>项目（模板按项目独立配置）<select id="templateProject"
            onchange="state.templateProjectId=Number(this.value);renderTemplates()">
            ${state.templateProjects.map(project => `
              <option value="${project.id}" ${
                String(project.id) === String(state.templateProjectId) ? "selected" : ""
              }>${esc(project.project_name)}</option>`).join("")}
          </select></label>
        </div>` : ""}
      <div class="template-tabs">
        ${[["inbound", "入库模板"], ["outbound", "出库模板"]].map(([id, text]) =>
          `<button class="segment ${state.templateType === id ? "active" : ""}" data-type="${id}">${text}</button>`
        ).join("")}
      </div>
      ${simpleTable(["标准字段", "Excel列名", "类型", "必填"],
        current.columns.map((column, index) => [
          esc(column.label || column.key),
          `<input data-index="${index}" data-field="name" value="${esc(column.name)}">`,
          column.type === "text" ? "文本" : column.type === "date" ? "日期" : "正整数",
          `<input type="checkbox" data-index="${index}" data-field="required"
            ${column.required ? "checked" : ""} ${column.locked ? "disabled" : ""}
            title="${column.locked ? "系统必填字段，不可取消" : "勾选后导入时该列不能留空"}">`,
        ]))}
      <div class="actions" style="margin-top:12px">
        <button class="btn primary" id="saveTemplate">保存并生成新版本</button>
        <button class="btn" id="toggleTemplate">${current.status === "active" ? "停用模板" : "启用模板"}</button>
        <span class="muted">当前版本 v${current.version}</span>
        <span class="muted">状态：${current.status === "active" ? "启用" : "停用"}</span>
      </div>
      <p class="muted">说明：修改「Excel列名」后，导入文件的表头需使用新列名；灰色的必填勾选是系统必填字段，不可取消。</p>
    </div>`;
  document.querySelectorAll(".segment").forEach(button => {
    button.onclick = () => {
      state.templateType = button.dataset.type;
      renderTemplates();
    };
  });
  document.getElementById("downloadTemplate").onclick = () =>
    download(`templates/${state.templateType}/download${projectQuery}`,
      `${state.templateType}-template.xlsx`);
  document.getElementById("saveTemplate").onclick = async () => {
    const columns = current.columns.map((column, index) => ({
      ...column,
      name: document.querySelector(`[data-index="${index}"][data-field="name"]`).value.trim(),
      required: document.querySelector(
        `[data-index="${index}"][data-field="required"]`
      ).checked,
    }));
    try {
      const updated = await api(`templates/${state.templateType}`, {
        method: "PUT",
        body: state.user.role === "admin"
          ? { columns, projectId: state.templateProjectId }
          : { columns },
      });
      toast(`模板已更新到 v${updated.version}`);
      await renderTemplates();
    } catch (error) { toast(error.message, true); }
  };
  document.getElementById("toggleTemplate").onclick = async () => {
    try {
      const status = current.status === "active" ? "disabled" : "active";
      await api(`templates/${state.templateType}/status`, {
        method: "POST",
        body: state.user.role === "admin"
          ? { status, projectId: state.templateProjectId }
          : { status },
      });
      toast(status === "active" ? "模板已启用" : "模板已停用");
      await renderTemplates();
    } catch (error) { toast(error.message, true); }
  };
}

// ---- 管理员单据管理：整单撤销（入库/出库/移库） ----
async function renderOrders() {
  if (!state.orderProjects?.length) {
    state.orderProjects = await api("projects");
  }
  if (state.orderProjectId === undefined) state.orderProjectId = "";
  const type = state.orderTypeFilter || "";
  const query = new URLSearchParams();
  if (state.orderProjectId) query.set("projectId", state.orderProjectId);
  if (type) query.set("type", type);
  const orders = await api(`admin/orders?${query.toString()}`);
  const typeLabels = {
    inbound: "入库", outbound: "出库", move: "移库", history_import: "历史初始化",
  };
  document.getElementById("page").innerHTML = `
    <div class="panel">
      <div class="panel-header">
        <h3>单据管理</h3>
        <select class="input" style="width:auto;min-width:180px"
          onchange="state.orderProjectId=this.value ? Number(this.value) : '';renderOrders()">
          <option value="">全部项目</option>
          ${state.orderProjects.map(project => `
            <option value="${project.id}" ${
              String(project.id) === String(state.orderProjectId) ? "selected" : ""
            }>${esc(project.project_name)}</option>`).join("")}
        </select>
      </div>
      <div class="template-tabs">
        ${[["", "全部"], ["inbound", "入库"], ["outbound", "出库"], ["move", "移库"]].map(([id, text]) =>
          `<button class="segment ${type === id ? "active" : ""}"
            data-type="${id}">${text}</button>`).join("")}
      </div>
      ${simpleTable(
        ["单号", "项目", "类型", "单据日期", "商品摘要", "操作人", "状态", "操作"],
        orders.map(order => [
          `<span class="mono">${esc(order.order_no)}</span>`,
          esc(order.projectName || "-"),
          typeLabels[order.order_type] || order.order_type,
          esc(order.order_date || formatTime(order.created_at).slice(0, 10)),
          esc(order.summary),
          esc(order.operator_name),
          order.status === "reversed"
            ? `<span class="badge disabled">已撤销</span>
               <div class="form-hint">${esc(order.reversed_by || "")} ${esc((order.reversed_at || "").slice(0, 16))}</div>`
            : setStatus(order.status === "completed" ? "completed"
                : order.status === "pending_picking" ? "pending" : order.status),
          order.status === "reversed" ? "-"
            : ["inbound", "move", "outbound"].includes(order.order_type)
            ? `<button class="btn small danger" data-id="${order.id}"
              data-no="${esc(order.order_no)}" data-type="${order.order_type}">撤销</button>`
            : "-",
        ]))}
      <p class="form-hint" style="padding:0 16px 12px">
        撤销为整单撤销：入库扣回库存（批次已被消耗会拒绝）、出库按原批次原货位恢复、
        移库反向回移；撤销记入库存流水与操作日志，单据保留并标记「已撤销」。
      </p>
    </div>`;
  document.querySelectorAll(".segment").forEach(button => {
    button.onclick = () => {
      state.orderTypeFilter = button.dataset.type;
      renderOrders();
    };
  });
  document.querySelectorAll("#page button[data-id]").forEach(button => {
    button.onclick = () => {
      const typeLabel = { inbound: "入库单", outbound: "出库单", move: "移库单" }[button.dataset.type];
      whConfirm(`撤销${typeLabel} ${button.dataset.no}`, `
        <p>确认撤销这张${typeLabel}?撤销后：</p>
        <ul class="form-hint" style="margin:6px 0 0 18px">
          <li>入库单：扣回对应批次库存（不足则整单拒绝）</li>
          <li>出库单：按原批次、原货位恢复库存</li>
          <li>移库单：从目标货位反向回移到源货位</li>
        </ul>
        <p class="form-hint">单据保留并标记「已撤销」，操作计入审计日志。</p>`,
        [
          { label: "取消" },
          { label: "确认撤销", danger: true, onClick: () => reverseOrder(button.dataset.id) },
        ]);
    };
  });
}

async function reverseOrder(id) {
  try {
    await api(`admin/orders/${id}/reverse`, { method: "POST", body: {} });
    toast("单据已撤销");
    await renderOrders();
  } catch (error) { toast(error.message, true); }
}

async function renderLogs() {
  const rows = await api("audit-logs");
  document.getElementById("page").innerHTML = `<div class="panel">
    <h3>操作日志</h3>
    ${simpleTable(["时间", "操作人", "操作", "内容"], rows.map(row => [
      `<span class="nowrap">${formatTime(row.created_at)}</span>`,
      esc(row.user_name), actionLabel(row.action), esc(row.detail || "-"),
    ]))}
  </div>`;
}

function actionLabel(value) {
  const labels = {
    "stock.inbound": "入库", "stock.outbound": "出库", "stock.move": "移库",
    "picking.complete": "拣货完成", "template.update": "模板修改",
    "template.status": "模板状态", "inventory.history_import": "历史库存导入",
    "user.create": "创建用户", "user.update": "修改用户",
    "user.reset_password": "重置密码", "product.create": "创建商品",
    "product.update": "修改商品", "warehouse.create": "创建仓库",
    "warehouse.location.create": "创建货位", "project.create": "创建项目",
    "project.update": "修改项目",
  };
  return labels[value] || value;
}

function renderSettings() {
  document.getElementById("page").innerHTML = `
    <div class="panel" style="max-width:520px">
      <h3>系统设置</h3>
      <form id="settingsForm" class="fields">
        <label>公司名称<input name="companyName" maxlength="50" required
          value="${esc(state.branding.companyName || "")}"></label>
        <p class="form-hint">公司名称显示在登录页系统名称下方，仅公司管理员可修改。</p>
        <button class="btn primary" type="submit">保存</button>
        <p id="settingsError" class="form-error"></p>
      </form>
    </div>`;
  document.getElementById("settingsForm").onsubmit = async event => {
    event.preventDefault();
    const data = Object.fromEntries(new FormData(event.target));
    const errorNode = document.getElementById("settingsError");
    errorNode.textContent = "";
    try {
      await api("company-name", {
        method: "PUT",
        body: { companyName: data.companyName.trim() },
      });
      state.branding.companyName = data.companyName.trim();
      applyBranding();
      toast("公司名称已更新");
    } catch (error) { errorNode.textContent = error.message; }
  };
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
      await api("change-password", {
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
  const page = state.user.mustChangePassword && state.page !== "password"
    ? "password" : state.page;
  document.getElementById("pageTitle").textContent = titles[page] || titles.dashboard;
  renderNav();
  renderProjectSelect();
  try {
    if (state.user.role === "admin") {
      if (page === "dashboard") await renderDashboard();
      else if (page === "inventory") await renderInventory();
      else if (page === "invAdjust") await renderInvAdjust();
      else if (page === "warehouses") await renderWarehouses();
      else if (page === "projects") await renderProjects();
      else if (page === "users") await renderUsers();
      else if (page === "settings") renderSettings();
      else if (page === "password") renderPassword();
      else location.hash = "#dashboard";
      return;
    }
    if (page === "dashboard") await renderDashboard();
    else if (page === "inbound") await renderImportPage("inbound");
    else if (page === "outbound") await renderImportPage("outbound");
    else if (page === "move") await renderImportPage("move");
    else if (page === "inventory") await renderInventory();
    else if (page === "invAdjust") await renderInvAdjust();
    else if (page === "picking") await renderPicking();
    else if (page === "templates") await renderTemplates();
    else if (page === "orders") await renderOrders();
    else if (page === "logs") await renderLogs();
    else if (page === "password") renderPassword();
    else location.hash = "#dashboard";
  } catch (error) {
    document.getElementById("page").innerHTML =
      `<div class="panel result-box error">${esc(error.message)}</div>`;
  }
}

document.getElementById("loginForm").onsubmit = async event => {
  event.preventDefault();
  const errorNode = document.getElementById("loginError");
  errorNode.textContent = "";
  try {
    const data = Object.fromEntries(new FormData(event.target));
    const result = await api("login", { method: "POST", body: data });
    state.token = result.token;
    localStorage.setItem(TOKEN_KEY, result.token);
    state.user = await api("me");
    state.projects = state.user.projects || [];
    showApp();
  } catch (error) { errorNode.textContent = error.message; }
};

document.getElementById("logoutButton").onclick = async () => {
  try { await api("logout", { method: "POST", body: {} }); } catch (_) {}
  clearSession();
  document.getElementById("appView").classList.add("hidden");
  document.getElementById("loginView").classList.remove("hidden");
};

document.getElementById("passwordButton").onclick = () =>
  location.hash = "#password";

document.addEventListener("click", event => {
  const wrapper = document.getElementById("projectSelectWrap");
  const menu = document.getElementById("projectMenu");
  const button = document.getElementById("projectSelect");
  if (!menu?.classList.contains("hidden") && !wrapper.contains(event.target)) {
    menu.classList.add("hidden");
    button.setAttribute("aria-expanded", "false");
  }
});

window.addEventListener("hashchange", () => {
  const page = location.hash.slice(1) || "dashboard";
  const allowed = allowedPages().some(item => item.id === page) || page === "password";
  state.page = allowed ? page : "dashboard";
  if (state.user) renderPage();
});

(async function initialize() {
  loadBranding();
  if (!state.token) {
    document.getElementById("loginView").classList.remove("hidden");
    return;
  }
  try {
    state.user = await api("me");
    state.projects = state.user.projects || [];
    if (state.user.role === "project_admin") {
      if (!state.projects.some(item => String(item.id) === state.projectId)) {
        state.projectId = state.projects[0]?.id || "";
      }
      if (state.projectId) localStorage.setItem(PROJECT_KEY, state.projectId);
    }
    showApp();
  } catch (_) {
    clearSession();
    document.getElementById("loginView").classList.remove("hidden");
  }
})();
