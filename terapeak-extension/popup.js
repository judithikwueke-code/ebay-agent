chrome.storage.local.get(["last_success", "last_send_status", "last_send_time", "last_attempt"], (data) => {
  // Always show the LATEST attempt (an older success could be stale)
  const s = data.last_attempt || data.last_success || {};
  const statusEl = document.getElementById("status");

  if (data.last_send_status === "ok") {
    statusEl.textContent = "Sent to agent";
    statusEl.className = "status-ok";
  } else if (data.last_send_status) {
    statusEl.textContent = data.last_send_status;
    statusEl.className = "status-err";
  } else if (s.keyword) {
    statusEl.textContent = "Captured (pending send)";
    statusEl.className = "status-pending";
  } else {
    statusEl.textContent = "No data yet - search a keyword in Terapeak";
    statusEl.className = "status-pending";
  }

  const num = (v, f) => (v === null || v === undefined ? "-" : f(v));
  document.getElementById("keyword").textContent = s.keyword || "-";
  document.getElementById("avg_sold").textContent = num(s.avg_sold_price, (v) => `GBP ${v.toFixed(2)}`);
  document.getElementById("sell_through").textContent = num(s.sell_through_pct, (v) => `${v}%`);
  document.getElementById("total_sold").textContent = num(s.total_sold, (v) => v.toLocaleString());
  document.getElementById("last_time").textContent = data.last_send_time
    ? new Date(data.last_send_time).toLocaleTimeString()
    : "-";

  document.getElementById("debug").textContent =
    `DEBUG (${s.error || "ok"}): ${s._seen || "the page showed no stat cards to the extension"}`;
});
