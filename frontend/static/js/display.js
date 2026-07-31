const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => Array.from(document.querySelectorAll(selector));

let currentMode = "attendance_qr";
const voiceEventStorageKey = "safepickLastVoiceEventId";
let lastVoiceEventId = Number(window.localStorage.getItem(voiceEventStorageKey) || 0);
let soundUnlocked = false;
let pendingVoiceEvent = null;
let voicePolling = false;
let voicePlaybackChain = Promise.resolve();

const modes = {
  attendance_qr: {
    title: "QR Kehadiran Siswa",
    status: "QR Kehadiran Siswa active",
    stream: "/video/qr?mode=attendance",
  },
  pickup_face: {
    title: "Muka Penjemputan Orang Tua",
    status: "Muka Penjemputan Orang Tua active",
    stream: "/video/recognize",
  },
  pickup_qr: {
    title: "QR Penjemputan Non-orang Tua",
    status: "QR Penjemputan Non-orang Tua active",
    stream: "/video/qr?mode=pickup",
  },
};

async function api(path) {
  const response = await fetch(path);
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = await response.json();
      detail = body.detail || detail;
    } catch {
      // keep response status text
    }
    throw new Error(detail);
  }
  return response.json();
}

function startStream(mode = "attendance_qr") {
  currentMode = mode;
  const selected = modes[mode] || modes.attendance_qr;
  const feed = $("#recognizeFeed");
  const separator = selected.stream.includes("?") ? "&" : "?";
  const nextStream = `${selected.stream}${separator}t=${Date.now()}`;
  feed.removeAttribute("src");
  $("#displayTitle").textContent = selected.title;
  $("#runtimeStatus").textContent = selected.status;
  $$(".mode-button").forEach((button) => {
    button.classList.toggle("active", button.dataset.mode === mode);
  });
  window.setTimeout(() => {
    feed.src = nextStream;
  }, 80);
}

async function loadStatus() {
  try {
    const status = await api("/api/status");
    const summary = status.database.ok ? status.database.data : {};
    $("#dbStatus").textContent = status.database.ok ? "MySQL connected" : "MySQL error";
    $("#dbStatus").className = status.database.ok ? "ok" : "error";
    $("#studentCount").textContent = `Students: ${summary.total_students ?? "-"}`;
    $("#parentCount").textContent = `Parents: ${summary.total_parents ?? "-"}`;
  } catch (error) {
    $("#dbStatus").textContent = `Status error: ${error.message}`;
    $("#dbStatus").className = "error";
  }
}

function setVoiceStatus(message) {
  const runtime = $("#runtimeStatus");
  if (runtime) runtime.textContent = message;
}

async function playVoiceEvent(event) {
  if (!event || !event.audio_url) return;
  pendingVoiceEvent = event;
  const audio = new Audio(event.audio_url);
  try {
    await audio.play();
    soundUnlocked = true;
    pendingVoiceEvent = null;
    setVoiceStatus(`Memanggil: ${event.text}`);
    await new Promise((resolve) => {
      audio.addEventListener("ended", resolve, { once: true });
      audio.addEventListener("error", resolve, { once: true });
    });
    await new Promise((resolve) => window.setTimeout(resolve, 900));
  } catch {
    soundUnlocked = false;
    setVoiceStatus("Klik halaman untuk mengaktifkan suara.");
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
  // Voice TTS aktif di semua mode scan (kehadiran QR, penjemputan QR,
  // penjemputan muka). Kategori event dibedakan di server (lihat
  // backend queue_voice_announcement). Tidak ada filter mode di sini.
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
    // Voice polling must not interrupt the display stream.
  } finally {
    voicePolling = false;
  }
}

function unlockSound() {
  soundUnlocked = true;
  if (pendingVoiceEvent) {
    const event = pendingVoiceEvent;
    pendingVoiceEvent = null;
    enqueueVoiceEvent(event);
  }
}

document.addEventListener("DOMContentLoaded", () => {
  startStream();
  $(".mode-bar").addEventListener("click", (event) => {
    const button = event.target.closest(".mode-button");
    if (!button) return;
    startStream(button.dataset.mode);
  });
  document.body.addEventListener("click", unlockSound);
  loadStatus();
  window.setInterval(loadStatus, 10000);
  window.setInterval(pollVoiceEvents, 1500);
});
