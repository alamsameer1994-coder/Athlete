const fmtDate = (iso) => new Date(iso + "T00:00:00").toLocaleDateString(undefined, { month: "short", day: "numeric" });
const fmtDateTime = (iso) => (iso ? new Date(iso.replace(" ", "T") + "Z").toLocaleString() : "never");
const fmtMinutes = (s) => (s ? Math.round(s / 60) + " min" : "—");
const fmtKm = (m) => (m ? (m / 1000).toFixed(1) + " km" : "—");
const daysUntil = (iso) => Math.ceil((new Date(iso + "T00:00:00") - new Date()) / 86400000);

let fitnessChart = null;
let weightChart = null;

function statTile(label, value, sub, tone) {
  const toneClass = tone ? ` tone-${tone}` : "";
  return `<div class="stat-tile"><div class="label">${label}</div><div class="value${toneClass}">${value}</div>${sub ? `<div class="sub">${sub}</div>` : ""}</div>`;
}

function renderStats(data) {
  const snap = data.fitness.snapshot;
  const tiles = [];

  tiles.push(statTile("Fitness (CTL)", snap.ctl ?? "—"));
  tiles.push(statTile("Fatigue (ATL)", snap.atl ?? "—"));
  const tsbTone = snap.tsb > 5 ? "good" : snap.tsb < -20 ? "bad" : "";
  tiles.push(statTile("Form (TSB)", snap.tsb ?? "—", snap.tsb > 5 ? "Fresh" : snap.tsb < -20 ? "High fatigue" : "Neutral", tsbTone));

  if (data.body_trend && data.body_trend.latest_weight_kg !== undefined) {
    const rate = data.body_trend.weekly_rate_kg;
    tiles.push(statTile("Weight", data.body_trend.latest_weight_kg + " kg", rate !== undefined ? `${rate > 0 ? "+" : ""}${rate} kg/wk` : ""));
  } else {
    tiles.push(statTile("Weight", "—", "Log a weigh-in"));
  }

  if (data.week && data.sessions.length) {
    const completed = data.sessions.filter((s) => s.status === "completed").length;
    tiles.push(statTile("This Week", `${completed}/${data.sessions.length}`, "sessions completed"));
  } else {
    tiles.push(statTile("This Week", "—", "No active plan"));
  }

  if (data.races.length) {
    const r = data.races[0];
    tiles.push(statTile("Next Race", `${daysUntil(r.race_date)}d`, r.name));
  } else {
    tiles.push(statTile("Next Race", "—", "None scheduled"));
  }

  document.getElementById("stats-grid").innerHTML = tiles.join("");
}

function chartsAvailable() {
  return typeof Chart !== "undefined";
}

function renderFitnessChart(series) {
  const ctx = document.getElementById("fitness-chart");
  if (!chartsAvailable()) return;
  const labels = series.map((p) => fmtDate(p.date));
  if (fitnessChart) fitnessChart.destroy();
  fitnessChart = new Chart(ctx, {
    type: "line",
    data: {
      labels,
      datasets: [
        { label: "CTL", data: series.map((p) => p.ctl), borderColor: "#2f6fed", backgroundColor: "transparent", tension: 0.25, pointRadius: 0 },
        { label: "ATL", data: series.map((p) => p.atl), borderColor: "#d98c00", backgroundColor: "transparent", tension: 0.25, pointRadius: 0 },
        { label: "TSB", data: series.map((p) => p.tsb), borderColor: "#1a9c6b", backgroundColor: "transparent", tension: 0.25, pointRadius: 0, yAxisID: "y1" },
      ],
    },
    options: {
      responsive: true,
      interaction: { mode: "index", intersect: false },
      scales: { y: { title: { display: true, text: "CTL / ATL" } }, y1: { position: "right", grid: { drawOnChartArea: false }, title: { display: true, text: "TSB" } } },
      plugins: { legend: { position: "bottom" } },
    },
  });
}

function renderWeightChart(series) {
  const ctx = document.getElementById("weight-chart");
  if (!chartsAvailable()) return;
  const withWeight = series.filter((p) => p.weight_kg !== null && p.weight_kg !== undefined);
  if (weightChart) weightChart.destroy();
  if (!withWeight.length) {
    ctx.getContext("2d").clearRect(0, 0, ctx.width, ctx.height);
    return;
  }
  weightChart = new Chart(ctx, {
    type: "line",
    data: {
      labels: withWeight.map((p) => fmtDate(p.date)),
      datasets: [{ label: "Weight (kg)", data: withWeight.map((p) => p.weight_kg), borderColor: "#2f6fed", backgroundColor: "transparent", tension: 0.25, pointRadius: 2 }],
    },
    options: { responsive: true, plugins: { legend: { display: false } } },
  });
}

function renderPlan(data) {
  const el = document.getElementById("plan-content");
  if (!data.plan) {
    el.innerHTML = `<div class="empty-state">No active plan. Ask your coach to create one with create_plan.</div>`;
    return;
  }
  if (!data.week || !data.sessions.length) {
    el.innerHTML = `<div class="empty-state">Active plan "${data.plan.name}", but no sessions scheduled for this week.</div>`;
    return;
  }
  const rows = data.sessions
    .map(
      (s) => `<tr>
        <td>${fmtDate(s.date)}</td>
        <td>${s.sport}</td>
        <td>${s.title}</td>
        <td>${s.target_value ?? ""} ${s.target_type ?? ""}</td>
        <td><span class="pill status-${s.status}">${s.status}</span></td>
      </tr>`
    )
    .join("");
  el.innerHTML = `<table><thead><tr><th>Date</th><th>Sport</th><th>Session</th><th>Target</th><th>Status</th></tr></thead><tbody>${rows}</tbody></table>`;
}

function renderActivities(data) {
  const el = document.getElementById("activities-content");
  if (!data.activities.length) {
    el.innerHTML = `<div class="empty-state">No activities yet. Click "Sync now".</div>`;
    return;
  }
  const rows = data.activities
    .slice(0, 10)
    .map(
      (a) => `<tr>
        <td>${fmtDate(a.start_time.slice(0, 10))}</td>
        <td>${a.sport}</td>
        <td>${a.name ?? ""}</td>
        <td>${fmtMinutes(a.duration_s)}</td>
        <td>${a.tss ?? "—"}</td>
      </tr>`
    )
    .join("");
  el.innerHTML = `<table><thead><tr><th>Date</th><th>Sport</th><th>Name</th><th>Time</th><th>TSS</th></tr></thead><tbody>${rows}</tbody></table>`;
}

function renderNutrition(data) {
  const el = document.getElementById("nutrition-content");
  const n = data.nutrition_targets;
  if (!n) {
    el.innerHTML = `<div class="empty-state">Set height/age/sex and log a weigh-in to compute targets.</div>`;
    return;
  }
  el.innerHTML = `
    <div class="stat-tile" style="box-shadow:none;border:none;padding:0;">
      <div class="label">Goal: ${n.goal.replace("_", " ")}</div>
      <div class="value">${n.target_calories} kcal/day</div>
      <div class="sub">${n.delta_note} &middot; TDEE ${n.tdee} kcal</div>
    </div>
    <div class="macro-row">
      <div><div class="m-val">${n.protein_g}g</div><div class="m-label">Protein</div></div>
      <div><div class="m-val">${n.carbs_g}g</div><div class="m-label">Carbs</div></div>
      <div><div class="m-val">${n.fat_g}g</div><div class="m-label">Fat</div></div>
    </div>`;
}

function renderStrength(data) {
  const el = document.getElementById("strength-content");
  const s = data.strength;
  if (!s.session_count) {
    el.innerHTML = `<div class="empty-state">No strength sessions logged in the last 28 days.</div>`;
    return;
  }
  el.innerHTML = `
    <div class="stat-tile" style="box-shadow:none;border:none;padding:0;">
      <div class="value">${s.sessions_per_week}/week</div>
      <div class="sub">${s.session_count} sessions &middot; avg ${s.avg_session_minutes} min</div>
    </div>`;
}

function renderRaces(data) {
  const el = document.getElementById("races-content");
  if (!data.races.length) {
    el.innerHTML = `<div class="empty-state">No upcoming races registered.</div>`;
    return;
  }
  const rows = data.races
    .map((r) => `<tr><td>${r.name}</td><td>${r.race_type}</td><td>${fmtDate(r.race_date)}</td><td>${daysUntil(r.race_date)}d</td></tr>`)
    .join("");
  el.innerHTML = `<table><thead><tr><th>Race</th><th>Type</th><th>Date</th><th>Countdown</th></tr></thead><tbody>${rows}</tbody></table>`;
}

async function loadDashboard() {
  const res = await fetch("/api/dashboard");
  const data = await res.json();

  document.getElementById("today-date").textContent = new Date(data.today + "T00:00:00").toLocaleDateString(undefined, {
    weekday: "long", month: "long", day: "numeric",
  });

  const sync = data.sync_status;
  const parts = [];
  parts.push(sync.strava_authorized ? "Strava connected" : "Strava not connected");
  parts.push(sync.garmin_configured ? "Garmin configured" : "Garmin not configured");
  parts.push(`last synced ${fmtDateTime(sync.last_activity_synced_at)}`);
  document.getElementById("sync-status").textContent = parts.join(" · ");

  // Render data panels first — none of these should ever be blocked by a
  // charting failure (missing/broken Chart.js, canvas error, etc).
  renderStats(data);
  renderPlan(data);
  renderActivities(data);
  renderNutrition(data);
  renderStrength(data);
  renderRaces(data);

  try {
    renderFitnessChart(data.fitness.series);
    renderWeightChart(data.body_series);
  } catch (e) {
    console.error("Chart rendering failed:", e);
  }
}

document.getElementById("sync-btn").addEventListener("click", async () => {
  const btn = document.getElementById("sync-btn");
  btn.disabled = true;
  btn.textContent = "Syncing…";
  try {
    const res = await fetch("/api/sync", { method: "POST" });
    const result = await res.json();
    await loadDashboard();
    const errored = [result.strava, result.garmin_activities].some((r) => r && r.error);
    btn.textContent = errored ? "Sync (see status)" : "Sync now";
  } catch (e) {
    btn.textContent = "Sync failed";
  } finally {
    btn.disabled = false;
    setTimeout(() => (btn.textContent = "Sync now"), 3000);
  }
});

loadDashboard();
