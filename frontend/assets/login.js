/*
 * login.js — page de connexion / inscription.
 *
 * Toute la validation de fond est faite côté serveur : le navigateur ne fait
 * ici que vérifier la présence des champs et afficher les messages d'erreur.
 */
(function () {
  "use strict";

  /* ---------------------------- Onglets ------------------------------ */
  const tabLogin = document.getElementById("tab-login");
  const tabRegister = document.getElementById("tab-register");
  const formLogin = document.getElementById("form-login");
  const formRegister = document.getElementById("form-register");

  function showTab(which) {
    const isLogin = which === "login";
    tabLogin.classList.toggle("is-active", isLogin);
    tabRegister.classList.toggle("is-active", !isLogin);
    tabLogin.setAttribute("aria-selected", String(isLogin));
    tabRegister.setAttribute("aria-selected", String(!isLogin));
    formLogin.hidden = !isLogin;
    formRegister.hidden = isLogin;
  }

  tabLogin.addEventListener("click", () => showTab("login"));
  tabRegister.addEventListener("click", () => showTab("register"));

  /* ------------------------- Affichage du mot de passe ---------------- */
  document.querySelectorAll(".toggle-visibility").forEach((button) => {
    button.addEventListener("click", () => {
      const input = document.getElementById(button.dataset.target);
      if (!input) return;
      const isPassword = input.type === "password";
      input.type = isPassword ? "text" : "password";
      button.textContent = isPassword ? "🙈" : "👁";
    });
  });

  /* --------------------------- Utilitaires ---------------------------- */
  function showMessage(element, message, kind) {
    element.hidden = false;
    element.className = `alert alert-${kind}`;
    element.textContent = message;
  }

  function hideMessage(element) {
    element.hidden = true;
    element.textContent = "";
  }

  function setLoading(button, loading, label) {
    button.disabled = loading;
    button.dataset.label = button.dataset.label || button.textContent;
    button.textContent = loading ? "Veuillez patienter…" : label || button.dataset.label;
  }

  /* ----------------------------- Connexion ---------------------------- */
  const loginError = document.getElementById("login-error");
  const loginSubmit = document.getElementById("login-submit");

  // Si un jeton existe déjà, on propose directement l'espace client.
  if (API.token) {
    window.location.replace("app.html");
    return;
  }

  formLogin.addEventListener("submit", async (event) => {
    event.preventDefault();
    hideMessage(loginError);

    const email = document.getElementById("login-email").value.trim();
    const password = document.getElementById("login-password").value;

    if (!email || !password) {
      showMessage(loginError, "Veuillez renseigner votre adresse e-mail et votre mot de passe.", "error");
      return;
    }

    setLoading(loginSubmit, true);
    try {
      const result = await API.request("/api/auth/login", {
        method: "POST",
        body: { email, password },
        auth: false,
      });
      API.setSession(result.access_token, result.user);
      window.location.replace("app.html");
    } catch (error) {
      showMessage(loginError, error.message, "error");
      setLoading(loginSubmit, false, "Se connecter");
    }
  });

  /* -------------------------- Compte de démo -------------------------- */
  document.getElementById("fill-demo").addEventListener("click", async () => {
    document.getElementById("login-email").value = "demo@nexpay.fr";
    document.getElementById("login-password").value = "Demo1234!";
    showTab("login");
    hideMessage(loginError);

    // Connexion immédiate pour aller droit à la démonstration.
    setLoading(loginSubmit, true);
    try {
      const result = await API.request("/api/auth/login", {
        method: "POST",
        body: { email: "demo@nexpay.fr", password: "Demo1234!" },
        auth: false,
      });
      API.setSession(result.access_token, result.user);
      window.location.replace("app.html");
    } catch (error) {
      showMessage(loginError, error.message, "error");
      setLoading(loginSubmit, false, "Se connecter");
    }
  });

  /* ---------------------------- Inscription --------------------------- */
  const registerError = document.getElementById("register-error");
  const registerSuccess = document.getElementById("register-success");

  formRegister.addEventListener("submit", async (event) => {
    event.preventDefault();
    hideMessage(registerError);
    hideMessage(registerSuccess);

    const payload = {
      full_name: document.getElementById("register-name").value.trim(),
      email: document.getElementById("register-email").value.trim(),
      password: document.getElementById("register-password").value,
      pin: document.getElementById("register-pin").value.trim() || "1234",
      city: document.getElementById("register-city").value.trim() || "Paris",
      phone: document.getElementById("register-phone").value.trim(),
    };

    if (!payload.full_name || !payload.email || !payload.password) {
      showMessage(registerError, "Nom, adresse e-mail et mot de passe sont obligatoires.", "error");
      return;
    }
    if (!/^\d{4,6}$/.test(payload.pin)) {
      showMessage(registerError, "Le code PIN doit contenir entre 4 et 6 chiffres.", "error");
      return;
    }

    try {
      const result = await API.request("/api/auth/register", { method: "POST", body: payload, auth: false });
      showMessage(registerSuccess, "Compte créé. Redirection vers votre espace…", "success");
      API.setSession(result.access_token, result.user);
      window.setTimeout(() => window.location.replace("app.html"), 700);
    } catch (error) {
      showMessage(registerError, error.message, "error");
    }
  });
})();
