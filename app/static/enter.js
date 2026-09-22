/* Runs in <head>, before the first paint: arms the landing entrance (app.js plays it, then drops it). Nothing happens with reduced motion. */
(function () {
  try {
    if (location.pathname !== "/") return;
    if (window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    document.documentElement.setAttribute("data-enter", "pending");
  } catch (e) { /* the page simply shows its final state */ }
})();
