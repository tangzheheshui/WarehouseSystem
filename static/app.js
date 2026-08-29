const BASE_PATH = location.pathname.replace(/(?:\/index\.html)?$/, "/");
const TOKEN_KEY = `wmsToken:${BASE_PATH}`;
const PROJECT_KEY = `wmsProject:${BASE_PATH}`;

const state = {
  token: localStorage.getItem(TOKEN_KEY) || "",
  user: null,
  projects: [],
  projectId: localStorage.getItem(PROJECT_KEY) || "",
  page: "dashboard",
  importResult: {},
  pickingDetail: null,
  templateType: "inbound",
  templates: [],
  editingProduct: null,
  editingProject: null,
  editingUser: null,
  historyProjectId: null,
};

const titles = {
  dashboard: "工作台",
  inbound: "入库管理",
  outbound: "出库管理",
  inventory: "库存查询",
  picking: "拣货单",
  products: "商品信息",
  warehouses: "仓库信息",
  projects: "项目管理",
  users: "用户管理",
  templates: "模板管理",
  logs: "操作日志",
  password: "修改密码",
};

const adminPages = [
  { id: "dashboard", label: "工作台" },
  { id: "products", label: "商品信息" },
  { id: "warehouses", label: "仓库信息" },
  { id: "projects", label: "项目管理" },
  { id: "users", label: "用户管理" },
];

const projectPages = [
  { id: "dashboard", label: "工作台" },
  { id: "inbound", label: "入库管理" },
  { id: "outbound", label: "出库管理" },
  { id: "inventory", label: "库存查询" },
  { id: "picking", label: "拣货单" },
  { id: "templates", label: "模板管理" },
  { id: "logs", label: "操作日志" },
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

function allowedPages() {
  return state.user.role === "admin" ? adminPages : projectPages;
}

function renderNav() {
  const nav = document.getElementById("nav");
  nav.innerHTML = allowedPages().map(item => `
    <button data-page="${item.id}" class="${state.page === item.id ? "active" : ""}">
      ${item.label}
    </button>
  `).join("");
  nav.querySelectorAll("button").forEach(button => {
    button.onclick = () => location.hash = `#${button.dataset.page}`;
  });
  document.getElementById("userName").textContent = state.user.displayName;
  document.getElementById("userRole").textContent = state.user.role === "admin"
    ? "管理员" : "项目管理员";
}

function renderProjectSelect() {
  const select = document.getElementById("projectSelect");
  if (state.user.role !== "project_admin") {
    select.classList.add("hidden");
    select.innerHTML = "";
    return;
  }
  select.classList.remove("hidden");
  select.innerHTML = state.projects.map(project => `
    <option value="${project.id}" ${String(project.id) === String(state.projectId) ? "selected" : ""}>
      ${esc(project.project_name)}
    </option>
  `).join("");
  select.onchange = () => {
    state.projectId = select.value;
    localStorage.setItem(PROJECT_KEY, state.projectId);
    state.templates = [];
    renderPage();
  };
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
    active: "启用", disabled: "停用",
  };
  return `<span class="badge ${esc(value)}">${labels[value] || esc(value)}</span>`;
}

function metric(label, value) {
  return `<div class="metric"><span>${label}</span><strong>${esc(value)}</strong></div>`;
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
        ${metric("项目管理员", data.users)}
        ${metric("有效项目", data.projects)}
        ${metric("有效商品", data.products)}
        ${metric("有效仓库", data.warehouses)}
      </div>
      <div class="panel">
        <div class="panel-header"><h3>项目列表</h3>
          <button class="btn" data-page="projects">维护项目</button></div>
        ${simpleTable(
          ["项目", "状态", "项目管理员", "库存批次", "说明"],
          projects.map(row => [esc(row.project_name), setStatus(row.status),
            row.user_count, row.lot_count, esc(row.remark || "-")]),
        )}
      </div>
      <div class="panel">
        <div class="panel-header"><h3>用户列表</h3>
          <button class="btn" data-page="users">维护用户</button></div>
        ${simpleTable(
          ["姓名", "姓名全拼", "角色", "状态", "授权项目"],
          users.map(row => [esc(row.display_name), esc(row.username),
            row.role === "admin" ? "管理员" : "项目管理员", setStatus(row.status),
            projectNames(row.projectIds)]),
        )}
      </div>`;
  } else {
    const data = await api("dashboard");
    document.getElementById("page").innerHTML = `
      <div class="metrics">
        ${metric("库存批次", data.lots)}
        ${metric("库存总数", data.quantity)}
        ${metric("今日单据", data.todayOrders)}
        ${metric("待拣货", data.pendingPicking)}
      </div>
      <div class="panel"><h3>最近单据</h3><div id="recentOrders"></div></div>`;
    const orders = await api("orders");
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
  return { inbound: "入库", outbound: "出库", history_import: "历史导入" }[value] || value;
}

function importResultHtml(result, type) {
  if (!result || !result.visible) return "";
  if (result.ok) {
    return `<div class="result-box">
      <div class="result-title">
        <span>${type === "inbound" ? "入库成功" : "出库成功"}</span>
        <span class="mono">${esc(result.orderNo)}</span>
      </div>
      <div>${result.aggregated.map(item => {
        const detail = item.batchNo ? ` / ${item.batchNo}` : "";
        return `${esc(item.productCode)}${esc(detail)}：${esc(item.quantity)}`;
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

async function renderImportPage(type) {
  const label = type === "inbound" ? "入库" : "出库";
  const result = state.importResult[type] || {};
  document.getElementById("page").innerHTML = `
    <div class="panel">
      <div class="panel-header"><h3>${label}导入</h3>
        <button class="btn" id="downloadTemplate">下载模板</button></div>
      <form id="importForm" class="fields-row wide">
        <label>Excel文件<input type="file" name="file" accept=".xlsx" required></label>
        <button class="btn primary" type="submit">上传校验并${label}</button>
      </form>
    </div>
    ${importResultHtml(result, type)}
    <div class="panel">
      <h3>${label}单</h3>
      <form id="orderFilter" class="fields-row">
        <label>单号/操作人<input name="keyword"></label>
        <label>开始日期<input name="startDate" type="date"></label>
        <label>结束日期<input name="endDate" type="date"></label>
        <button class="btn" type="submit">查询</button>
      </form>
      <div id="orderList" class="table-top"></div>
    </div>`;
  document.getElementById("downloadTemplate").onclick = () =>
    download(`templates/${type}/download`, `${type}-template.xlsx`);
  document.getElementById("importForm").onsubmit = async event => {
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
  const errorButton = document.getElementById("downloadErrors");
  if (errorButton) errorButton.onclick = () => {
    postDownload("import-errors/export", {
      details: state.importResult[type].details,
    }, "import-errors.xlsx");
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
  const orders = await api(`orders?${query}`);
  document.getElementById("orderList").innerHTML = orderTable(orders);
}

async function renderInventory() {
  document.getElementById("page").innerHTML = `
    <div class="panel">
      <h3>查询库存</h3>
      <form id="inventoryForm" class="fields-row wide">
        <label>仓库<input name="warehouseName"></label>
        <label>库区<input name="storageArea"></label>
        <label>货区<input name="cargoArea"></label>
        <label>货位<input name="locationName"></label>
        <label>货品编号<input name="productCode"></label>
        <label>货品名称<input name="productName"></label>
        <label>生产批号<input name="batchNo"></label>
        <label>品质标记<select name="qualityStatus">
          <option value=""></option><option value="良品">良品</option><option value="扣留">扣留</option>
        </select></label>
        <label>禁止出库<select name="prohibitOutbound">
          <option value=""></option><option value="1">是</option><option value="0">否</option>
        </select></label>
        <button class="btn primary" type="submit">查询</button>
      </form>
    </div>
    <div class="panel"><h3>商品汇总</h3><div id="inventorySummary"></div></div>
    <div class="panel">
      <div class="panel-header"><h3>批次明细</h3>
        <button class="btn" id="exportInventory">导出Excel</button></div>
      <div id="inventoryTable"></div>
    </div>`;
  const form = document.getElementById("inventoryForm");
  form.onsubmit = event => {
    event.preventDefault();
    loadInventory(new FormData(form));
  };
  document.getElementById("exportInventory").onclick = () => {
    const query = new URLSearchParams(new FormData(form)).toString();
    download(`inventory/export?${query}`, "库存查询结果.xlsx");
  };
  await loadInventory(new FormData(form));
}

async function loadInventory(form) {
  const query = new URLSearchParams(form).toString();
  const rows = await api(`inventory?${query}`);
  const summary = Object.values(rows.reduce((result, row) => {
    if (!result[row.product_id]) {
      result[row.product_id] = {
        product_id: row.product_id, product_name: row.product_name,
        spec: row.spec, unit: row.unit, quantity: 0, available: 0,
        locations: new Set(),
      };
    }
    result[row.product_id].quantity += row.total_quantity;
    result[row.product_id].available += row.available_quantity;
    if (row.total_quantity > 0) result[row.product_id].locations.add(row.full_location);
    return result;
  }, {}));
  document.getElementById("inventorySummary").innerHTML = simpleTable(
    ["货品编号", "货品名称", "规格", "标准单位", "库存总数", "可用库存", "货位"],
    summary.map(row => [
      `<span class="mono">${esc(row.product_id)}</span>`, esc(row.product_name),
      esc(row.spec || "-"), esc(row.unit || "-"), row.quantity, row.available,
      Array.from(row.locations).map(esc).join("、") || "-",
    ]),
  );
  document.getElementById("inventoryTable").innerHTML = simpleTable(
    ["仓库", "库区", "货区", "货位", "货品编号", "货品名称", "生产批号",
     "生产日期", "到期日期", "库存总数", "分拣占用", "移动占用", "可用库存",
     "品质标记", "禁止出库"],
    rows.map(row => [
      esc(row.warehouse_name), esc(row.storage_area), esc(row.cargo_area),
      esc(row.location_name), `<span class="mono">${esc(row.product_id)}</span>`,
      esc(row.product_name), esc(row.batch_no), esc(row.production_date || "-"),
      esc(row.expiry_date || "-"), row.total_quantity, row.picking_reserved,
      row.moving_reserved, row.available_quantity, esc(row.quality_status),
      row.prohibit_outbound ? "是" : "否",
    ]),
  );
}

async function renderPicking() {
  document.getElementById("page").innerHTML = `
    <div class="panel">
      <div class="panel-header"><h3>拣货单</h3>
        <div class="actions">
          <select id="pickStatus"><option value="">全部状态</option>
            <option value="pending">待拣货</option><option value="completed">已完成</option></select>
          <button class="btn" id="refreshPicking">刷新</button>
        </div></div>
      <div id="pickList"></div>
    </div>
    <div class="panel">
      <div class="panel-header"><h3>拣货明细</h3><div class="actions" id="pickActions"></div></div>
      <div id="pickDetail"></div>
    </div>`;
  document.getElementById("pickStatus").onchange = loadPicking;
  document.getElementById("refreshPicking").onclick = loadPicking;
  await loadPicking();
}

async function loadPicking() {
  const status = document.getElementById("pickStatus").value;
  const rows = await api(`picking-orders${status ? `?status=${status}` : ""}`);
  document.getElementById("pickList").innerHTML = simpleTable(
    ["拣货单号", "出库单号", "状态", "生成时间", "操作"],
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
      ${simpleTable(["顺序", "货位", "货品编号", "货品名称", "规格", "生产批号", "标准单位数"],
        items.map(item => [item.sequence, esc(item.location.full_name),
          `<span class="mono">${esc(item.product_id)}</span>`, esc(item.product_name),
          esc(item.spec || "-"), esc(item.batch_no), item.quantity]))}
    </div>`;
  document.getElementById("printPick").onclick = () => printPicking(state.pickingDetail);
  document.getElementById("exportPick").onclick = () =>
    download(`picking-orders/${id}/export`, `picking-${order.pick_no}.xlsx`);
  const completeButton = document.getElementById("completePick");
  if (completeButton) completeButton.onclick = async () => {
    try {
      await api(`picking-orders/${id}/complete`, { method: "POST", body: {} });
      toast("拣货完成");
      await loadPicking();
    } catch (error) { toast(error.message, true); }
  };
}

function printPicking(detail) {
  const order = detail.order;
  document.getElementById("printArea").innerHTML = `
    <h1>拣货单</h1>
    <div class="meta">拣货单号：${esc(order.pick_no)} 出库单号：${esc(order.outbound_order_no)} 生成时间：${formatTime(order.generated_at)}</div>
    ${simpleTable(["顺序", "货位", "货品编号", "货品名称", "规格", "生产批号", "标准单位数"],
      detail.items.map(item => [item.sequence, esc(item.location.full_name),
        esc(item.product_id), esc(item.product_name), esc(item.spec || "-"),
        esc(item.batch_no), item.quantity]))}`;
  window.print();
}

async function renderProducts() {
  const editing = state.editingProduct;
  document.getElementById("page").innerHTML = `
    <div class="panel">
      <div class="panel-header"><h3>${editing ? "修改商品" : "录入商品"}</h3>
        ${editing ? `<button class="btn" id="cancelProduct">取消修改</button>` : ""}</div>
      <form id="productForm" class="fields-row wide">
        <label>货品编号<input name="productCode" required ${editing ? "readonly" : ""}></label>
        <label>货品名称<input name="productName" required></label>
        <label>规格<input name="spec"></label>
        <label>标准单位<input name="unit"></label>
        <label>状态<select name="status"><option value="active">启用</option>
          <option value="disabled">停用</option></select></label>
        <button class="btn primary" type="submit">${editing ? "保存" : "创建"}</button>
      </form>
    </div>
    <div class="panel">
      <form id="productFilter" class="fields-row"><label>关键字<input name="keyword"></label>
        <button class="btn" type="submit">查询</button></form>
      <div id="productTable" class="table-top"></div>
    </div>`;
  if (editing) {
    const form = document.getElementById("productForm");
    form.productCode.value = editing.product_id;
    form.productName.value = editing.product_name;
    form.spec.value = editing.spec || "";
    form.unit.value = editing.unit || "";
    form.status.value = editing.status;
    document.getElementById("cancelProduct").onclick = () => {
      state.editingProduct = null;
      renderProducts();
    };
  }
  document.getElementById("productForm").onsubmit = async event => {
    event.preventDefault();
    const data = Object.fromEntries(new FormData(event.target));
    try {
      if (state.editingProduct) {
        await api(`products/${encodeURIComponent(data.productCode)}`, {
          method: "PUT",
          body: {
            productName: data.productName, spec: data.spec,
            unit: data.unit, status: data.status,
          },
        });
      } else {
        await api("products", { method: "POST", body: data });
      }
      state.editingProduct = null;
      toast("商品已保存");
      await renderProducts();
    } catch (error) { toast(error.message, true); }
  };
  document.getElementById("productFilter").onsubmit = event => {
    event.preventDefault();
    loadProducts(new FormData(event.target));
  };
  await loadProducts(new FormData(document.getElementById("productFilter")));
}

async function loadProducts(form = new FormData()) {
  const query = new URLSearchParams(form).toString();
  const rows = await api(`products${query ? `?${query}` : ""}`);
  document.getElementById("productTable").innerHTML = simpleTable(
    ["货品编号", "货品名称", "规格", "标准单位", "状态", "操作"],
    rows.map(row => [
      `<span class="mono">${esc(row.product_id)}</span>`, esc(row.product_name),
      esc(row.spec || "-"), esc(row.unit || "-"), setStatus(row.status),
      `<button class="btn small" data-id="${esc(row.product_id)}">修改</button>`,
    ]),
  );
  document.querySelectorAll("#productTable button[data-id]").forEach(button => {
    button.onclick = () => {
      state.editingProduct = rows.find(row => row.product_id === button.dataset.id);
      renderProducts();
    };
  });
}

async function renderWarehouses() {
  const warehouses = await api("warehouses");
  document.getElementById("page").innerHTML = `
    <div class="panel">
      <h3>新增仓库</h3>
      <form id="warehouseForm" class="fields-row wide">
        <label>仓库名称<input name="warehouseName" required></label>
        <label>地址<input name="address"></label>
        <label>联系人<input name="contactName"></label>
        <label>联系电话<input name="contactPhone"></label>
        <button class="btn primary" type="submit">创建</button>
      </form>
    </div>
    <div class="panel">
      <h3>新增货位</h3>
      <form id="locationForm" class="fields-row wide">
        <label>仓库<select name="warehouseId" required>${warehouses.map(item =>
          `<option value="${item.id}">${esc(item.warehouse_name)}</option>`).join("")}</select></label>
        <label>库区<input name="storageArea" required></label>
        <label>货区<input name="cargoArea" required></label>
        <label>货位<input name="locationName" required></label>
        <button class="btn primary" type="submit">创建</button>
      </form>
    </div>
    <div class="panel"><h3>仓库与货位</h3><div id="warehouseTable"></div></div>`;
  document.getElementById("warehouseForm").onsubmit = async event => {
    event.preventDefault();
    try {
      await api("warehouses", {
        method: "POST", body: Object.fromEntries(new FormData(event.target)),
      });
      toast("仓库已创建");
      await renderWarehouses();
    } catch (error) { toast(error.message, true); }
  };
  document.getElementById("locationForm").onsubmit = async event => {
    event.preventDefault();
    try {
      await api("warehouse-locations", {
        method: "POST", body: Object.fromEntries(new FormData(event.target)),
      });
      toast("货位已创建");
      await renderWarehouses();
    } catch (error) { toast(error.message, true); }
  };
  const rows = [];
  for (const warehouse of warehouses) {
    rows.push([
      `<strong>${esc(warehouse.warehouse_name)}</strong>`, esc(warehouse.address || "-"),
      esc(warehouse.contact_name || "-"), esc(warehouse.contact_phone || "-"),
      setStatus(warehouse.status),
      `<button class="btn small" data-type="warehouses" data-id="${warehouse.id}"
        data-status="${warehouse.status === "active" ? "disabled" : "active"}">
        ${warehouse.status === "active" ? "停用" : "启用"}</button>`,
    ]);
    for (const location of warehouse.locations) {
      rows.push([
        `<span class="muted">${esc(location.storage_area)} / ${esc(location.cargo_area)} / ${esc(location.location_name)}</span>`,
        "-", "-", "-", setStatus(location.status),
        `<button class="btn small" data-type="warehouse_locations" data-id="${location.id}"
          data-status="${location.status === "active" ? "disabled" : "active"}">
          ${location.status === "active" ? "停用" : "启用"}</button>`,
      ]);
    }
  }
  document.getElementById("warehouseTable").innerHTML = simpleTable(
    ["仓库 / 货位", "地址", "联系人", "联系电话", "状态", "操作"], rows,
  );
  document.querySelectorAll("#warehouseTable button[data-type]").forEach(button => {
    button.onclick = async () => {
      try {
        await api(`${button.dataset.type}/${button.dataset.id}/status`, {
          method: "POST", body: { status: button.dataset.status },
        });
        toast("状态已更新");
        await renderWarehouses();
      } catch (error) { toast(error.message, true); }
    };
  });
}

async function renderProjects() {
  if (!state.projects.length || state.user.role !== "admin") {
    state.projects = await api("projects");
  }
  const projects = state.user.role === "admin"
    ? state.projects : state.projects;
  const editing = state.editingProject;
  document.getElementById("page").innerHTML = `
    <div class="panel">
      <div class="panel-header"><h3>${editing ? "修改项目" : "创建项目"}</h3>
        ${editing ? `<button class="btn" id="cancelProject">取消修改</button>` : ""}</div>
      <form id="projectForm" class="fields-row">
        <label>项目名称<input name="projectName" required></label>
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
    document.getElementById("cancelProject").onclick = () => {
      state.editingProject = null;
      renderProjects();
    };
  }
  document.getElementById("projectForm").onsubmit = async event => {
    event.preventDefault();
    const data = Object.fromEntries(new FormData(event.target));
    try {
      if (editing) {
        await api(`projects/${editing.id}`, { method: "PUT", body: data });
      } else {
        await api("projects", { method: "POST", body: data });
      }
      state.editingProject = null;
      state.projects = [];
      toast("项目已保存");
      await renderProjects();
    } catch (error) { toast(error.message, true); }
  };
  document.getElementById("projectTable").innerHTML = simpleTable(
    ["项目", "状态", "项目管理员", "库存批次", "说明", "操作"],
    projects.map(project => [
      esc(project.project_name), setStatus(project.status), project.user_count || 0,
      project.lot_count ?? 0, esc(project.remark || "-"),
      `<div class="actions">
        <button class="btn small" data-action="edit" data-id="${project.id}">修改</button>
        <button class="btn small" data-action="status" data-id="${project.id}"
          data-status="${project.status === "active" ? "disabled" : "active"}">
          ${project.status === "active" ? "停用" : "启用"}</button>
        ${!project.lot_count ? `<button class="btn small" data-action="history" data-id="${project.id}">导入历史库存</button>` : ""}
      </div>`,
    ]),
  );
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
  if (!state.templates.length) state.templates = await api("templates");
  const current = state.templates.find(item => item.type === state.templateType);
  document.getElementById("page").innerHTML = `
    <div class="panel">
      <div class="panel-header"><h3>模板列配置</h3>
        <button class="btn" id="downloadTemplate">下载当前模板</button></div>
      <div class="template-tabs">
        <button class="segment ${state.templateType === "inbound" ? "active" : ""}" data-type="inbound">入库模板</button>
        <button class="segment ${state.templateType === "outbound" ? "active" : ""}" data-type="outbound">出库模板</button>
      </div>
      ${simpleTable(["系统字段", "Excel列名", "类型", "必填"],
        current.columns.map((column, index) => [
          `<span class="mono">${esc(column.key)}</span>`,
          `<input data-index="${index}" data-field="name" value="${esc(column.name)}">`,
          column.type === "text" ? "文本" : column.type === "date" ? "日期" : "正整数",
          `<input type="checkbox" data-index="${index}" data-field="required"
            ${column.required ? "checked" : ""}>`,
        ]))}
      <div class="actions" style="margin-top:12px">
        <button class="btn primary" id="saveTemplate">保存并生成新版本</button>
        <button class="btn" id="toggleTemplate">${current.status === "active" ? "停用模板" : "启用模板"}</button>
        <span class="muted">当前版本 v${current.version}</span>
        <span class="muted">状态：${current.status === "active" ? "启用" : "停用"}</span>
      </div>
    </div>`;
  document.querySelectorAll(".segment").forEach(button => {
    button.onclick = () => {
      state.templateType = button.dataset.type;
      renderTemplates();
    };
  });
  document.getElementById("downloadTemplate").onclick = () =>
    download(`templates/${state.templateType}/download`,
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
        method: "PUT", body: { columns },
      });
      state.templates = await api("templates");
      toast(`模板已更新到 v${updated.version}`);
      await renderTemplates();
    } catch (error) { toast(error.message, true); }
  };
  document.getElementById("toggleTemplate").onclick = async () => {
    try {
      const status = current.status === "active" ? "disabled" : "active";
      await api(`templates/${state.templateType}/status`, {
        method: "POST", body: { status },
      });
      state.templates = await api("templates");
      toast(status === "active" ? "模板已启用" : "模板已停用");
      await renderTemplates();
    } catch (error) { toast(error.message, true); }
  };
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
    "stock.inbound": "入库", "stock.outbound": "出库",
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
      else if (page === "products") await renderProducts();
      else if (page === "warehouses") await renderWarehouses();
      else if (page === "projects") await renderProjects();
      else if (page === "users") await renderUsers();
      else if (page === "password") renderPassword();
      else location.hash = "#dashboard";
      return;
    }
    if (page === "dashboard") await renderDashboard();
    else if (page === "inbound") await renderImportPage("inbound");
    else if (page === "outbound") await renderImportPage("outbound");
    else if (page === "inventory") await renderInventory();
    else if (page === "picking") await renderPicking();
    else if (page === "templates") await renderTemplates();
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

document.querySelector(".topbar button[data-page]").onclick = () =>
  location.hash = "#password";

window.addEventListener("hashchange", () => {
  const page = location.hash.slice(1) || "dashboard";
  const allowed = allowedPages().some(item => item.id === page) || page === "password";
  state.page = allowed ? page : "dashboard";
  if (state.user) renderPage();
});

(async function initialize() {
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
