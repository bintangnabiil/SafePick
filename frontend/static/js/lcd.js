const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => Array.from(document.querySelectorAll(selector));

// ── Kiosk content protection ─────────────────────────────────────────
// Block: right-click menu, drag, text selection, devtools hotkey, copy hotkey.
// Walaupun kiosk Chromium sudah menyembunyikan UI, ini lapisan tambahan
// supaya guru/siswa/orang iseng yang colok USB keyboard tidak bisa
// keluar fokus, buka inspector, atau copy data.
(function installKioskGuards() {
  const block = (e) => {
    e.preventDefault();
    e.stopPropagation();
    return false;
  };
  // Right-click / long-press context menu
  document.addEventListener("contextmenu", block, { capture: true });
  // Drag start (foto, link, dll.)
  document.addEventListener("dragstart", block, { capture: true });
  // Text selection start
  document.addEventListener("selectstart", block, { capture: true });
  // Hotkey blocker: F12, Ctrl+Shift+I/J/C, Ctrl+U (view source), Ctrl+P (print),
  // Ctrl+S (save), Ctrl+C/X/A (copy/cut/select all), Ctrl+= / Ctrl+- (zoom),
  // Ctrl+R / F5 (refresh).
  document.addEventListener("keydown", (e) => {
    const k = e.key;
    const ctrl = e.ctrlKey || e.metaKey;
    const shift = e.shiftKey;
    const blockedAlone = ["F12", "F5"];
    const blockedCtrl = ["u", "U", "p", "P", "s", "S", "c", "C", "x", "X",
                         "a", "A", "r", "R", "=", "-", "+", "0"];
    const blockedCtrlShift = ["I", "J", "C", "K"];
    if (blockedAlone.includes(k)) return block(e);
    if (ctrl && shift && blockedCtrlShift.includes(k)) return block(e);
    if (ctrl && blockedCtrl.includes(k)) return block(e);
  }, { capture: true });
  // Copy / cut / paste events
  ["copy", "cut", "paste"].forEach((evt) =>
    document.addEventListener(evt, block, { capture: true })
  );
})();

let currentMode = "home";
const voiceEventStorageKey = "safepickLastVoiceEventId";
let lastVoiceEventId = Number(window.localStorage.getItem(voiceEventStorageKey) || 0);
let pendingVoiceEvent = null;
let voicePolling = false;
let voicePlaybackChain = Promise.resolve();

const modes = {
  attendance_qr: {
    title: "QR Kehadiran Siswa",
    stream: "/video/qr?mode=attendance",
  },
  pickup_qr: {
    title: "QR Penjemputan Non-orang Tua",
    stream: "/video/qr?mode=pickup",
  },
  pickup_face: {
    title: "Muka Penjemputan Orang Tua",
    stream: "/video/recognize",
  },
};

async function api(path) {
  const response = await fetch(path);
  if (!response.ok) throw new Error(response.statusText);
  return response.json();
}

function setHome() {
  currentMode = "home";
  // Paksa browser tutup MJPEG connection: kosongkan src + load empty image.
  // `removeAttribute("src")` saja tidak selalu langsung abort koneksi MJPEG
  // di Chromium, dan kalau lambat close, lock recognize_frames di backend
  // belum sempat release saat user kembali ke face mode.
  const feed = $("#scanFeed");
  feed.removeAttribute("src");
  feed.src = "about:blank";
  $(".lcd-shell").dataset.view = "home";
  document.body.classList.add("view-home");
  // Force-stop kamera di backend supaya LED kamera mati saat di menu Home.
  // Chromium kadang nahan TCP MJPEG socket beberapa detik setelah src
  // diganti — endpoint ini memaksa server release kamera secara eksplisit.
  fetch("/api/camera/release", { method: "POST" }).catch(() => {});
}

function setScan(mode) {
  currentMode = mode;
  const selected = modes[mode] || modes.attendance_qr;
  const feed = $("#scanFeed");
  const separator = selected.stream.includes("?") ? "&" : "?";
  const nextStream = `${selected.stream}${separator}t=${Date.now()}`;

  // Reset src dulu (force tutup koneksi MJPEG sebelumnya kalau ada).
  feed.removeAttribute("src");
  feed.src = "about:blank";
  $("#scanTitle").textContent = selected.title;
  $(".lcd-shell").dataset.view = "scan";
  document.body.classList.remove("view-home");

  // Delay 800ms supaya backend recognize_lock release dulu sebelum kita
  // open stream baru. Kombinasi dengan timeout 8s di backend acquire(),
  // race "stream already active" jadi sangat tidak mungkin.
  window.setTimeout(() => {
    feed.src = nextStream;
  }, 800);
}

async function loadStatus() {
  try {
    const status = await api("/api/status");
    $("#dbStatus").textContent = status.database.ok ? "Online" : "MySQL";
    $("#dbStatus").className = `status-badge ${status.database.ok ? "ok" : "error"}`;
  } catch {
    $("#dbStatus").textContent = "Offline";
    $("#dbStatus").className = "status-badge error";
  }
}

async function playVoiceEvent(event) {
  if (!event || !event.audio_url) return;
  pendingVoiceEvent = event;
  const audio = new Audio(event.audio_url);
  try {
    await audio.play();
    pendingVoiceEvent = null;
    await new Promise((resolve) => {
      audio.addEventListener("ended", resolve, { once: true });
      audio.addEventListener("error", resolve, { once: true });
    });
    await new Promise((resolve) => window.setTimeout(resolve, 900));
  } catch {
    // Browser may require a tap before audio playback is allowed.
  }
}

function enqueueVoiceEvent(event) {
  voicePlaybackChain = voicePlaybackChain
    .then(() => playVoiceEvent(event))
    .catch(() => {});
  return voicePlaybackChain;
}

async function pollVoiceEvents() {
  if (voicePolling) return;
  voicePolling = true;
  try {
    const data = await api(`/api/voice/events?after=${lastVoiceEventId}`);
    // Counter voice_event_id di server reset ke 0 setiap restart proses
    // uvicorn. Kalau localStorage punya angka yang lebih besar dari counter
    // server, polling akan selalu kembali kosong sampai counter menyusul.
    // Deteksi situasi itu dan reset state lokal supaya event baru kembali
    // sampai ke speaker.
    const serverMaxId = Number(data.server_max_id);
    if (Number.isFinite(serverMaxId) && serverMaxId < lastVoiceEventId) {
      lastVoiceEventId = 0;
      window.localStorage.setItem(voiceEventStorageKey, "0");
      return;
    }
    const events = data.events || [];
    for (const event of events) {
      lastVoiceEventId = Math.max(lastVoiceEventId, Number(event.id));
      window.localStorage.setItem(voiceEventStorageKey, String(lastVoiceEventId));
      enqueueVoiceEvent(event);
    }
  } catch {
    // Voice polling must not interrupt the scan UI.
  } finally {
    voicePolling = false;
  }
}

function unlockSound() {
  if (pendingVoiceEvent) {
    const event = pendingVoiceEvent;
    pendingVoiceEvent = null;
    enqueueVoiceEvent(event);
  }
}

// ─── Kiosk exit button & admin URL popup (only present on display.html) ───

async function exitKiosk() {
  if (!window.confirm("Tutup aplikasi SafePick?")) return;
  try {
    // Endpoint cuma jalan kalau dipanggil dari localhost (Pi sendiri).
    // Dari laptop akan 403 — itu by design, supaya orang luar gak bisa
    // matikan tampilan gate dari jauh.
    await fetch("/api/system/exit-kiosk", { method: "POST" });
  } catch (_) {
    // network error -> diabaikan, kita masih coba window.close()
  }
  // Untuk window yang dibuka via launcher script (kiosk), backend yang
  // bakal kill Chromium. window.close() ini cuma fallback untuk window
  // yang dibuka via window.open() (tidak applicable di kiosk).
  window.close();
}

async function showAdminUrlPopup() {
  const popup = document.getElementById("adminUrlPopup");
  if (!popup) return;
  const urlText = document.getElementById("adminUrlText");
  const countdown = document.getElementById("adminPopupCountdown");
  const closeBtn = document.getElementById("adminPopupClose");

  try {
    const res = await fetch("/api/network/info");
    if (!res.ok) throw new Error("network/info HTTP " + res.status);
    const data = await res.json();
    if (urlText) urlText.textContent = data.admin_url || "—";
  } catch (e) {
    if (urlText) urlText.textContent = "Info network tidak tersedia.";
    console.warn("Gagal ambil admin URL:", e);
  }

  popup.classList.remove("hidden");
  let secondsLeft = 30;
  if (countdown) countdown.textContent = secondsLeft;

  const timer = window.setInterval(() => {
    secondsLeft -= 1;
    if (countdown) countdown.textContent = secondsLeft;
    if (secondsLeft <= 0) {
      popup.classList.add("hidden");
      window.clearInterval(timer);
    }
  }, 1000);

  if (closeBtn) {
    closeBtn.addEventListener("click", () => {
      popup.classList.add("hidden");
      window.clearInterval(timer);
    }, { once: true });
  }
}

document.addEventListener("DOMContentLoaded", () => {
  // Initial view = home → tombol ✕ exit-kiosk disembunyikan.
  document.body.classList.add("view-home");
  $$(".scan-card").forEach((button) => {
    button.addEventListener("click", () => setScan(button.dataset.mode));
  });
  $("#homeBtn").addEventListener("click", setHome);
  document.body.addEventListener("click", unlockSound);
  loadStatus();
  window.setInterval(loadStatus, 10000);
  window.setInterval(pollVoiceEvents, 1500);

  // Tombol close kiosk (kalau ada di template, mis. /display)
  const exitBtn = document.getElementById("exitKioskBtn");
  if (exitBtn) {
    exitBtn.addEventListener("click", exitKiosk);
  }

  // Tombol Home di display home-view: refresh kembali ke /display.
  // Sengaja TIDAK ke /admin supaya orang tua / siswa yang kepencet tidak
  // masuk dashboard admin.
  const homeReloadBtn = document.getElementById("displayHomeReloadBtn");
  if (homeReloadBtn) {
    homeReloadBtn.addEventListener("click", () => {
      window.location.href = "/display";
    });
  }

  // Popup URL admin (cuma ada di /display, bukan /lcd)
  if (document.getElementById("adminUrlPopup")) {
    showAdminUrlPopup();
  }

  // Setup WiFi quick-button + modal (kalau elemen ada di template)
  if (document.getElementById("wifiBtn")) {
    setupWifiModal();
  }
  // Setup Bluetooth quick-button + modal
  if (document.getElementById("btBtn")) {
    setupBluetoothModal();
  }
  // Setup Volume quick-button + modal
  if (document.getElementById("volBtn")) {
    setupVolumeModal();
  }
});

// ─── WiFi modal (only on /display) ─────────────────────────────────────

let selectedSsid = null;
let selectedSecured = true;

function setupWifiModal() {
  const wifiBtn = document.getElementById("wifiBtn");
  const closeBtn = document.getElementById("wifiCloseBtn");
  const rescanBtn = document.getElementById("wifiRescanBtn");
  const cancelBtn = document.getElementById("wifiCancelBtn");
  const connectBtn = document.getElementById("wifiConnectBtn");
  const passwordInput = document.getElementById("wifiPasswordInput");

  wifiBtn.addEventListener("click", openWifiModal);
  closeBtn.addEventListener("click", closeWifiModal);
  rescanBtn.addEventListener("click", () => loadWifiData(true));
  cancelBtn.addEventListener("click", showWifiList);
  connectBtn.addEventListener("click", doConnect);

  // Scroll list lewat tombol ▲▼ (instant, bukan smooth — smooth = repaint
  // kontinu yg bikin hang di kiosk software-render).
  const list = document.getElementById("wifiList");
  const WIFI_SCROLL_STEP = 90;
  document
    .getElementById("wifiScrollUp")
    .addEventListener("click", () => {
      list.scrollTop -= WIFI_SCROLL_STEP;
    });
  document
    .getElementById("wifiScrollDown")
    .addEventListener("click", () => {
      list.scrollTop += WIFI_SCROLL_STEP;
    });

  // Tap input password → buka virtual keyboard
  passwordInput.addEventListener("click", () => openKeyboard(passwordInput));
}

function openWifiModal() {
  document.getElementById("wifiModal").classList.remove("hidden");
  // Sembunyikan tombol ✕ merah exit kiosk supaya tidak bingung dengan
  // tombol ✕ tutup modal WiFi sendiri.
  document.body.classList.add("wifi-modal-open");
  showWifiList();
  loadWifiData(false);
}

function closeWifiModal() {
  document.getElementById("wifiModal").classList.add("hidden");
  document.body.classList.remove("wifi-modal-open");
  closeKeyboard();
}

function showWifiList() {
  document.getElementById("wifiViewList").classList.remove("hidden");
  document.getElementById("wifiViewForm").classList.add("hidden");
  closeKeyboard();
}

function showWifiForm() {
  document.getElementById("wifiViewList").classList.add("hidden");
  document.getElementById("wifiViewForm").classList.remove("hidden");
  document.getElementById("wifiFormStatus").textContent = "";
  document.getElementById("wifiPasswordInput").value = "";
}

let wifiBusy = false; // cegah loadWifiData overlap (rescan bounce di touch resistif)

async function loadWifiData(forceRescan) {
  if (wifiBusy) return; // tap rescan beruntun diabaikan sampai load selesai
  wifiBusy = true;
  const currentEl = document.getElementById("wifiCurrent");
  const listEl = document.getElementById("wifiList");
  const rescanBtn = document.getElementById("wifiRescanBtn");
  if (rescanBtn) rescanBtn.disabled = true;

  currentEl.textContent = forceRescan ? "Memuat ulang…" : "Memuat status…";
  currentEl.classList.remove("connected");
  listEl.innerHTML = "";

  try {
    const [statusRes, scanRes] = await Promise.all([
      fetch("/api/wifi/status").then((r) => r.json()),
      fetch("/api/wifi/scan").then((r) => r.json()),
    ]);

    if (statusRes && statusRes.connected && statusRes.ssid) {
      currentEl.textContent = "Tersambung: " + statusRes.ssid;
      currentEl.classList.add("connected");
    } else {
      currentEl.textContent = "Tidak tersambung";
    }

    const networks = (scanRes && scanRes.networks) || [];
    if (networks.length === 0) {
      listEl.innerHTML = '<div class="wifi-list-item">(Tidak ada WiFi terdeteksi)</div>';
      return;
    }

    listEl.innerHTML = networks
      .map((n) => {
        const lock = n.secured ? "🔒" : "🔓";
        const conn = n.connected ? " connected" : "";
        return `
          <div class="wifi-list-item${conn}" data-ssid="${escapeHtml(n.ssid)}" data-secured="${n.secured ? "1" : "0"}">
            <span class="ssid">${escapeHtml(n.ssid)}</span>
            <span class="meta">${lock} ${n.signal}%</span>
          </div>
        `;
      })
      .join("");

    listEl.querySelectorAll(".wifi-list-item[data-ssid]").forEach((el) => {
      el.addEventListener("click", () => {
        selectedSsid = el.dataset.ssid;
        selectedSecured = el.dataset.secured === "1";
        document.getElementById("wifiSelectedSsid").textContent = selectedSsid;
        showWifiForm();
        if (!selectedSecured) {
          // Open network: langsung connect tanpa password
          doConnect();
        }
      });
    });
  } catch (e) {
    currentEl.textContent = "Gagal load WiFi: " + e.message;
  } finally {
    wifiBusy = false;
    if (rescanBtn) rescanBtn.disabled = false;
  }
}

async function doConnect() {
  const passwordInput = document.getElementById("wifiPasswordInput");
  const statusEl = document.getElementById("wifiFormStatus");
  const password = passwordInput.value;

  if (selectedSecured && !password) {
    statusEl.textContent = "Password diperlukan untuk WiFi ini.";
    statusEl.className = "wifi-form-status error";
    return;
  }

  statusEl.textContent = "Menyambungkan ke " + selectedSsid + "…";
  statusEl.className = "wifi-form-status";

  try {
    const res = await fetch("/api/wifi/connect", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ssid: selectedSsid, password: password || null }),
    });
    const data = await res.json();
    if (data.ok) {
      statusEl.textContent = "✓ " + (data.message || "Tersambung.");
      statusEl.className = "wifi-form-status success";
      setTimeout(() => {
        showWifiList();
        loadWifiData(false);
      }, 2000);
    } else {
      statusEl.textContent = "✗ " + (data.message || data.detail || "Gagal connect.");
      statusEl.className = "wifi-form-status error";
      // Retry harus mulai dari kosong. Tanpa ini, password baru ter-append ke
      // percobaan lama oleh virtual keyboard dan menghasilkan PSK gabungan.
      passwordInput.value = "";
      openKeyboard(passwordInput);
    }
  } catch (e) {
    statusEl.textContent = "Network error: " + e.message;
    statusEl.className = "wifi-form-status error";
    passwordInput.value = "";
    openKeyboard(passwordInput);
  }
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[c]);
}

// ─── Virtual keyboard (QWERTY) ─────────────────────────────────────────

let keyboardTarget = null;
let keyboardShifted = false;

const KB_ROWS_LOWER = [
  ["1", "2", "3", "4", "5", "6", "7", "8", "9", "0"],
  ["q", "w", "e", "r", "t", "y", "u", "i", "o", "p"],
  ["a", "s", "d", "f", "g", "h", "j", "k", "l"],
  ["⇧", "z", "x", "c", "v", "b", "n", "m", "⌫"],
  ["⌨▼", ".", "_", "-", "@", "/", "Spasi", "OK"],
];
const KB_ROWS_UPPER = [
  ["!", "@", "#", "$", "%", "^", "&", "*", "(", ")"],
  ["Q", "W", "E", "R", "T", "Y", "U", "I", "O", "P"],
  ["A", "S", "D", "F", "G", "H", "J", "K", "L"],
  ["⇧", "Z", "X", "C", "V", "B", "N", "M", "⌫"],
  ["⌨▼", "+", "=", "?", ":", ";", "Spasi", "OK"],
];

function openKeyboard(targetInput) {
  keyboardTarget = targetInput;
  keyboardShifted = false;
  renderKeyboard();
  document.getElementById("virtualKeyboard").classList.remove("hidden");
}

function closeKeyboard() {
  const kb = document.getElementById("virtualKeyboard");
  if (kb) kb.classList.add("hidden");
  keyboardTarget = null;
}

function renderKeyboard() {
  const kb = document.getElementById("virtualKeyboard");
  const rows = keyboardShifted ? KB_ROWS_UPPER : KB_ROWS_LOWER;
  kb.innerHTML = rows
    .map((row) => {
      const keys = row
        .map((k) => {
          let cls = "vk-key";
          let label = k;
          if (k === "⇧") {
            cls += " special wide" + (keyboardShifted ? " shift-on" : "");
          } else if (k === "⌫") {
            cls += " special wide";
          } else if (k === "Spasi") {
            cls += " special extra-wide";
          } else if (k === "OK") {
            cls += " special wide";
          } else if (k === "⌨▼") {
            cls += " special hide-kb";
          }
          return `<button type="button" class="${cls}" data-key="${escapeHtml(k)}">${escapeHtml(label)}</button>`;
        })
        .join("");
      return `<div class="vk-row">${keys}</div>`;
    })
    .join("");

  kb.querySelectorAll(".vk-key").forEach((btn) => {
    btn.addEventListener("click", () => handleKeyPress(btn.dataset.key));
  });
}

function handleKeyPress(key) {
  if (!keyboardTarget) return;
  const current = keyboardTarget.value;

  if (key === "⇧") {
    keyboardShifted = !keyboardShifted;
    renderKeyboard();
    return;
  }
  if (key === "⌫") {
    keyboardTarget.value = current.slice(0, -1);
    return;
  }
  if (key === "Spasi") {
    keyboardTarget.value = current + " ";
    return;
  }
  if (key === "OK" || key === "⌨▼") {
    closeKeyboard();
    return;
  }
  // Normal character (single tap → append)
  keyboardTarget.value = current + key;
  // Sticky shift off setelah ketik 1 char uppercase / simbol
  if (keyboardShifted) {
    keyboardShifted = false;
    renderKeyboard();
  }
}

// ─── Bluetooth modal (only on /display) ────────────────────────────────

function setupBluetoothModal() {
  document.getElementById("btBtn").addEventListener("click", openBtModal);
  document.getElementById("btCloseBtn").addEventListener("click", closeBtModal);
  document.getElementById("btRescanBtn").addEventListener("click", () => loadBtData(true));
}

function openBtModal() {
  document.getElementById("btModal").classList.remove("hidden");
  // Pakai class yg sama dgn wifi-modal-open supaya ✕ exit-kiosk tersembunyi
  document.body.classList.add("wifi-modal-open");
  document.getElementById("btFormStatus").textContent = "";
  loadBtData(false);
}

function closeBtModal() {
  document.getElementById("btModal").classList.add("hidden");
  document.body.classList.remove("wifi-modal-open");
}

let btBusy = false; // cegah loadBtData overlap (scan BT ~8s; tap beruntun di-skip)

async function loadBtData(rescan) {
  if (btBusy) return;
  btBusy = true;
  const currentEl = document.getElementById("btCurrent");
  const listEl = document.getElementById("btList");
  const rescanBtn = document.getElementById("btRescanBtn");
  if (rescanBtn) rescanBtn.disabled = true;
  currentEl.textContent = rescan ? "Scanning Bluetooth (~8 detik)…" : "Memuat status…";
  listEl.innerHTML = "";

  try {
    // Status terkini
    let powered = true;
    try {
      const status = await api("/api/bluetooth/status");
      powered = Boolean(status.powered);
      if (!status.powered) {
        currentEl.textContent = "Bluetooth: OFF (adapter mati)";
      } else if (status.connected && status.connected.length > 0) {
        const names = status.connected.map((d) => d.name).join(", ");
        currentEl.textContent = `Tersambung: ${names}`;
      } else {
        currentEl.textContent = "Bluetooth: ON, tidak ada device tersambung";
      }
    } catch {
      currentEl.textContent = "Bluetooth: tidak tersedia di device ini";
      return;
    }

    // Scan device
    try {
      const path = rescan ? "/api/bluetooth/scan" : "/api/bluetooth/status";
      const data = await api(path);
      const devices = rescan ? (data.devices || []) : [];
      if (rescan) {
        renderBtList(devices);
      } else {
        // Saat pertama buka modal, langsung trigger scan supaya user lihat list
        const initial = await api("/api/bluetooth/scan");
        renderBtList(initial.devices || []);
      }
    } catch (err) {
      listEl.innerHTML = `<div class="wifi-empty">Scan gagal: ${escapeHtml(String(err))}</div>`;
    }
  } finally {
    btBusy = false;
    if (rescanBtn) rescanBtn.disabled = false;
  }
}

function renderBtList(devices) {
  const listEl = document.getElementById("btList");
  if (!devices.length) {
    listEl.innerHTML = `<div class="wifi-empty">Tidak ada device Bluetooth ditemukan.</div>`;
    return;
  }
  listEl.innerHTML = devices.map((d) => {
    const status = d.connected ? "✓ Connected" : d.paired ? "Paired" : "";
    const icon = d.icon && d.icon.includes("audio") ? "🔊"
              : d.icon && d.icon.includes("input") ? "⌨️"
              : d.icon && d.icon.includes("phone") ? "📱"
              : "🔵";
    const cls = `wifi-list-item bt-row${d.connected ? " connected" : ""}`;
    const forgetBtn = d.paired
      ? `<button class="bt-forget" data-mac="${escapeHtml(d.mac)}" title="Lupakan device">Lupakan</button>`
      : "";
    return `
      <div class="${cls}">
        <div class="bt-main" data-mac="${escapeHtml(d.mac)}"
             data-connected="${d.connected ? '1' : '0'}"
             data-paired="${d.paired ? '1' : '0'}">
          <span class="ssid">${icon} ${escapeHtml(d.name)}</span>
          <span class="meta">${escapeHtml(status)}</span>
        </div>
        ${forgetBtn}
      </div>
    `;
  }).join("");

  listEl.querySelectorAll(".bt-main").forEach((el) => {
    el.addEventListener("click", () => {
      const mac = el.dataset.mac;
      const connected = el.dataset.connected === "1";
      if (connected) {
        btDisconnect(mac);
      } else {
        btConnect(mac);
      }
    });
  });

  listEl.querySelectorAll(".bt-forget").forEach((el) => {
    el.addEventListener("click", (e) => {
      e.stopPropagation();
      btForget(el.dataset.mac);
    });
  });
}

async function btForget(mac) {
  if (!window.confirm(`Lupakan ${mac}? Perlu pairing ulang untuk memakainya lagi.`)) return;
  const statusEl = document.getElementById("btFormStatus");
  statusEl.textContent = `Melupakan ${mac}…`;
  try {
    const res = await fetch("/api/bluetooth/unpair", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mac }),
    });
    const data = await res.json();
    statusEl.textContent = data.ok
      ? `✓ ${data.message || "Device dilupakan."}`
      : `✗ ${data.message}`;
    // Rescan supaya list ke-refresh (device hilang dari daftar paired).
    window.setTimeout(() => loadBtData(true), 800);
  } catch (err) {
    statusEl.textContent = `✗ Error: ${err}`;
  }
}

async function btConnect(mac) {
  const statusEl = document.getElementById("btFormStatus");
  statusEl.textContent = `Pairing & connecting ke ${mac}…`;
  try {
    const res = await fetch("/api/bluetooth/connect", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mac }),
    });
    const data = await res.json();
    statusEl.textContent = data.ok ? `✓ ${data.message}` : `✗ ${data.message}`;
    // Refresh status (tanpa rescan supaya cepat)
    window.setTimeout(() => loadBtData(false), 1500);
  } catch (err) {
    statusEl.textContent = `✗ Error: ${err}`;
  }
}

async function btDisconnect(mac) {
  const statusEl = document.getElementById("btFormStatus");
  statusEl.textContent = `Disconnect ${mac}…`;
  try {
    const res = await fetch("/api/bluetooth/disconnect", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mac }),
    });
    const data = await res.json();
    statusEl.textContent = data.ok ? `✓ ${data.message}` : `✗ ${data.message}`;
    window.setTimeout(() => loadBtData(false), 1000);
  } catch (err) {
    statusEl.textContent = `✗ Error: ${err}`;
  }
}

// ─── Volume modal (only on /display) ───────────────────────────────────

let volMuted = false;
let volPercent = 50; // posisi terakhir diketahui, dipakai tombol step −/+
let volBusy = false; // guard 1 request volume in-flight (cegah pile-up)
const VOL_STEP = 10;

function setupVolumeModal() {
  document.getElementById("volBtn").addEventListener("click", openVolModal);
  document.getElementById("volCloseBtn").addEventListener("click", closeVolModal);
  document.getElementById("volMuteBtn").addEventListener("click", toggleVolMute);
  document
    .getElementById("volDownBtn")
    .addEventListener("click", () => stepVolume(-VOL_STEP));
  document
    .getElementById("volUpBtn")
    .addEventListener("click", () => stepVolume(VOL_STEP));
}

function stepVolume(delta) {
  if (volBusy) return; // abaikan tap saat request sebelumnya belum balik
  const next = Math.max(0, Math.min(100, volPercent + delta));
  if (next === volPercent) return;
  setVolume(next);
}

function openVolModal() {
  document.getElementById("volModal").classList.remove("hidden");
  document.body.classList.add("wifi-modal-open");
  loadVolume();
}

function closeVolModal() {
  document.getElementById("volModal").classList.add("hidden");
  document.body.classList.remove("wifi-modal-open");
}

function updateVolLabel(percent) {
  document.getElementById("volPercent").textContent = `${percent}%`;
  document.getElementById("volBarFill").style.width = `${percent}%`;
}

function applyVolState(state) {
  if (!state) return;
  volMuted = Boolean(state.muted);
  if (typeof state.percent === "number") {
    volPercent = state.percent;
    updateVolLabel(state.percent);
  }
  document.getElementById("volMuteBtn").textContent = volMuted ? "🔇" : "🔊";
  const cur = document.getElementById("volCurrent");
  cur.textContent = volMuted ? "Bisu" : `Volume ${state.percent}%`;
  cur.classList.toggle("connected", !volMuted && state.percent > 0);
}

async function loadVolume() {
  const cur = document.getElementById("volCurrent");
  cur.textContent = "Memuat…";
  cur.classList.remove("connected");
  try {
    const res = await fetch("/api/volume/status");
    const data = await res.json();
    if (res.ok) {
      applyVolState(data);
    } else {
      cur.textContent = "Volume tidak tersedia: " + (data.detail || res.status);
    }
  } catch (e) {
    cur.textContent = "Gagal load volume: " + e.message;
  }
}

function setVolBusy(busy) {
  volBusy = busy;
  const down = document.getElementById("volDownBtn");
  const up = document.getElementById("volUpBtn");
  if (down) down.disabled = busy;
  if (up) up.disabled = busy;
}

async function setVolume(percent) {
  if (volBusy) return;
  setVolBusy(true);
  // Optimistic: gerakkan bar dulu biar responsif walau request masih jalan.
  updateVolLabel(Math.max(0, Math.min(100, Number(percent))));
  try {
    const res = await fetch("/api/volume/set", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ percent: Number(percent) }),
    });
    const data = await res.json();
    if (res.ok) applyVolState(data);
  } catch (e) {
    /* abaikan; biarkan posisi bar apa adanya */
  } finally {
    setVolBusy(false);
  }
}

async function toggleVolMute() {
  if (volBusy) return;
  setVolBusy(true);
  try {
    const res = await fetch("/api/volume/mute", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ muted: !volMuted }),
    });
    const data = await res.json();
    if (res.ok) applyVolState(data);
  } catch (e) {
    /* abaikan */
  } finally {
    setVolBusy(false);
  }
}
