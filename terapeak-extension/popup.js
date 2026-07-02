chrome.storage.local.get(["last_success", "last_send_status", "last_send_time", "last_attempt"], (data) => {
  const s = data.last_success || data.last_attempt || {};
  const statusEl = document.getElementById("status");

  if (data.last_send_status === "ok") {
    statusEl.textContent = "Sent to agent";
    statusEl.className = "status-ok";
  } else if (data.last_send_status && data.last_send_status !== "ok") {
    statusEl.textContent = data.last_send_status;
    statusEl.className = "status-err";
  } else if (s.keyword) {
    statusEl.textContent = "Captured (pending send)";
    statusEl.className = "status-pending";
  } else {
    statusEl.textContent = "No data yet — search a keyword in Terapeak";
    statusEl.className = "status-pending";
  }

  document.getElementById("keyword").textContent = s.keyword || "—";
  document.getElementById("avg_sold").textContent = s.avg_sold_price ? `£${s.avg_sold_price.toFixed(2)}` : "—";
  document.getElementById("sell_through").textContent = s.sell_through_pct != null ? `${s.sell_through_pct}%` : "—";
  document.getElementById("total_sold").textContent = s.total_sold != null ? s.total_sold.toLocaleString() : "—";
  document.getElementById("last_time").textContent = data.last_send_time
    ? new Date(data.last_send_time).toLocaleTimeString()
    : "—";

  if (s.error) {
    document.getElementById("debug").textContent = `Debug: ${s.error}. If this persists, eBay may have updated their page layout — contact your agent.`;
  }
});
