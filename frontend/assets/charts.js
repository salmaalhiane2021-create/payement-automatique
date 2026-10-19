/*
 * charts.js — graphiques SVG maison (aucune bibliothèque externe).
 *
 * Le prototype doit fonctionner hors ligne : plutôt que d'importer une
 * bibliothèque de graphiques depuis un CDN, on génère du SVG à la main.
 * Quatre visualisations sont fournies : anneau (répartition), barres
 * (7 derniers jours), courbe (évolution du solde) et jauge (score de risque).
 */
(function (global) {
  "use strict";

  const PALETTE = [
    "#4f7dff", "#35e0b0", "#a56bff", "#ffb340", "#ff5f6d",
    "#39c5ff", "#f7d04b", "#7dea9b", "#ff8fb1", "#8ea3ff",
  ];

  const Fmt = global.Fmt;

  /* ------------------------------------------------------------------ */
  /* Anneau — répartition des dépenses par catégorie                    */
  /* ------------------------------------------------------------------ */
  function donut(container, rows, options = {}) {
    const size = options.size || 190;
    const stroke = options.stroke || 24;
    const total = rows.reduce((sum, row) => sum + Number(row.total || 0), 0);
    const radius = (size - stroke) / 2;
    const circumference = 2 * Math.PI * radius;
    const center = size / 2;

    if (!total) {
      container.innerHTML = '<div class="empty">Aucune dépense sur la période.</div>';
      return;
    }

    let offset = 0;
    const segments = rows
      .slice(0, 8)
      .map((row, index) => {
        const share = Number(row.total) / total;
        const length = share * circumference;
        const segment = `
          <circle cx="${center}" cy="${center}" r="${radius}"
                  fill="none" stroke="${PALETTE[index % PALETTE.length]}"
                  stroke-width="${stroke}" stroke-linecap="butt"
                  stroke-dasharray="${length} ${circumference - length}"
                  stroke-dashoffset="${-offset}"
                  transform="rotate(-90 ${center} ${center})">
            <title>${Fmt.escape(row.category)} : ${Fmt.money(row.total)} (${Fmt.percent(share, 1)})</title>
          </circle>`;
        offset += length;
        return segment;
      })
      .join("");

    const label = options.label || "Dépenses";
    container.innerHTML = `
      <div class="donut-wrap">
        <svg class="chart" viewBox="0 0 ${size} ${size}" width="${size}" height="${size}" role="img"
             aria-label="Répartition des dépenses par catégorie">
          <circle cx="${center}" cy="${center}" r="${radius}" fill="none" stroke="#1b2743" stroke-width="${stroke}"></circle>
          ${segments}
          <text x="${center}" y="${center - 4}" text-anchor="middle" fill="#93a2c4" font-size="11"
                font-family="Segoe UI, system-ui" letter-spacing="0.06em">${Fmt.escape(label.toUpperCase())}</text>
          <text x="${center}" y="${center + 20}" text-anchor="middle" fill="#eaf0ff" font-size="19" font-weight="700"
                font-family="Segoe UI, system-ui">${Fmt.money(total)}</text>
        </svg>
        <div class="legend">
          ${rows
            .slice(0, 8)
            .map(
              (row, index) => `
            <div class="legend-item">
              <span class="swatch" style="background:${PALETTE[index % PALETTE.length]}"></span>
              <span>${Fmt.escape(row.category)}</span>
              <span class="value">${Fmt.money(row.total)}</span>
              <span class="share">${Fmt.percent(row.share)}</span>
            </div>`
            )
            .join("")}
        </div>
      </div>`;
  }

  /* ------------------------------------------------------------------ */
  /* Barres — débits des derniers jours                                 */
  /* ------------------------------------------------------------------ */
  function bars(container, rows, options = {}) {
    const height = options.height || 150;
    const gap = 10;
    const width = Math.max(container.clientWidth || 520, 320);
    const maxValue = Math.max(1, ...rows.map((row) => Number(row.debit || 0)));
    const barWidth = (width - gap * (rows.length - 1)) / Math.max(1, rows.length);
    const labelHeight = 22;

    if (!rows.length) {
      container.innerHTML = '<div class="empty">Pas encore d\'opération enregistrée.</div>';
      return;
    }

    const bars = rows
      .map((row, index) => {
        const value = Number(row.debit || 0);
        const barHeight = Math.max(3, ((height - labelHeight - 18) * value) / maxValue);
        const x = index * (barWidth + gap);
        const y = height - labelHeight - barHeight;
        const day = new Intl.DateTimeFormat("fr-FR", { weekday: "short" }).format(new Date(`${row.date}T12:00:00`));
        const dayNumber = row.date.slice(8, 10);
        return `
          <g>
            <rect x="${x}" y="${y}" width="${barWidth}" height="${barHeight}" rx="6"
                  fill="url(#barGradient)">
              <title>${day} ${dayNumber} : ${Fmt.money(value)}</title>
            </rect>
            <text x="${x + barWidth / 2}" y="${height - 6}" text-anchor="middle" fill="#6c7ba0"
                  font-size="11" font-family="Segoe UI, system-ui">${day} ${dayNumber}</text>
            ${
              value > 0
                ? `<text x="${x + barWidth / 2}" y="${y - 6}" text-anchor="middle" fill="#93a2c4" font-size="10"
                         font-family="Segoe UI, system-ui">${value >= 1000 ? Math.round(value) : value.toFixed(0)}</text>`
                : ""
            }
          </g>`;
      })
      .join("");

    container.innerHTML = `
      <svg class="chart" viewBox="0 0 ${width} ${height}" height="${height}" preserveAspectRatio="none"
           role="img" aria-label="Dépenses des 7 derniers jours">
        <defs>
          <linearGradient id="barGradient" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stop-color="#6f5bff"></stop>
            <stop offset="100%" stop-color="#4f7dff"></stop>
          </linearGradient>
        </defs>
        ${bars}
      </svg>`;
  }

  /* ------------------------------------------------------------------ */
  /* Courbe — évolution du solde                                        */
  /* ------------------------------------------------------------------ */
  function line(container, values, options = {}) {
    const height = options.height || 130;
    const width = Math.max(container.clientWidth || 620, 320);
    const padding = 8;

    if (!values || values.length < 2) {
      container.innerHTML = '<div class="empty">Pas assez de données pour tracer la courbe.</div>';
      return;
    }

    const min = Math.min(...values);
    const max = Math.max(...values);
    const span = max - min || 1;
    const stepX = (width - padding * 2) / (values.length - 1);
    const points = values.map((value, index) => {
      const x = padding + index * stepX;
      const y = padding + (height - padding * 2) * (1 - (value - min) / span);
      return [x, y];
    });

    const path = points.map(([x, y], index) => `${index === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`).join(" ");
    const area = `${path} L${points[points.length - 1][0].toFixed(1)},${height - padding} L${padding},${height - padding} Z`;

    container.innerHTML = `
      <svg class="chart" viewBox="0 0 ${width} ${height}" height="${height}" preserveAspectRatio="none"
           role="img" aria-label="Évolution du solde sur 30 jours">
        <defs>
          <linearGradient id="areaGradient" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stop-color="rgba(79,125,255,0.42)"></stop>
            <stop offset="100%" stop-color="rgba(79,125,255,0)"></stop>
          </linearGradient>
        </defs>
        <path d="${area}" fill="url(#areaGradient)"></path>
        <path d="${path}" fill="none" stroke="#7ea2ff" stroke-width="2.2" stroke-linejoin="round"></path>
        <circle cx="${points[points.length - 1][0].toFixed(1)}" cy="${points[points.length - 1][1].toFixed(1)}" r="4"
                fill="#35e0b0"></circle>
        <text x="${padding}" y="${padding + 10}" fill="#6c7ba0" font-size="10"
              font-family="Segoe UI, system-ui">max ${Fmt.money(max)}</text>
        <text x="${padding}" y="${height - 2}" fill="#6c7ba0" font-size="10"
              font-family="Segoe UI, system-ui">min ${Fmt.money(min)}</text>
      </svg>`;
  }

  /* ------------------------------------------------------------------ */
  /* Jauge — score de risque                                            */
  /* ------------------------------------------------------------------ */
  function gauge(container, score, decision) {
    const value = Math.max(0, Math.min(100, Number(score || 0)));
    const size = 150;
    const stroke = 13;
    const radius = (size - stroke) / 2;
    const circumference = Math.PI * radius; // demi-cercle
    const filled = (value / 100) * circumference;
    const colors = { accept: "#35e0b0", verify: "#ffb340", block: "#ff5f6d" };
    const color = colors[decision] || colors.accept;

    container.innerHTML = `
      <svg class="chart" viewBox="0 0 ${size} ${size / 1.75}" width="${size}" height="${size / 1.75}"
           role="img" aria-label="Score de risque ${value} sur 100">
        <path d="M ${stroke / 2} ${size / 2} A ${radius} ${radius} 0 0 1 ${size - stroke / 2} ${size / 2}"
              fill="none" stroke="#1b2743" stroke-width="${stroke}" stroke-linecap="round"></path>
        <path d="M ${stroke / 2} ${size / 2} A ${radius} ${radius} 0 0 1 ${size - stroke / 2} ${size / 2}"
              fill="none" stroke="${color}" stroke-width="${stroke}" stroke-linecap="round"
              stroke-dasharray="${filled} ${circumference}"></path>
        <text x="${size / 2}" y="${size / 2 - 12}" text-anchor="middle" fill="${color}" font-size="30"
              font-weight="800" font-family="Segoe UI, system-ui">${value.toFixed(0)}</text>
        <text x="${size / 2}" y="${size / 2 + 4}" text-anchor="middle" fill="#93a2c4" font-size="10"
              letter-spacing="0.08em" font-family="Segoe UI, system-ui">SCORE / 100</text>
      </svg>`;
  }

  /* ------------------------------------------------------------------ */
  /* Barres horizontales — profil horaire                               */
  /* ------------------------------------------------------------------ */
  function hourProfile(container, distribution) {
    if (!distribution || distribution.length !== 24) {
      container.innerHTML = '<div class="empty">Profil horaire indisponible.</div>';
      return;
    }
    const max = Math.max(...distribution, 1e-6);
    const peak = distribution.indexOf(max);
    const bars = distribution
      .map((value, hour) => {
        const height = Math.max(3, Math.round((value / max) * 88));
        return `<div class="bar ${hour === peak ? "is-peak" : ""}" style="height:${height}px"
                     title="${hour}h : ${(value * 100).toFixed(1)} % de vos opérations"></div>`;
      })
      .join("");
    const labels = distribution
      .map((_, hour) => `<span>${hour % 4 === 0 ? hour : ""}</span>`)
      .join("");
    container.innerHTML = `<div class="hour-bars">${bars}</div><div class="hour-labels">${labels}</div>`;
  }

  global.Charts = { donut, bars, line, gauge, hourProfile, PALETTE };
})(window);
