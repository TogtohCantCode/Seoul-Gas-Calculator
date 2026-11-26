const blocks = ["morning", "daytime", "evening", "night"];
let useSchedule = false;
let debounceTimer = null;
let latestRecommendations = [];

const el = (id) => document.getElementById(id);

function syncBoilerModeUI() {
  const mode = el("boiler_mode").value;
  el("panel-ondol").style.display = mode === "ondol_water" ? "block" : "none";
  el("panel-away").style.display = mode === "away" ? "block" : "none";
  el("panel-indoor-slider").style.display = mode === "indoor_temp" ? "block" : "none";
  el("mode-switch-wrap").style.display = mode === "indoor_temp" ? "flex" : "none";

  if (mode !== "indoor_temp") {
    el("sec-const").style.display = "block";
    el("sec-sched").style.display = "none";
  } else {
    el("sec-const").style.display = useSchedule ? "none" : "block";
    el("sec-sched").style.display = useSchedule ? "block" : "none";
  }
}

function applyRecommendation(index) {
  const tip = latestRecommendations[index];
  if (!tip || !tip.action_payload) return;
  const p = tip.action_payload;

  if (p.boiler_mode) {
    el("boiler_mode").value = p.boiler_mode;
  }
  if (typeof p.use_schedule === "boolean") {
    useSchedule = p.use_schedule;
    el("btn-sched").classList.toggle("active", useSchedule);
    el("btn-const").classList.toggle("active", !useSchedule);
  }
  if (typeof p.target_temp_c === "number") {
    el("target_temp_c").value = String(p.target_temp_c);
  }
  if (typeof p.ondol_temp_c === "number") {
    el("ondol_temp_c").value = String(p.ondol_temp_c);
  }
  if (p.schedule && typeof p.schedule === "object") {
    for (const b of blocks) {
      if (p.schedule[b]) {
        el("act-" + b).checked = Boolean(p.schedule[b].active);
        el("temp-" + b).value = String(p.schedule[b].temp_c);
      }
    }
  }
  syncBoilerModeUI();
  scheduleUpdate();
}

function renderRecommendations(recs) {
  latestRecommendations = Array.isArray(recs) ? recs : [];
  const list = el("savings-list");
  const badge = el("max-save-badge");

  if (latestRecommendations.length === 0) {
    badge.textContent = "OPTIMIZED";
    list.innerHTML = `<div class="tip-empty">Your settings are already well-tuned for warmth and low gas usage.</div>`;
    return;
  }

  const topSavings = latestRecommendations[0].savings_krw;
  badge.textContent = `SAVE UP TO ₩${Number(topSavings).toLocaleString("ko-KR")}`;

  list.innerHTML = latestRecommendations
    .map(
      (tip, idx) => `
      <div class="tip-card">
        <div class="tip-top">
          <div class="tip-title">${tip.title}</div>
          <span class="tip-save">-₩${Number(tip.savings_krw).toLocaleString("ko-KR")}/cycle</span>
        </div>
        <div class="tip-note">${tip.comfort_note}</div>
        ${
          tip.action_label
            ? `<button type="button" class="tip-apply-btn" data-tip-idx="${idx}">${tip.action_label}</button>`
            : ""
        }
      </div>
    `
    )
    .join("");

  list.querySelectorAll(".tip-apply-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const idx = Number(btn.dataset.tipIdx);
      applyRecommendation(idx);
    });
  });
}

function applyPreset(name) {
  document.querySelectorAll(".chip").forEach((c) => c.classList.toggle("active", c.dataset.preset === name));
  if (name === "oneroom_jan") {
    el("month").value = "jan";
    el("building_type").value = "oneroom";
    el("area_pyeong").value = "8";
    el("household_size").value = "1";
    el("stove_type").value = "induction";
    el("boiler_mode").value = "indoor_temp";
    el("target_temp_c").value = "22";
    el("heating_hours_per_day").value = "12";
    el("is_top_or_corner_unit").checked = true;
    el("is_older_building").checked = false;
  } else if (name === "wfh_balanced") {
    el("month").value = "dec";
    el("building_type").value = "officetel";
    el("area_pyeong").value = "14";
    el("household_size").value = "1";
    el("stove_type").value = "induction";
    el("boiler_mode").value = "ondol_water";
    el("ondol_temp_c").value = "58";
    el("heating_hours_per_day").value = "18";
    el("is_top_or_corner_unit").checked = false;
    el("is_older_building").checked = false;
  } else if (name === "apt_family") {
    el("month").value = "jan";
    el("building_type").value = "apartment";
    el("area_pyeong").value = "26";
    el("household_size").value = "3";
    el("stove_type").value = "gas";
    el("boiler_mode").value = "indoor_temp";
    el("target_temp_c").value = "22.5";
    el("heating_hours_per_day").value = "14";
    el("is_top_or_corner_unit").checked = false;
    el("is_older_building").checked = true;
  }
  syncBoilerModeUI();
  scheduleUpdate();
}

function renderWeatherChart(dailyLedger) {
  const svg = el("temp-chart");
  if (!Array.isArray(dailyLedger) || dailyLedger.length === 0) {
    svg.innerHTML = "";
    return;
  }
  const w = 420, h = 155, padX = 28, padY = 22;
  const innerW = w - padX * 2;
  const innerH = h - padY * 2;

  const temps = dailyLedger.map((d) => Number(d.avg_temp_c));
  const minT = Math.min(-10, Math.floor(Math.min(...temps) - 2));
  const maxT = Math.max(15, Math.ceil(Math.max(...temps) + 2));

  const xFor = (i) =>
    dailyLedger.length === 1 ? padX + innerW / 2 : padX + (i / (dailyLedger.length - 1)) * innerW;
  const yFor = (t) => padY + innerH - ((t - minT) / Math.max(1, maxT - minT)) * innerH;

  const pts = dailyLedger.map((d, i) => ({
    x: xFor(i),
    y: yFor(Number(d.avg_temp_c)),
    d,
  }));

  const linePath = pts.map((p, i) => `${i === 0 ? "M" : "L"} ${p.x.toFixed(1)} ${p.y.toFixed(1)}`).join(" ");
  const areaPath = `${linePath} L ${pts[pts.length - 1].x.toFixed(1)} ${(padY + innerH).toFixed(1)} L ${pts[0].x.toFixed(1)} ${(padY + innerH).toFixed(1)} Z`;

  svg.innerHTML = `
    <defs>
      <linearGradient id="wxGrad" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0%" stop-color="#F6821F" stop-opacity="0.28" />
        <stop offset="100%" stop-color="#F6821F" stop-opacity="0.02" />
      </linearGradient>
    </defs>
    <line x1="${padX}" y1="${padY + innerH}" x2="${w - padX}" y2="${padY + innerH}" stroke="#E2E5E9" stroke-width="1" />
    <path d="${areaPath}" fill="url(#wxGrad)" />
    <path d="${linePath}" fill="none" stroke="#F6821F" stroke-width="2.2" />
    ${pts.map((p) => `<circle cx="${p.x.toFixed(1)}" cy="${p.y.toFixed(1)}" r="3" fill="#F6821F" />`).join("")}
    <text x="${padX}" y="${h - 4}" fill="#6C737A" font-family="monospace" font-size="10">${dailyLedger[0].date}</text>
    <text x="${w - padX}" y="${h - 4}" text-anchor="end" fill="#6C737A" font-family="monospace" font-size="10">${dailyLedger[dailyLedger.length - 1].date}</text>
  `;
}

function renderLedger(dailyLedger) {
  const container = el("daily-ledger");
  if (!Array.isArray(dailyLedger) || dailyLedger.length === 0) {
    container.innerHTML = "";
    return;
  }
  container.innerHTML = [...dailyLedger]
    .reverse()
    .map(
      (row) => `
      <div class="ledger-row">
        <span>${row.date} · <strong>${Number(row.avg_temp_c).toFixed(1)}°C</strong> · ${Number(row.wind_kmh).toFixed(0)}km/h</span>
        <span>${Number(row.total_m3).toFixed(2)} m³ · ~₩${Number(row.approx_usage_krw).toLocaleString("ko-KR")}</span>
      </div>
    `
    )
    .join("");
}

async function runEstimate() {
  const targetTemp = parseFloat(el("target_temp_c").value);
  const ondolTemp = parseFloat(el("ondol_temp_c").value);
  const heatingHours = parseFloat(el("heating_hours_per_day").value);
  const awayMin = parseFloat(el("away_interval_min").value);
  const awayHrs = parseFloat(el("away_cycle_hours").value);

  el("lbl-target-temp").textContent = targetTemp.toFixed(1) + "°C";
  el("lbl-ondol-temp").textContent = ondolTemp.toFixed(0) + "°C";
  el("lbl-hours").textContent = heatingHours + "h / day";
  el("lbl-away-pattern").textContent = `${awayMin}m every ${awayHrs}h`;

  const schedule = {};
  for (const b of blocks) {
    const t = parseFloat(el("temp-" + b).value);
    const active = el("act-" + b).checked;
    el("temp-" + b).disabled = !active;
    el("val-" + b).textContent = active ? t.toFixed(1) + "°C" : "Away";
    schedule[b] = { temp_c: t, active };
  }

  const boilerMode = el("boiler_mode").value;
  const payload = {
    billing_start_date: el("billing_start_date").value,
    billing_cycle_days: el("billing_cycle_days").value,
    month: el("month").value,
    building_type: el("building_type").value,
    area_pyeong: el("area_pyeong").value,
    household_size: el("household_size").value,
    stove_type: el("stove_type").value,
    boiler_mode: boilerMode,
    ondol_temp_c: ondolTemp,
    away_interval_min: awayMin,
    away_cycle_hours: awayHrs,
    is_top_or_corner_unit: el("is_top_or_corner_unit").checked,
    is_older_building: el("is_older_building").checked,
    target_temp_c: targetTemp,
    heating_hours_per_day: heatingHours,
    use_schedule: boilerMode === "indoor_temp" ? useSchedule : false,
    schedule,
    unit_rate_krw_per_mj: el("unit_rate_krw_per_mj").value,
    base_charge_krw: el("base_charge_krw").value,
    avg_calorific_mj_per_m3: el("avg_calorific_mj_per_m3").value,
    correction_factor: el("correction_factor").value,
  };

  const res = await fetch("/api/estimate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    el("wx-badge").textContent = `API ERROR (${res.status})`;
    return;
  }
  const data = await res.json();

  el("ml-badge").textContent = "SLAB + WIND ENGINE ACTIVE";
  const wx = data.weather_tracker;
  const fcTag = wx.forecast_live ? ` + ${wx.live_forecast_days_used}D FCST` : "";
  el("wx-badge").textContent = `${wx.days_tracked}D LIVE${fcTag}`;

  el("hero-total").innerHTML = `₩${Number(data.billing.total_bill_krw).toLocaleString("ko-KR")} <span>/ cycle</span>`;
  el("hero-usage").textContent = `${data.estimated_m3} m³ · ${data.billing.billed_mj} MJ (${wx.days_tracked}d elapsed + ${wx.remaining_days}d est)`;

  el("accrued-summary").innerHTML =
    `Accrued (${wx.billing_start_date} → ${wx.as_of_date}): <strong>₩${Number(wx.accrued_to_date_krw).toLocaleString("ko-KR")}</strong> (<strong>${wx.accrued_to_date_m3} m³</strong>)`;

  el("kv-avg-temp").textContent = `${Number(wx.elapsed_avg_temp_c).toFixed(1)}°C (${Number(wx.elapsed_avg_wind_kmh).toFixed(0)}km/h)`;
  el("kv-hdd").textContent = `${Number(wx.cumulative_hdd).toFixed(1)} (Fcst ${Number(wx.forecast_avg_temp_c).toFixed(1)}°C)`;

  const heatM3 = data.breakdown_m3.space_heating;
  const hwM3 = data.breakdown_m3.hot_water_and_cooking;
  const splitSum = Math.max(0.01, heatM3 + hwM3);
  el("bar-heat").style.width = ((heatM3 / splitSum) * 100).toFixed(1) + "%";
  el("bar-hw").style.width = ((hwM3 / splitSum) * 100).toFixed(1) + "%";
  el("leg-heat").textContent = `Heating: ${heatM3} m³`;
  el("leg-hw").textContent = `Hot Water/Cook: ${hwM3} m³`;

  for (const item of data.comparison.schedule_blocks) {
    const cEl = el("cost-" + item.id);
    if (cEl) cEl.textContent = "₩" + Number(item.monthly_heating_cost_krw).toLocaleString("ko-KR");
  }

  renderRecommendations(data.recommendations);
  renderWeatherChart(wx.daily_ledger);
  renderLedger(wx.daily_ledger);
}

function scheduleUpdate() {
  clearTimeout(debounceTimer);
  debounceTimer = setTimeout(runEstimate, 50);
}

el("boiler_mode").addEventListener("change", () => {
  syncBoilerModeUI();
  scheduleUpdate();
});

el("btn-const").addEventListener("click", () => {
  useSchedule = false;
  el("btn-const").classList.add("active");
  el("btn-sched").classList.remove("active");
  syncBoilerModeUI();
  scheduleUpdate();
});

el("btn-sched").addEventListener("click", () => {
  useSchedule = true;
  el("btn-sched").classList.add("active");
  el("btn-const").classList.remove("active");
  syncBoilerModeUI();
  scheduleUpdate();
});

document.querySelectorAll(".chip").forEach((chip) => {
  chip.addEventListener("click", () => applyPreset(chip.dataset.preset));
});

document.querySelectorAll("input, select").forEach((node) => {
  node.addEventListener("input", scheduleUpdate);
  node.addEventListener("change", scheduleUpdate);
});

syncBoilerModeUI();
runEstimate();