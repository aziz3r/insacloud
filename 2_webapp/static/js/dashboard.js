// InsaCloud — tableau de bord (vanilla JS, aucun script inline : CSP stricte)
(function () {
  "use strict";

  // --- Durée personnalisée --------------------------------------------------
  var custom = document.getElementById("custom"), customRadio = document.getElementById("custom-radio");
  if (custom && customRadio) {
    custom.addEventListener("focus", function () { customRadio.checked = true; });
    custom.addEventListener("input", function () { customRadio.checked = true; customRadio.value = custom.value; });
    customRadio.addEventListener("change", function () { if (customRadio.checked) custom.focus(); });
  }

  // --- Aide contextuelle du mode -------------------------------------------
  var hint = document.getElementById("mode-hint");
  document.querySelectorAll('input[name="mode"]').forEach(function (r) {
    r.addEventListener("change", function () { if (hint) hint.textContent = r.getAttribute("data-hint"); });
  });

  // --- Toasts : fermeture + disparition automatique -------------------------
  document.querySelectorAll(".flash-close").forEach(function (b) {
    b.addEventListener("click", function () { b.closest(".toast").remove(); });
  });
  setTimeout(function () {
    var t = document.getElementById("toasts");
    if (t) { t.classList.add("fade"); setTimeout(function () { t.remove(); }, 700); }
  }, 7000);

  // --- Révélation unique : fermer = effacer définitivement de la page ------
  var reveal = document.getElementById("reveal");
  if (reveal) {
    reveal.querySelector(".reveal-close").addEventListener("click", function () {
      reveal.querySelectorAll(".secret").forEach(function (s) { s.textContent = "••••••••••••••••"; });
      reveal.remove();
    });
  }

  // --- Coffre : compte à rebours, puis rechargement (les accès se masquent) ---
  var vault = document.getElementById("vault-countdown");
  if (vault) {
    var vaultStart = Date.now(), vaultInitial = parseInt(vault.dataset.remaining, 10) || 0;
    (function vtick() {
      var left = Math.max(0, vaultInitial - Math.floor((Date.now() - vaultStart) / 1000));
      var m = Math.floor(left / 60), s = left % 60;
      vault.textContent = (m < 10 ? "0" : "") + m + ":" + (s < 10 ? "0" : "") + s;
      if (left === 0) { location.reload(); return; }
      setTimeout(vtick, 1000);
    })();
  }

  // --- Confirmation des actions destructives --------------------------------
  document.querySelectorAll("form[data-confirm]").forEach(function (f) {
    f.addEventListener("submit", function (e) { if (!confirm(f.getAttribute("data-confirm"))) e.preventDefault(); });
  });

  // --- Compte à rebours + barres ------------------------------------------
  var startedAt = Date.now(), reloadScheduled = false;
  var counters = Array.prototype.slice.call(document.querySelectorAll(".countdown"));
  counters.forEach(function (el) { el.dataset.initial = el.dataset.remaining; el.bar = el.closest("article").querySelector(".bar"); });
  function fmt(s) { var h = Math.floor(s/3600), m = Math.floor((s%3600)/60), x = s%60;
    var mm = (m<10?"0":"")+m, ss = (x<10?"0":"")+x; return h ? h+":"+mm+":"+ss : mm+":"+ss; }
  function tick() {
    var elapsed = Math.floor((Date.now() - startedAt) / 1000);
    counters.forEach(function (el) {
      var left = Math.max(0, parseInt(el.dataset.initial, 10) - elapsed), total = parseInt(el.dataset.total, 10) || 1;
      el.textContent = fmt(left);
      el.bar.style.width = Math.max(0, Math.min(100, left / total * 100)) + "%";
      var urgent = left <= 60; el.bar.classList.toggle("urgent", urgent); el.classList.toggle("urgent", urgent);
      if (left === 0 && !reloadScheduled) { reloadScheduled = true; el.textContent = "expirée"; setTimeout(function () { location.reload(); }, 12000); }
    });
  }
  if (counters.length) { tick(); setInterval(tick, 1000); }

  // --- Copier (presse-papiers) ------------------------------------------------
  var CHECK = '<polyline points="20 6 9 17 4 12"/>';
  document.querySelectorAll(".copy-btn").forEach(function (btn) {
    var ico = btn.querySelector(".ico"), lbl = btn.querySelector(".lbl"), original = ico ? ico.innerHTML : "";
    function done() { if (ico) ico.innerHTML = CHECK; if (lbl) lbl.textContent = "Copié";
      setTimeout(function () { if (ico) ico.innerHTML = original; if (lbl) lbl.textContent = "Copier"; }, 1600); }
    btn.addEventListener("click", function () {
      var text = btn.getAttribute("data-copy");
      if (navigator.clipboard && window.isSecureContext) { navigator.clipboard.writeText(text).then(done); }
      else { var ta = document.createElement("textarea"); ta.value = text; ta.setAttribute("readonly", "");
        ta.classList.add("sr-only"); document.body.appendChild(ta); ta.select();
        try { document.execCommand("copy"); done(); } catch (e) {} document.body.removeChild(ta); }
    });
  });
})();
