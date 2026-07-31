const form = document.querySelector("#loginForm");
const statusLine = document.querySelector("#loginStatus");

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  statusLine.textContent = "Memeriksa akun...";

  const payload = Object.fromEntries(new FormData(form).entries());
  try {
    const response = await fetch("/api/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });

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

    window.location.href = "/admin";
  } catch (error) {
    statusLine.textContent = error.message;
  }
});
