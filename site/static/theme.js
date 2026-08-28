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

  var ORDER = ["light", "dark", "system"];
  var ICON = {light: "\u2600", dark: "\u263E", system: "\u25D0"};
  var NAME = {light: "Light", dark: "Dark", system: "Auto"};

  function sync() {
    var choice = current();
    document.querySelectorAll("[data-theme-set]").forEach(function (btn) {
      btn.setAttribute("aria-pressed", String(btn.dataset.themeSet === choice));
    });
    var icon = document.querySelector("[data-theme-icon]");
    if (icon) icon.textContent = ICON[choice] || ICON.system;
    var cycle = document.querySelector("[data-theme-cycle]");
    if (cycle) {
      var next = ORDER[(ORDER.indexOf(choice) + 1) % ORDER.length];
      cycle.setAttribute("aria-label",
        "Colour theme: " + NAME[choice] + ". Activate for " + NAME[next] + ".");
    }
  }

  document.addEventListener("click", function (e) {
    var btn = e.target.closest("[data-theme-set]");
    if (btn) { apply(btn.dataset.themeSet); return; }
    if (e.target.closest("[data-theme-cycle]")) {
      apply(ORDER[(ORDER.indexOf(current()) + 1) % ORDER.length]);
    }
  });

  sync();
})();

// The nav scrolls horizontally on a narrow screen, so the current tab can sit
// off-screen -- leaving no visible answer to "which page am I on". Bring it
// into view, without smooth scrolling, so it is simply already correct on load.
(function () {
  var nav = document.querySelector('.site-head nav');
  if (!nav) return;
  var here = nav.querySelector('[aria-current="page"]');
  if (!here) return;
  // Only when it is actually out of view, so a nav that fits is left alone.
  var navBox = nav.getBoundingClientRect();
  var box = here.getBoundingClientRect();
  if (box.left >= navBox.left && box.right <= navBox.right) return;
  // Measured against the nav itself: offsetLeft is relative to the nearest
  // positioned ancestor, which is not the nav, so using it overshot by the
  // width of everything to its left and scrolled the tab half out of view.
  nav.scrollLeft += (box.left - navBox.left) - (nav.clientWidth - box.width) / 2;
})();
