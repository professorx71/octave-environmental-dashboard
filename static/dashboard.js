
let lastData = null;
let historyRows = [];
let useF = false;
let historyLoadedAt = 0;

const $ = id => document.getElementById(id);
const cv = c => useF ? (Number(c) * 9 / 5 + 32) : Number(c);
const unit = () => useF ? "°F" : "°C";
const f = v => v == null || Number.isNaN(Number(v)) ? "--" : cv(v).toFixed(1);

function tm(s) {
  return s ? new Date(s).toLocaleString() : "No recent data";
}

function setUnit(fahrenheit) {
  useF = !!fahrenheit;
  $("cBtn")?.classList.toggle("unit-active", !useF);
  $("fBtn")?.classList.toggle("unit-active", useF);
  if (lastData) {
    render(lastData);
    drawAllMiniCharts();
  }
}

async function acknowledge() {
  if (!lastData) return;
  const active = lastData.devices.filter(d => d.alarm && !d.acked);
  if (!active.length) return;
  const who = prompt("Enter your name or initials for the audit acknowledgement:", "Maintenance");
  if (!who || !who.trim()) return;
  await Promise.all(active.map(d =>
    fetch(`/api/ack/${d.logger_sn}`, {
      method: "POST",
      headers: {"Content-Type":"application/json"},
      body: JSON.stringify({acknowledged_by:who.trim()})
    })
  ));
  await loadCurrent();
}

async function loadCurrent() {
  try {
    const r = await fetch("/api/current", {cache: "no-store"});
    if (!r.ok) throw new Error(await r.text());
    lastData = await r.json();
    render(lastData);
    $("conn").innerHTML =
      '<span class="good">● LI-COR CONNECTED</span><br><small>' +
      new Date().toLocaleTimeString() + '</small>';
  } catch (e) {
    $("conn").innerHTML = '<span class="bad">● CONNECTION ERROR</span>';
  }
}

async function loadDashboardHistory(force=false) {
  // Refresh 6-hour graphics every five minutes.
  if (!force && Date.now() - historyLoadedAt < 5 * 60 * 1000) {
    drawAllMiniCharts();
    return;
  }
  try {
    const r = await fetch("/api/dashboard-history", {cache: "no-store"});
    if (!r.ok) throw new Error(await r.text());
    const x = await r.json();
    historyRows = x.data || [];
    historyLoadedAt = Date.now();
    drawAllMiniCharts();
  } catch (e) {
    document.querySelectorAll(".mini-chart-note").forEach(el => {
      el.textContent = "6-hour graph temporarily unavailable";
    });
  }
}

function render(x) {
  const devices = x.devices || [];
  const active = devices.filter(d => d.alarm);

  $("normal").textContent =
    devices.filter(d => !d.alarm && d.comm_status === "ONLINE").length;
  $("alarms").textContent = active.length;
  $("online").textContent =
    devices.filter(d => d.comm_status === "ONLINE").length;
  $("offline").textContent =
    devices.filter(d => d.comm_status !== "ONLINE").length;
  $("suppressed").textContent = devices.filter(d => !d.effective_monitoring_enabled).length;

  $("cards").innerHTML = devices.map(d => {
    const stat = !d.monitoring_enabled ? "NOT IN USE" :
      (d.schedule_enabled && !d.schedule_active_now ? "SCHEDULED OFF" : (d.alarm ? "ALARM" : d.comm_status));
    const cls = !d.effective_monitoring_enabled ? "warn" :
      (d.alarm ? "bad" : d.comm_status === "ONLINE" ? "" : "warn");
    const tempClass = d.alarm ? "reading-temp alarm-reading" : "reading-temp";
    const monitorOnly = d.alarm_type === "monitor";
    const limitLabel = monitorOnly ? "Monitor Only" : (d.alarm_type === "low" ? "Low Limit" : "High Limit");

    return `<article class="instrument graph-card ${d.alarm ? "alarm" : ""} ${d.comm_status !== "ONLINE" ? "stale" : ""} ${!d.effective_monitoring_enabled ? "monitoring-off" : ""}">
      <div class="inst-head">
        <div>
          <h3>${d.name}</h3>
          <div class="serial">Logger ${d.logger_sn}${d.model ? " · " + d.model : ""}</div>
        </div>
        <span class="pill ${cls}">● ${stat}</span>
      </div>

      <div class="live-readings">
        <div class="live-reading temp-reading">
          <div class="reading-icon thermometer">🌡️</div>
          <div>
            <div class="reading-label">Temperature</div>
            <div class="${tempClass}">${f(d.temperature)} ${unit()}</div>
            <div class="reading-limit">${monitorOnly ? ((d.model||"Logger") + " · Temperature + Humidity monitoring") : (limitLabel + ": " + f(d.limit) + " " + unit() + (!d.effective_monitoring_enabled ? " · ALARM SUPPRESSED" : ""))}</div>
            ${Number(d.temperature_offset||0)!==0 ? `<div class="calibration-live">CAL ${Number(d.temperature_offset)>=0?"+":""}${Number(d.temperature_offset).toFixed(1)} °C</div>` : ""}
          </div>
        </div>
        <div class="live-divider"></div>
        <div class="live-reading humidity-reading">
          <div class="reading-icon droplet">💧</div>
          <div>
            <div class="reading-label">Humidity</div>
            <div class="reading-rh">${d.rh == null ? "--" : Number(d.rh).toFixed(1)} %RH</div>
            <div class="reading-limit">Relative Humidity</div>
            ${Number(d.humidity_offset||0)!==0 ? `<div class="calibration-live">CAL ${Number(d.humidity_offset)>=0?"+":""}${Number(d.humidity_offset).toFixed(1)} %RH</div>` : ""}
          </div>
        </div>
      </div>

      <div class="mini-chart-header">
        <span>6-Hour Trend</span>
        <small class="mini-chart-note">Temperature + Humidity</small>
      </div>
      <div class="schedule-strip ${monitorOnly ? "schedule-on" : (d.effective_monitoring_enabled ? "schedule-on" : "schedule-off")}">
        ${monitorOnly ? "Monitoring: Temperature + Humidity · No temperature alarm configured" : ("Alarm: " + (d.effective_monitoring_enabled ? "ACTIVE" : "SUPPRESSED") + " · " + (d.schedule_summary || "Manual mode"))}
      </div>
      <canvas class="mini-chart" id="mini-${d.logger_sn}"></canvas>
      <div class="mini-legend">
        <span><i class="legend-temp"></i>Temperature</span>
        <span><i class="legend-rh"></i>Humidity</span>
        ${monitorOnly ? "" : `<span><i class="legend-limit"></i>${limitLabel}</span>`}
      </div>

      <div class="inst-foot">
        <span>Updated: ${tm(d.timestamp)}</span>
        <span>${d.age_minutes == null ? "No data" : d.age_minutes + " min old"}</span>
      </div>
    </article>`;
  }).join("");

  $("status").innerHTML = devices.map(d => `
    <div class="comm-row">
      <div><b>${d.name}</b><small>Logger ${d.logger_sn}${d.model ? " · " + d.model : ""}</small></div>
      <div>
        <span class="status-pill ${d.comm_status === "ONLINE" ? "good" : d.comm_status === "STALE" ? "warn" : "bad"}">
          ● ${d.comm_status}
        </span>
        <small>Last: ${tm(d.timestamp)} · ${d.age_minutes == null ? "No data" : d.age_minutes + " min old"}</small>
      </div>
    </div>`).join("");

  if (active.length) {
    $("alarmBanner").classList.remove("hidden");
    const allAck = active.every(d => d.acked);
    $("alarmBanner").classList.toggle("acked", allAck);
    $("alarmBanner").querySelector("strong").textContent =
      allAck ? "ALARM ACKNOWLEDGED — CONDITION STILL ACTIVE" : "ACTIVE TEMPERATURE ALARM";
    $("alarmText").innerHTML = active.map(d =>
      `${d.name}: ${f(d.temperature)} ${unit()} · ${d.alarm_type === "low" ? "LOW" : "HIGH"} limit ${f(d.limit)} ${unit()}${d.acked ? " · ACKNOWLEDGED" : ""}`
    ).join("<br>");
  } else {
    $("alarmBanner").classList.add("hidden");
  }

  requestAnimationFrame(drawAllMiniCharts);
}

function rowsForLogger(sn, type) {
  return historyRows
    .filter(q =>
      String(q.logger_sn) === String(sn) &&
      String(q.sensor_measurement_type || "").toLowerCase() === type
    )
    .map(q => ({
      t: new Date(q.timestamp),
      v: Number(q.value)
    }))
    .filter(q => !Number.isNaN(q.t.getTime()) && Number.isFinite(q.v))
    .sort((a,b) => a.t - b.t);
}

function drawAllMiniCharts() {
  if (!lastData) return;
  for (const d of lastData.devices || []) {
    drawMiniChart(d);
  }
}

function drawMiniChart(device) {
  const canvas = document.getElementById("mini-" + device.logger_sn);
  if (!canvas) return;

  const tempRaw = rowsForLogger(device.logger_sn, "temperature");
  const rh = rowsForLogger(device.logger_sn, "rh");
  const temp = tempRaw.map(p => ({t:p.t, v:cv(p.v)}));

  const ctx = canvas.getContext("2d");
  const cssW = Math.max(280, canvas.clientWidth || 420);
  const cssH = 160;
  const dpr = window.devicePixelRatio || 1;
  canvas.width = Math.round(cssW * dpr);
  canvas.height = Math.round(cssH * dpr);
  ctx.setTransform(dpr,0,0,dpr,0,0);
  ctx.clearRect(0,0,cssW,cssH);

  if (!temp.length && !rh.length) {
    ctx.fillStyle = "#8fb2c9";
    ctx.font = "12px Segoe UI";
    ctx.fillText("Waiting for 6-hour trend data…", 14, 34);
    return;
  }

  const pad = {l:38, r:38, t:12, b:27};
  const w = cssW - pad.l - pad.r;
  const h = cssH - pad.t - pad.b;

  const allTimes = [...temp.map(p=>p.t.getTime()), ...rh.map(p=>p.t.getTime())];
  const tMin = Math.min(...allTimes);
  const tMax = Math.max(...allTimes);
  const timeSpan = Math.max(1, tMax - tMin);

  const hasAlarmLimit = device.alarm_type === "high" || device.alarm_type === "low";
  const limit = hasAlarmLimit ? cv(device.limit) : null;
  const tempVals = temp.map(p=>p.v);
  const baseTempVals = tempVals.length ? tempVals : [0, 10];
  let tempMin = hasAlarmLimit ? Math.min(...baseTempVals, limit) : Math.min(...baseTempVals);
  let tempMax = hasAlarmLimit ? Math.max(...baseTempVals, limit) : Math.max(...baseTempVals);
  const margin = Math.max(2, (tempMax-tempMin)*0.18);
  tempMin -= margin;
  tempMax += margin;
  if (tempMax <= tempMin) tempMax = tempMin + 10;

  const x = ms => pad.l + ((ms - tMin) / timeSpan) * w;
  const yT = v => pad.t + h - ((v - tempMin)/(tempMax-tempMin))*h;
  const yRH = v => pad.t + h - (Math.max(0,Math.min(100,v))/100)*h;

  // background
  ctx.fillStyle = "#0f2535";
  ctx.fillRect(pad.l,pad.t,w,h);

  // grid + left temperature labels
  ctx.font = "10px Segoe UI";
  ctx.textBaseline = "middle";
  for (let i=0;i<=4;i++) {
    const yy = pad.t + h*i/4;
    ctx.strokeStyle = "#27475d";
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(pad.l,yy); ctx.lineTo(pad.l+w,yy); ctx.stroke();

    const tv = tempMax - (tempMax-tempMin)*i/4;
    ctx.fillStyle = "#8fb2c9";
    ctx.textAlign = "right";
    ctx.fillText(tv.toFixed(0) + "°", pad.l-5, yy);

    const hv = 100 - 25*i;
    ctx.textAlign = "left";
    ctx.fillText(hv + "%", pad.l+w+5, yy);
  }

  // time labels
  ctx.textAlign = "center";
  ctx.textBaseline = "top";
  for (let i=0;i<=4;i++) {
    const ms = tMin + timeSpan*i/4;
    const xx = x(ms);
    const dt = new Date(ms);
    ctx.fillStyle="#8fb2c9";
    ctx.fillText(dt.toLocaleTimeString([], {hour:"numeric", minute:"2-digit"}), xx, pad.t+h+7);
  }

  // alarm limit (coolers/proofers only; plant ambient logger is monitor-only)
  if (hasAlarmLimit) {
    const ly = yT(limit);
    ctx.save();
    ctx.setLineDash([6,5]);
    ctx.strokeStyle = device.alarm_type === "low" ? "#e17c2f" : "#e34a53";
    ctx.lineWidth = 1.7;
    ctx.beginPath();
    ctx.moveTo(pad.l,ly); ctx.lineTo(pad.l+w,ly); ctx.stroke();
    ctx.restore();
  }

  // temperature area fill
  if (temp.length > 1) {
    ctx.beginPath();
    temp.forEach((p,i) => {
      const xx=x(p.t.getTime()), yy=yT(p.v);
      if (i===0) ctx.moveTo(xx, yy); else ctx.lineTo(xx, yy);
    });
    ctx.lineTo(x(temp[temp.length-1].t.getTime()), pad.t+h);
    ctx.lineTo(x(temp[0].t.getTime()), pad.t+h);
    ctx.closePath();
    ctx.fillStyle = device.alarm ? "rgba(255,93,103,.10)" : "rgba(35,209,139,.10)";
    ctx.fill();

    ctx.beginPath();
    temp.forEach((p,i) => {
      const xx=x(p.t.getTime()), yy=yT(p.v);
      if (i===0) ctx.moveTo(xx,yy); else ctx.lineTo(xx,yy);
    });
    ctx.strokeStyle = device.alarm ? "#ff5d67" : "#23d18b";
    ctx.lineWidth = 2.3;
    ctx.lineJoin = "round";
    ctx.lineCap = "round";
    ctx.stroke();
  }

  // RH line
  if (rh.length > 1) {
    ctx.beginPath();
    rh.forEach((p,i) => {
      const xx=x(p.t.getTime()), yy=yRH(p.v);
      if (i===0) ctx.moveTo(xx,yy); else ctx.lineTo(xx,yy);
    });
    ctx.strokeStyle = "#51b7ff";
    ctx.lineWidth = 2.0;
    ctx.lineJoin = "round";
    ctx.lineCap = "round";
    ctx.stroke();
  }
}

async function initialLoad() {
  await loadCurrent();
  await loadDashboardHistory(true);
}

window.addEventListener("resize", () => {
  clearTimeout(window.__chartResize);
  window.__chartResize = setTimeout(drawAllMiniCharts, 150);
});

initialLoad();
setInterval(loadCurrent, 60000);
setInterval(() => loadDashboardHistory(true), 5 * 60 * 1000);
