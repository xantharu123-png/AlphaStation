"use strict";

const byId = (id) => document.getElementById(id);
const escapeHtml = (value) => String(value ?? "")
  .replaceAll("&", "&amp;")
  .replaceAll("<", "&lt;")
  .replaceAll(">", "&gt;")
  .replaceAll('"', "&quot;")
  .replaceAll("'", "&#39;");
const numeric = (value, fallback = 0) => {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
};
const statusClass = (status) => {
  const value = String(status || "error").toLowerCase();
  return new Set(["ok", "disabled", "error", "stale", "empty", "building"])
    .has(value) ? value : "error";
};
const badge = (status) => {
  const safe = statusClass(status);
  return `<span class="badge b-${safe}">${escapeHtml(safe)}</span>`;
};
const fmtUsd = (value) => value == null ? "–" :
  `${numeric(value) >= 0 ? "+" : "−"}${Math.abs(numeric(value)).toLocaleString(
    "de-DE", { maximumFractionDigits: 1 }
  )} M`;

function renderEtf(section) {
  const rows = (section.rows || []).map((row) => `<tr>
    <td>${escapeHtml(row.date)}</td>
    <td class="num ${numeric(row.ibit_musd) >= 0 ? "pos" : "neg"}">${
      row.ibit_musd == null ? "–" : fmtUsd(row.ibit_musd)
    }</td>
    <td class="num ${numeric(row.total_musd) >= 0 ? "pos" : "neg"}">${fmtUsd(row.total_musd)}</td>
  </tr>`).join("");
  byId("sec-etf").innerHTML = `<h2>📈 BTC-ETF-Flows ${badge(section.status)}</h2>
    ${section.status === "ok" ? `<table><tr><th>Datum</th><th class="num">IBIT (BlackRock)</th>
      <th class="num">Alle ETFs</th></tr>${rows}</table>` :
      `<div class="err">${escapeHtml(section.error || "keine Daten")}</div>`}
    <div class="note">${escapeHtml(section.note || "")} · Quelle: ${escapeHtml(section.source || "—")}</div>`;
}

function renderWaves(section) {
  const rows = (section.waves || []).map((wave) => `<tr class="${wave.wave ? "wave" : ""}">
    <td>${escapeHtml(wave.label || wave.symbol)}</td><td>${escapeHtml(wave.symbol)}</td>
    <td class="num">${numeric(wave.rvol).toLocaleString("de-DE")}×</td>
    <td class="num">${numeric(wave.dollar_volume_musd).toLocaleString("de-DE")} M$</td>
    <td>${wave.wave ? "🌊 Welle" : ""}</td></tr>`).join("");
  byId("sec-waves").innerHTML = `<h2>🌊 Volumen-Wellen (Makro + Krypto) ${badge(section.status)}</h2>
    ${rows ? `<table><tr><th>Instrument</th><th>Symbol</th><th class="num">RVOL</th>
      <th class="num">$-Volumen</th><th></th></tr>${rows}</table>` :
      `<div class="err">${escapeHtml(section.error || section.note || "keine Daten")}</div>`}
    <div class="note">${escapeHtml(section.note || "")}</div>`;
}

function renderClusters(section) {
  const rows = (section.clusters || []).map((cluster) => {
    const buying = cluster.side === "buy";
    const names = (cluster.names || []).map(escapeHtml).join(", ");
    return `<tr><td><b>${escapeHtml(cluster.symbol)}</b></td>
      <td class="${buying ? "pos" : "neg"}">${buying ? "🟢 KAUF-CLUSTER" : "🔴 VERKAUF-CLUSTER"}</td>
      <td class="num"><b>${numeric(cluster.insiders)}</b> Insider</td>
      <td class="num">${(numeric(cluster.total_value_usd) / 1000).toLocaleString("de-DE", {maximumFractionDigits: 0})}k $</td>
      <td class="note">${names}</td></tr>`;
  }).join("");
  const status = section.building ? "building" : section.status;
  byId("sec-clusters").innerHTML = `<h2>🧩 Insider-Cluster ${badge(status)}</h2>
    ${rows ? `<table><tr><th>Firma</th><th>Richtung</th><th class="num">Breite</th>
      <th class="num">Summe</th><th>Namen</th></tr>${rows}</table>` :
      `<div class="${section.status === "error" ? "err" : "note"}">${
        escapeHtml(section.error || "Noch keine Cluster im Fenster erkannt")
      }</div>`}<div class="note">${escapeHtml(section.note || "")}</div>`;
}

function renderInsider(section) {
  const rows = (section.trades || []).map((trade) => {
    const buying = trade.kind === "buy";
    return `<tr><td><b>${escapeHtml(trade.ticker || "?")}</b></td>
      <td>${escapeHtml(trade.insider || "?")}${trade.title ? ` <span class="note">(${escapeHtml(trade.title)})</span>` : ""}</td>
      <td class="${buying ? "pos" : "neg"}">${buying ? "🟢 KAUF" : "🔴 VERKAUF"}</td>
      <td class="num">${(numeric(trade.value_usd) / 1000).toLocaleString("de-DE", {maximumFractionDigits: 0})}k $</td>
      <td>${escapeHtml(trade.date || "–")}</td></tr>`;
  }).join("");
  byId("sec-insider").innerHTML = `<h2>🕵️ Insider-Trades (SEC Form 4) ${badge(section.status)}</h2>
    ${rows ? `<table><tr><th>Ticker</th><th>Insider</th><th>Richtung</th>
      <th class="num">Wert</th><th>Datum</th></tr>${rows}</table>` :
      `<div class="${section.status === "empty" ? "note" : "err"}">${
        escapeHtml(section.error || "Keine Deals ≥ $100k in den letzten Filings")
      }</div>`}<div class="note">${escapeHtml(section.note || "")}</div>`;
}

function renderStockWaves(section) {
  const rows = (section.waves || []).map((wave) => `<tr class="${wave.wave ? "wave" : ""}">
    <td><b>${escapeHtml(wave.ticker)}</b></td>
    <td class="num ${wave.direction === "up" ? "pos" : "neg"}">${wave.change_pct == null ? "–" :
      `${numeric(wave.change_pct) > 0 ? "+" : ""}${numeric(wave.change_pct).toLocaleString("de-DE")}%`}</td>
    <td class="num">${wave.rvol == null ? "–" : `${numeric(wave.rvol)}×`}</td>
    <td class="num">${numeric(wave.dollar_volume_musd).toLocaleString("de-DE")} M$</td>
    <td>${wave.wave ? "🌊 Welle" : ""}</td></tr>`).join("");
  const status = section.building ? "building" : section.status;
  byId("sec-stock").innerHTML = `<h2>🏦 Monster-Volumen Aktien ${badge(status)}</h2>
    ${rows ? `<table><tr><th>Ticker</th><th class="num">Heute</th><th class="num">RVOL</th>
      <th class="num">$-Volumen</th><th></th></tr>${rows}</table>` :
      `<div class="err">${escapeHtml(section.error || section.note || "keine Daten")}</div>`}
    <div class="note">${escapeHtml(section.note || "")}${section.data_date ? ` · Daten vom ${escapeHtml(section.data_date)}` : ""}</div>`;
}

const transferKinds = {
  exchange_inflow: ["→ Börse (Verkaufsdruck)", "k-in"],
  exchange_outflow: ["← Börse (Akkumulation)", "k-out"],
  wallet_to_wallet: ["Wallet → Wallet", "k-w2w"],
};

function renderWhales(section) {
  const rows = (section.transactions || []).map((transaction) => {
    const [label, cssClass] = transferKinds[transaction.kind] || transferKinds.wallet_to_wallet;
    const timestamp = numeric(transaction.timestamp, NaN);
    return `<tr><td>${escapeHtml(String(transaction.symbol || "?").toUpperCase())}</td>
      <td class="num">${transaction.amount_usd ? `${(numeric(transaction.amount_usd) / 1e6).toLocaleString("de-DE", {maximumFractionDigits: 1})} M$` : "–"}</td>
      <td class="${cssClass}">${label}</td><td>${Number.isFinite(timestamp) ?
        escapeHtml(new Date(timestamp * 1000).toLocaleString("de-DE")) : "–"}</td></tr>`;
  }).join("");
  byId("sec-whales").innerHTML = `<h2>🐳 Whale-Transfers ${badge(section.status)}</h2>
    ${rows ? `<table><tr><th>Asset</th><th class="num">Größe</th><th>Richtung</th><th>Zeit</th></tr>${rows}</table>` :
      `<div class="${section.status === "disabled" ? "note" : "err"}">${
        escapeHtml(section.note || section.error || "keine Daten")
      }</div>`}<div class="note">${escapeHtml(section.note || "")}</div>`;
}

async function loadRadar(refresh) {
  byId("reload").disabled = true;
  try {
    const response = await fetch(`/api/smart-money-radar${refresh ? "?refresh=1" : ""}`);
    if (!response.ok) throw new Error("radar_unavailable");
    const data = await response.json();
    byId("generated").textContent = `Stand: ${data.generated_at || "?"} · Cache: ${data.cache || "?"}`;
    const sections = data.sections || {};
    renderEtf(sections.etf_flows || {status: "error", error: "fehlt"});
    renderClusters(sections.insider_clusters || {status: "error", error: "fehlt"});
    renderStockWaves(sections.stock_waves || {status: "error", error: "fehlt"});
    renderInsider(sections.insider_trades || {status: "error", error: "fehlt"});
    renderWaves(sections.volume_waves || {status: "error", error: "fehlt"});
    renderWhales(sections.whale_alerts || {status: "error", error: "fehlt"});
  } catch (_error) {
    byId("generated").textContent = "Radar derzeit nicht verfügbar.";
  } finally {
    byId("reload").disabled = false;
  }
}

byId("reload").addEventListener("click", () => loadRadar(true));
loadRadar(false);
