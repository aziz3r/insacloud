// InsaCloud — page de connexion : bascule Connexion / Créer un compte
(function () {
  "use strict";
  var active   = document.body.getAttribute("data-active-tab") || "login";
  var panels   = document.querySelectorAll(".tab-panel");
  var title    = document.getElementById("form-title");
  var subtitle = document.getElementById("form-subtitle");
  var copy = {
    login:    ["Connexion",       "Retrouvez vos machines."],
    register: ["Créer un compte", "Un identifiant et un mot de passe suffisent."]
  };
  function show(tab) {
    panels.forEach(function (p) { p.classList.toggle("hidden", p.id !== "panel-" + tab); });
    title.textContent = copy[tab][0]; subtitle.textContent = copy[tab][1];
    var first = document.querySelector("#panel-" + tab + " input:not([type=hidden])");
    if (first && document.activeElement !== first) first.focus();
  }
  document.querySelectorAll("[data-tab-link]").forEach(function (a) {
    a.addEventListener("click", function (e) { e.preventDefault(); show(a.getAttribute("data-tab-link")); });
  });
  show(active);
})();
