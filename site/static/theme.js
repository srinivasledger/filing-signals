// Theme toggle. Three states, because "auto" is a real preference and not the
// absence of one: it means follow the operating system from now on.
(function () {
  var root = document.documentElement;
  var KEY = "fs-theme";

  function current() {
    try { return localStorage.getItem(KEY) || "system"; } catch (e) { return "system"; }
  }

  function apply(choice) {
    if (choice === "system") {
      delete root.dataset.theme;
      try { localStorage.removeItem(KEY); } catch (e) {}
    } else {
      root.dataset.theme = choice;
      try { localStorage.setItem(KEY, choice); } catch (e) {}
    }
    sync();
  }

  function sync() {
    var choice = current();
    document.querySelectorAll("[data-theme-set]").forEach(function (btn) {
      btn.setAttribute("aria-pressed", String(btn.dataset.themeSet === choice));
    });
  }

  document.addEventListener("click", function (e) {
    var btn = e.target.closest("[data-theme-set]");
    if (btn) apply(btn.dataset.themeSet);
  });

  sync();
})();
