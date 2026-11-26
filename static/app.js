const blocks = ["morning", "daytime", "evening", "night"];
let useSchedule = false;
let debounceTimer = null;

const el = (id) => document.getElementById(id);

function applyPreset(name) {
  document.querySelectorAll(".chip").forEach((c) => c.classList.toggle("active", c.dataset.preset === name));
  if (name === "oneroom_jan") {
    el("month").value = "jan";
    el("building_type").value = "oneroom";
    el("area_pyeong").value = "8";
    el("household_size").value = "1";
    el("target_temp_c").value = "22";
    el("heating_hours_per_day").value = "12";
    el("is_top_or_corner_unit").checked = true;
    el("is_older_building").checked = false;
  } else if (name === "wfh_balanced") {
    el("month").value = "dec";
    el("building_type").value = "officetel";
    el("area_pyeong").value = "14";
    el("household_size").value = "1";
    el("target_temp_c").value = "21.5";
    el("heating_hours_per_day").value = "18";
    el("is_top_or_corner_unit").checked = false;
    el("is_older_building").checked = false;
  } else if (name === "apt_family") {
    el("month").value = "jan";
    el("building_type").value = "apartment";
    el("area_pyeong").value = "26";
    el("household_size").value = "3";
    el("target_temp_c").value = "22.5";
    el("heating_hours_per_day").value = "14";
    el("is_top_or_corner_unit").checked = false;
    el("is_older_building").checked = true;
  }
  scheduleUpdate();
}

function renderScheduleChart(scheduleBlocks, constantTemp) {
  const svg = el("temp-chart");
  const w = 420, h = 165, padX = 32, padY = 24;
  const innerW = w - padX * 2;
  const innerH = h - padY * 2;
  const minT = 15, maxT = 30;

  const yForTemp = (t) => padY + innerH - ((t - minT) / (maxT - minT)) * innerH;
  const stepX = innerW / scheduleBlocks.length;

  const pts = scheduleBlocks.map((b, i) => {
    const effectiveT = b.active ? b.temp_c : 15.5;
    const x = padX + i * stepX + stepX / 2;
    const y = yForTemp(effectiveT);
    return { x, y, b };
  });

  const linePath = pts.map((p, i) => `${i === 0 ? "M" : "L"} ${p.x.toFixed(1)} ${p.y.toFixed(1)}`).join(" ");
  const areaPath = `${linePath} L ${pts[pts.length - 1].x.toFixed(1)} ${(padY + innerH).toFixed(1)} L ${pts[0].x.toFixed(1)} ${(padY + innerH).toFixed(1)} Z`;
  const refY = yForTemp(constantTemp).toFixed(1);

  svg.innerHTML = `
    <defs>
      <linearGradient id="cfGrad" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0%" stop-color="#F6821F" stop-opacity="0.28" />
        <stop offset="100%" stop-color="#F6821F" stop-opacity="0.02" />
      </linearGradient>
    </defs>
    <line x1="${padX}" y1="${padY}" x2="${w - padX}" y2="${padY}" stroke="#E2E5E9" stroke-width="1" />
    <line x1="${padX}" y1="${padY + innerH}" x2="${w - padX}" y2="${padY + innerH}" stroke="#E2E5E9" stroke-width="1" />
    <line x1="${padX}" y1="${refY}" x2="${w - padX}" y2="${refY}" stroke="#00112C" stroke-dasharray="4 4" stroke-width="1.25" />
    <text x="${w - padX}" y="${Number(refY) - 5}" text-anchor="end" fill="#4A5056" font-family="monospace" font-size="9.5" font-weight="700">
      Steady ${constantTemp.toFixed(1)}°C
    </text>
    <path d="${areaPath}" fill="url(#cfGrad)" />
    <path d="${linePath}" fill="none" stroke="#F6821F" stroke-width="2.5" stroke-linejoin="round" />
    ${pts.map((p) => `
      <circle cx="${p.x}" cy="${p.y}" r="4.5" fill="${p.b.active ? "#F6821F" : "#94A3B8"}" stroke="#FFFFFF" stroke-width="1.5" />
      <text x="${p.x}" y="${p.y - 9}" text-anchor="middle" fill="#1D1E20" font-family="monospace" font-size="10.5" font-weight="700">
        ${p.b.active ? p.b.temp_c.toFixed(1) + "°" : "Away"}
      </text>
      <text x="${p.x}" y="${h - 5}" text-anchor="middle" fill="#6C737A" font-size="10.5" font-weight="600">
        ${p.b.label}
      </text>
    `).join("")}
  `;
}

async function runEstimate() {
  const targetTemp = parseFloat(el("target_temp_c").value);
  const heatingHours = parseFloat(el("heating_hours_per_day").value);
  el("lbl-target-temp").textContent = targetTemp.toFixed(1) + "°C";
  el("lbl-hours").textContent = heatingHours + "h / day";

  const schedule = {};
  for (const b of blocks) {
    const t = parseFloat(el("temp-" + b).value);
    const active = el("act-" + b).checked;
    el("temp-" + b).disabled = !active;
    el("val-" + b).textContent = active ? t.toFixed(1) + "°C" : "Away";
    schedule[b] = { temp_c: t, active };
  }

  const payload = {
    month: el("month").value,
    building_type: el("building_type").value,
    area_pyeong: el("area_pyeong").value,
    household_size: el("household_size").value,
    is_top_or_corner_unit: el("is_top_or_corner_unit").checked,
    is_older_building: el("is_older_building").checked,
    target_temp_c: targetTemp,
    heating_hours_per_day: heatingHours,
    use_schedule: useSchedule,
    schedule,
    previous_reading_m3: el("previous_reading_m3").value,
    current_reading_m3: el("current_reading_m3").value,
    elapsed_days: el("elapsed_days").value,
  };

  const res = await fetch("/api/estimate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const data = await res.json();

  el("hero-total").innerHTML = `₩${Number(data.billing.total_bill_krw).toLocaleString("ko-KR")} <span>/ mo</span>`;
  el("hero-usage").textContent = `${data.estimated_m3} m³ · ${data.billing.billed_mj} MJ · Seoul ~${data.assumptions.seoul_avg_temp_c}°C`;

  const sub = data.billing.base_charge_krw + data.billing.usage_charge_krw;
  el("kv-sub").textContent = "₩" + Number(sub).toLocaleString("ko-KR");
  el("kv-vat").textContent = "₩" + Number(data.billing.vat_krw).toLocaleString("ko-KR");

  const heatM3 = data.breakdown_m3.space_heating;
  const hwM3 = data.breakdown_m3.hot_water_and_cooking;
  const totalSplit = Math.max(0.01, heatM3 + hwM3);
  el("bar-heat").style.width = ((heatM3 / totalSplit) * 100).toFixed(1) + "%";
  el("bar-hw").style.width = ((hwM3 / totalSplit) * 100).toFixed(1) + "%";
  el("leg-heat").textContent = `Space Heating: ${heatM3} m³`;
  el("leg-hw").textContent = `Hot Water: ${hwM3} m³`;

  for (const item of data.comparison.schedule_blocks) {
    const cEl = el("cost-" + item.id);
    if (cEl) cEl.textContent = "₩" + Number(item.monthly_heating_cost_krw).toLocaleString("ko-KR");
  }

  const savings = data.comparison.schedule_savings_krw;
  const pill = el("delta-pill");
  if (savings >= 0) {
    pill.className = "pill good";
    pill.textContent = `24h Schedule saves ₩${savings.toLocaleString("ko-KR")}/mo vs. steady ${targetTemp.toFixed(1)}°C`;
  } else {
    pill.className = "pill warn";
    pill.textContent = `24h Schedule costs +₩${Math.abs(savings).toLocaleString("ko-KR")}/mo vs. steady ${targetTemp.toFixed(1)}°C`;
  }

  renderScheduleChart(data.comparison.schedule_blocks, targetTemp);
}

function scheduleUpdate() {
  clearTimeout(debounceTimer);
  debounceTimer = setTimeout(runEstimate, 40);
}

el("btn-const").addEventListener("click", () => {
  useSchedule = false;
  el("btn-const").classList.add("active");
  el("btn-sched").classList.remove("active");
  el("sec-const").style.display = "block";
  el("sec-sched").style.display = "none";
  scheduleUpdate();
});

el("btn-sched").addEventListener("click", () => {
  useSchedule = true;
  el("btn-sched").classList.add("active");
  el("btn-const").classList.remove("active");
  el("sec-sched").style.display = "block";
  el("sec-const").style.display = "none";
  scheduleUpdate();
});

document.querySelectorAll(".chip").forEach((chip) => {
  chip.addEventListener("click", () => applyPreset(chip.dataset.preset));
});

document.querySelectorAll("input, select").forEach((node) => {
  node.addEventListener("input", scheduleUpdate);
  node.addEventListener("change", scheduleUpdate);
});

runEstimate();