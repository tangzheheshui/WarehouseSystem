// 万顺控制台：平台管理员管理公司租户（创建、停用启用、重置公司管理员密码）。
const TOKEN_KEY = "wmsConsoleToken";

const state = {
  token: localStorage.getItem(TOKEN_KEY) || "",
  user: null,
  page: "companies",
  showCreateForm: false,
  domain: "",
};

const titles = { companies: "公司管理", password: "修改密码" };

const pages = [
  { id: "companies", label: "公司管理", tone: "blue" },
  { id: "password", label: "修改密码", tone: "slate" },
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
  if (settings.body) {
    settings.headers["Content-Type"] = "application/json";
    settings.body = JSON.stringify(settings.body);
  }
  const response = await fetch(`api/console/${path}`, settings);
  let data = {};
  try { data = await response.json(); } catch (_) {}
  if (response.status === 401 && state.token) {
    clearSession();
    showLogin();
  }
  if (!response.ok) {
    throw Object.assign(new Error(data.error || "请求失败"), {
      status: response.status,
      data,
    });
  }
  return data;
}

function clearSession() {
  state.token = "";
  state.user = null;
  localStorage.removeItem(TOKEN_KEY);
}

function showLogin() {
  document.getElementById("appView").classList.add("hidden");
  document.getElementById("loginView").classList.remove("hidden");
}

function renderNav() {
  const nav = document.getElementById("nav");
  nav.innerHTML = `
    <div class="nav-group">
      <div class="nav-title">控制台</div>
      ${pages.map(item => `
        <button data-page="${item.id}" data-tone="${item.tone}"
          class="${state.page === item.id ? "active" : ""}">
          <span class="nav-dot"></span><span>${esc(item.label)}</span>
        </button>`).join("")}
    </div>`;
  nav.querySelectorAll("button").forEach(button => {
    button.onclick = () => { state.page = button.dataset.page; render(); };
  });
  document.getElementById("userName").textContent =
    state.user?.displayName || state.user?.username || "";
  document.getElementById("userAvatar").textContent = String(
    state.user?.displayName || state.user?.username || "?"
  ).slice(0, 1).toUpperCase();
}

function companyUrl(prefix) {
  return state.domain ? `${prefix}.${state.domain}` : prefix;
}

function secretPanel(title, lines, password) {
  return `
    <div class="panel" style="max-width:560px">
      <h3>${esc(title)}</h3>
      ${lines.map(line => `<p class="form-hint">${line}</p>`).join("")}
      <div class="secret-box">
        <code>${esc(password)}</code>
        <button class="btn small" type="button" data-copy="${esc(password)}">复制</button>
      </div>
      <p class="form-error" style="margin-top:8px">密码仅本次显示，关闭后无法再次查看。</p>
      <div class="actions" style="margin-top:10px">
        <button class="btn" type="button" data-close="1">我已保存，关闭</button>
      </div>
    </div>`;
}

function bindSecretPanel(container) {
  container.querySelectorAll("[data-copy]").forEach(button => {
    button.onclick = async () => {
      try {
        await navigator.clipboard.writeText(button.dataset.copy);
        toast("已复制到剪贴板");
      } catch (_) {
        toast("复制失败，请手动选择复制", true);
      }
    };
  });
  container.querySelectorAll("[data-close]").forEach(button => {
    button.onclick = () => render();
  });
}

async function renderCompanies() {
  const companies = await api("companies");
  const page = document.getElementById("page");
  const createForm = `
    <div class="panel">
      <div class="panel-header">
        <h3>创建公司</h3>
        <button id="toggleCreate" class="btn small" type="button">
          ${state.showCreateForm ? "收起" : "新建公司"}
        </button>
      </div>
      ${state.showCreateForm ? `
        <form id="createForm" class="fields" style="max-width:560px">
          <label>公司前缀（子域名与数据目录，2-32位小写字母/数字/连字符）
            <input name="prefix" pattern="[a-z][a-z0-9-]*[a-z0-9]" maxlength="32" required
              placeholder="例如 acme"></label>
          <label>公司名称（显示在该公司登录页）
            <input name="name" maxlength="50" required placeholder="例如 ACME贸易有限公司"></label>
          <label>公司管理员姓名全拼
            <input name="adminUsername" pattern="[a-z]{2,50}" maxlength="50" required
              placeholder="例如 zhangsan"></label>
          <label>公司管理员姓名（选填，默认同姓名全拼）
            <input name="adminDisplayName" maxlength="50" placeholder="例如 张三"></label>
          <p class="form-hint">初始密码自动生成，保存后一次性显示。</p>
          <button class="btn primary" type="submit">创建公司</button>
          <p id="createError" class="form-error"></p>
        </form>` : ""}
    </div>
    <div class="panel table-top">
      <div class="panel-header"><h3>公司列表（${companies.length} 家）</h3></div>
      <div class="table-wrap">
        <table>
          <thead><tr>
            <th>公司前缀</th><th>公司名称</th><th>管理员用户名</th>
            <th>访问地址</th><th>状态</th><th>创建时间</th><th>操作</th>
          </tr></thead>
          <tbody id="companyRows">
            ${companies.length ? companies.map(company => `
              <tr>
                <td class="mono">${esc(company.prefix)}</td>
                <td>${esc(company.name)}</td>
                <td class="mono">${esc(company.adminUsername)}</td>
                <td class="mono">${esc(companyUrl(company.prefix))}</td>
                <td><span class="badge ${company.status}">${
                  company.status === "active" ? "启用" : "停用"
                }</span></td>
                <td class="nowrap">${formatTime(company.createdAt)}</td>
                <td><div class="actions">
                  <button class="btn small" data-action="reset"
                    data-prefix="${esc(company.prefix)}">重置管理员密码</button>
                  <button class="btn small ${company.status === "active" ? "danger" : "success"}"
                    data-action="status" data-prefix="${esc(company.prefix)}"
                    data-status="${company.status === "active" ? "disabled" : "active"}">
                    ${company.status === "active" ? "停用" : "启用"}</button>
                </div></td>
              </tr>`).join("") : `<tr><td colspan="7" class="empty">还没有公司，点击「新建公司」开通第一家</td></tr>`}
          </tbody>
        </table>
      </div>
    </div>`;
  page.innerHTML = createForm;

  const toggle = document.getElementById("toggleCreate");
  if (toggle) toggle.onclick = () => {
    state.showCreateForm = !state.showCreateForm;
    render();
  };
  const form = document.getElementById("createForm");
  if (form) form.onsubmit = async event => {
    event.preventDefault();
    const errorNode = document.getElementById("createError");
    errorNode.textContent = "";
    const data = Object.fromEntries(new FormData(event.target));
    try {
      const result = await api("companies", {
        method: "POST",
        body: {
          prefix: data.prefix.trim().toLowerCase(),
          name: data.name.trim(),
          adminUsername: data.adminUsername.trim(),
          adminDisplayName: data.adminDisplayName.trim(),
        },
      });
      state.showCreateForm = false;
      page.innerHTML = secretPanel(
        `公司「${result.name}」已创建`,
        [
          `访问地址：<b>${esc(result.subdomain)}</b>`,
          `公司管理员账号：<b class="mono">${esc(result.adminUsername)}</b>，初始密码如下：`,
        ],
        result.initialPassword,
      );
      bindSecretPanel(page);
      toast("公司创建成功");
    } catch (error) { errorNode.textContent = error.message; }
  };
  page.querySelectorAll("#companyRows button[data-action]").forEach(button => {
    button.onclick = async () => {
      const prefix = button.dataset.prefix;
      try {
        if (button.dataset.action === "reset") {
          if (!confirm(`确认重置公司 ${prefix} 管理员密码？旧密码与登录态将全部失效。`)) return;
          const result = await api(`companies/${prefix}/reset-admin-password`, {
            method: "POST", body: {},
          });
          page.innerHTML = secretPanel(
            `公司 ${esc(prefix)} 管理员密码已重置`,
            ["新初始密码如下，请立即转交公司管理员："],
            result.password,
          );
          bindSecretPanel(page);
        } else {
          const target = button.dataset.status === "disabled" ? "停用" : "启用";
          if (!confirm(`确认${target}公司 ${prefix}？${target}后该公司全部子域名${
            target === "停用" ? "立即不可访问" : "恢复访问"
          }。`)) return;
          await api(`companies/${prefix}/status`, {
            method: "POST", body: { status: button.dataset.status },
          });
          toast(`公司已${target}`);
          render();
        }
      } catch (error) { toast(error.message, true); }
    };
  });
}

function renderPassword() {
  document.getElementById("page").innerHTML = `
    <div class="panel" style="max-width:520px">
      <h3>修改控制台密码</h3>
      <form id="passwordForm" class="fields">
        <label>原密码<input name="oldPassword" type="password" required></label>
        <label>新密码<input name="newPassword" type="password" required></label>
        <label>确认新密码<input name="confirmPassword" type="password" required></label>
        <p class="form-hint">密码须为8-20位，且同时包含字母和数字。</p>
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
      if (state.user) state.user.mustChangePassword = false;
      toast("密码已修改");
      state.page = "companies";
      render();
    } catch (error) { errorNode.textContent = error.message; }
  };
}

async function render() {
  if (!state.user) return;
  if (state.user.mustChangePassword && state.page !== "password") {
    state.page = "password";
  }
  document.getElementById("pageTitle").textContent =
    titles[state.page] || titles.companies;
  renderNav();
  try {
    if (state.page === "companies") await renderCompanies();
    else if (state.page === "password") renderPassword();
  } catch (error) {
    document.getElementById("page").innerHTML =
      `<div class="panel result-box error">${esc(error.message)}</div>`;
  }
}

function showApp() {
  document.getElementById("loginView").classList.add("hidden");
  document.getElementById("appView").classList.remove("hidden");
  state.domain = state.user?.domain || state.domain;
  render();
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
    showApp();
  } catch (error) { errorNode.textContent = error.message; }
};

document.getElementById("logoutButton").onclick = async () => {
  try { await api("logout", { method: "POST", body: {} }); } catch (_) {}
  clearSession();
  showLogin();
};

document.getElementById("passwordButton").onclick = () => {
  state.page = "password";
  render();
};

(async function initialize() {
  if (!state.token) {
    showLogin();
    return;
  }
  try {
    state.user = await api("me");
    showApp();
  } catch (_) {
    clearSession();
    showLogin();
  }
})();
