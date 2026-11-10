// SM Remote Web Portal Engine
const App = {
  state: {
    serverUrl: localStorage.getItem("smremote_server") || window.location.origin,
    token: localStorage.getItem("smremote_token") || "",
    username: localStorage.getItem("smremote_user") || "Пользователь",
    workspaceKey: "",
    isLocalMode: false,
    availableNodes: [],
    selectedNodeId: "node1",
    rawEntities: [],
    items: [],
    folders: [],
    activeFolder: "all",
    activeFilter: "all",
    searchQuery: "",
    viewMode: localStorage.getItem("smremote_view_mode") || "grid",
    favorites: JSON.parse(localStorage.getItem("smremote_favs") || "[]"),
    isLoading: false,
    isEmbedded: false
  },

  async init() {
    const params = new URLSearchParams(window.location.search);
    if (params.get("embed") === "1" || params.get("embed") === "true") {
      this.state.isEmbedded = true;
      document.body.classList.add("is-embedded");
    }

    if (params.get("server")) {
      this.state.serverUrl = params.get("server");
    }
    if (params.get("token")) {
      this.state.token = params.get("token");
    }

    this.bindEvents();

    // Check if running on local server with direct DB access
    const isLocal = await this.checkLocalServer();
    if (isLocal) {
      await this.loadLocalData(this.state.selectedNodeId);
    } else if (this.state.token) {
      await this.loadRemoteData();
    } else {
      this.showLogin();
    }
  },

  async checkLocalServer() {
    try {
      const res = await fetch("/api/nodes", { signal: AbortSignal.timeout(1500) });
      if (res.ok) {
        const nodes = await res.json();
        if (Array.isArray(nodes) && nodes.length > 0) {
          this.state.isLocalMode = true;
          this.state.availableNodes = nodes;
          this.state.selectedNodeId = nodes[0].id;
          this.renderNodeSelector();
          return true;
        }
      }
    } catch (e) {
      // Not local mode
    }
    return false;
  },

  renderNodeSelector() {
    const sel = document.getElementById("nodeSelector");
    const container = document.getElementById("nodeSelectorContainer");
    if (!sel || !container) return;

    container.classList.remove("hidden");
    let html = "";
    this.state.availableNodes.forEach(n => {
      html += `<option value="${n.id}">${n.name}</option>`;
    });
    sel.innerHTML = html;
    sel.value = this.state.selectedNodeId;

    sel.onchange = (e) => {
      this.state.selectedNodeId = e.target.value;
      this.state.activeFolder = "all";
      this.loadLocalData(this.state.selectedNodeId);
    };
  },

  async loadLocalData(nodeId) {
    this.showLoading(true);
    try {
      const res = await fetch(`/api/connections?node=${nodeId}`);
      if (!res.ok) throw new Error("Ошибка загрузки данных из локальной базы");

      const data = await res.json();
      const allItems = data.items || [];

      this.processItems(allItems);
      this.showApp();
      this.renderSidebar();
      this.renderItems();
    } catch (err) {
      console.error(err);
      this.showToast("Ошибка: " + err.message, "error");
    } finally {
      this.showLoading(false);
    }
  },

  processItems(decryptedItems) {
    const activeItems = decryptedItems.filter(x => !x.deleted);
    this.state.items = activeItems;

    const foldersMap = {};
    activeItems.forEach(item => {
      if (item.type === "folder") {
        foldersMap[item.id] = { id: item.id, name: item.name, parent: item.folder };
      } else if (item.folder) {
        if (!foldersMap[item.folder]) {
          foldersMap[item.folder] = { id: item.folder, name: item.folder, parent: "" };
        }
      }
    });
    this.state.folders = Object.values(foldersMap);
  },

  bindEvents() {
    document.getElementById("loginForm")?.addEventListener("submit", (e) => {
      e.preventDefault();
      this.handleLogin();
    });

    document.getElementById("searchInput")?.addEventListener("input", (e) => {
      this.state.searchQuery = e.target.value.toLowerCase().trim();
      this.renderItems();
    });

    document.getElementById("logoutBtn")?.addEventListener("click", () => {
      this.logout();
    });

    document.getElementById("refreshBtn")?.addEventListener("click", () => {
      if (this.state.isLocalMode) {
        this.loadLocalData(this.state.selectedNodeId);
      } else {
        this.loadRemoteData(true);
      }
    });

    document.getElementById("viewGridBtn")?.addEventListener("click", () => {
      this.setViewMode("grid");
    });
    document.getElementById("viewListBtn")?.addEventListener("click", () => {
      this.setViewMode("list");
    });

    document.getElementById("agentHelpBtn")?.addEventListener("click", () => {
      this.toggleModal("agentModal", true);
    });

    document.getElementById("closeAgentModal")?.addEventListener("click", () => {
      this.toggleModal("agentModal", false);
    });

    window.addEventListener("keydown", (e) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        const searchInput = document.getElementById("searchInput");
        searchInput?.focus();
        searchInput?.select();
      }
    });
  },

  // ===================== CRYPTO (Web Crypto API) =====================
  async decryptEntity(cipherTextBase64, token) {
    if (!cipherTextBase64 || !token) return null;
    try {
      const binaryString = atob(cipherTextBase64);
      const len = binaryString.length;
      if (len < 16) return null;

      const bytes = new Uint8Array(len);
      for (let i = 0; i < len; i++) {
        bytes[i] = binaryString.charCodeAt(i);
      }

      const iv = bytes.slice(0, 16);
      const data = bytes.slice(16);

      const enc = new TextEncoder();
      const tokenBuffer = enc.encode(token);
      const keyHash = await crypto.subtle.digest("SHA-256", tokenBuffer);

      const cryptoKey = await crypto.subtle.importKey(
        "raw",
        keyHash,
        { name: "AES-CBC" },
        false,
        ["decrypt"]
      );

      const decrypted = await crypto.subtle.decrypt(
        { name: "AES-CBC", iv: iv },
        cryptoKey,
        data
      );

      const jsonStr = new TextDecoder().decode(decrypted);
      return JSON.parse(jsonStr);
    } catch (err) {
      return null;
    }
  },

  // ===================== REMOTE CLEAR.NODE API =====================
  async handleLogin() {
    const serverInput = document.getElementById("serverInput").value.trim().replace(/\/+$/, "");
    const usernameInput = document.getElementById("usernameInput").value.trim();
    const passwordInput = document.getElementById("passwordInput").value;
    const errorBox = document.getElementById("loginError");

    errorBox.classList.add("hidden");
    const submitBtn = document.getElementById("loginSubmitBtn");
    submitBtn.disabled = true;
    submitBtn.textContent = "Вход...";

    try {
      this.state.serverUrl = serverInput || window.location.origin;

      const res = await fetch(`${this.state.serverUrl}/auth/login`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username: usernameInput, password: passwordInput })
      });

      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.detail || "Неверный логин или пароль");
      }

      const authData = await res.json();
      this.state.token = authData.access_token;
      this.state.username = authData.username || usernameInput;

      localStorage.setItem("smremote_server", this.state.serverUrl);
      localStorage.setItem("smremote_token", this.state.token);
      localStorage.setItem("smremote_user", this.state.username);

      await this.loadRemoteData();
    } catch (err) {
      errorBox.textContent = err.message || "Ошибка подключения к серверу";
      errorBox.classList.remove("hidden");
    } finally {
      submitBtn.disabled = false;
      submitBtn.textContent = "Войти";
    }
  },

  logout() {
    this.state.token = "";
    this.state.workspaceKey = "";
    this.state.items = [];
    localStorage.removeItem("smremote_token");
    this.showLogin();
  },

  async loadRemoteData(isRefresh = false) {
    this.showLoading(true);
    try {
      const keyRes = await fetch(`${this.state.serverUrl}/sync/workspace_key`, {
        headers: { Authorization: `Bearer ${this.state.token}` }
      });
      if (keyRes.status === 401 || keyRes.status === 403) {
        this.logout();
        return;
      }
      const keyData = await keyRes.json();
      this.state.workspaceKey = keyData.key;

      const pullRes = await fetch(`${this.state.serverUrl}/sync/pull?since_revision=0`, {
        headers: { Authorization: `Bearer ${this.state.token}` }
      });
      if (!pullRes.ok) throw new Error("Не удалось загрузить данные с сервера");

      const rawEntities = await pullRes.json();
      this.state.rawEntities = rawEntities;

      const decryptedItems = [];
      for (const ent of rawEntities) {
        if (ent.deleted) continue;
        const dec = await this.decryptEntity(ent.encrypted_data, this.state.workspaceKey);
        if (dec) {
          decryptedItems.push({
            id: dec.id || ent.id,
            name: dec.name || "Без названия",
            type: (dec.type || "iiko").toLowerCase(),
            ip: dec.ip || "",
            port: dec.port || "",
            login: dec.login || "",
            password: dec.password || "",
            folder: dec.folder || "",
            description: dec.description || "",
            deleted: !!dec.deleted
          });
        }
      }

      this.processItems(decryptedItems);
      this.showApp();
      this.renderSidebar();
      this.renderItems();

      if (isRefresh) {
        this.showToast("Данные успешно синхронизированы!");
      }
    } catch (err) {
      console.error("loadRemoteData error:", err);
      this.showToast("Ошибка: " + err.message, "error");
    } finally {
      this.showLoading(false);
    }
  },

  // ===================== UI RENDERING =====================
  showLogin() {
    document.getElementById("loginView")?.classList.remove("hidden");
    document.getElementById("appView")?.classList.add("hidden");
    const serverInput = document.getElementById("serverInput");
    if (serverInput) serverInput.value = this.state.serverUrl;
    const userInput = document.getElementById("usernameInput");
    if (userInput && this.state.username) userInput.value = this.state.username;
  },

  showApp() {
    document.getElementById("loginView")?.classList.add("hidden");
    document.getElementById("appView")?.classList.remove("hidden");
    const userLabel = document.getElementById("currentUsername");
    if (userLabel) userLabel.textContent = this.state.username;
    const serverLabel = document.getElementById("currentServerLabel");
    if (serverLabel) {
      serverLabel.textContent = this.state.isLocalMode ? "Локальная база" : "CLEAR.node";
    }
  },

  showLoading(loading) {
    this.state.isLoading = loading;
    const spinner = document.getElementById("loadingSpinner");
    if (spinner) {
      if (loading) spinner.classList.remove("hidden");
      else spinner.classList.add("hidden");
    }
  },

  setViewMode(mode) {
    this.state.viewMode = mode;
    localStorage.setItem("smremote_view_mode", mode);
    document.getElementById("viewGridBtn")?.classList.toggle("bg-gray-700", mode === "grid");
    document.getElementById("viewListBtn")?.classList.toggle("bg-gray-700", mode === "list");
    this.renderItems();
  },

  renderSidebar() {
    const list = document.getElementById("folderList");
    if (!list) return;

    const connectionsCount = this.state.items.filter(x => x.type !== "folder").length;
    const favCount = this.state.favorites.length;

    let html = `
      <div onclick="App.setFolder('all')" class="cursor-pointer flex items-center justify-between px-3 py-2 rounded-lg ${this.state.activeFolder === 'all' ? 'bg-amber-500/20 text-amber-400 font-medium' : 'text-gray-300 hover:bg-gray-800/60'}">
        <div class="flex items-center gap-2">
          <span>📁</span>
          <span>Все подключения</span>
        </div>
        <span class="text-xs px-2 py-0.5 rounded-full bg-gray-800 text-gray-400">${connectionsCount}</span>
      </div>

      <div onclick="App.setFilter('fav')" class="cursor-pointer flex items-center justify-between px-3 py-2 rounded-lg ${this.state.activeFilter === 'fav' ? 'bg-amber-500/20 text-amber-400 font-medium' : 'text-gray-300 hover:bg-gray-800/60'}">
        <div class="flex items-center gap-2">
          <span>⭐</span>
          <span>Избранное</span>
        </div>
        <span class="text-xs px-2 py-0.5 rounded-full bg-gray-800 text-gray-400">${favCount}</span>
      </div>
      
      <div class="my-2 border-t border-gray-800/80"></div>
      <div class="px-3 text-xs font-semibold text-gray-400 uppercase tracking-wider mb-1">Папки (${this.state.folders.length})</div>
    `;

    this.state.folders.slice(0, 100).forEach(f => {
      const count = this.state.items.filter(x => (x.folder === f.id || x.folder === f.name) && x.type !== "folder").length;
      const isSel = this.state.activeFolder === f.id || this.state.activeFolder === f.name;
      html += `
        <div onclick="App.setFolder('${f.id}')" class="cursor-pointer flex items-center justify-between px-3 py-1.5 rounded-lg text-xs ${isSel ? 'bg-amber-500/20 text-amber-400 font-medium' : 'text-gray-400 hover:bg-gray-800/60 hover:text-gray-200'}">
          <div class="flex items-center gap-1.5 truncate">
            <span>🗂️</span>
            <span class="truncate" title="${f.name}">${f.name}</span>
          </div>
          <span class="text-[11px] px-1.5 py-0.2 rounded bg-gray-800/70 text-gray-400">${count}</span>
        </div>
      `;
    });

    list.innerHTML = html;
  },

  setFolder(folderId) {
    this.state.activeFolder = folderId;
    this.state.activeFilter = "all";
    this.renderSidebar();
    this.renderItems();
  },

  setFilter(filterType) {
    this.state.activeFilter = filterType;
    if (filterType === "fav") {
      this.state.activeFolder = "all";
    }
    this.renderSidebar();
    this.renderItems();
  },

  getFilteredItems() {
    let list = this.state.items.filter(x => x.type !== "folder");

    if (this.state.activeFilter === "fav") {
      list = list.filter(x => this.state.favorites.includes(x.id));
    } else if (this.state.activeFilter !== "all") {
      const f = this.state.activeFilter;
      if (f === "iiko") {
        list = list.filter(x => x.type === "iiko" || x.type === "iikooffice" || x.type === "iikochain" || x.type === "chain");
      } else if (f === "ammyy") {
        list = list.filter(x => x.type === "ammyy" || x.type === "ammyyadmin");
      } else {
        list = list.filter(x => x.type === f);
      }
    }

    if (this.state.activeFolder !== "all") {
      list = list.filter(x => x.folder === this.state.activeFolder || (this.getFolderName(x.folder) === this.state.activeFolder));
    }

    if (this.state.searchQuery) {
      const q = this.state.searchQuery;
      list = list.filter(x =>
        x.name.toLowerCase().includes(q) ||
        x.ip.toLowerCase().includes(q) ||
        x.login.toLowerCase().includes(q) ||
        x.description.toLowerCase().includes(q) ||
        this.getFolderName(x.folder).toLowerCase().includes(q)
      );
    }

    return list;
  },

  getFolderName(folderId) {
    if (!folderId) return "";
    const f = this.state.folders.find(x => x.id === folderId);
    return f ? f.name : folderId;
  },

  renderItems() {
    const container = document.getElementById("itemsContainer");
    const counter = document.getElementById("itemsCountBadge");
    if (!container) return;

    const items = this.getFilteredItems();
    if (counter) counter.textContent = `Найдено: ${items.length}`;

    if (items.length === 0) {
      container.innerHTML = `
        <div class="col-span-full py-16 text-center text-gray-400">
          <div class="text-4xl mb-3">🔍</div>
          <p class="text-base font-medium">Ничего не найдено</p>
          <p class="text-xs text-gray-400 mt-1">Попробуйте изменить поисковый запрос или выбрать другую категорию</p>
        </div>
      `;
      return;
    }

    const displayItems = items.slice(0, 300);

    if (this.state.viewMode === "list") {
      this.renderListView(container, displayItems, items.length);
    } else {
      this.renderGridView(container, displayItems, items.length);
    }
  },

  renderGridView(container, items, totalCount) {
    container.className = "grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4";
    let html = "";

    items.forEach(item => {
      const isFav = this.state.favorites.includes(item.id);
      const badgeClass = this.getTypeBadgeClass(item.type);
      const typeLabel = this.getTypeLabel(item.type);
      const folderName = this.getFolderName(item.folder);
      const isIiko = item.type === "iiko" || item.type === "iikooffice" || item.type === "iikochain" || item.type === "chain";

      html += `
        <div class="conn-card rounded-xl p-4 flex flex-col justify-between relative group">
          <div>
            <div class="flex items-start justify-between gap-2 mb-2">
              <div class="flex items-center gap-2">
                <span class="text-xs font-semibold px-2 py-0.5 rounded ${badgeClass}">${typeLabel}</span>
                ${folderName ? `<span class="text-xs text-gray-400 truncate max-w-[130px]" title="${folderName}">📁 ${folderName}</span>` : ""}
              </div>
              <button onclick="App.toggleFavorite('${item.id}')" class="text-gray-400 hover:text-amber-400 transition" title="В избранное">
                ${isFav ? "⭐" : "☆"}
              </button>
            </div>

            <h3 class="font-bold text-base text-gray-100 truncate mb-1" title="${item.name}">${item.name}</h3>
            
            <div class="space-y-1.5 text-xs text-gray-300 mt-3 bg-gray-900/60 p-2.5 rounded-lg border border-gray-800">
              <div class="flex items-center justify-between">
                <span class="text-gray-400">Адрес:</span>
                <span class="font-mono text-gray-200 truncate select-all" title="${item.ip}${item.port ? ':' + item.port : ''}">
                  ${item.ip || "—"}${item.port && item.port !== '0' ? ':' + item.port : ''}
                </span>
              </div>
              <div class="flex items-center justify-between">
                <span class="text-gray-400">Логин:</span>
                <div class="flex items-center gap-1.5">
                  <span class="font-mono text-gray-200 truncate max-w-[120px]">${item.login || "—"}</span>
                  ${item.login ? `<button onclick="App.copyText('${item.login}', 'Логин')" class="text-gray-400 hover:text-white" title="Копировать логин">📋</button>` : ""}
                </div>
              </div>
              ${item.password ? `
                <div class="flex items-center justify-between">
                  <span class="text-gray-400">Пароль:</span>
                  <div class="flex items-center gap-1.5">
                    <span class="font-mono text-gray-400">••••••••</span>
                    <button onclick="App.copyText('${item.password}', 'Пароль')" class="text-gray-400 hover:text-white" title="Копировать пароль">📋</button>
                  </div>
                </div>
              ` : ""}
            </div>

            ${item.description ? `<p class="text-xs text-gray-400 mt-2 line-clamp-2" title="${item.description}">${item.description}</p>` : ""}
          </div>

          <div class="mt-4 pt-3 border-t border-gray-800 flex flex-col gap-2">
            <!-- Launch Button -->
            <button onclick="App.launch('${item.id}')" class="btn-gold w-full py-2.5 px-4 rounded-lg flex items-center justify-center gap-2 text-sm font-bold shadow-md cursor-pointer">
              <span>🚀</span>
              <span>ЗАПУСТИТЬ</span>
            </button>

            <!-- Actions Row -->
            <div class="flex items-center gap-1.5">
              ${isIiko ? `
                <button onclick="window.open('https://pp.iiko.ru', '_blank')" class="btn-secondary flex-1 py-1.5 px-2 rounded-md text-xs font-semibold flex items-center justify-center gap-1" title="Открыть Портал Партнеров pp.iiko.ru">
                  <span>ℹ️</span>
                  <span>PARTNER</span>
                </button>
                <button onclick="App.openWebOffice('${item.ip}', '${item.port}')" class="btn-secondary py-1.5 px-2.5 rounded-md text-xs" title="Открыть Web Office">
                  🌐 Web
                </button>
              ` : ""}
              <button onclick="App.copyLaunchLink('${item.id}')" class="btn-secondary py-1.5 px-2.5 rounded-md text-xs flex-1 text-center truncate" title="Скопировать ссылку smremote://">
                🔗 Ссылка
              </button>
            </div>
          </div>
        </div>
      `;
    });

    if (totalCount > items.length) {
      html += `
        <div class="col-span-full py-4 text-center text-xs text-gray-400">
          Показано первых ${items.length} из ${totalCount} подключений. Воспользуйтесь поиском или выберите конкретную папку.
        </div>
      `;
    }

    container.innerHTML = html;
  },

  renderListView(container, items, totalCount) {
    container.className = "flex flex-col gap-2";
    let html = `
      <div class="overflow-x-auto rounded-xl border border-gray-800 bg-gray-900/40">
        <table class="w-full text-left text-xs text-gray-300">
          <thead class="bg-gray-800/70 text-gray-400 font-semibold uppercase tracking-wider">
            <tr>
              <th class="py-3 px-4 w-10">★</th>
              <th class="py-3 px-4">Тип</th>
              <th class="py-3 px-4">Название</th>
              <th class="py-3 px-4">Адрес / Порт</th>
              <th class="py-3 px-4">Логин</th>
              <th class="py-3 px-4">Папка</th>
              <th class="py-3 px-4 text-right">Действия</th>
            </tr>
          </thead>
          <tbody class="divide-y divide-gray-800/60">
    `;

    items.forEach(item => {
      const isFav = this.state.favorites.includes(item.id);
      const badgeClass = this.getTypeBadgeClass(item.type);
      const typeLabel = this.getTypeLabel(item.type);
      const folderName = this.getFolderName(item.folder);
      const isIiko = item.type === "iiko" || item.type === "iikooffice" || item.type === "iikochain" || item.type === "chain";

      html += `
        <tr class="hover:bg-gray-800/40 transition">
          <td class="py-3 px-4">
            <button onclick="App.toggleFavorite('${item.id}')" class="text-gray-400 hover:text-amber-400">
              ${isFav ? "⭐" : "☆"}
            </button>
          </td>
          <td class="py-3 px-4">
            <span class="font-semibold px-2 py-0.5 rounded text-[11px] ${badgeClass}">${typeLabel}</span>
          </td>
          <td class="py-3 px-4 font-bold text-gray-100">${item.name}</td>
          <td class="py-3 px-4 font-mono select-all text-gray-300">${item.ip || '—'}${item.port && item.port !== '0' ? ':' + item.port : ''}</td>
          <td class="py-3 px-4 font-mono">
            ${item.login ? `
              <span class="inline-flex items-center gap-1">
                ${item.login}
                <button onclick="App.copyText('${item.login}', 'Логин')" class="text-gray-400 hover:text-white">📋</button>
              </span>
            ` : "—"}
          </td>
          <td class="py-3 px-4 text-gray-400">${folderName || "—"}</td>
          <td class="py-3 px-4 text-right">
            <div class="inline-flex items-center gap-1.5 justify-end">
              ${isIiko ? `
                <button onclick="window.open('https://pp.iiko.ru', '_blank')" class="btn-secondary py-1 px-2 rounded text-xs" title="Открыть pp.iiko.ru">
                  PARTNER
                </button>
              ` : ""}
              <button onclick="App.copyLaunchLink('${item.id}')" class="btn-secondary py-1 px-2 rounded text-xs" title="Скопировать ссылку smremote://">
                🔗
              </button>
              <button onclick="App.launch('${item.id}')" class="btn-gold py-1 px-3 rounded text-xs font-bold cursor-pointer">
                🚀 Запустить
              </button>
            </div>
          </td>
        </tr>
      `;
    });

    html += `</tbody></table></div>`;
    if (totalCount > items.length) {
      html += `
        <div class="py-3 text-center text-xs text-gray-400">
          Показано первых ${items.length} из ${totalCount} строк.
        </div>
      `;
    }
    container.innerHTML = html;
  },

  getTypeBadgeClass(type) {
    switch (type) {
      case "iiko":
      case "iikooffice":
      case "iikochain":
      case "chain":
        return "badge-iiko";
      case "rdp":
        return "badge-rdp";
      case "anydesk":
        return "badge-anydesk";
      case "rustdesk":
        return "badge-rustdesk";
      case "aspia":
        return "bg-purple-500/20 text-purple-300 border border-purple-500/30";
      case "ammyy":
      case "ammyyadmin":
        return "bg-emerald-500/20 text-emerald-300 border border-emerald-500/30";
      default:
        return "badge-folder";
    }
  },

  getTypeLabel(type) {
    switch (type) {
      case "iiko":
      case "iikooffice":
        return "iiko Office";
      case "iikochain":
      case "chain":
        return "iiko Chain";
      case "rdp":
        return "RDP";
      case "anydesk":
        return "AnyDesk";
      case "rustdesk":
        return "RustDesk";
      case "aspia":
        return "Aspia";
      case "ammyy":
      case "ammyyadmin":
        return "Ammyy";
      case "rudesktop":
        return "RuDesktop";
      case "assistant":
        return "Ассистент";
      default:
        return type.toUpperCase();
    }
  },

  // ===================== LAUNCH & PROTOCOL =====================
  normalizeType(t) {
    if (!t) return "iiko";
    t = t.toLowerCase();
    if (t === "iikooffice" || t === "iikochain" || t === "chain") return "iiko";
    if (t === "ammyyadmin") return "ammyy";
    return t;
  },

  buildSmRemoteUri(item) {
    const type = this.normalizeType(item.type);
    let ipPort = item.ip;
    let login = item.login;
    let password = item.password;

    if (type === "anydesk" || type === "ammyy" || type === "rustdesk") {
      if (!login && ipPort) login = ipPort;
      if (!ipPort && login) ipPort = login;
    }

    if (item.port && item.port !== "0" && type === "iiko") {
      ipPort = `${ipPort}:${item.port}`;
    }
    
    let uri = `smremote://?type=${encodeURIComponent(type)}`;
    if (ipPort) {
      uri += `&ip=${encodeURIComponent(ipPort)}`;
    }
    if (login) {
      uri += `&login=${encodeURIComponent(login)}`;
    }
    if (password) {
      uri += `&password=${encodeURIComponent(password)}`;
    }
    return uri;
  },

  launch(id) {
    const item = this.state.items.find(x => x.id === id);
    if (!item) return;

    const uri = this.buildSmRemoteUri(item);
    console.log("Launching SM Remote URI:", uri);

    window.location.href = uri;

    this.showToast(`Отправлена команда на запуск: ${item.name}`, "success");
  },

  copyLaunchLink(id) {
    const item = this.state.items.find(x => x.id === id);
    if (!item) return;
    const uri = this.buildSmRemoteUri(item);
    this.copyText(uri, "Ссылка для запуска");
  },

  openWebOffice(ip, port) {
    const host = ip || "";
    const p = port || "443";
    const proto = p === "443" || p === "8443" ? "https" : "http";
    window.open(`${proto}://${host}:${p}/resto`, "_blank");
  },

  toggleFavorite(id) {
    const idx = this.state.favorites.indexOf(id);
    if (idx === -1) {
      this.state.favorites.push(id);
    } else {
      this.state.favorites.splice(idx, 1);
    }
    localStorage.setItem("smremote_favs", JSON.stringify(this.state.favorites));
    this.renderSidebar();
    this.renderItems();
  },

  copyText(text, label = "Текст") {
    navigator.clipboard.writeText(text).then(() => {
      this.showToast(`${label} скопирован в буфер!`);
    }).catch(() => {
      const ta = document.createElement("textarea");
      ta.value = text;
      document.body.appendChild(ta);
      ta.select();
      document.execCommand("copy");
      document.body.removeChild(ta);
      this.showToast(`${label} скопирован в буфер!`);
    });
  },

  showToast(message, type = "info") {
    const toast = document.getElementById("toast");
    const toastMsg = document.getElementById("toastMessage");
    if (!toast || !toastMsg) return;

    toastMsg.textContent = message;
    toast.classList.remove("hidden", "translate-y-8", "opacity-0");
    toast.classList.add("translate-y-0", "opacity-100");

    clearTimeout(this._toastTimeout);
    this._toastTimeout = setTimeout(() => {
      toast.classList.add("translate-y-8", "opacity-0");
      setTimeout(() => toast.classList.add("hidden"), 300);
    }, 4000);
  },

  toggleModal(modalId, show) {
    const m = document.getElementById(modalId);
    if (!m) return;
    if (show) m.classList.remove("hidden");
    else m.classList.add("hidden");
  }
};

window.App = App;
document.addEventListener("DOMContentLoaded", () => App.init());
