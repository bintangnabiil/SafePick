const state = {
  settings: null,
  enrollSession: null,
  activeView: "dashboard",
  enrollAutoTimer: null,
  enrollCapturing: false,
  logs: [],
  students: [],
  parents: [],
  logFilters: {
    date: "",
    student: "",
    class: "",
  },
  logPagination: {},
  databasePagination: {
    students: { page: 1, pageSize: 10 },
    parents: { page: 1, pageSize: 10 },
  },
  navDirectMove: false,
  statusPolling: false,
};

const viewTitles = {
  dashboard: "Dashboard",
  "log-attendance": "QR Kehadiran Siswa",
  "log-pickup-qr": "QR Penjemputan Non-orang Tua",
  "log-pickup-face": "Muka Penjemputan Orang Tua",
  "log-unknown-face": "Muka Tidak Dikenal",
  enroll: "Enroll",
  database: "Database",
  settings: "Settings",
};

const logTables = [
  {
    type: "KEHADIRAN_QR",
    body: "#logsAttendanceBody",
    count: "#logsAttendanceCount",
    empty: "Belum ada log QR Kehadiran Siswa yang tercatat.",
  },
  {
    type: "PENJEMPUTAN_QR",
    body: "#logsPickupQrBody",
    count: "#logsPickupQrCount",
    empty: "Belum ada log QR Penjemputan Non-orang Tua yang tercatat.",
  },
  {
    type: "PENJEMPUTAN_FACE",
    body: "#logsPickupFaceBody",
    count: "#logsPickupFaceCount",
    empty: "Belum ada log Muka Penjemputan Orang Tua yang tercatat.",
  },
  {
    type: "UNKNOWN_FACE",
    body: "#logsUnknownFaceBody",
    count: "#logsUnknownFaceCount",
    empty: "Belum ada log Muka Tidak Dikenal yang tercatat.",
  },
];

function formatNumber(value, digits = 1) {
  const number = Number(value);
  return Number.isFinite(number) ? number.toFixed(digits) : "-";
}

function formatMb(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "-";
  if (number >= 1024) return `${(number / 1024).toFixed(1)} GB`;
  return `${number.toFixed(0)} MB`;
}

function formatUptime(seconds) {
  const total = Math.max(0, Math.floor(Number(seconds) || 0));
  const days = Math.floor(total / 86400);
  const hours = Math.floor((total % 86400) / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  if (days > 0) return `${days}d ${hours}h`;
  if (hours > 0) return `${hours}h ${minutes}m`;
  return `${minutes}m`;
}

function renderPerformance(perf = {}) {
  $("#perfCpuTemp").textContent = perf.cpu_temp_c == null ? "N/A" : `${formatNumber(perf.cpu_temp_c, 1)}°C`;
  $("#perfCpu").textContent = `${formatNumber(perf.cpu_percent, 1)}%`;
  $("#perfLoad").textContent = Array.isArray(perf.load_avg)
    ? `Load ${perf.load_avg.map((value) => formatNumber(value, 2)).join(" / ")}`
    : `Cores ${perf.cpu_count ?? "-"}`;
  $("#perfMemory").textContent = `${formatNumber(perf.memory_percent, 1)}%`;
  $("#perfMemoryDetail").textContent = `${formatMb(perf.memory_used_mb)} / ${formatMb(perf.memory_total_mb)}`;
  $("#perfDisk").textContent = `${formatNumber(perf.disk_percent, 1)}%`;
  $("#perfDiskDetail").textContent = `${formatNumber(perf.disk_free_gb, 2)} GB free / ${formatNumber(perf.disk_total_gb, 2)} GB`;
  $("#perfProcess").textContent = formatMb(perf.process_rss_mb);
  $("#perfThreads").textContent = `${perf.threads ?? "-"} threads`;
  $("#perfUptime").textContent = formatUptime(perf.uptime_seconds);
  $("#perfCpuFreq").textContent = perf.cpu_freq_mhz == null ? "CPU freq N/A" : `${formatNumber(perf.cpu_freq_mhz, 0)} MHz`;
}

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => Array.from(document.querySelectorAll(selector));

const liquidGlassFilterCache = new Map();
const supportsBackdropFilterUrl = (() => {
  const test = document.createElement("div");
  test.style.backdropFilter = "url(#liquid-glass-test)";
  return test.style.backdropFilter.includes("url(");
})();

// Adapted from nikdelvin/liquid-glass (MIT): edge displacement + RGB channel split.
function liquidGlassMap({ width, height, radius, depth }) {
  const xStart = Math.ceil((radius / width) * 15);
  const xEnd = Math.floor(100 - (radius / width) * 15);
  const yStart = Math.ceil((radius / height) * 15);
  const yEnd = Math.floor(100 - (radius / height) * 15);
  const svg = `<svg width="${width}" height="${height}" viewBox="0 0 ${width} ${height}" xmlns="http://www.w3.org/2000/svg">
    <style>.mix{mix-blend-mode:screen}</style>
    <defs>
      <linearGradient id="y" x1="0" x2="0" y1="${yStart}%" y2="${yEnd}%"><stop stop-color="#0f0"/><stop offset="1" stop-color="#000"/></linearGradient>
      <linearGradient id="x" x1="${xStart}%" x2="${xEnd}%" y1="0" y2="0"><stop stop-color="#f00"/><stop offset="1" stop-color="#000"/></linearGradient>
    </defs>
    <rect width="${width}" height="${height}" fill="#808080"/>
    <g filter="blur(2px)">
      <rect width="${width}" height="${height}" fill="#000080"/>
      <rect width="${width}" height="${height}" fill="url(#y)" class="mix"/>
      <rect width="${width}" height="${height}" fill="url(#x)" class="mix"/>
      <rect x="${depth}" y="${depth}" width="${width - depth * 2}" height="${height - depth * 2}" rx="${radius}" fill="#808080" filter="blur(${depth}px)"/>
    </g>
  </svg>`;
  return `data:image/svg+xml;utf8,${encodeURIComponent(svg)}`;
}

function liquidGlassFilter(width, height) {
  width = Math.max(1, Math.round(width));
  height = Math.max(1, Math.round(height));
  const key = `${width}x${height}`;
  if (liquidGlassFilterCache.has(key)) return liquidGlassFilterCache.get(key);

  const radius = Math.min(18, height / 2);
  const depth = Math.min(8, Math.max(2, Math.floor(height / 5)));
  const strength = 34;
  const aberration = 2.5;
  const map = liquidGlassMap({ width, height, radius, depth });
  const svg = `<svg width="${width}" height="${height}" viewBox="0 0 ${width} ${height}" xmlns="http://www.w3.org/2000/svg">
    <defs><filter id="displace" color-interpolation-filters="sRGB">
      <feImage width="${width}" height="${height}" href="${map}" result="map"/>
      <feDisplacementMap in="SourceGraphic" in2="map" scale="${strength + aberration * 2}" xChannelSelector="R" yChannelSelector="G"/>
      <feColorMatrix values="1 0 0 0 0  0 0 0 0 0  0 0 0 0 0  0 0 0 1 0" result="r"/>
      <feDisplacementMap in="SourceGraphic" in2="map" scale="${strength + aberration}" xChannelSelector="R" yChannelSelector="G"/>
      <feColorMatrix values="0 0 0 0 0  0 1 0 0 0  0 0 0 0 0  0 0 0 1 0" result="g"/>
      <feDisplacementMap in="SourceGraphic" in2="map" scale="${strength}" xChannelSelector="R" yChannelSelector="G"/>
      <feColorMatrix values="0 0 0 0 0  0 0 0 0 0  0 0 1 0 0  0 0 0 1 0" result="b"/>
      <feBlend in="r" in2="g" mode="screen"/><feBlend in2="b" mode="screen"/>
    </filter></defs>
  </svg>`;
  const value = `blur(1px) url("data:image/svg+xml;utf8,${encodeURIComponent(svg)}#displace") blur(5px) brightness(1.15) saturate(1.55)`;
  if (liquidGlassFilterCache.size >= 24) {
    liquidGlassFilterCache.delete(liquidGlassFilterCache.keys().next().value);
  }
  liquidGlassFilterCache.set(key, value);
  return value;
}

function toast(message, isError = false) {
  const el = $("#toast");
  el.textContent = message;
  el.classList.toggle("error", isError);
  el.classList.add("show");
  window.setTimeout(() => el.classList.remove("show"), 3200);
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = await response.json();
      detail = body.detail || detail;
    } catch {
      // keep response status text
    }
    if (response.status === 401) {
      window.location.href = "/login";
      return null;
    }
    throw new Error(detail);
  }
  return response.json();
}

function setView(view) {
  if (view !== "enroll") {
    stopAutoEnroll();
  }
  const previousView = state.activeView;
  const wasLogView = previousView.startsWith("log-");
  state.activeView = view;
  const isLogView = view.startsWith("log-");
  const movingInsideLog = wasLogView && isLogView;
  clearTimeout(setView._navExitTimer);
  $$(".nav-item[data-view], .nav-subitem[data-view]").forEach((button) => {
    button.classList.toggle("active", button.dataset.view === view);
  });
  $("#logNavBtn").classList.toggle("active", isLogView);
  const groupWasClosed = !$("#logNavGroup").classList.contains("open");
  if (isLogView) {
    $("#logNavBtn").setAttribute("aria-expanded", "true");
    $("#logNavGroup").classList.add("open");
  }
  $$(".view").forEach((section) => {
    section.classList.toggle("active", section.id === view);
  });
  $("#viewTitle").textContent = viewTitles[view] || view;
  updateActiveStreams();
  state.navDirectMove = movingInsideLog;
  requestAnimationFrame(moveNavIndicator);
  // Submenu Log pakai max-height + transform 260ms. Saat group baru dibuka
  // (Dashboard -> Log), getBoundingClientRect mengembalikan posisi
  // mid-transition (subitem masih translateY(-6px)) -> blob terhitung
  // sedikit di atas target. Re-measure setelah transition selesai.
  if (isLogView && groupWasClosed) {
    setTimeout(moveNavIndicator, 280);
  }
}

function setupNavIndicator() {
  const nav = $(".nav");
  if (!nav || $("#navGoo")) return;
  const goo = document.createElement("div");
  goo.id = "navGoo";
  goo.className = "nav-goo";
  goo.innerHTML = '<div class="nav-blob" id="navBlobLead"></div>';
  nav.appendChild(goo);
  document.documentElement.classList.toggle("liquid-glass-supported", supportsBackdropFilterUrl);
}

function setBlob(blob, box) {
  blob.style.transform = `translate(${box.x}px, ${box.y}px)`;
  blob.style.width = `${box.w}px`;
  blob.style.height = `${box.h}px`;
  if (supportsBackdropFilterUrl) {
    const filter = liquidGlassFilter(box.w, box.h);
    blob.style.backdropFilter = filter;
    blob.style.webkitBackdropFilter = filter;
  }
}

// Gooey stretch: 1 blob memanjang menutupi posisi lama+baru, lalu mengkerut ke target.
function moveNavIndicator() {
  const nav = $(".nav");
  const blob = $("#navBlobLead");
  if (!nav || !blob) return;
  let el = $$(".nav-item[data-view], .nav-subitem[data-view]").find(
    (b) => b.dataset.view === state.activeView
  );
  // activeView == "log" -> blob langsung ke tombol Log parent.
  if (!el && state.activeView === "log") el = $("#logNavBtn");
  // Submenu Log tertutup -> subitem aktif tersembunyi, jatuhkan ke tombol Log.
  if (el && el.offsetParent === null) el = $("#logNavBtn");
  if (!el) return;
  const navRect = nav.getBoundingClientRect();
  const rect = el.getBoundingClientRect();
  const target = {
    x: rect.left - navRect.left,
    y: rect.top - navRect.top,
    w: rect.width,
    h: rect.height,
  };

  // Belum tampil -> tempatkan langsung tanpa animasi.
  if (!blob.classList.contains("show")) {
    blob.classList.add("show");
    setBlob(blob, target);
    return;
  }
  // Titik awal = posisi blob saat ini (live), tahan reflow submenu Log collapse.
  const br = blob.getBoundingClientRect();
  const prev = {
    x: br.left - navRect.left,
    y: br.top - navRect.top,
    w: br.width,
    h: br.height,
  };
  if (state.navDirectMove) {
    state.navDirectMove = false;
    clearTimeout(moveNavIndicator._t);
    setBlob(blob, target);
    return;
  }
  // Fase 1: regang menutupi posisi sekarang + target (tinggi = rentang gabungan).
  const top = Math.min(prev.y, target.y);
  const bottom = Math.max(prev.y + prev.h, target.y + target.h);
  setBlob(blob, { x: target.x, y: top, w: target.w, h: bottom - top });
  // Fase 2: mengkerut ke target -> ujung trailing menyusul -> efek liquid stretch.
  clearTimeout(moveNavIndicator._t);
  moveNavIndicator._t = setTimeout(() => setBlob(blob, target), 180);
}

function updateActiveStreams() {
  const enrollFeed = $("#enrollFeed");
  if (state.activeView === "enroll") {
    enrollFeed.src = `/video/preview?t=${Date.now()}`;
  } else {
    enrollFeed.removeAttribute("src");
  }
}

function renderStatus(status) {
  state.settings = status.settings;
  const dbStatus = $("#dbStatus");
  if (status.database.ok) {
    dbStatus.className = "sidebar-status ok";
    dbStatus.textContent = "MySQL connected";
  } else {
    dbStatus.className = "sidebar-status error";
    dbStatus.textContent = `MySQL error: ${status.database.error}`;
  }

  const summary = status.database.ok ? status.database.data : {};
  $("#metricStudents").textContent = summary.total_students ?? "-";
  $("#metricParents").textContent = summary.total_parents ?? "-";
  $("#metricEmbeddings").textContent = status.embeddings.rows;
  $("#metricQr").textContent = status.qr_count;

  $("#summaryCamera").textContent = `Camera ${status.settings.cam_index}`;
  $("#summaryResolution").textContent = `${status.settings.width}x${status.settings.height}`;
  $("#summaryThreshold").textContent = Number(status.settings.threshold).toFixed(2);
  $("#summaryDetSize").textContent = status.settings.det_size;
  $("#summaryEmbeddingFile").textContent = status.embeddings.exists ? "Available" : "Missing";
  $("#summaryEmbeddingShape").textContent = status.embeddings.exists
    ? `${status.embeddings.rows} x ${status.embeddings.dim}`
    : "-";
  $("#summaryDatabase").textContent = status.database.ok ? status.database.name : "Unavailable";
  $("#summaryMirror").textContent = status.settings.mirror_camera ? "ON" : "OFF";

  fillSettings(status.settings);
}

function fillSettings(settings) {
  const form = $("#settingsForm");
  Object.entries(settings).forEach(([key, value]) => {
    const field = form.elements[key];
    if (!field) return;
    if (field.type === "checkbox") {
      field.checked = Boolean(value);
    } else {
      field.value = value;
    }
  });
  $("#enrollSamples").value = settings.samples;
  $("#enrollAutoCapture").checked = Boolean(settings.auto_capture_enroll);
  $("#enrollAutoInterval").value = settings.auto_capture_interval_ms ?? 1200;
}

async function loadStatus() {
  const status = await api("/api/status");
  if (status) renderStatus(status);
}

async function loadDatabase() {
  try {
    const [students, parents] = await Promise.all([
      api("/api/students"),
      api("/api/parents"),
    ]);
    state.students = students || [];
    state.parents = parents || [];
    renderStudents();
    renderParents();
  } catch (error) {
    toast(error.message, true);
  }
}

async function loadLogs() {
  try {
    const rows = await api("/api/logs?limit=500");
    state.logs = rows || [];
    renderLogs();
  } catch (error) {
    toast(error.message, true);
  }
}

async function refreshDashboard() {
  await Promise.all([loadStatus(), loadDatabase(), loadLogs()]);
  try {
    const performance = await api(`/api/performance?t=${Date.now()}`);
    renderPerformance(performance);
  } catch {
    // Performance fetch is best-effort; cards stay at "-" if unavailable.
  }
  toast("Data dashboard diperbarui.");
}

function startStatusPolling() {
  window.setInterval(async () => {
    if (state.activeView !== "dashboard" || state.statusPolling) return;
    state.statusPolling = true;
    try {
      const performance = await api(`/api/performance?t=${Date.now()}`);
      renderPerformance(performance);
    } catch (error) {
      console.warn("Performance polling failed:", error);
    } finally {
      state.statusPolling = false;
    }
  }, 1000);
}

function getDatabasePagination(type) {
  if (!state.databasePagination[type]) {
    state.databasePagination[type] = { page: 1, pageSize: 10 };
  }
  return state.databasePagination[type];
}

function getPaginatedDatabaseRows(type, rows) {
  const pagination = getDatabasePagination(type);
  const totalPages = Math.max(1, Math.ceil(rows.length / pagination.pageSize));
  pagination.page = Math.min(Math.max(1, pagination.page), totalPages);
  const start = (pagination.page - 1) * pagination.pageSize;
  return {
    pagination,
    totalPages,
    pageRows: rows.slice(start, start + pagination.pageSize),
  };
}

function renderStudents(rows = state.students) {
  const { pagination, totalPages, pageRows } = getPaginatedDatabaseRows("students", rows);
  $("#studentsCount").textContent = `${rows.length} rows`;
  $("#studentsBody").innerHTML = pageRows.length ? pageRows.map((row) => `
    <tr data-nis="${escapeHtml(row.nis)}">
      <td class="mono">${escapeHtml(row.nis)}</td>
      <td><input name="nama" type="text" value="${escapeHtml(row.nama)}"></td>
      <td><input name="kelas" type="text" value="${escapeHtml(row.kelas)}"></td>
      <td>
        <div class="action-row">
          <button class="button compact" data-action="save-student" type="button">Save</button>
          <button class="button compact secondary" data-action="show-qr" type="button">Show QR</button>
          <button class="button compact danger" data-action="delete-student" type="button">Delete</button>
        </div>
      </td>
    </tr>
  `).join("") : `
    <tr>
      <td colspan="4" class="empty-cell">Belum ada data siswa.</td>
    </tr>
  `;
  renderDatabasePagination("students", "#studentsBody", pagination, rows.length, totalPages);
}

function renderParents(rows = state.parents) {
  const { pagination, totalPages, pageRows } = getPaginatedDatabaseRows("parents", rows);
  $("#parentsCount").textContent = `${rows.length} rows`;
  $("#parentsBody").innerHTML = pageRows.length ? pageRows.map((row) => `
    <tr data-parent-id="${row.parent_id}">
      <td><input name="nis" type="text" value="${escapeHtml(row.nis)}"></td>
      <td><input name="nama_ortu" type="text" value="${escapeHtml(row.nama_ortu)}"></td>
      <td>${escapeHtml(row.nama_anak)}</td>
      <td>${escapeHtml(row.kelas)}</td>
      <td class="mono">${row.embedding_index}</td>
      <td>
        <div class="action-row">
          <button class="button compact" data-action="save-parent" type="button">Save</button>
          <button class="button compact danger" data-action="delete-parent" type="button">Delete</button>
        </div>
      </td>
    </tr>
  `).join("") : `
    <tr>
      <td colspan="6" class="empty-cell">Belum ada parent yang enroll.</td>
    </tr>
  `;
  renderDatabasePagination("parents", "#parentsBody", pagination, rows.length, totalPages);
}

function renderDatabasePagination(type, bodySelector, pagination, totalRows, totalPages) {
  const htmlTable = $(bodySelector).closest("table");
  let controls = htmlTable.nextElementSibling;
  if (!controls || !controls.classList.contains("database-pagination")) {
    controls = document.createElement("div");
    controls.className = "log-pagination database-pagination";
    htmlTable.insertAdjacentElement("afterend", controls);
  }

  const start = totalRows ? ((pagination.page - 1) * pagination.pageSize) + 1 : 0;
  const end = Math.min(pagination.page * pagination.pageSize, totalRows);
  controls.innerHTML = `
    <label>
      Baris
      <select class="database-page-size" data-table="${type}">
        ${[10, 50, 100].map((size) => `
          <option value="${size}" ${size === pagination.pageSize ? "selected" : ""}>${size}</option>
        `).join("")}
      </select>
    </label>
    <span>${start}-${end} dari ${totalRows}</span>
    <div class="log-page-actions">
      <button class="button compact secondary database-page-prev" data-table="${type}" type="button" ${pagination.page <= 1 ? "disabled" : ""}>Sebelumnya</button>
      <strong>Halaman ${pagination.page} / ${totalPages}</strong>
      <button class="button compact secondary database-page-next" data-table="${type}" type="button" ${pagination.page >= totalPages ? "disabled" : ""}>Berikutnya</button>
    </div>
  `;
}

function changeDatabasePage(type, delta) {
  const pagination = getDatabasePagination(type);
  pagination.page += delta;
  if (type === "students") renderStudents();
  if (type === "parents") renderParents();
}

function changeDatabasePageSize(type, pageSize) {
  const pagination = getDatabasePagination(type);
  pagination.pageSize = pageSize;
  pagination.page = 1;
  if (type === "students") renderStudents();
  if (type === "parents") renderParents();
}

function logMatchesFilters(row) {
  const date = state.logFilters.date;
  const student = state.logFilters.student.trim().toLowerCase();
  const className = state.logFilters.class.trim().toLowerCase();
  const rowDate = String(row.waktu_absen || "").slice(0, 10);
  const rowStudent = String(row.nama_siswa || "").toLowerCase();
  const rowClass = String(row.kelas || "").toLowerCase();

  if (date && rowDate !== date) return false;
  if (student && !rowStudent.includes(student)) return false;
  if (className && !rowClass.includes(className)) return false;
  return true;
}

function syncLogFilterInputs() {
  $$(".log-filter-input").forEach((input) => {
    input.value = state.logFilters[input.dataset.filter] || "";
  });
}

function setLogFilter(key, value) {
  state.logFilters[key] = value;
  resetLogPages();
  syncLogFilterInputs();
  renderLogs();
}

function resetLogFilters() {
  state.logFilters = { date: "", student: "", class: "" };
  resetLogPages();
  syncLogFilterInputs();
  renderLogs();
}

function getLogPagination(type) {
  if (!state.logPagination[type]) {
    state.logPagination[type] = { page: 1, pageSize: 10 };
  }
  return state.logPagination[type];
}

function resetLogPages() {
  Object.values(state.logPagination).forEach((pagination) => {
    pagination.page = 1;
  });
}

function renderLogs(rows = state.logs) {
  logTables.forEach((table) => {
    const typeRows = rows.filter((row) => row.jenis_absen === table.type);
    const filteredRows = typeRows.filter(logMatchesFilters);
    const pagination = getLogPagination(table.type);
    const totalPages = Math.max(1, Math.ceil(filteredRows.length / pagination.pageSize));
    pagination.page = Math.min(Math.max(1, pagination.page), totalPages);
    const start = (pagination.page - 1) * pagination.pageSize;
    const pageRows = filteredRows.slice(start, start + pagination.pageSize);
    renderLogTable(table.body, table.count, pageRows, table.empty, filteredRows.length, typeRows.length);
    renderLogPagination(table, pagination, filteredRows.length, totalPages);
  });
}

function displayLogStatus(status) {
  return status === "DIBATALKAN" ? "TIDAK VALID" : "VALID";
}

function renderLogTable(bodySelector, countSelector, rows, emptyMessage, filteredTotal, totalRows) {
  $(countSelector).textContent = filteredTotal === totalRows
    ? `${totalRows} rows`
    : `${filteredTotal} dari ${totalRows} rows`;
  $(bodySelector).innerHTML = rows.length ? rows.map((row) => `
    <tr data-log-id="${row.id}" class="${row.status === "DIBATALKAN" ? "cancelled-row" : ""}">
      <td class="mono">${escapeHtml(row.nis)}</td>
      <td>${escapeHtml(row.nama_siswa || "-")}</td>
      <td>${escapeHtml(row.kelas)}</td>
      <td>${escapeHtml(row.waktu_absen)}</td>
      <td>${row.bukti_foto
        ? `<button class="button compact secondary" data-action="view-photo" data-photo="${escapeHtml(row.bukti_foto)}" type="button">Lihat</button>`
        : `<span class="muted">-</span>`}
      </td>
      <td>
        <span class="status-pill ${row.status === "DIBATALKAN" ? "cancelled" : "active"}">
          ${escapeHtml(displayLogStatus(row.status))}
        </span>
        ${row.cancel_reason ? `<div class="muted small-text">${escapeHtml(row.cancel_reason)}</div>` : ""}
      </td>
      <td>
        ${row.status === "DIBATALKAN"
          ? `<span class="muted">-</span>`
          : `<button class="button compact danger" data-action="cancel-log" type="button">Batalkan</button>`}
      </td>
    </tr>
  `).join("") : `
    <tr>
      <td colspan="7" class="empty-cell">${escapeHtml(emptyMessage)}</td>
    </tr>
  `;
}

function renderLogPagination(table, pagination, filteredTotal, totalPages) {
  const htmlTable = $(table.body).closest("table");
  let controls = htmlTable.nextElementSibling;
  if (!controls || !controls.classList.contains("log-pagination")) {
    controls = document.createElement("div");
    controls.className = "log-pagination";
    htmlTable.insertAdjacentElement("afterend", controls);
  }

  const start = filteredTotal ? ((pagination.page - 1) * pagination.pageSize) + 1 : 0;
  const end = Math.min(pagination.page * pagination.pageSize, filteredTotal);
  controls.innerHTML = `
    <label>
      Baris
      <select class="log-page-size" data-log-type="${table.type}">
        ${[10, 50, 100].map((size) => `
          <option value="${size}" ${size === pagination.pageSize ? "selected" : ""}>${size}</option>
        `).join("")}
      </select>
    </label>
    <span>${start}-${end} dari ${filteredTotal}</span>
    <div class="log-page-actions">
      <button class="button compact secondary log-page-prev" data-log-type="${table.type}" type="button" ${pagination.page <= 1 ? "disabled" : ""}>Sebelumnya</button>
      <strong>Halaman ${pagination.page} / ${totalPages}</strong>
      <button class="button compact secondary log-page-next" data-log-type="${table.type}" type="button" ${pagination.page >= totalPages ? "disabled" : ""}>Berikutnya</button>
    </div>
  `;
}

function changeLogPage(type, delta) {
  const pagination = getLogPagination(type);
  pagination.page += delta;
  renderLogs();
}

function changeLogPageSize(type, pageSize) {
  const pagination = getLogPagination(type);
  pagination.pageSize = pageSize;
  pagination.page = 1;
  renderLogs();
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#039;",
  }[char]));
}

async function startEnroll() {
  stopAutoEnroll();
  const payload = {
    nis: $("#enrollNis").value.trim(),
    parent_name: $("#enrollParent").value.trim(),
    samples: Number($("#enrollSamples").value),
  };
  if (!payload.nis || !payload.parent_name) {
    toast("NIS dan nama orang tua wajib diisi.", true);
    return;
  }
  try {
    const session = await api("/api/enroll/start", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    state.enrollSession = session;
    $("#captureEnrollBtn").disabled = false;
    updateEnrollProgress(0, session.target);
    $("#enrollStatus").textContent = `Ready: ${session.student.nama} (${session.student.kelas})`;
    if ($("#enrollAutoCapture").checked) {
      startAutoEnroll();
    }
  } catch (error) {
    toast(error.message, true);
  }
}

function startAutoEnroll() {
  stopAutoEnroll();
  const interval = Math.max(300, Number($("#enrollAutoInterval").value) || 1200);
  $("#enrollStatus").textContent = "Auto capture aktif. Arahkan wajah ke beberapa sudut.";
  state.enrollAutoTimer = window.setInterval(() => {
    captureEnroll({ automatic: true });
  }, interval);
  captureEnroll({ automatic: true });
}

function stopAutoEnroll() {
  if (!state.enrollAutoTimer) return;
  window.clearInterval(state.enrollAutoTimer);
  state.enrollAutoTimer = null;
}

async function captureEnroll(options = {}) {
  if (!state.enrollSession || state.enrollCapturing) return;
  state.enrollCapturing = true;

  const image = $("#enrollFeed");
  const canvas = $("#captureCanvas");
  canvas.width = image.naturalWidth || image.clientWidth;
  canvas.height = image.naturalHeight || image.clientHeight;
  const ctx = canvas.getContext("2d");
  ctx.drawImage(image, 0, 0, canvas.width, canvas.height);

  const imageData = canvas.toDataURL("image/jpeg", 0.9);
  try {
    const result = await api("/api/enroll/capture", {
      method: "POST",
      body: JSON.stringify({
        session_id: state.enrollSession.session_id,
        image_data: imageData,
      }),
    });
    updateEnrollProgress(result.captured, result.target);
    if (result.complete) {
      stopAutoEnroll();
      $("#captureEnrollBtn").disabled = true;
      $("#enrollStatus").textContent = `Complete. Embedding index: ${result.embedding_index}`;
      state.enrollSession = null;
      await Promise.all([loadStatus(), loadDatabase()]);
      toast("Enrollment complete.");
    } else {
      $("#enrollStatus").textContent = `Captured ${result.captured}/${result.target}`;
    }
  } catch (error) {
    if (options.automatic && error.message.includes("Wajah belum terdeteksi jelas")) {
      $("#enrollStatus").textContent = "Menunggu wajah terdeteksi jelas...";
    } else {
      if (options.automatic) {
        stopAutoEnroll();
      }
      toast(error.message, true);
    }
  } finally {
    state.enrollCapturing = false;
  }
}

function updateEnrollProgress(captured, target) {
  const pct = target > 0 ? Math.round((captured / target) * 100) : 0;
  $("#enrollProgressBar").style.width = `${pct}%`;
}

async function submitStudent(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const payload = Object.fromEntries(new FormData(form).entries());
  try {
    await api("/api/students", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    form.reset();
    await Promise.all([loadStatus(), loadDatabase()]);
    toast("Student added.");
  } catch (error) {
    toast(error.message, true);
  }
}

async function saveStudent(row) {
  const nis = row.dataset.nis;
  const payload = {
    nama: row.querySelector("input[name='nama']").value.trim(),
    kelas: row.querySelector("input[name='kelas']").value.trim(),
  };
  await api(`/api/students/${encodeURIComponent(nis)}`, {
    method: "PUT",
    body: JSON.stringify(payload),
  });
  await Promise.all([loadStatus(), loadDatabase()]);
  toast("Student updated.");
}

async function deleteStudent(row) {
  const nis = row.dataset.nis;
  if (!window.confirm(`Hapus siswa NIS ${nis}?`)) return;
  await api(`/api/students/${encodeURIComponent(nis)}`, { method: "DELETE" });
  await Promise.all([loadStatus(), loadDatabase()]);
  toast("Student deleted.");
}

function showStudentQr(row) {
  const nis = row.dataset.nis;
  // Bust cache supaya kalau QR baru di-generate, image segar yang tampil.
  // Endpoint /api/qr/image/{nis} auto-generate kalau file belum ada.
  const url = `/api/qr/image/${encodeURIComponent(nis)}?t=${Date.now()}`;
  openPhotoModal(url);
}

async function saveParent(row) {
  const parentId = row.dataset.parentId;
  const payload = {
    nis: row.querySelector("input[name='nis']").value.trim(),
    nama_ortu: row.querySelector("input[name='nama_ortu']").value.trim(),
  };
  await api(`/api/parents/${encodeURIComponent(parentId)}`, {
    method: "PUT",
    body: JSON.stringify(payload),
  });
  await Promise.all([loadStatus(), loadDatabase()]);
  toast("Parent updated.");
}

async function deleteParent(row) {
  const parentId = row.dataset.parentId;
  if (!window.confirm("Delete parent ini dari MySQL? Embedding lama tidak dipakai lagi oleh data parent.")) return;
  await api(`/api/parents/${encodeURIComponent(parentId)}`, { method: "DELETE" });
  await Promise.all([loadStatus(), loadDatabase()]);
  toast("Parent deleted.");
}

async function saveSettings(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const payload = {};
  ["cam_index", "width", "height", "samples", "auto_capture_interval_ms"].forEach((key) => {
    payload[key] = Number(form.elements[key].value);
  });
  payload.auto_capture_enroll = form.elements.auto_capture_enroll.checked;
  payload.voice_announcement_enabled = form.elements.voice_announcement_enabled.checked;
  payload.mirror_camera = form.elements.mirror_camera.checked;

  try {
    const settings = await api("/api/settings", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    state.settings = settings;
    updateActiveStreams();
    await loadStatus();
    toast("Settings saved.");
  } catch (error) {
    toast(error.message, true);
  }
}

async function logout() {
  await api("/api/logout", { method: "POST" });
  window.location.href = "/login";
}

async function cancelLog(row) {
  const logId = row.dataset.logId;
  const reason = window.prompt("Alasan pembatalan log:");
  if (reason === null) return;
  const trimmed = reason.trim();
  if (trimmed.length < 3) {
    toast("Alasan pembatalan minimal 3 karakter.", true);
    return;
  }
  await api(`/api/logs/${encodeURIComponent(logId)}/cancel`, {
    method: "POST",
    body: JSON.stringify({ reason: trimmed }),
  });
  await loadLogs();
  toast("Log dibatalkan.");
}

function openPhotoModal(src) {
  $("#photoModalImage").src = src;
  $("#photoModal").classList.add("show");
  $("#photoModal").setAttribute("aria-hidden", "false");
}

function closePhotoModal() {
  $("#photoModal").classList.remove("show");
  $("#photoModal").setAttribute("aria-hidden", "true");
  $("#photoModalImage").removeAttribute("src");
}

const EXPORT_LABELS = {
  KEHADIRAN_QR: "QR Kehadiran Siswa",
  PENJEMPUTAN_QR: "QR Penjemputan Non-orang Tua",
  PENJEMPUTAN_FACE: "Muka Penjemputan Orang Tua",
  UNKNOWN_FACE: "Muka Tidak Dikenal",
};
let exportJenis = null;

function openExportModal(jenis) {
  exportJenis = jenis;
  $("#exportSub").textContent = `Log: ${EXPORT_LABELS[jenis] || jenis}`;
  $("#exportExcel").checked = true;
  $("#exportImages").checked = false;
  $("#exportModal").classList.add("show");
  $("#exportModal").setAttribute("aria-hidden", "false");
}

function closeExportModal() {
  $("#exportModal").classList.remove("show");
  $("#exportModal").setAttribute("aria-hidden", "true");
}

let exportBusy = false;

async function runExport() {
  if (!exportJenis || exportBusy) return;
  const excel = $("#exportExcel").checked;
  const images = $("#exportImages").checked;
  if (!excel && !images) {
    toast("Pilih minimal satu: Excel atau Gambar.", true);
    return;
  }
  exportBusy = true;
  const params = new URLSearchParams({
    jenis: exportJenis,
    excel: String(excel),
    images: String(images),
    date: state.logFilters.date || "",
    student: state.logFilters.student || "",
    kelas: state.logFilters.class || "",
  });
  const btn = $("#exportConfirmBtn");
  btn.disabled = true;
  btn.textContent = "Menyiapkan…";
  try {
    const res = await fetch(`/api/logs/export?${params.toString()}`);
    if (!res.ok) {
      let detail = res.statusText;
      try {
        detail = (await res.json()).detail || detail;
      } catch {
        // keep status text
      }
      if (res.status === 401) {
        window.location.href = "/login";
        return;
      }
      throw new Error(detail);
    }
    const blob = await res.blob();
    const cd = res.headers.get("Content-Disposition") || "";
    const match = cd.match(/filename="?([^"]+)"?/);
    const filename = match ? match[1] : `${exportJenis.toLowerCase()}.${images ? "zip" : "xlsx"}`;
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    // Jangan revoke/ remove sinkron — untuk blob besar (zip foto) revoke yang
    // langsung balapan dgn start download → Chromium restart → file ke-download
    // 2×. Tunda biar download benar2 jalan dulu.
    window.setTimeout(() => {
      a.remove();
      URL.revokeObjectURL(url);
    }, 60000);
    closeExportModal();
    toast("Export selesai: " + filename);
  } catch (error) {
    toast(error.message, true);
  } finally {
    exportBusy = false;
    btn.disabled = false;
    btn.textContent = "Export";
  }
}

async function handleTableAction(event) {
  const button = event.target.closest("[data-action]");
  if (!button) return;

  const row = button.closest("tr");
  try {
    if (button.dataset.action === "view-photo") {
      openPhotoModal(button.dataset.photo);
      return;
    }
    if (button.dataset.action === "save-student") await saveStudent(row);
    if (button.dataset.action === "delete-student") await deleteStudent(row);
    if (button.dataset.action === "show-qr") showStudentQr(row);
    if (button.dataset.action === "save-parent") await saveParent(row);
    if (button.dataset.action === "delete-parent") await deleteParent(row);
    if (button.dataset.action === "cancel-log") await cancelLog(row);
  } catch (error) {
    toast(error.message, true);
  }
}

function bindEvents() {
  $$(".nav-item[data-view], .nav-subitem[data-view]").forEach((button) => {
    button.addEventListener("click", () => setView(button.dataset.view));
  });
  $("#logNavBtn").addEventListener("click", () => {
    const group = $("#logNavGroup");
    const isOnLogSub = state.activeView.startsWith("log-");
    if (isOnLogSub) {
      // Sudah di log-X subview -> collapse submenu + pindah highlight ke Log
      // parent. Konten panel TETAP terlihat sesuai mode log terakhir supaya
      // tidak kosong.
      const currentLogView = state.activeView;
      state.activeView = "log";
      group.classList.remove("open");
      $("#logNavBtn").setAttribute("aria-expanded", "false");
      $$(".nav-item[data-view], .nav-subitem[data-view]").forEach((b) =>
        b.classList.remove("active")
      );
      $("#logNavBtn").classList.add("active");
      $("#viewTitle").textContent = viewTitles[currentLogView] || "QR Kehadiran Siswa";
      updateActiveStreams();
      requestAnimationFrame(moveNavIndicator);
      return;
    }
    // Belum di log subview -> masuk: setView buka submenu & pilih QR Kehadiran.
    setView("log-attendance");
  });
  $("#refreshBtn").addEventListener("click", () => {
    refreshDashboard().catch((error) => toast(error.message, true));
  });
  $("#startEnrollBtn").addEventListener("click", startEnroll);
  $("#captureEnrollBtn").addEventListener("click", captureEnroll);
  $("#studentForm").addEventListener("submit", submitStudent);
  $("#settingsForm").addEventListener("submit", saveSettings);
  $("#logoutBtn").addEventListener("click", logout);
  $("#studentsBody").addEventListener("click", handleTableAction);
  $("#parentsBody").addEventListener("click", handleTableAction);
  logTables.forEach((table) => {
    $(table.body).addEventListener("click", handleTableAction);
  });
  $$(".log-filter-input").forEach((input) => {
    input.addEventListener("input", () => {
      setLogFilter(input.dataset.filter, input.value);
    });
  });
  $$(".log-filter-reset").forEach((button) => {
    button.addEventListener("click", resetLogFilters);
  });
  document.addEventListener("click", (event) => {
    const previous = event.target.closest(".log-page-prev");
    if (previous) {
      changeLogPage(previous.dataset.logType, -1);
      return;
    }
    const next = event.target.closest(".log-page-next");
    if (next) {
      changeLogPage(next.dataset.logType, 1);
      return;
    }
    const databasePrevious = event.target.closest(".database-page-prev");
    if (databasePrevious) {
      changeDatabasePage(databasePrevious.dataset.table, -1);
      return;
    }
    const databaseNext = event.target.closest(".database-page-next");
    if (databaseNext) changeDatabasePage(databaseNext.dataset.table, 1);
  });
  document.addEventListener("change", (event) => {
    const select = event.target.closest(".log-page-size");
    if (select) {
      changeLogPageSize(select.dataset.logType, Number(select.value));
      return;
    }
    const databaseSelect = event.target.closest(".database-page-size");
    if (databaseSelect) changeDatabasePageSize(databaseSelect.dataset.table, Number(databaseSelect.value));
  });
  $("#photoCloseBtn").addEventListener("click", closePhotoModal);
  $("#photoModal").addEventListener("click", (event) => {
    if (event.target.id === "photoModal") closePhotoModal();
  });
  $$(".log-export-btn").forEach((button) => {
    button.addEventListener("click", () => openExportModal(button.dataset.jenis));
  });
  $("#exportCancelBtn").addEventListener("click", closeExportModal);
  $("#exportConfirmBtn").addEventListener("click", runExport);
  $("#exportModal").addEventListener("click", (event) => {
    if (event.target.id === "exportModal") closeExportModal();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      closePhotoModal();
      closeExportModal();
    }
  });
}

document.addEventListener("DOMContentLoaded", async () => {
  bindEvents();
  setupNavIndicator();
  startStatusPolling();
  requestAnimationFrame(moveNavIndicator);
  window.addEventListener("resize", moveNavIndicator);
  try {
    await loadStatus();
    await loadDatabase();
    await loadLogs();
  } catch (error) {
    toast(error.message, true);
  }
});
