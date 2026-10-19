"""Test de bout en bout du parcours de paiement (et des endpoints sécurité).

Utilise le compte yassine@nexpay.fr pour ne pas polluer le compte de démo.
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8011"
EMAIL = "yassine@nexpay.fr"
PASSWORD = "Demo1234!"
PIN = "4321"

PASSED = 0
FAILED = 0


def call(path: str, method: str = "GET", body=None, token: str | None = None):
    req = urllib.request.Request(BASE + path, method=method)
    if body is not None:
        req.add_header("Content-Type", "application/json")
        req.data = json.dumps(body).encode("utf-8")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as err:
        try:
            return err.code, json.loads(err.read().decode("utf-8"))
        except Exception:
            return err.code, {"detail": "<illisible>"}


def check(label: str, condition: bool, info: str = "") -> None:
    global PASSED, FAILED
    if condition:
        PASSED += 1
        print(f"  [OK]   {label} {info}")
    else:
        FAILED += 1
        print(f"  [ECHEC] {label} {info}")


def main() -> None:
    code, login = call("/api/auth/login", "POST", {"email": EMAIL, "password": PASSWORD})
    assert code == 200, (code, login)
    token = login["access_token"]

    ctx_code, ctx = call("/api/payments/context", token=token)
    print(f"\n--- Contexte de paiement (HTTP {ctx_code})")
    device = ctx["devices"][0]
    print(f"    solde {ctx['balance']} €, TTL {ctx['otp_ttl_seconds']} s, appareils {len(ctx['devices'])}, "
          f"villes {len(ctx['cities'])}, catégories {len(ctx['categories'])}")
    check("context expose city_countries", "Lagos" in ctx["city_countries"] and ctx["city_countries"]["Lagos"] == "Nigéria")
    check("context expose le téléphone masqué", "•" in ctx["masked_phone"], ctx["masked_phone"])

    # ------------------------------------------------------------------ T1
    print("\n--- T1 : paiement habituel (accept)")
    code, quote = call(
        "/api/payments/quote",
        "POST",
        {
            "merchant": "Boulangerie Maison Blanc",
            "amount": 7.4,
            "category": "Alimentation",
            "city": "Lyon",
            "country": "France",
            "channel": "carte",
            "device_fingerprint": device["fingerprint"],
            "device_label": device["label"],
        },
        token=token,
    )
    print(f"    décision={quote['decision']} score={quote['risk_score']} résumé: {quote['summary']}")
    check("T1 décision accept", quote["decision"] == "accept", f"(score {quote['risk_score']})")
    check("T1 défi créé", quote["challenge_id"] is not None)
    check("T1 code simulé présent", bool(quote.get("simulation_code")))

    cid, otp = quote["challenge_id"], quote["simulation_code"]
    code, status = call(f"/api/payments/challenge/{cid}", token=token)
    print(f"    défi #{cid}: {status['seconds_remaining']} s restantes, {status['attempts_left']} tentatives")
    check("T1 compte à rebours <= 60 s", 0 < status["seconds_remaining"] <= 60)

    code, result = call("/api/payments/confirm", "POST", {"challenge_id": cid, "code": otp}, token=token)
    print(f"    confirmation (HTTP {code}) : {result['message']} — nouveau solde {result.get('new_balance')} €")
    check("T1 paiement exécuté", code == 200 and result["status"] == "completed")
    check("T1 solde débité", result["new_balance"] is not None and result["new_balance"] < ctx["balance"])

    code, replay = call("/api/payments/confirm", "POST", {"challenge_id": cid, "code": otp}, token=token)
    print(f"    réutilisation du code (HTTP {code}) : {replay.get('detail')}")
    check("T1 code à usage unique (rejeu refusé)", code == 400)

    # ------------------------------------------------------------------ T2
    print("\n--- T2 : code erroné puis verrouillage (3 tentatives)")
    code, quote2 = call(
        "/api/payments/quote",
        "POST",
        {
            "merchant": "Monoprix",
            "amount": 23.9,
            "category": "Alimentation",
            "city": "Lyon",
            "country": "France",
            "channel": "carte",
            "device_fingerprint": device["fingerprint"],
            "device_label": device["label"],
        },
        token=token,
    )
    cid2 = quote2["challenge_id"]
    wrong = "000000" if quote2["simulation_code"] != "000000" else "111111"
    for attempt in range(1, 4):
        code, res = call("/api/payments/confirm", "POST", {"challenge_id": cid2, "code": wrong}, token=token)
        print(f"    tentative {attempt} (HTTP {code}) : {res.get('detail')}")
        check(f"T2 tentative {attempt} refusée", code == 400)
    code, res = call("/api/payments/confirm", "POST", {"challenge_id": cid2, "code": quote2["simulation_code"]}, token=token)
    print(f"    après épuisement (HTTP {code}) : {res.get('detail')}")
    check("T2 défi verrouillé après 3 échecs", code == 400 and "invalid" in str(res.get("detail", "")).lower())

    # ------------------------------------------------------------------ T3
    print("\n--- T3 : montant inhabituel (verify + PIN)")
    code, quote3 = call(
        "/api/payments/quote",
        "POST",
        {
            "merchant": "TechDeal Store",
            "amount": 850.0,
            "category": "Shopping",
            "city": "Lyon",
            "country": "France",
            "channel": "en_ligne",
            "simulated_hour": 15,
            "device_fingerprint": device["fingerprint"],
            "device_label": device["label"],
        },
        token=token,
    )
    print(f"    décision={quote3['decision']} score={quote3['risk_score']} pin_requis={quote3['require_pin']}")
    print(f"    facteurs : {', '.join(f['label'] for f in quote3['factors'])}")
    check("T3 décision verify (et non accept)", quote3["decision"] == "verify", f"(score {quote3['risk_score']})")
    check("T3 PIN exigé", quote3["require_pin"] is True)

    cid3, otp3 = quote3["challenge_id"], quote3["simulation_code"]
    if cid3:
        code, res = call("/api/payments/confirm", "POST", {"challenge_id": cid3, "code": otp3}, token=token)
        print(f"    sans PIN (HTTP {code}) : {res.get('detail')}")
        check("T3 refus sans PIN", code == 400)

        code, res = call(
            "/api/payments/confirm", "POST", {"challenge_id": cid3, "code": otp3, "pin": "0000"}, token=token
        )
        print(f"    mauvais PIN (HTTP {code}) : {res.get('detail')}")
        check("T3 refus mauvais PIN", code == 400)

        code, res = call(
            "/api/payments/confirm", "POST", {"challenge_id": cid3, "code": otp3, "pin": PIN}, token=token
        )
        print(f"    avec PIN (HTTP {code}) : {res.get('message')} — solde {res.get('new_balance')} €")
        check("T3 paiement validé avec PIN", code == 200 and res["status"] == "completed")

    # ------------------------------------------------------------------ T4
    print("\n--- T4 : scénario Lagos (blocage)")
    code, quote4 = call(
        "/api/payments/quote",
        "POST",
        {
            "merchant": "Western Union",
            "amount": 620.0,
            "category": "Transfert international",
            "city": "Lagos",
            "country": "Nigéria",
            "channel": "virement",
            "device_label": "Terminal pirate",
        },
        token=token,
    )
    print(f"    décision={quote4['decision']} score={quote4['risk_score']}")
    print(f"    facteurs : {', '.join(f['label'] for f in quote4['factors'])}")
    check("T4 décision block", quote4["decision"] == "block", f"(score {quote4['risk_score']})")
    check("T4 aucun défi émis", quote4["challenge_id"] is None)

    # ------------------------------------------------------------------ T5
    print("\n--- T5 : annulation d'un défi")
    code, quote5 = call(
        "/api/payments/quote",
        "POST",
        {
            "merchant": "Fnac",
            "amount": 15.0,
            "category": "Loisirs",
            "city": "Lyon",
            "country": "France",
            "channel": "carte",
            "device_fingerprint": device["fingerprint"],
            "device_label": device["label"],
        },
        token=token,
    )
    code, res = call(f"/api/payments/challenge/{quote5['challenge_id']}/cancel", "POST", {}, token=token)
    print(f"    annulation (HTTP {code}) : {res.get('message')}")
    check("T5 annulation acceptée", code == 200)
    code, res = call(
        "/api/payments/confirm", "POST", {"challenge_id": quote5["challenge_id"], "code": quote5["simulation_code"]}, token=token
    )
    print(f"    confirmation après annulation (HTTP {code}) : {res.get('detail')}")
    check("T5 confirm impossible après annulation", code == 400)

    # ------------------------------------------------------------------ T6
    print("\n--- T6 : endpoints du centre de sécurité")
    for path, label in [
        ("/api/security/profile", "profil"),
        ("/api/security/alerts", "alertes"),
        ("/api/security/alerts/summary", "résumé alertes"),
        ("/api/security/anomalies", "anomalies"),
        ("/api/security/recommendations", "recommandations"),
        ("/api/security/model-card", "carte de modèle"),
        ("/api/security/devices", "appareils"),
        ("/api/security/audit", "journal d'audit"),
    ]:
        code, payload = call(path, token=token)
        size = len(payload) if isinstance(payload, list) else "objet"
        check(f"T6 {label}", code == 200, f"(HTTP {code}, {size})")

    code, recos = call("/api/security/recommendations", token=token)
    print(f"    {len(recos)} recommandations :")
    for reco in recos:
        print(f"      · [{reco['kind']}] {reco['title']}")

    code, anomalies = call("/api/security/anomalies", token=token)
    print(f"    {len(anomalies)} anomalies récentes :")
    for item in anomalies[:4]:
        txn = item["transaction"]
        print(f"      · score IA {item['anomaly_score']:.3f} — {txn['merchant']} ({abs(txn['amount']):.2f} €) "
              f"[{txn['status']}]")

    # ------------------------------------------------------------------ T7
    print("\n--- T7 : liste des transactions et filtres")
    code, page = call("/api/transactions?limit=5", token=token)
    check("T7 pagination", code == 200 and len(page["items"]) == 5, f"(total {page['total']})")
    code, flagged = call("/api/transactions?flagged=true&limit=10", token=token)
    check("T7 filtre 'flagged'", code == 200 and flagged["total"] >= 1, f"({flagged['total']} opérations signalées)")
    code, searched = call("/api/transactions?search=loyer", token=token)
    check("T7 recherche plein texte", code == 200 and searched["total"] >= 1, f"({searched['total']} résultats)")
    code, cats = call("/api/transactions/categories", token=token)
    check("T7 catégories", code == 200 and isinstance(cats.get("categories"), list) and len(cats["categories"]) >= 5,
          f"({len(cats.get('categories', []))} catégories)")

    # ------------------------------------------------------------------ T8
    print("\n--- T8 : déplacement impossible (paiement local puis Lagos)")
    code, prep = call(
        "/api/payments/quote",
        "POST",
        {
            "merchant": "Boulangerie Maison Blanc",
            "amount": 6.2,
            "category": "Alimentation",
            "city": "Lyon",
            "country": "France",
            "channel": "carte",
            "simulated_hour": 21,
            "simulated_minute": 0,
            "device_fingerprint": device["fingerprint"],
            "device_label": device["label"],
        },
        token=token,
    )
    code, res = call(
        "/api/payments/confirm",
        "POST",
        {"challenge_id": prep["challenge_id"], "code": prep["simulation_code"], "pin": PIN},
        token=token,
    )
    print(f"    paiement local : {prep['decision']} ({prep['risk_score']}) confirmé={res.get('status')}")
    check("T8 paiement local exécuté", code == 200 and res.get("status") == "completed")

    code, journey = call(
        "/api/payments/quote",
        "POST",
        {
            "merchant": "Western Union",
            "amount": 620.0,
            "category": "Transfert international",
            "city": "Lagos",
            "country": "Nigéria",
            "channel": "virement",
            "simulated_hour": 21,
            "simulated_minute": 4,
            "device_label": "Terminal pirate",
        },
        token=token,
    )
    labels = [factor["label"] for factor in journey["factors"]]
    print(f"    Lagos : {journey['decision']} ({journey['risk_score']}) — {', '.join(labels)}")
    check("T8 Lagos bloqué", journey["decision"] == "block", f"(score {journey['risk_score']})")
    check("T8 facteur « déplacement impossible »", "Déplacement impossible" in labels)

    print(f"\n=== Bilan : {PASSED} vérifications réussies, {FAILED} échecs ===")
    sys.exit(1 if FAILED else 0)


if __name__ == "__main__":
    main()
