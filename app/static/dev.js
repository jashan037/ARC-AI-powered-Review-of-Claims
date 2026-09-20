/* Developer extras for the customer page. Loaded ONLY with ?dev=1 (see customer.js). Not part of what a customer sees.
 * - a live / offline badge from GET /health, with a warning banner when the offline stand-in is answering
 * - under each answer, a collapsed "How ARC got this answer" panel from the sanitized trace_summary field (tool name, ok, milliseconds; never arguments) */
(function () {
  "use strict";
  var css = document.createElement("link");
  css.rel = "stylesheet";
  css.href = "/static/dev.css";
  document.head.appendChild(css);

  function h(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }

  var slot = document.getElementById("dev-slot");
  var badge = h("span", "dev-badge dev-unknown", "Checking…");
  badge.setAttribute("role", "status");
  slot.appendChild(badge);
  slot.appendChild(h("span", "dev-tag", "dev mode"));

  fetch("/health").then(function (r) { return r.json(); }).then(function (d) {
    badge.className = "dev-badge " + (d.live ? "dev-live" : "dev-offline");
    badge.textContent = d.live ? "Live agent" : "Offline stand-in";
    badge.title = d.live ? "Answers come from the real Azure agent and index" : "Answers come from a keyword router, not the real agent";
    if (!d.live) {
      var banner = h("div", "dev-banner");
      banner.setAttribute("role", "alert");
      banner.textContent = "Offline stand-in: these answers come from a simple keyword router, not the real ARC agent. Do not present them as ARC's answers.";
      document.querySelector(".site-header").insertAdjacentElement("afterend", banner);
    }
  }, function () { badge.className = "dev-badge dev-down"; badge.textContent = "Server unreachable"; });

  document.addEventListener("arc:answer", function (e) {
    var steps = e.detail.data.trace_summary || [];
    var d = h("details", "dev-trace");
    d.appendChild(h("summary", null, "How ARC got this answer"));
    var body = h("div", "dev-body");
    body.appendChild(h("p", null, steps.length ? "The tools ARC used, in order. Arguments are never shown." : "ARC used no tools for this answer."));
    if (steps.length) {
      var ol = h("ol", "dev-list");
      steps.forEach(function (s, i) {
        var li = h("li");
        li.appendChild(h("span", "dev-step", String(i + 1) + "."));
        li.appendChild(h("span", "dev-name", String(s.tool)));
        li.appendChild(h("span", s.ok ? "dev-ok" : "dev-fail", s.ok ? "✓ ok" : "✗ failed"));
        li.appendChild(h("span", "dev-ms", s.ms == null ? "" : s.ms + " ms"));
        ol.appendChild(li);
      });
      body.appendChild(ol);
    }
    d.appendChild(body);
    e.detail.card.appendChild(d);
  });
})();
