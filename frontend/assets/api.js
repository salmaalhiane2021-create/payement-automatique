/*
 * api.js — client HTTP minimal pour l'API NexPay AI.
 *
 * Responsabilités :
 *  - gérer le jeton de session (localStorage) ;
 *  - centraliser les appels fetch et la gestion d'erreurs en français ;
 *  - rediriger vers la page de connexion en cas de session expirée (401).
 */
(function (global) {
  "use strict";

  const STORAGE_KEY = "nexpay_token";
  const USER_KEY = "nexpay_user";

  class ApiError extends Error {
    constructor(message, status) {
      super(message);
      this.name = "ApiError";
      this.status = status;
    }
  }

  const API = {
    ApiError,

    get token() {
      return localStorage.getItem(STORAGE_KEY) || "";
    },

    setSession(token, user) {
      localStorage.setItem(STORAGE_KEY, token);
      if (user) localStorage.setItem(USER_KEY, JSON.stringify(user));
    },

    get user() {
      try {
        return JSON.parse(localStorage.getItem(USER_KEY) || "null");
      } catch (_error) {
        return null;
      }
    },

    clearSession() {
      localStorage.removeItem(STORAGE_KEY);
      localStorage.removeItem(USER_KEY);
    },

    /** Redirige vers la connexion si aucun jeton n'est présent. */
    requireSession() {
      if (!this.token) {
        window.location.replace("login.html");
        return false;
      }
      return true;
    },

    /**
     * Appel HTTP générique.
     * @param {string} path   chemin relatif, ex. "/api/dashboard"
     * @param {object} options { method, body, auth }
     */
    async request(path, options = {}) {
      const { method = "GET", body = null, auth = true } = options;
      const headers = { Accept: "application/json" };

      if (body !== null) headers["Content-Type"] = "application/json";
      if (auth && this.token) headers.Authorization = `Bearer ${this.token}`;

      let response;
      try {
        response = await fetch(path, {
          method,
          headers,
          body: body === null ? undefined : JSON.stringify(body),
        });
      } catch (_networkError) {
        throw new ApiError("Serveur injoignable. Vérifiez que l'application est démarrée.", 0);
      }

      // Session expirée : on nettoie et on renvoie l'utilisateur à la connexion.
      if (response.status === 401 && auth) {
        this.clearSession();
        if (!window.location.pathname.endsWith("login.html")) {
          window.location.replace("login.html");
        }
        throw new ApiError("Votre session a expiré. Veuillez vous reconnecter.", 401);
      }

      let payload = null;
      const text = await response.text();
      if (text) {
        try {
          payload = JSON.parse(text);
        } catch (_error) {
          payload = { detail: text.slice(0, 300) };
        }
      }

      if (!response.ok) {
        const detail = payload && payload.detail ? payload.detail : `Erreur HTTP ${response.status}`;
        throw new ApiError(typeof detail === "string" ? detail : JSON.stringify(detail), response.status);
      }
      return payload;
    },

    get(path) {
      return this.request(path);
    },
    post(path, body) {
      return this.request(path, { method: "POST", body });
    },
    del(path) {
      return this.request(path, { method: "DELETE" });
    },
  };

  /* ------------------------------------------------------------------ */
  /* Formatage (montants, dates, pourcentages)                          */
  /* ------------------------------------------------------------------ */
  const Formatters = {
    /** Convertit une chaîne ISO en Date en tenant compte du fuseau éventuel. */
    toDate(value) {
      if (!value) return null;
      const text = String(value);
      const hasTimezone = /(Z|[+-]\d{2}:?\d{2})$/.test(text);
      return new Date(hasTimezone ? text : `${text}Z`); // les dates naïves sont en UTC
    },

    money(value, currency = "EUR") {
      const amount = Number(value || 0);
      return new Intl.NumberFormat("fr-FR", {
        style: "currency",
        currency,
        minimumFractionDigits: 2,
      }).format(amount);
    },

    dateTime(value) {
      const date = this.toDate(value);
      if (!date) return "—";
      return new Intl.DateTimeFormat("fr-FR", {
        day: "2-digit",
        month: "short",
        hour: "2-digit",
        minute: "2-digit",
      }).format(date);
    },

    dateOnly(value) {
      const date = this.toDate(value);
      if (!date) return "—";
      return new Intl.DateTimeFormat("fr-FR", { day: "2-digit", month: "short", year: "numeric" }).format(date);
    },

    timeOnly(value) {
      const date = this.toDate(value);
      if (!date) return "—";
      return new Intl.DateTimeFormat("fr-FR", { hour: "2-digit", minute: "2-digit" }).format(date);
    },

    percent(value, digits = 0) {
      return `${(Number(value || 0) * 100).toFixed(digits).replace(".", ",")} %`;
    },

    /** Échappe le HTML : tout contenu serveur est traité comme non fiable. */
    escape(value) {
      return String(value === null || value === undefined ? "" : value)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
    },
  };

  global.API = API;
  global.Fmt = Formatters;
})(window);
