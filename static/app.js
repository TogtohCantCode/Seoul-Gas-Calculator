const blocks = ["morning", "daytime", "evening", "night"];
const STORAGE_KEY = "kr_gas_saver_v3";

const REGION_DEFAULTS = {
  seoul: { rate: "22.5268", base: "1250" },
  gyeonggi: { rate: "22.6226", base: "1250" },
  incheon: { rate: "22.5084", base: "1000" },
};

const I18N = {
  en: {
    eyebrow: "Capital Area Thermal Physics + 14-Day Live Forecast",
    heroTitle: "Save money on your gas bill while keeping your home warm.",
    heroSub: "Tracks daily temperatures and wind speeds from your billing start date, projects your full-cycle bill, and simulates real KRW savings without freezing.",
    presetOneroom: "Oneroom · Cold",
    presetWfh: "WFH · Ondol 58°C",
    presetApt: "Apartment · 3 Residents",
    step01: "Billing Cycle & Home Profile",
    lblRegion: "Region & Tariff",
    lblStart: "Billing Cycle Start Date",
    lblDays: "Cycle Length (Days)",
    lblMonth: "Fallback Month (Day 15+)",
    lblHousing: "Housing Type",
    lblArea: "Floor Area (평)",
    lblPeople: "Household Size",
    lblOrient: "Sun Exposure & Floor",
    lblStove: "Cooking Stove",
    chkCorner: "Top / Corner Unit",
    chkOlder: "Older Building",
    tariffSummary: "City Gas Tariff & Calorific Override (고지서 단가·열량 보정)",
    step02: "Boiler Mode & Temperature Control",
    lblBoilerMode: "Boiler Control Mode (보일러 난방모드)",
    btnConst: "Constant Temp Slider",
    btnSched: "24h Temp Schedule",
    lblTargetTemp: "Target Indoor Temp (실내온도)",
    lblOndolTemp: "Floor Water Temp (온돌 난방수 온도)",
    lblAwayPattern: "Interval Firing Pattern (예약 가동)",
    lblDuration: "Active Heating Duration",
    projTitle: "Projected Full-Cycle Bill",
    kvTemp: "Elapsed Avg Temp & Wind",
    kvHdd: "Cumulative HDD & Fcst",
    optTitle: "Smart Warmth & Savings Optimizer",
    shareBtn: "Copy Savings Plan",
    linkBtn: "Copy Link",
    copied: "Copied!",
    wxTitle: "Cycle-to-Date Weather & Daily Log",
    waking: "Waking weather & thermal engine...",
    optimized: "OPTIMIZED",
    optEmpty: "Your settings are already well-tuned for warmth and low gas usage.",
  },
  ko: {
    eyebrow: "수도권 난방 열역학 + 14일 실시간 기상 예보",
    heroTitle: "집은 따뜻하게 유지하면서 도시가스 요금을 줄이는 가장 확실한 방법.",
    heroSub: "검침 시작일부터 오늘까지의 기온·풍속과 14일 예보를 반영해 이번 달 예상 요금을 계산하고, 추위 없이 아낄 수 있는 맞춤 절약액을 시뮬레이션합니다.",
    presetOneroom: "원룸 · 한파 모드",
    presetWfh: "재택근무 · 온돌 58°C",
    presetApt: "아파트 · 3인 가구",
    step01: "검침 주기 및 주거 환경 설정",
    lblRegion: "지역 (도시가스 요금)",
    lblStart: "검침 시작일 (당월 기준)",
    lblDays: "청구 주기 (일)",
    lblMonth: "예보 이후 기준 월 (15일+)",
    lblHousing: "주거 형태",
    lblArea: "전용 면적 (평)",
    lblPeople: "거주 인원 (명)",
    lblOrient: "채광 및 층수",
    lblStove: "주방 화구",
    chkCorner: "탑층 / 코너 세대",
    chkOlder: "노후 건물 (단열 취약)",
    tariffSummary: "도시가스 단가 및 평균열량 직접 수정 (고지서 기준)",
    step02: "보일러 난방모드 및 온도 설정",
    lblBoilerMode: "보일러 제어 방식",
    btnConst: "일정 온도 유지",
    btnSched: "24시간 온도 스케줄",
    lblTargetTemp: "희망 실내온도",
    lblOndolTemp: "온돌 난방수 온도",
    lblAwayPattern: "예약 가동 간격",
    lblDuration: "일일 난방 가동 시간",
    projTitle: "이번 주기 예상 도시가스 청구액",
    kvTemp: "경과 평균 기온 · 풍속",
    kvHdd: "누적 난방도일 · 예보",
    optTitle: "따뜻함 유지 + 가스비 절약 시뮬레이터",
    shareBtn: "절약 플랜 복사",
    linkBtn: "링크 복사",
    copied: "복사 완료!",
    wxTitle: "검침일 이후 일별 기온 및 추정 사용량",
    waking: "날씨 및 난방 엔진 연결 중...",
    optimized: "최적화 완료",
    optEmpty: "현재 설정은 따뜻함과 가스 절약 균형이 이미 매우 잘 잡혀 있습니다.",
  },
};

let currentLang = "en";
let useSchedule = false;
let debounceTimer = null;
let inflightController = null;
let latestRecommendations = [];
let latestResponse = null;

const el = (id) => document.getElementById(id);

function setLanguage(lang) {
  currentLang = lang === "ko" ? "ko" : "en";
  el("lang-en").classList.toggle("active", currentLang === "en");
  el("lang-ko").classList.toggle("active", currentLang === "ko");

  const dict = I18N[currentLang];
  document.querySelectorAll("[data-i18n]").forEach((node) => {
    const key = node.dataset.i18n;
    if (dict[key]) node.textContent = dict[key];
  });

  if (latestResponse) {
    renderRecommendations(latestResponse);
  }
  saveStateToBrowser();
}

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

function collectState() {
  const schedule = {};
  for (const b of blocks) {
    schedule[b] = {
      temp_c: parseFloat(el("temp-" + b).value),
      active: el("act-" + b).checked,
    };
  }
  return {
    lang: currentLang,
    region: el("region").value,
    billing_start_date: el("billing_start_date").value,
    billing_cycle_days: el("billing_cycle_days").value,
    month: el("month").value,
    building_type: el("building_type").value,
    area_pyeong: el("area_pyeong").value,
    household_size: el("household_size").value,
    orientation: el("orientation").value,
    stove_type: el("stove_type").value,
    boiler_mode: el("boiler_mode").value,
    ondol_temp_c: parseFloat(el("ondol_temp_c").value),
    away_interval_min: parseFloat(el("away_interval_min").value),
    away_cycle_hours: parseFloat(el("away_cycle_hours").value),
    is_top_or_corner_unit: el("is_top_or_corner_unit").checked,
    is_older_building: el("is_older_building").checked,
    target_temp_c: parseFloat(el("target_temp_c").value),
    heating_hours_per_day: parseFloat(el("heating_hours_per_day").value),
    use_schedule: el("boiler_mode").value === "indoor_temp" ? useSchedule : false,
    schedule,
    unit_rate_krw_per_mj: el("unit_rate_krw_per_mj").value,
    base_charge_krw: el("base_charge_krw").value,
    avg_calorific_mj_per_m3: el("avg_calorific_mj_per_m3").value,
    correction_factor: el("correction_factor").value,
  };
}

function applyState(state) {
  if (!state || typeof state !== "object") return;
  const fields = [
    "region", "billing_start_date", "billing_cycle_days", "month",
    "building_type", "area_pyeong", "household_size", "orientation",
    "stove_type", "boiler_mode", "ondol_temp_c", "away_interval_min",
    "away_cycle_hours", "target_temp_c", "heating_hours_per_day",
    "unit_rate_krw_per_mj", "base_charge_krw", "avg_calorific_mj_per_m3",
    "correction_factor",
  ];
  for (const key of fields) {
    if (state[key] !== undefined && el(key)) {
      el(key).value = String(state[key]);
    }
  }
  if (typeof state.is_top_or_corner_unit === "boolean") {
    el("is_top_or_corner_unit").checked = state.is_top_or_corner_unit;
  }
  if (typeof state.is_older_building === "boolean") {
    el("is_older_building").checked = state.is_older_building;
  }
  if (typeof state.use_schedule === "boolean") {
    useSchedule = state.use_schedule;
    el("btn-sched").classList.toggle("active", useSchedule);
    el("btn-const").classList.toggle("active", !useSchedule);
  }
  if (state.schedule && typeof state.schedule === "object") {
    for (const b of blocks) {
      if (state.schedule[b]) {
        el("act-" + b).checked = Boolean(state.schedule[b].active);
        el("temp-" + b).value = String(state.schedule[b].temp_c);
      }
    }
  }
  if (state.lang) {
    currentLang = state.lang === "ko" ? "ko" : "en";
    el("lang-en").classList.toggle("active", currentLang === "en");
    el("lang-ko").classList.toggle("active", currentLang === "ko");
  }
}

function saveStateToBrowser() {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(collectState()));
  } catch (_) {}
}

function loadInitialState() {
  const params = new URLSearchParams(window.location.search);
  const encoded = params.get("cfg");
  if (encoded) {
    try {
      const decoded = JSON.parse(decodeURIComponent(escape(atob(encoded))));
      applyState(decoded);
      return;
    } catch (_) {}
  }
  try {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (saved) applyState(JSON.parse(saved));
  } catch (_) {}
}

function applyRecommendation(index) {
  const tip = latestRecommendations[index];
  if (!tip || !tip.action_payload) return;
  const p = tip.action_payload;

  if (p.boiler_mode) el("boiler_mode").value = p.boiler_mode;
  if (typeof p.use_schedule === "boolean") {
    useSchedule = p.use_schedule;
    el("btn-sched").classList.toggle("active", useSchedule);
    el("btn-const").classList.toggle("active", !useSchedule);
  }
  if (typeof p.target_temp_c === "number") el("target_temp_c").value = String(p.target_temp_c);
  if (typeof p.ondol_temp_c === "number") el("ondol_temp_c").value = String(p.ondol_temp_c);
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

function renderRecommendations(data) {
  const recs = Array.isArray(data.recommendations) ? data.recommendations : [];
  latestRecommendations = recs;
  const list = el("savings-list");
  const badge = el("max-save-badge");
  const banner = el("combined-banner");
  const alertBox = el("shock-alert");
  const dict = I18N[currentLang];

  if (data.shock_alert) {
    alertBox.style.display = "block";
    alertBox.textContent = currentLang === "ko" ? data.shock_alert.ko : data.shock_alert.en;
  } else {
    alertBox.style.display = "none";
  }

  if (recs.length === 0) {
    badge.textContent = dict.optimized;
    banner.style.display = "none";
    list.innerHTML = `<div class="tip-empty">${dict.optEmpty}</div>`;
    return;
  }

  const combined = Number(data.combined_savings_krw || recs[0].savings_krw);
  badge.textContent =
    currentLang === "ko"
      ? `최대 ₩${combined.toLocaleString("ko-KR")} 절약`
      : `SAVE UP TO ₩${combined.toLocaleString("ko-KR")}`;

  banner.style.display = "block";
  banner.innerHTML =
    currentLang === "ko"
      ? `아래 절약 팁을 함께 적용하면 이번 주기 예상 요금을 최대 <strong>₩${combined.toLocaleString("ko-KR")}</strong> 줄일 수 있습니다.`
      : `Combining these warmth-safe adjustments can cut up to <strong>₩${combined.toLocaleString("ko-KR")}</strong> off this cycle.`;

  list.innerHTML = recs
    .map((tip, idx) => {
      const title = currentLang === "ko" ? tip.title_ko : tip.title_en;
      const note = currentLang === "ko" ? tip.note_ko : tip.note_en;
      const btnLabel = currentLang === "ko" ? tip.label_ko : tip.label_en;
      const cycleUnit = currentLang === "ko" ? "/주기" : "/cycle";
      return `
      <div class="tip-card">
        <div class="tip-top">
          <div class="tip-title">${title}</div>
          <span class="tip-save">-₩${Number(tip.savings_krw).toLocaleString("ko-KR")}${cycleUnit}</span>
        </div>
        <div class="tip-note">${note}</div>
        ${btnLabel ? `<button type="button" class="tip-apply-btn" data-tip-idx="${idx}">${btnLabel}</button>` : ""}
      </div>
    `;
    })
    .join("");

  list.querySelectorAll(".tip-apply-btn").forEach((btn) => {
    btn.addEventListener("click", () => applyRecommendation(Number(btn.dataset.tipIdx)));
  });
}

function applyPreset(name) {
  document.querySelectorAll(".chip").forEach((c) => c.classList.toggle("active", c.dataset.preset === name));
  if (name === "oneroom_jan") {
    el("month").value = "jan";
    el("building_type").value = "oneroom";
    el("area_pyeong").value = "8";
    el("household_size").value = "1";
    el("orientation").value = "east_west";
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
    el("orientation").value = "south";
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
    el("orientation").value = "south";
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
  el("lbl-hours").textContent = heatingHours + (currentLang === "ko" ? "시간 / 일" : "h / day");
  el("lbl-away-pattern").textContent =
    currentLang === "ko" ? `${awayHrs}시간마다 ${awayMin}분` : `${awayMin}m every ${awayHrs}h`;

  for (const b of blocks) {
    const t = parseFloat(el("temp-" + b).value);
    const active = el("act-" + b).checked;
    el("temp-" + b).disabled = !active;
    el("val-" + b).textContent = active ? t.toFixed(1) + "°C" : currentLang === "ko" ? "외출" : "Away";
  }

  const payload = collectState();
  saveStateToBrowser();

  if (inflightController) inflightController.abort();
  inflightController = new AbortController();

  let res;
  try {
    res = await fetch("/api/estimate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      signal: inflightController.signal,
    });
  } catch (err) {
    if (err && err.name === "AbortError") return;
    el("wx-badge").textContent = "RETRYING...";
    return;
  }

  if (!res.ok) {
    el("wx-badge").textContent = `API ERROR (${res.status})`;
    return;
  }

  const data = await res.json();
  latestResponse = data;
  el("wakeup-banner").style.display = "none";

  const wx = data.weather_tracker;
  const fcTag = wx.forecast_live ? ` + ${wx.live_forecast_days_used}D FCST` : "";
  el("wx-badge").textContent = `${data.region.toUpperCase()} · ${wx.days_tracked}D${fcTag}`;

  const cycleUnit = currentLang === "ko" ? "/ 주기" : "/ cycle";
  el("hero-total").innerHTML = `₩${Number(data.billing.total_bill_krw).toLocaleString("ko-KR")} <span>${cycleUnit}</span>`;
  el("hero-usage").textContent =
    currentLang === "ko"
      ? `${data.estimated_m3} m³ · ${data.billing.billed_mj} MJ (경과 ${wx.days_tracked}일 + 잔여 ${wx.remaining_days}일)`
      : `${data.estimated_m3} m³ · ${data.billing.billed_mj} MJ (${wx.days_tracked}d elapsed + ${wx.remaining_days}d est)`;

  el("accrued-summary").innerHTML =
    currentLang === "ko"
      ? `오늘까지 누적 (${wx.billing_start_date} → ${wx.as_of_date}): <strong>₩${Number(wx.accrued_to_date_krw).toLocaleString("ko-KR")}</strong> (<strong>${wx.accrued_to_date_m3} m³</strong>)`
      : `Accrued (${wx.billing_start_date} → ${wx.as_of_date}): <strong>₩${Number(wx.accrued_to_date_krw).toLocaleString("ko-KR")}</strong> (<strong>${wx.accrued_to_date_m3} m³</strong>)`;

  el("kv-avg-temp").textContent = `${Number(wx.elapsed_avg_temp_c).toFixed(1)}°C (${Number(wx.elapsed_avg_wind_kmh).toFixed(0)}km/h)`;
  el("kv-hdd").textContent = `${Number(wx.cumulative_hdd).toFixed(1)} (Fcst ${Number(wx.forecast_avg_temp_c).toFixed(1)}°C)`;

  const heatM3 = data.breakdown_m3.space_heating;
  const hwM3 = data.breakdown_m3.hot_water_and_cooking;
  const splitSum = Math.max(0.01, heatM3 + hwM3);
  el("bar-heat").style.width = ((heatM3 / splitSum) * 100).toFixed(1) + "%";
  el("bar-hw").style.width = ((hwM3 / splitSum) * 100).toFixed(1) + "%";
  el("leg-heat").textContent = currentLang === "ko" ? `난방: ${heatM3} m³` : `Heating: ${heatM3} m³`;
  el("leg-hw").textContent = currentLang === "ko" ? `온수·취사: ${hwM3} m³` : `Hot Water/Cook: ${hwM3} m³`;

  for (const item of data.comparison.schedule_blocks) {
    const cEl = el("cost-" + item.id);
    if (cEl) cEl.textContent = "₩" + Number(item.monthly_heating_cost_krw).toLocaleString("ko-KR");
  }

  renderRecommendations(data);
  renderWeatherChart(wx.daily_ledger);
  renderLedger(wx.daily_ledger);
}

function scheduleUpdate() {
  clearTimeout(debounceTimer);
  debounceTimer = setTimeout(runEstimate, 220);
}

el("region").addEventListener("change", () => {
  const reg = REGION_DEFAULTS[el("region").value];
  if (reg) {
    el("unit_rate_krw_per_mj").value = reg.rate;
    el("base_charge_krw").value = reg.base;
  }
  scheduleUpdate();
});

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

el("lang-en").addEventListener("click", () => setLanguage("en"));
el("lang-ko").addEventListener("click", () => setLanguage("ko"));

el("btn-copy-plan").addEventListener("click", async () => {
  if (!latestResponse) return;
  const bill = Number(latestResponse.billing.total_bill_krw).toLocaleString("ko-KR");
  const save = Number(latestResponse.combined_savings_krw || 0).toLocaleString("ko-KR");
  const topTip = latestRecommendations[0];
  const tipText = topTip ? (currentLang === "ko" ? topTip.title_ko : topTip.title_en) : "";
  const summary =
    currentLang === "ko"
      ? `[도시가스 절약 계산기] 이번 달 예상 요금: ₩${bill} (${latestResponse.estimated_m3}m³)\n💡 추천 절약법: ${tipText} (최대 ₩${save} 절약)\n👉 ${window.location.origin}`
      : `[Korea Gas Bill Saver] Projected bill: ₩${bill} (${latestResponse.estimated_m3} m³)\n💡 Top warmth-safe tip: ${tipText} (Save up to ₩${save})\n👉 ${window.location.origin}`;
  try {
    await navigator.clipboard.writeText(summary);
    el("btn-copy-plan").textContent = I18N[currentLang].copied;
    setTimeout(() => (el("btn-copy-plan").textContent = I18N[currentLang].shareBtn), 1800);
  } catch (_) {}
});

el("btn-copy-link").addEventListener("click", async () => {
  try {
    const encoded = btoa(unescape(encodeURIComponent(JSON.stringify(collectState()))));
    const shareUrl = `${window.location.origin}/?cfg=${encodeURIComponent(encoded)}`;
    await navigator.clipboard.writeText(shareUrl);
    el("btn-copy-link").textContent = I18N[currentLang].copied;
    setTimeout(() => (el("btn-copy-link").textContent = I18N[currentLang].linkBtn), 1800);
  } catch (_) {}
});

document.querySelectorAll(".chip").forEach((chip) => {
  chip.addEventListener("click", () => applyPreset(chip.dataset.preset));
});

document.querySelectorAll("input, select").forEach((node) => {
  node.addEventListener("input", scheduleUpdate);
  node.addEventListener("change", scheduleUpdate);
});

loadInitialState();
setLanguage(currentLang);
syncBoilerModeUI();
runEstimate();