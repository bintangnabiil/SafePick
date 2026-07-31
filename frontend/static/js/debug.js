(function installSafePickDebug() {
  "use strict";

  const endpoint = "/api/debug/client-error";
  const recent = new Map();

  function text(value, limit) {
    return String(value == null ? "" : value).slice(0, limit);
  }

  function shouldSend(key) {
    const now = Date.now();
    const last = recent.get(key) || 0;
    if (now - last < 5000) return false;
    recent.set(key, now);
    if (recent.size > 100) {
      for (const [item, timestamp] of recent) {
        if (now - timestamp > 60000) recent.delete(item);
      }
    }
    return true;
  }

  function report(error, context) {
    const err = error instanceof Error ? error : new Error(text(error, 2000));
    const info = context || {};
    const payload = {
      message: text(info.message || err.message || "Unknown browser error", 2000),
      source: text(info.source || "", 500) || null,
      line: Number.isFinite(info.line) ? info.line : null,
      column: Number.isFinite(info.column) ? info.column : null,
      stack: text(err.stack || info.stack || "", 8000) || null,
      page: text(window.location.pathname, 500),
      user_agent: text(navigator.userAgent, 500),
    };
    const key = `${payload.page}|${payload.source}|${payload.message}`;
    if (!shouldSend(key)) return;

    const body = JSON.stringify(payload);
    if (navigator.sendBeacon) {
      navigator.sendBeacon(
        endpoint,
        new Blob([body], { type: "application/json" }),
      );
      return;
    }
    window.fetch(endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body,
      keepalive: true,
    }).catch(() => {});
  }

  window.SafePickDebug = { report };

  window.addEventListener("error", (event) => {
    report(event.error || event.message, {
      message: event.message,
      source: event.filename,
      line: event.lineno,
      column: event.colno,
    });
  });

  window.addEventListener("unhandledrejection", (event) => {
    report(event.reason, { source: "unhandledrejection" });
  });

  const originalFetch = window.fetch.bind(window);
  window.fetch = async function debugFetch(input, init) {
    const rawUrl = typeof input === "string" ? input : input && input.url;
    const path = (() => {
      try {
        return new URL(rawUrl || "", window.location.origin).pathname;
      } catch {
        return text(rawUrl, 500);
      }
    })();
    try {
      const response = await originalFetch(input, init);
      if (response.status >= 500 && path !== endpoint) {
        report(`HTTP ${response.status}`, { source: path });
      }
      return response;
    } catch (error) {
      if (path !== endpoint) report(error, { source: path || "fetch" });
      throw error;
    }
  };
})();
