/*
 * app.js — logique de l'espace client.
 *
 * Organisation :
 *   0. Utilitaires et composants réutilisables
 *   1. Navigation entre les vues
 *   2. Vue « Tableau de bord »
 *   3. Vue « Transactions »
 *   4. Vue « Paiement sécurisé » (analyse de risque + code à usage unique)
 *   5. Vue « Sécurité & IA » (profil, anomalies, alertes, recommandations, modèle)
 *   6. Fil temps réel des alertes
 *
 * Toutes les données affichées proviennent de l'API ; aucun contenu serveur
 * n'est inséré sans échappement HTML (protection contre l'injection).
 */
(function () {
  "use strict";

  if (!API.requireSession()) return;

  const $ = (id) => document.getElementById(id);
  const Fmt = window.Fmt;

  /* =====================================================================
     0. Utilitaires
     ===================================================================== */

  /** Notification éphémère en bas à droite. */
  function toast(kind, title, message, timeout = 5200) {
    const node = document.createElement("div");
    node.className = `toast is-${kind}`;
    node.innerHTML = `<strong>${Fmt.escape(title)}</strong><small>${Fmt.escape(message)}</small>`;
    $("toasts").appendChild(node);
    window.setTimeout(() => node.remove(), timeout);
  }

  /** Libellé et couleur d'une décision du moteur de risque. */
  function decisionBadge(decision) {
    const map = {
      accept: ["badge-success", "accepté"],
      verify: ["badge-warning", "vérification"],
      block: ["badge-danger", "bloqué"],
    };
    const [className, label] = map[decision] || ["badge-neutral", decision || "—"];
    return `<span class="badge ${className}">${Fmt.escape(label)}</span>`;
  }

  /** Badge de statut d'une opération. */
  function statusBadge(status) {
    const map = {
      completed: ["badge-neutral", "exécutée"],
      blocked: ["badge-danger", "bloquée"],
      pending: ["badge-warning", "en attente"],
    };
    const [className, label] = map[status] || ["badge-neutral", status];
    return `<span class="badge ${className}">${Fmt.escape(label)}</span>`;
  }

  /** Couleur d'un score de risque (0-100). */
  function riskColor(score) {
    const value = Number(score || 0);
    if (value >= 70) return "var(--danger)";
    if (value >= 35) return "var(--warning)";
    return "var(--accent)";
  }

  function riskCell(score) {
    const value = Number(score || 0);
    const width = Math.max(3, Math.min(100, value));
    return `
      <div style="min-width:104px">
        <div class="flex-between" style="margin-bottom:3px">
          <span class="mono" style="font-size:12px;color:${riskColor(value)}">${value.toFixed(0)}/100</span>
        </div>
        <div class="progress ${value >= 70 ? "is-danger" : value >= 35 ? "is-warning" : ""}">
          <span style="width:${width}%"></span>
        </div>
      </div>`;
  }

  /** Badge de gravité d'une alerte. */
  function severityBadge(severity) {
    const map = {
      critique: ["badge-danger", "critique"],
      eleve: ["badge-warning", "élevé"],
      moyen: ["badge-info", "moyen"],
      faible: ["badge-neutral", "faible"],
    };
    const [className, label] = map[severity] || ["badge-neutral", severity];
    return `<span class="badge ${className}">${Fmt.escape(label)}</span>`;
  }

  function emptyState(message) {
    return `<div class="empty">${Fmt.escape(message)}</div>`;
  }

  function escapeAll(value) {
    return Fmt.escape(value);
  }

  /* =====================================================================
     1. Navigation
     ===================================================================== */
  const VIEW_META = {
    dashboard: ["Tableau de bord", "Vue d'ensemble de vos finances et de votre sécurité"],
    transactions: ["Transactions", "Historique complet, filtres et analyse de chaque opération"],
    payment: ["Paiement sécurisé", "Simulation : analyse de risque puis code à usage unique de 60 secondes"],
    security: ["Sécurité & IA", "Profil comportemental, anomalies, alertes et recommandations"],
  };

  let currentView = "dashboard";

  function switchView(name) {
    currentView = name;
    Object.keys(VIEW_META).forEach((key) => {
      const section = $(`view-${key}`);
      if (section) section.hidden = key !== name;
    });
    document.querySelectorAll("#nav button").forEach((button) => {
      button.classList.toggle("is-active", button.dataset.view === name);
    });
    const [title, subtitle] = VIEW_META[name];
    $("view-title").textContent = title;
    $("view-subtitle").textContent = subtitle;
    window.scrollTo({ top: 0, behavior: "smooth" });

    if (name === "dashboard") loadDashboard();
    if (name === "transactions") loadTransactions({ reset: true });
    if (name === "payment") loadPaymentContext();
    if (name === "security") loadSecurity();
  }

  $("nav").addEventListener("click", (event) => {
    const button = event.target.closest("button[data-view]");
    if (button) switchView(button.dataset.view);
  });

  document.querySelectorAll("[data-goto]").forEach((button) => {
    button.addEventListener("click", () => switchView(button.dataset.goto));
  });

  $("bell").addEventListener("click", () => switchView("security"));

  $("logout").addEventListener("click", async () => {
    try {
      await API.post("/api/auth/logout", {});
    } catch (_error) {
      /* la déconnexion locale suffit */
    }
    API.clearSession();
    window.location.replace("login.html");
  });

  /* =====================================================================
     2. Vue « Tableau de bord »
     ===================================================================== */
  async function loadDashboard() {
    try {
      const data = await API.get("/api/dashboard");

      $("kpi-balance").textContent = Fmt.money(data.checking_balance);
      $("kpi-savings").textContent = `Épargne : ${Fmt.money(data.savings_balance)}`;
      $("top-balance").textContent = Fmt.money(data.checking_balance);

      $("kpi-spending").textContent = Fmt.money(data.month_spending);
      const ratio = data.month_income > 0 ? (data.month_spending / data.month_income) * 100 : 0;
      $("kpi-spending-sub").textContent =
        data.month_income > 0 ? `${ratio.toFixed(0)} % des revenus du mois` : "aucun revenu enregistré";

      $("kpi-income").textContent = Fmt.money(data.month_income);
      $("kpi-net").textContent =
        data.month_net >= 0
          ? `Épargne du mois : +${Fmt.money(data.month_net)}`
          : `Déficit du mois : ${Fmt.money(data.month_net)}`;

      $("kpi-subscriptions").textContent = Fmt.money(data.subscriptions_monthly);

      Charts.donut($("chart-categories"), data.categories || []);
      Charts.bars($("chart-daily"), data.daily || []);
      Charts.line($("chart-balance"), data.balance_history || []);

      renderDashboardTransactions(data.recent_transactions || []);
      renderDashboardAlerts(data.recent_alerts || [], data.alerts_unread || 0);
      updateAlertBadges(data.alerts_unread || 0);
    } catch (error) {
      toast("error", "Chargement impossible", error.message);
    }
  }

  function renderDashboardTransactions(items) {
    const table = $("dashboard-transactions");
    if (!items.length) {
      table.innerHTML = `<tbody><tr><td>${emptyState("Aucune opération enregistrée.")}</td></tr></tbody>`;
      return;
    }
    table.innerHTML = `
      <thead><tr>
        <th>Date</th><th>Libellé</th><th>Catégorie</th><th class="right">Montant</th><th>Risque</th><th>Statut</th>
      </tr></thead>
      <tbody>
        ${items
          .map(
            (txn) => `
          <tr>
            <td class="nowrap muted">${Fmt.dateTime(txn.ts)}</td>
            <td><strong>${escapeAll(txn.merchant)}</strong><br /><small class="dim">${escapeAll(txn.city)}</small></td>
            <td><span class="tag-pill">${escapeAll(txn.category)}</span></td>
            <td class="right ${txn.amount < 0 ? "amount-debit" : "amount-credit"}">${Fmt.money(txn.amount)}</td>
            <td>${riskCell(txn.risk_score)}</td>
            <td>${statusBadge(txn.status)}</td>
          </tr>`
          )
          .join("")}
      </tbody>`;
  }

  function renderDashboardAlerts(alerts, unread) {
    const container = $("dashboard-alerts");
    $("alerts-badge").textContent = `${unread} non lue${unread > 1 ? "s" : ""}`;
    if (!alerts.length) {
      container.innerHTML = emptyState("Aucune alerte : tout est calme.");
      return;
    }
    container.innerHTML = alerts
      .slice(0, 5)
      .map(
        (alert) => `
      <div class="alert-item ${alert.read ? "" : "is-unread"}">
        <span class="bullet sev-${escapeAll(alert.severity)}"></span>
        <div style="flex:1">
          <div class="flex-between">
            <strong style="font-size:13.5px">${escapeAll(alert.title)}</strong>
            <small class="dim nowrap">${Fmt.dateTime(alert.created_at)}</small>
          </div>
          <p>${escapeAll(alert.message)}</p>
        </div>
      </div>`
      )
      .join("");
  }

  /* =====================================================================
     3. Vue « Transactions »
     ===================================================================== */
  const transactionState = { offset: 0, limit: 25, total: 0, items: [] };

  async function loadTransactions({ reset = false } = {}) {
    if (reset) transactionState.offset = 0;

    const params = new URLSearchParams();
    const search = $("filter-search").value.trim();
    const category = $("filter-category").value;
    const days = $("filter-days").value;
    const flagged = $("filter-flagged").checked;

    if (search) params.set("search", search);
    if (category) params.set("category", category);
    if (days) params.set("days", days);
    if (flagged) params.set("flagged", "true");
    params.set("limit", String(transactionState.limit));
    params.set("offset", String(transactionState.offset));

    try {
      const data = await API.get(`/api/transactions?${params.toString()}`);
      transactionState.total = data.total;

      if (reset) transactionState.items = data.items;
      else transactionState.items = transactionState.items.concat(data.items);

      renderTransactions();
      $("transactions-total").textContent = `${data.total} opération${data.total > 1 ? "s" : ""}`;
      $("transactions-count").textContent =
        `${transactionState.items.length} affichée(s) sur ${data.total} — filtre actif`;
      $("transactions-more").hidden = transactionState.items.length >= data.total;

      // Alimentation du filtre des catégories (une seule fois)
      const select = $("filter-category");
      if (select.options.length <= 1) {
        const result = await API.get("/api/transactions/categories");
        result.categories.forEach((item) => {
          const option = document.createElement("option");
          option.value = item;
          option.textContent = item;
          select.appendChild(option);
        });
      }
    } catch (error) {
      toast("error", "Chargement impossible", error.message);
    }
  }

  function renderTransactions() {
    const table = $("transactions-table");
    if (!transactionState.items.length) {
      table.innerHTML = `<tbody><tr><td>${emptyState("Aucune opération ne correspond à ces critères.")}</td></tr></tbody>`;
      return;
    }
    table.innerHTML = `
      <thead><tr>
        <th>Date</th><th>Libellé</th><th>Catégorie</th><th>Ville</th><th>Canal</th>
        <th class="right">Montant</th><th>Score IA</th><th>Statut</th><th></th>
      </tr></thead>
      <tbody>
        ${transactionState.items
          .map(
            (txn) => `
          <tr>
            <td class="nowrap muted">${Fmt.dateTime(txn.ts)}</td>
            <td>
              <strong>${escapeAll(txn.merchant)}</strong>
              ${txn.is_fraud ? '<span class="badge badge-danger" style="margin-left:6px">fraude simulée</span>' : ""}
            </td>
            <td><span class="tag-pill">${escapeAll(txn.category)}</span></td>
            <td class="muted">${escapeAll(txn.city)}</td>
            <td class="muted">${escapeAll(txn.channel)}</td>
            <td class="right ${txn.amount < 0 ? "amount-debit" : "amount-credit"}">${Fmt.money(txn.amount)}</td>
            <td>${riskCell(txn.risk_score)}</td>
            <td>${statusBadge(txn.status)}</td>
            <td><button class="btn btn-ghost btn-sm" data-detail="${txn.id}">Détail</button></td>
          </tr>`
          )
          .join("")}
      </tbody>`;

    table.querySelectorAll("[data-detail]").forEach((button) => {
      button.addEventListener("click", () => openTransactionDetail(button.dataset.detail));
    });
  }

  /** Fenêtre de détail : explique la décision de sécurité d'une opération. */
  async function openTransactionDetail(id) {
    try {
      const detail = await API.get(`/api/transactions/${id}`);
      const factors = detail.facteurs || [];
      const backdrop = document.createElement("div");
      backdrop.className = "modal-backdrop";
      backdrop.innerHTML = `
        <div class="modal" role="dialog" aria-modal="true">
          <div class="card-head">
            <div>
              <h3>${escapeAll(detail.transaction.merchant)}</h3>
              <small class="muted">${Fmt.dateTime(detail.date_locale)} · ${escapeAll(detail.transaction.city)} ·
                ${escapeAll(detail.transaction.category)}</small>
            </div>
            <button class="btn btn-ghost btn-sm" data-close>Fermer</button>
          </div>

          <div class="metric-grid mb-16">
            <div class="metric"><span>Montant</span><strong>${Fmt.money(detail.transaction.amount)}</strong></div>
            <div class="metric"><span>Score de risque</span><strong style="color:${riskColor(detail.risk_score)}">${Number(
              detail.risk_score
            ).toFixed(0)}/100</strong></div>
            <div class="metric"><span>Score IA</span><strong>${Number(detail.anomaly_score).toFixed(2)}</strong></div>
            <div class="metric"><span>Décision</span><strong>${decisionBadge(detail.decision)}</strong></div>
          </div>

          ${detail.explication ? `<div class="alert alert-info">${escapeAll(detail.explication)}</div>` : ""}

          <h4>Facteurs analysés</h4>
          ${
            factors.length
              ? factors
                  .map(
                    (factor) => `
            <div class="factor-row">
              <div>
                <div class="label">${escapeAll(factor.label)}</div>
                <div class="detail">${escapeAll(factor.detail)}</div>
              </div>
              <div class="weight" style="color:${riskColor(factor.weight * 4)}">+${Number(factor.weight).toFixed(1)}</div>
            </div>`
                  )
                  .join("")
              : '<p class="muted">Aucun facteur de risque enregistré pour cette opération.</p>'
          }

          <p class="muted mt-16">
            Canal : ${escapeAll(detail.transaction.channel)} · Statut : ${statusBadge(detail.transaction.status)}
            ${detail.fraude_simulee ? ` · Scénario injecté : <strong>${escapeAll(detail.fraude_simulee)}</strong>` : ""}
          </p>
        </div>`;

      backdrop.addEventListener("click", (event) => {
        if (event.target === backdrop || event.target.closest("[data-close]")) backdrop.remove();
      });
      document.addEventListener("keydown", function onEscape(event) {
        if (event.key === "Escape") {
          backdrop.remove();
          document.removeEventListener("keydown", onEscape);
        }
      });
      document.body.appendChild(backdrop);
    } catch (error) {
      toast("error", "Détail indisponible", error.message);
    }
  }

  $("apply-filters").addEventListener("click", () => loadTransactions({ reset: true }));  $("filter-search").addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      loadTransactions({ reset: true });
    }
  });
  $("transactions-more").addEventListener("click", () => {
    transactionState.offset += transactionState.limit;
    loadTransactions();
  });

  /* =====================================================================
     4. Vue « Paiement sécurisé »
     ===================================================================== */
  const paymentState = {
    context: null,
    quote: null,
    timer: null,
    countryOverride: null,
    simulatedMinute: null,
    hoursReady: false,
  };

  async function loadPaymentContext(force = false) {
    if (paymentState.context && !force) return paymentState.context;
    try {
      const context = await API.get("/api/payments/context");
      paymentState.context = context;

      const deviceSelect = $("pay-device");
      deviceSelect.innerHTML = "";
      context.devices.forEach((device) => {
        const option = document.createElement("option");
        option.value = device.fingerprint;
        option.textContent = `${device.label} (${device.platform})${device.is_trusted ? "" : " — non vérifié"}`;
        deviceSelect.appendChild(option);
      });
      const unknown = document.createElement("option");
      unknown.value = "__nouveau__";
      unknown.textContent = "Appareil inconnu (nouveau matériel)";
      deviceSelect.appendChild(unknown);

      const citySelect = $("pay-city");
      citySelect.innerHTML = "";
      context.cities.forEach((city) => {
        const option = document.createElement("option");
        option.value = city;
        option.textContent = city;
        citySelect.appendChild(option);
      });
      if (citySelect.querySelector(`option[value="${CSS.escape(API.user?.city || "Paris")}"]`)) {
        citySelect.value = API.user?.city || "Paris";
      }

      const categorySelect = $("pay-category");
      categorySelect.innerHTML = "";
      context.categories.forEach((category) => {
        const option = document.createElement("option");
        option.value = category;
        option.textContent = category;
        categorySelect.appendChild(option);
      });
      categorySelect.value = "Shopping";

      const now = new Date();
      if (!paymentState.hoursReady) {
        const hourSelect = $("pay-hour");
        hourSelect.innerHTML = [
          `<option value="">Heure réelle</option>`,
          ...Array.from({ length: 24 }, (_, hour) => {
            const label = `${String(hour).padStart(2, "0")} h 00`;
            const marker = hour === 3 ? " — test achat nocturne" : "";
            return `<option value="${hour}">${label}${marker}</option>`;
          }),
        ].join("");
        paymentState.hoursReady = true;
      }
      document.querySelector('#pay-hour option[value=""]').textContent =
        `Heure réelle (${now.getHours()} h ${String(now.getMinutes()).padStart(2, "0")})`;

      loadScenarios();
      return context;
    } catch (error) {
      toast("error", "Contexte indisponible", error.message);
      return null;
    }
  }

  async function loadScenarios() {
    try {
      const data = await API.get("/api/payments/scenarios");
      $("scenario-list").innerHTML = data.scenarios
        .map(
          (scenario) => `
        <button type="button" class="scenario" data-scenario="${escapeAll(scenario.code)}">
          <strong>${escapeAll(scenario.title)}</strong>
          <small>${escapeAll(scenario.description)}</small>
        </button>`
        )
        .join("");

      document.querySelectorAll("[data-scenario]").forEach((button) => {
        button.addEventListener("click", async () => {
          const scenario = data.scenarios.find((item) => item.code === button.dataset.scenario);
          if (!scenario) return;
          // Certains scénarios se jouent en deux temps : un paiement local
          // réellement exécuté, puis l'opération suspecte (déplacement
          // impossible détecté par la vélocité géographique).
          if (scenario.pre_payment) {
            await runPrepPayment(scenario.pre_payment);
          }
          applyScenario(scenario.payload);
        });
      });
    } catch (error) {
      $("scenario-list").innerHTML = emptyState("Scénarios indisponibles.");
    }
  }

  /**
   * Paiement préparatoire d'un scénario (ex. « déplacement impossible »).
   *
   * Il est exécuté pour de vrai — analyse, code à usage unique, débit simulé —
   * afin de créer l'historique sur lequel le moteur s'appuiera pour détecter
   * l'opération suivante (ici : un retrait à l'autre bout du monde quelques
   * minutes plus tard). Le code OTP est repris de la réponse de démonstration.
   */
  async function runPrepPayment(payload) {
    const context = await loadPaymentContext();
    if (!context) return;

    const deviceSelect = $("pay-device");
    const useKnown =
      payload.use_known_device && deviceSelect.options.length > 0 && deviceSelect.options[0].value !== "__nouveau__";
    const fingerprint = useKnown ? deviceSelect.options[0].value : `unknown-${Math.random().toString(16).slice(2, 14)}`;

    const body = {
      merchant: payload.merchant,
      amount: payload.amount,
      category: payload.category,
      city: payload.city,
      country: payload.country || (context.city_countries || {})[payload.city] || "France",
      channel: payload.channel,
      device_fingerprint: fingerprint,
      device_label: useKnown ? deviceSelect.options[0].textContent.split(" (")[0] : "Appareil inconnu",
      simulated_hour: payload.simulated_hour ?? null,
      simulated_minute: payload.simulated_minute ?? null,
    };

    try {
      const quote = await API.post("/api/payments/quote", body);
      if (!quote.challenge_id) {
        toast("warning", "Paiement préparatoire bloqué", quote.summary);
        return;
      }
      const confirmBody = { challenge_id: quote.challenge_id, code: quote.simulation_code };
      if (quote.require_pin) {
        const pin = window.prompt("Code PIN requis pour le paiement préparatoire (démonstration : 4321)");
        if (!pin) return;
        confirmBody.pin = pin;
      }
      const result = await API.post("/api/payments/confirm", confirmBody);
      toast("info", "Paiement local exécuté", result.message);
      loadDashboard();
      refreshAlertCounters();
    } catch (error) {
      toast("warning", "Paiement préparatoire refusé", error.message);
    }
  }

  function applyScenario(payload) {
    $("pay-merchant").value = payload.merchant;
    $("pay-amount").value = payload.amount;
    $("pay-city").value = payload.city;
    $("pay-channel").value = payload.channel;
    $("pay-category").value = payload.category;
    $("pay-hour").value = payload.simulated_hour !== undefined ? String(payload.simulated_hour) : "";
    paymentState.simulatedMinute = payload.simulated_minute ?? null;
    // Le scénario impose un pays (ex. « Nigéria ») : il prime sur la ville seule.
    paymentState.countryOverride = payload.country || null;

    const deviceSelect = $("pay-device");
    if (payload.use_known_device && deviceSelect.options.length > 0 && deviceSelect.options[0].value !== "__nouveau__") {
      deviceSelect.selectedIndex = 0;
    } else {
      deviceSelect.value = "__nouveau__";
    }
    resetPaymentPanel();
    $("pay-submit").click();
  }

  function resetPaymentPanel() {
    if (paymentState.timer) window.clearInterval(paymentState.timer);
    paymentState.timer = null;
    paymentState.quote = null;
    $("pay-stage").textContent = "en attente";
    $("pay-stage").className = "badge badge-neutral";
    $("payment-result").innerHTML = `
      <div class="empty">
        Renseignez une opération puis lancez l'analyse :<br />
        score de risque détaillé, décision du moteur et code à usage unique.
      </div>
      <h4 class="mt-16">Scénarios de démonstration</h4>
      <div class="scenario-list" id="scenario-list"></div>`;
    loadScenarios();
  }

  // Un changement manuel de ville annule le pays imposé par un scénario.
  $("pay-city").addEventListener("change", () => {
    paymentState.countryOverride = null;
  });

  // Un changement manuel d'heure annule la minute simulée du scénario.
  $("pay-hour").addEventListener("change", () => {
    paymentState.simulatedMinute = null;
  });

  $("payment-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const context = await loadPaymentContext();
    if (!context) return;

    const deviceValue = $("pay-device").value;
    const isUnknownDevice = deviceValue === "__nouveau__";
    const fingerprint = isUnknownDevice
      ? `unknown-${Math.random().toString(16).slice(2, 14)}`
      : deviceValue;

    const payload = {
      merchant: $("pay-merchant").value.trim(),
      amount: Number($("pay-amount").value),
      category: $("pay-category").value,
      city: $("pay-city").value,
      country: paymentState.countryOverride || (context.city_countries || {})[$("pay-city").value] || "France",
      channel: $("pay-channel").value,
      device_fingerprint: fingerprint,
      device_label: isUnknownDevice
        ? "Appareil inconnu"
        : $("pay-device").selectedOptions[0].textContent.split(" (")[0],
      simulated_hour: $("pay-hour").value ? Number($("pay-hour").value) : null,
      simulated_minute: paymentState.simulatedMinute,
    };

    const button = $("pay-submit");
    button.disabled = true;
    button.textContent = "Analyse en cours…";

    try {
      const quote = await API.post("/api/payments/quote", payload);
      paymentState.quote = quote;
      renderQuote(quote, payload);
    } catch (error) {
      toast("error", "Analyse impossible", error.message);
    } finally {
      button.disabled = false;
      button.textContent = "Analyser le risque";
    }
  });

  /** Affiche le score, les facteurs puis le défi d'authentification. */
  function renderQuote(quote, payload) {
    const factors = quote.factors || [];
    const amount = Fmt.money(payload.amount);

    const stage = $("pay-stage");
    stage.textContent = { accept: "accepté", verify: "vérification", block: "bloqué" }[quote.decision];
    stage.className = `badge badge-${quote.decision === "accept" ? "success" : quote.decision === "verify" ? "warning" : "danger"}`;

    const factorsHtml = factors.length
      ? factors
          .map(
            (factor) => `
        <div class="factor-row">
          <div>
            <div class="label">${escapeAll(factor.label)}
              ${factor.severity === "critique" ? '<span class="badge badge-danger">critique</span>' : ""}</div>
            <div class="detail">${escapeAll(factor.detail)}</div>
          </div>
          <div class="weight" style="color:${factor.weight > 0 ? "var(--warning)" : "var(--text-dim)"}">
            ${factor.weight > 0 ? `+${Number(factor.weight).toFixed(1)}` : "—"}</div>
        </div>`
          )
          .join("")
      : '<p class="muted">Aucun signal de risque détecté : opération parfaitement conforme à vos habitudes.</p>';

    let html = `
      <div class="gauge-wrap">
        <div id="pay-gauge"></div>
        <div>
          <div class="gauge-value" style="color:${riskColor(quote.risk_score)}">${Number(quote.risk_score).toFixed(0)}</div>
          <div class="muted">score de risque sur 100</div>
          <div class="muted mt-8">Anomalie IA : <strong>${Number(quote.anomaly_score).toFixed(2)}</strong> / 1</div>
        </div>
      </div>

      <div class="decision-box decision-${quote.decision}">
        <strong>${escapeAll(quote.decision_label)}</strong> — ${escapeAll(quote.summary)}
      </div>

      <h4>Facteurs analysés</h4>
      ${factorsHtml}
    `;

    if (quote.decision === "block") {
      html += `
        <div class="result-hero">
          <div class="mark">⛔</div>
          <h3>Opération refusée</h3>
          <p class="muted">Aucun code n'a été émis : le paiement ne peut pas être validé.
            Une alerte a été ajoutée à votre centre de sécurité.</p>
          <button class="btn btn-ghost mt-8" id="pay-reset-btn">Nouvelle analyse</button>
        </div>`;
      $("payment-result").innerHTML = html;
      Charts.gauge($("pay-gauge"), quote.risk_score, quote.decision);
      $("pay-reset-btn").addEventListener("click", resetPaymentPanel);
      toast("warning", "Paiement bloqué", "Le moteur de risque a refusé l'opération.");
      refreshAlertCounters();
      return;
    }

    // --- Défi d'authentification à usage unique ------------------------
    html += `
      <div class="otp-panel mt-16">
        <div class="otp-head">
          <div>
            <strong>Code à usage unique</strong>
            <div class="muted">Envoyé par SMS simulé au ${escapeAll(quote.masked_phone)}</div>
          </div>
          <div class="countdown" id="otp-countdown">
            <span id="otp-seconds">${quote.otp_ttl_seconds}</span> s
            <div class="countdown-bar"><span id="otp-bar" style="width:100%"></span></div>
          </div>
        </div>

        ${
          quote.simulation_code
            ? `<div class="sms-sim">📱 <strong>SMS simulé (mode démonstration)</strong> — votre code de validation est
                 <code>${escapeAll(quote.simulation_code)}</code>. En production, ce code ne serait jamais affiché ici.</div>`
            : ""
        }

        <div class="otp-inputs" id="otp-inputs">
          ${[0, 1, 2, 3, 4, 5]
            .map(
              (index) =>
                `<input type="text" inputmode="numeric" maxlength="1" data-otp="${index}" aria-label="Chiffre ${index + 1}" />`
            )
            .join("")}
        </div>

        ${
          quote.require_pin
            ? `<label class="field"><span>Code PIN (vérification renforcée)
                 — <span class="dim">démonstration : 4321</span></span>
                 <input type="password" id="otp-pin" inputmode="numeric" maxlength="6" placeholder="••••" /></label>`
            : ""
        }

        <div id="otp-error" class="alert alert-error" hidden></div>

        <div class="flex gap-8">
          <button class="btn btn-primary" id="otp-confirm">Valider le paiement de ${amount}</button>
          <button class="btn btn-ghost" id="otp-cancel">Annuler</button>
        </div>
      </div>

      <div id="payment-outcome"></div>`;

    $("payment-result").innerHTML = html;
    Charts.gauge($("pay-gauge"), quote.risk_score, quote.decision);
    setupOtpInputs();
    startCountdown(quote.otp_ttl_seconds);
    $("otp-confirm").addEventListener("click", confirmPayment);
    $("otp-cancel").addEventListener("click", cancelPayment);
  }

  /** Saisie des 6 chiffres : avance automatique, retour arrière, collage. */
  function setupOtpInputs() {
    const inputs = Array.from(document.querySelectorAll("[data-otp]"));
    inputs.forEach((input, index) => {
      input.addEventListener("input", () => {
        input.value = input.value.replace(/\D/g, "").slice(0, 1);
        if (input.value && index < inputs.length - 1) inputs[index + 1].focus();
      });
      input.addEventListener("keydown", (event) => {
        if (event.key === "Backspace" && !input.value && index > 0) inputs[index - 1].focus();
      });
      input.addEventListener("paste", (event) => {
        event.preventDefault();
        const digits = (event.clipboardData.getData("text") || "").replace(/\D/g, "").slice(0, 6);
        digits.split("").forEach((digit, position) => {
          if (inputs[position]) inputs[position].value = digit;
        });
        const next = Math.min(digits.length, inputs.length - 1);
        inputs[next].focus();
      });
    });
    inputs[0].focus();
  }

  function readOtpCode() {
    return Array.from(document.querySelectorAll("[data-otp]"))
      .map((input) => input.value)
      .join("");
  }

  /** Compte à rebours de 60 secondes, en cohérence avec le serveur. */
  function startCountdown(seconds) {
    if (paymentState.timer) window.clearInterval(paymentState.timer);
    let remaining = Number(seconds);
    const label = $("otp-seconds");
    const bar = $("otp-bar");
    const wrapper = $("otp-countdown");

    const tick = () => {
      remaining -= 1;
      if (label) label.textContent = String(Math.max(0, remaining));
      if (bar) bar.style.width = `${Math.max(0, (remaining / seconds) * 100)}%`;
      if (remaining <= 0) {
        window.clearInterval(paymentState.timer);
        paymentState.timer = null;
        if (wrapper) wrapper.classList.add("is-expired");
        const confirm = $("otp-confirm");
        if (confirm) confirm.disabled = true;
        const error = $("otp-error");
        if (error) {
          error.hidden = false;
          error.textContent =
            "Code expiré (durée de vie 60 secondes). Relancez l'analyse pour obtenir un nouveau code.";
        }
      }
    };
    tick();
    paymentState.timer = window.setInterval(tick, 1000);
  }

  async function confirmPayment() {
    const error = $("otp-error");
    error.hidden = true;

    const code = readOtpCode();
    if (code.length !== 6) {
      error.hidden = false;
      error.textContent = "Saisissez les 6 chiffres du code reçu.";
      return;
    }

    const pinField = $("otp-pin");
    const body = { challenge_id: paymentState.quote.challenge_id, code };
    if (pinField) body.pin = pinField.value.trim() || null;

    const button = $("otp-confirm");
    button.disabled = true;
    button.textContent = "Vérification…";

    try {
      const result = await API.post("/api/payments/confirm", body);
      if (paymentState.timer) window.clearInterval(paymentState.timer);
      renderPaymentOutcome(result);
      loadDashboard();
      refreshAlertCounters();
    } catch (requestError) {
      error.hidden = false;
      error.textContent = requestError.message;
      button.disabled = false;
      button.textContent = "Réessayer la validation";
      toast("error", "Validation refusée", requestError.message);
      refreshAlertCounters();
    }
  }

  function renderPaymentOutcome(result) {
    const success = result.status === "completed";
    $("otp-confirm").disabled = true;
    $("payment-result").querySelector(".otp-panel").hidden = true;
    $("payment-outcome").innerHTML = `
      <div class="result-hero">
        <div class="mark">${success ? "✅" : "⛔"}</div>
        <h3>${success ? "Paiement validé" : "Paiement refusé"}</h3>
        <p class="muted">${escapeAll(result.message)}</p>
        ${
          result.new_balance !== null && result.new_balance !== undefined
            ? `<p class="muted">Nouveau solde : <strong>${Fmt.money(result.new_balance)}</strong></p>`
            : ""
        }
        ${result.alert_id ? '<p class="muted">Une alerte a été ajoutée à votre centre de sécurité.</p>' : ""}
        <button class="btn btn-ghost mt-8" id="pay-reset-btn">Nouvelle analyse</button>
      </div>`;
    $("pay-reset-btn").addEventListener("click", resetPaymentPanel);
    $("pay-stage").textContent = success ? "exécuté" : "refusé";
    $("pay-stage").className = `badge badge-${success ? "success" : "danger"}`;
    toast(success ? "success" : "warning", success ? "Paiement exécuté" : "Paiement refusé", result.message);
  }

  async function cancelPayment() {
    if (paymentState.quote && paymentState.quote.challenge_id) {
      try {
        await API.post(`/api/payments/challenge/${paymentState.quote.challenge_id}/cancel`, {});
      } catch (_error) {
        /* l'annulation locale reste valable */
      }
    }
    resetPaymentPanel();
    toast("info", "Paiement annulé", "Aucun montant n'a été débité.");
  }

  /* =====================================================================
     5. Vue « Sécurité & IA »
     ===================================================================== */
  async function loadSecurity() {
    loadProfile();
    loadAnomalies();
    loadAlerts();
    loadRecommendations();
    loadModelCard();
    loadDevices();
    loadAudit();
  }

  async function loadProfile() {
    try {
      const profile = await API.get("/api/security/profile");
      $("profile-updated").textContent =
        `recalculé le ${Fmt.dateOnly(profile.updated_at)} — ${profile.total_transactions} opérations analysées`;

      $("profile-metrics").innerHTML = `
        <div class="metric"><span>Panier moyen</span><strong>${Fmt.money(profile.average_amount)}</strong></div>
        <div class="metric"><span>Montant médian</span><strong>${Fmt.money(profile.median_amount)}</strong></div>
        <div class="metric"><span>Plage habituelle</span><strong style="font-size:15px">
          ${Fmt.money(profile.typical_range[0])} – ${Fmt.money(profile.typical_range[1])}</strong></div>
        <div class="metric"><span>Heure favorite</span><strong>${profile.favorite_hour} h</strong></div>
        <div class="metric"><span>Dépense mensuelle</span><strong>${Fmt.money(profile.monthly_average_spending)}</strong></div>
        <div class="metric"><span>Opérations de nuit</span><strong>${Fmt.percent(profile.night_ratio)}</strong></div>
        <div class="metric"><span>Paiements à distance</span><strong>${Fmt.percent(profile.online_ratio)}</strong></div>
        <div class="metric"><span>Villes connues</span><strong>${profile.known_cities.length}</strong></div>`;

      Charts.hourProfile($("profile-hours"), profile.hourly_distribution);

      $("profile-insights").innerHTML = (profile.insights || [])
        .map((insight) => `<li>${escapeAll(insight)}</li>`)
        .join("");

      const subscriptions = profile.subscriptions || [];
      if (subscriptions.length) {
        $("profile-insights").insertAdjacentHTML(
          "beforeend",
          `<li><strong>Abonnements détectés :</strong> ${subscriptions
            .map((item) => `${escapeAll(item.merchant)} (${Fmt.money(item.amount)})`)
            .join(" · ")}</li>`
        );
      }
    } catch (error) {
      toast("error", "Profil indisponible", error.message);
    }
  }

  async function loadAnomalies() {
    try {
      const rows = await API.get("/api/security/anomalies?limit=8");
      if (!rows.length) {
        $("anomalies-list").innerHTML = emptyState("Aucune anomalie détectée dans votre historique.");
        return;
      }
      $("anomalies-list").innerHTML = rows
        .map(
          (row) => `
        <div class="alert-item">
          <span class="bullet ${row.anomaly_score >= 0.75 ? "sev-critique" : row.anomaly_score >= 0.5 ? "sev-eleve" : "sev-moyen"}"></span>
          <div style="flex:1">
            <div class="flex-between">
              <strong style="font-size:13.5px">${escapeAll(row.transaction.merchant)} —
                ${Fmt.money(row.transaction.amount)}</strong>
              <span class="mono" style="font-size:12px;color:${riskColor(row.risk_score)}">
                IA ${Number(row.anomaly_score).toFixed(2)}</span>
            </div>
            <p>${escapeAll(row.reasons.join(" · "))}</p>
            <small class="dim">${Fmt.dateTime(row.transaction.ts)} · ${escapeAll(row.transaction.city)} ·
              ${statusBadge(row.transaction.status)}</small>
          </div>
        </div>`
        )
        .join("");
    } catch (error) {
      $("anomalies-list").innerHTML = emptyState("Anomalies indisponibles.");
    }
  }

  async function loadAlerts() {
    try {
      const alerts = await API.get("/api/security/alerts?limit=40");
      const unread = alerts.filter((alert) => !alert.read_at).length;
      updateAlertBadges(unread);

      if (!alerts.length) {
        $("alerts-list").innerHTML = emptyState("Aucune alerte : tout est calme.");
        return;
      }

      $("alerts-list").innerHTML = alerts
        .map(
          (alert) => `
        <div class="alert-item ${alert.read_at ? "" : "is-unread"}">
          <span class="bullet sev-${escapeAll(alert.severity)}"></span>
          <div style="flex:1">
            <div class="flex-between">
              <strong style="font-size:13.5px">${escapeAll(alert.title)}</strong>
              <small class="dim nowrap">${Fmt.dateTime(alert.created_at)}</small>
            </div>
            <p>${escapeAll(alert.message)}</p>
            <div class="flex gap-8 mt-8">
              ${severityBadge(alert.severity)}
              <span class="tag-pill">${escapeAll(alert.kind)}</span>
              ${alert.read_at ? "" : `<button class="btn btn-ghost btn-sm" data-read="${alert.id}">Marquer comme lue</button>`}
            </div>
          </div>
        </div>`
        )
        .join("");

      $("alerts-list").querySelectorAll("[data-read]").forEach((button) => {
        button.addEventListener("click", async () => {
          try {
            await API.post(`/api/security/alerts/${button.dataset.read}/read`, {});
            loadAlerts();
          } catch (error) {
            toast("error", "Action impossible", error.message);
          }
        });
      });
    } catch (error) {
      $("alerts-list").innerHTML = emptyState("Alertes indisponibles.");
    }
  }

  $("read-all").addEventListener("click", async () => {
    try {
      await API.post("/api/security/alerts/read-all", {});
      loadAlerts();
      toast("success", "Alertes", "Toutes les alertes ont été marquées comme lues.");
    } catch (error) {
      toast("error", "Action impossible", error.message);
    }
  });

  async function loadRecommendations() {
    try {
      const rows = await API.get("/api/security/recommendations");
      if (!rows.length) {
        $("recommendations").innerHTML = emptyState("Aucune recommandation pour le moment.");
        return;
      }
      const kindLabel = { securite: "sécurité", epargne: "épargne", budget: "budget" };
      $("recommendations").innerHTML = rows
        .map(
          (row) => `
        <div class="reco kind-${escapeAll(row.kind)}">
          <div class="reco-head">
            <strong>${escapeAll(row.title)}</strong>
            <span class="badge badge-${row.kind === "securite" ? "danger" : row.kind === "epargne" ? "success" : "warning"}">
              ${escapeAll(kindLabel[row.kind] || row.kind)}</span>
          </div>
          <p>${escapeAll(row.detail)}</p>
          ${row.impact_eur ? `<div class="impact">Impact estimé : ${Fmt.money(row.impact_eur)} / mois</div>` : ""}
        </div>`
        )
        .join("");
    } catch (error) {
      $("recommendations").innerHTML = emptyState("Recommandations indisponibles.");
    }
  }

  async function loadModelCard() {
    try {
      const card = await API.get("/api/security/model-card");
      const metrics = card.metrics || {};
      const decisions = card.decision_metrics || {};

      $("model-card").innerHTML = `
        <div class="metric-grid mb-16">
          <div class="metric"><span>Algorithme</span><strong style="font-size:14px">${
            card.available ? "Isolation Forest" : "non entraîné"
          }</strong></div>
          <div class="metric"><span>Exemples d'entraînement</span><strong>${card.n_samples}</strong></div>
          <div class="metric"><span>Variables</span><strong>${card.n_features}</strong></div>
          <div class="metric"><span>Contamination</span><strong>${Fmt.percent(card.contamination, 1)}</strong></div>
          <div class="metric"><span>AUC (ROC)</span><strong style="color:var(--accent)">${
            metrics.roc_auc !== undefined ? metrics.roc_auc : "—"
          }</strong></div>
          <div class="metric"><span>Entraîné le</span><strong style="font-size:13px">${
            card.trained_at ? Fmt.dateOnly(card.trained_at) : "—"
          }</strong></div>
        </div>

        <h4>Performance du système complet (règles + IA)</h4>
        <div class="metric-grid mb-16">
          <div class="metric"><span>Fraudes bloquées ou escaladées</span>
            <strong style="color:var(--accent)">${Fmt.percent(decisions.taux_detection || 0)}</strong></div>
          <div class="metric"><span>Friction sur paiements légitimes</span>
            <strong style="color:var(--warning)">${Fmt.percent(decisions.taux_friction || 0)}</strong></div>
          <div class="metric"><span>Score moyen — fraudes</span><strong>${decisions.score_moyen_fraude || 0}/100</strong></div>
          <div class="metric"><span>Score moyen — légitimes</span><strong>${decisions.score_moyen_legitime || 0}/100</strong></div>
        </div>

        <p class="muted">
          Évaluation réalisée sur ${decisions.n_fraudes || 0} fraudes simulées et ${decisions.n_legitimes || 0}
          opérations légitimes. L'AUC mesure la capacité de tri du modèle (1 = parfait, 0,5 = aléatoire).
          La précision brute au seuil optimal (${metrics.precision ?? "—"}) reste mécaniquement limitée par la
          faible proportion de fraudes dans l'historique (${metrics.n_fraud || 0} cas sur ${metrics.n_labeled || 0}).
        </p>

        <h4>Variables utilisées</h4>
        <div class="feature-list">${card.features.map((feature) => `<div>${escapeAll(feature)}</div>`).join("")}</div>`;
    } catch (error) {
      $("model-card").innerHTML = emptyState("Fiche modèle indisponible.");
    }
  }

  $("retrain").addEventListener("click", async () => {
    const button = $("retrain");
    button.disabled = true;
    button.textContent = "Entraînement…";
    try {
      const result = await API.post("/api/security/retrain", {});
      toast("success", "Modèle réentraîné", `${result.message} (${result.duration_seconds} s)`);
      loadModelCard();
      loadAnomalies();
    } catch (error) {
      toast("error", "Entraînement impossible", error.message);
    } finally {
      button.disabled = false;
      button.textContent = "Réentraîner";
    }
  });

  async function loadDevices() {
    try {
      const data = await API.get("/api/security/devices");
      if (!data.devices.length) {
        $("devices-list").innerHTML = emptyState("Aucun appareil enregistré.");
        return;
      }
      $("devices-list").innerHTML = data.devices
        .map(
          (device) => `
        <div class="device-row">
          <div>
            <strong>${escapeAll(device.label)}</strong>
            ${device.is_trusted ? '<span class="badge badge-success">vérifié</span>' : '<span class="badge badge-warning">non vérifié</span>'}
            <div class="muted">${escapeAll(device.platform)} · dernière utilisation ${Fmt.dateOnly(device.last_seen)}</div>
          </div>
          <div class="flex gap-8">
            ${
              device.is_trusted
                ? ""
                : `<button class="btn btn-success btn-sm" data-trust="${device.id}">Vérifier</button>`
            }
            <button class="btn btn-danger btn-sm" data-delete="${device.id}">Supprimer</button>
          </div>
        </div>`
        )
        .join("");

      $("devices-list").querySelectorAll("[data-trust]").forEach((button) => {
        button.addEventListener("click", async () => {
          try {
            await API.post(`/api/security/devices/${button.dataset.trust}/trust`, {});
            toast("success", "Appareil vérifié", "Cet appareil ne déclenchera plus d'alerte.");
            loadDevices();
          } catch (error) {
            toast("error", "Action impossible", error.message);
          }
        });
      });
      $("devices-list").querySelectorAll("[data-delete]").forEach((button) => {
        button.addEventListener("click", async () => {
          try {
            await API.del(`/api/security/devices/${button.dataset.delete}`);
            toast("success", "Appareil supprimé", "L'accès de cet appareil est révoqué.");
            loadDevices();
          } catch (error) {
            toast("error", "Suppression impossible", error.message);
          }
        });
      });
    } catch (error) {
      $("devices-list").innerHTML = emptyState("Appareils indisponibles.");
    }
  }

  async function loadAudit() {
    try {
      const data = await API.get("/api/security/audit?limit=15");
      $("audit-list").innerHTML = data.entries
        .map(
          (entry) => `
        <div class="flex-between" style="padding:8px 0;border-bottom:1px solid var(--border-soft)">
          <div>
            <strong style="font-size:13px">${escapeAll(entry.action.replace(/_/g, " "))}</strong>
            <div class="muted" style="font-size:12.5px">${escapeAll(entry.detail)}</div>
          </div>
          <div class="right nowrap">
            <div class="dim" style="font-size:12px">${Fmt.dateTime(entry.date_locale)}</div>
            <span class="badge ${entry.status === "ok" ? "badge-neutral" : "badge-danger"}">${escapeAll(entry.status)}</span>
          </div>
        </div>`
        )
        .join("");
    } catch (error) {
      $("audit-list").innerHTML = emptyState("Journal indisponible.");
    }
  }

  /* =====================================================================
     6. Fil temps réel des alertes
     ===================================================================== */
  let lastAlertsSignature = null;

  function updateAlertBadges(count) {
    const bell = $("bell-count");
    const nav = $("nav-alert-count");
    [bell, nav].forEach((node) => {
      node.textContent = String(count);
      node.hidden = count === 0;
    });
  }

  async function refreshAlertCounters() {
    try {
      const summary = await API.get("/api/security/alerts/summary");
      updateAlertBadges(summary.unread);

      // Nouvelle alerte détectée : on rafraîchit le fil affiché.
      const signature = `${summary.unread}:${summary.latest_id}`;
      if (lastAlertsSignature !== null && lastAlertsSignature !== signature && currentView === "security") {
        loadAlerts();
      }
      lastAlertsSignature = signature;
    } catch (_error) {
      /* silencieux : le rafraîchissement est périodique */
    }
  }

  // L'API est interrogée toutes les 4 secondes : c'est le « temps réel » du prototype.
  // En production, on utiliserait plutôt un flux WebSocket ou Server-Sent Events.
  window.setInterval(refreshAlertCounters, 4000);

  /* =====================================================================
     Démarrage
     ===================================================================== */
  const user = API.user;
  if (user) {
    $("user-name").textContent = user.full_name;
    $("user-email").textContent = user.email;
  }
  switchView("dashboard");
  refreshAlertCounters();
})();
