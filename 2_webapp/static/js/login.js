// InsaCloud — page de connexion : bascule Connexion / Créer un compte (aucune donnée sensible)
(function () {
  "use strict";
  var active   = document.body.getAttribute("data-active-tab") || "login";
  var radios   = document.querySelectorAll('input[name="tab"]');
  var panels   = document.querySelectorAll(".tab-panel");
  var title    = document.getElementById("form-title");
  var subtitle = document.getElementById("form-subtitle");
  var copy = {
    login:    ["Bon retour parmi nous", "Connectez-vous pour retrouver vos machines."],
    register: ["Créer un compte",       "Mot de passe : 10 caractères minimum."]
  };
  function show(tab) {
    panels.forEach(function (p) { p.classList.toggle("hidden", p.id !== "panel-" + tab); });
    radios.forEach(function (r) { r.checked = (r.value === tab); });
    title.textContent = copy[tab][0]; subtitle.textContent = copy[tab][1];
    var first = document.querySelector("#panel-" + tab + " input:not([type=hidden])");
    if (first && document.activeElement !== first) first.focus();
  }
  radios.forEach(function (r) { r.addEventListener("change", function () { show(r.value); }); });
  show(active);
})();
