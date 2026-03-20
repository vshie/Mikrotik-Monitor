/* global Chart */

function $(sel) {
  return document.querySelector(sel);
}

function tabInit() {
  document.querySelectorAll(".tab").forEach((btn) => {
    btn.addEventListener("click", () => {
      const id = btn.dataset.tab;
      document.querySelectorAll(".tab").forEach((b) => b.classList.toggle("active", b === btn));
      document.querySelectorAll(".panel").forEach((p) => {
        p.classList.toggle("active", p.id === `panel-${id}`);
      });
      if (id === "chart") loadChart();
    });
  });
}

function fmtNum(v) {
  if (v === "" || v === null || v === undefined) return "—";
  const n = Number(v);
  return Number.isFinite(n) ? n.toFixed(2) : String(v);
}

async function fetchJSON(url, opts) {
  const r = await fetch(url, opts);
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
  return r.json();
}

async function refreshStatus() {
  try {
    const s = await fetchJSON("/api/status");
    const good = s.reachable;
    $("#reach").textContent = good
      ? `Reachable (${s.reach_method || "?"})`
      : "Unreachable";
    $("#reach").style.color = good ? "var(--good)" : "var(--bad)";

    const errEl = $("#last-error");
    const pathEl = $("#reg-path");
    if (s.last_error) {
      errEl.textContent = s.last_error;
      errEl.classList.remove("hidden");
    } else {
      errEl.textContent = "";
      errEl.classList.add("hidden");
    }
    if (s.registration_path) {
      pathEl.textContent = `API path: ${s.registration_path}`;
      pathEl.classList.remove("hidden");
    } else {
      pathEl.textContent = "";
      pathEl.classList.add("hidden");
    }

    const lk = s.last_link || {};
    const kv = $("#link-kv");
    kv.innerHTML = "";
    const rows = [
      ["SNR (dB)", fmtNum(lk.snr_db)],
      ["Signal (dBm)", fmtNum(lk.signal_dbm)],
      ["TX (dBm)", fmtNum(lk.tx_dbm)],
      ["RX (dBm)", fmtNum(lk.rx_dbm)],
      ["Noise (dBm)", fmtNum(lk.noise_floor_dbm)],
      ["TX rate", fmtNum(lk.tx_rate_mbps)],
      ["RX rate", fmtNum(lk.rx_rate_mbps)],
      ["AP MAC", lk.ap_mac || "—"],
      ["Interface", lk.interface || "—"],
    ];
    for (const [k, v] of rows) {
      const dt = document.createElement("dt");
      dt.textContent = k;
      const dd = document.createElement("dd");
      dd.textContent = v;
      kv.append(dt, dd);
    }

    const g = s.last_gps;
    $("#gps-line").textContent = g
      ? `${g.lat.toFixed(6)}, ${g.lon.toFixed(6)}`
      : "No GPS fix";

    const d = s.last_distance_m;
    $("#dist-line").textContent =
      d != null && Number.isFinite(d) ? `${d.toFixed(1)} m to reference` : "Distance —";

    const errs = s.last_mavlink_errors || [];
    $("#mav-errors").textContent =
      errs.length > 0 ? errs.join(" · ") : s.mavlink_enabled === false ? "Disabled" : "OK";
  } catch (e) {
    $("#reach").textContent = "Status error";
    $("#reach").style.color = "var(--bad)";
  }
}

let chart;

function parseTS(row) {
  const t = row.timestamp_utc;
  if (!t) return null;
  const d = new Date(t);
  return Number.isFinite(d.getTime()) ? d : null;
}

async function loadChart() {
  const { points } = await fetchJSON("/api/history?minutes=20");
  const labels = [];
  const snr = [];
  const sig = [];
  const dist = [];
  for (const p of points) {
    const d = parseTS(p);
    if (!d) continue;
    labels.push(d.toLocaleTimeString());
    snr.push(p.snr_db === "" ? null : Number(p.snr_db));
    sig.push(p.signal_dbm === "" ? null : Number(p.signal_dbm));
    dist.push(p.distance_m === "" ? null : Number(p.distance_m));
  }

  const data = {
    labels,
    datasets: [
      {
        label: "SNR (dB)",
        data: snr,
        borderColor: "#3d9cf5",
        tension: 0.2,
        spanGaps: true,
        yAxisID: "y",
      },
      {
        label: "Signal (dBm)",
        data: sig,
        borderColor: "#3dd68c",
        tension: 0.2,
        spanGaps: true,
        yAxisID: "y",
      },
      {
        label: "Distance (m)",
        data: dist,
        borderColor: "#e0b15a",
        tension: 0.2,
        spanGaps: true,
        yAxisID: "y1",
      },
    ],
  };

  const canvas = $("#chart");
  if (chart) chart.destroy();
  chart = new Chart(canvas, {
    type: "line",
    data,
    options: {
      responsive: true,
      interaction: { mode: "index", intersect: false },
      scales: {
        x: {
          grid: { color: "#2a3542" },
          ticks: { color: "#8b9bab", maxRotation: 45, autoSkip: true, maxTicksLimit: 12 },
        },
        y: {
          position: "left",
          grid: { color: "#2a3542" },
          ticks: { color: "#8b9bab" },
        },
        y1: {
          position: "right",
          grid: { drawOnChartArea: false },
          ticks: { color: "#e0b15a" },
        },
      },
      plugins: {
        legend: { labels: { color: "#e8eef4" } },
      },
    },
  });
}

async function loadSettingsForm() {
  const s = await fetchJSON("/api/settings");
  const form = $("#settings-form");
  for (const [k, v] of Object.entries(s)) {
    const el = form.elements.namedItem(k);
    if (!el) continue;
    if (el.type === "checkbox") {
      el.checked = Boolean(v);
    } else if (v === null || v === undefined) {
      el.value = "";
    } else {
      el.value = String(v);
    }
  }
}

function settingsInit() {
  $("#settings-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const form = ev.target;
    const msg = $("#save-msg");
    const body = {};
    const data = new FormData(form);
    for (const [k, raw] of data.entries()) {
      body[k] = raw;
    }
    // Always persist password field (empty = no password, MikroTik factory default)
    body.router_password = form.elements.router_password.value;
    body.router_api_port = Number(body.router_api_port);
    body.poll_interval_s = Number(body.poll_interval_s);
    body.gps_component_id = Number(body.gps_component_id);
    body.mavlink_header_system_id = Number(body.mavlink_header_system_id);
    body.mavlink_header_component_id = Number(body.mavlink_header_component_id);
    body.target_system = Number(body.target_system);
    body.target_component = Number(body.target_component);
    body.mavlink_enabled = form.elements.mavlink_enabled.checked;
    body.mavlink_send_distance = form.elements.mavlink_send_distance.checked;
    body.router_plaintext_login = form.elements.router_plaintext_login.checked;
    body.router_try_wifiwave2 = form.elements.router_try_wifiwave2.checked;

    const lat = form.reference_latitude.value.trim();
    const lon = form.reference_longitude.value.trim();
    body.reference_latitude = lat === "" ? null : Number(lat);
    body.reference_longitude = lon === "" ? null : Number(lon);
    if (body.reference_latitude !== null && !Number.isFinite(body.reference_latitude)) {
      msg.textContent = "Invalid reference latitude";
      return;
    }
    if (body.reference_longitude !== null && !Number.isFinite(body.reference_longitude)) {
      msg.textContent = "Invalid reference longitude";
      return;
    }

    try {
      await fetchJSON("/api/settings", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      msg.textContent = "Saved.";
    } catch (e) {
      msg.textContent = `Error: ${e.message}`;
    }
    setTimeout(() => {
      msg.textContent = "";
    }, 4000);
  });
}

tabInit();
settingsInit();
loadSettingsForm();
refreshStatus();
setInterval(refreshStatus, 2000);
