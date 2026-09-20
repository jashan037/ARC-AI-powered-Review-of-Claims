/* ARC demo UI. Talks to the API on the same origin: GET /health, GET /samples, POST /sessions, POST /sessions/{id}/claim, POST /sessions/{id}/chat.
 * No build step and no libraries other than md.js (a small markdown renderer) and labels.js (label helpers) next to this file.
 * Everything from the API is inserted as text; only answer text goes through ARCMarkdown, which escapes HTML first.
 * Answers show summary_markdown first; each entry of `sections` is a collapsed "Show details" block. Citations are chips that open their excerpt.
 * The "How ARC got this answer" panel uses ONLY the sanitized `trace_summary` field (tool, ok, ms). It never sees or shows tool arguments. */
(function () {
  "use strict";

  var TIMEOUT_MS = 80000;   // the server itself stops a turn after 60 s and answers with a "try again" message; this is the safety net

  // Quick questions, taken word for word from docs/DEMO.md (the "You type" column). tests/test_web_ui.py fails if the two drift apart.
  // Only the group for the loaded claim is shown ("" = no claim, "*" = any other claim).
  var QUICK = [
    { group: "Policy questions (no claim)", claim: "", items: [
      { label: "Is knee replacement covered?", q: "Is knee replacement covered and what is the waiting period?" },
      { label: "Claim settlement ratio", q: "What was HDFC ERGO's claim settlement ratio last financial year?" },
      { label: "Waiting period served? (cataract)", q: "Policy started 1 March 2025. The insured was admitted on 15 July 2026 for cataract surgery. Has the waiting period been served?" }
    ] },
    { group: "This claim (TC07, appendectomy)", claim: "TC07", items: [
      { label: "Assess this claim", q: "Assess this claim" },
      { label: "Why was room rent deducted?", q: "Why was the room rent deducted on this claim?" },
      { label: "Which documents are missing?", q: "Which documents are still missing for this claim?" },
      { label: "What if room rent were 5,000?", q: "What would the payable amount be if the room rent had been 5,000 a day?" }
    ] },
    { group: "This claim (TC02, gastroenteritis)", claim: "TC02", items: [
      { label: "Assess this claim", q: "Assess this claim" }
    ] },
    { group: "This claim", claim: "*", items: [
      { label: "Assess this claim", q: "Assess this claim" },
      { label: "Which documents are missing?", q: "Which documents are still missing for this claim?" }
    ] }
  ];

  var TYPE_LABEL = {
    claim_assessment: "Claim assessment", coverage_answer: "Coverage", waiting_period_answer: "Waiting period",
    deduction_explanation: "How the amount was worked out", documents_answer: "Documents", definition_answer: "Policy wording",
    insufficient_information: "Insufficient information", general_answer: "General"
  };

  var el = {
    select: document.getElementById("claim-select"), summary: document.getElementById("claim-summary"), quick: document.getElementById("quick"),
    transcript: document.getElementById("transcript"), hello: document.getElementById("hello"), form: document.getElementById("composer"),
    input: document.getElementById("chat-input"), send: document.getElementById("send"), status: document.getElementById("status"),
    newChat: document.getElementById("new-chat"), badge: document.getElementById("live-badge"), banner: document.getElementById("offline-banner")
  };
  var helloHtml = el.hello.outerHTML;
  var state = { sid: null, claim: "", busy: false, last: "", samples: [] };

  // ------------------------------------------------------------------ small helpers
  function h(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }

  function ApiError(kind, message) { this.kind = kind; this.message = message || ""; }

  function api(path, method, body) {
    var ctrl = new AbortController();
    var timer = setTimeout(function () { ctrl.abort(); }, TIMEOUT_MS);
    return fetch(path, {
      method: method || "GET", headers: body ? { "Content-Type": "application/json" } : undefined,
      body: body ? JSON.stringify(body) : undefined, signal: ctrl.signal
    }).then(function (res) {
      return res.json().catch(function () { return null; }).then(function (data) {
        if (!res.ok) throw new ApiError(res.status, data && data.error && data.error.message);
        return data;
      });
    }).catch(function (e) {
      if (e instanceof ApiError) throw e;
      throw new ApiError(e && e.name === "AbortError" ? "timeout" : "network");
    }).then(function (data) { clearTimeout(timer); return data; }, function (e) { clearTimeout(timer); throw e; });
  }

  function friendly(err) {
    var k = err && err.kind;
    if (k === "timeout") return "ARC took too long to answer, so nothing was assessed. Please try again.";
    if (k === "network") return "ARC cannot reach the server. Check that it is running, then try again.";
    if (k === 413) return "That message is too large. Please shorten it.";
    if (k === 422) return err.message ? "ARC could not use that: " + err.message : "ARC could not use that request. Please check it and try again.";
    if (k === 404) return "This conversation is no longer available on the server.";
    if (typeof k === "number" && k >= 500) return "Something went wrong on the server. Please try again in a moment.";
    return "Something unexpected happened. Please try again.";
  }

  function setStatus(text) { el.status.textContent = text || ""; }

  function setBusy(busy) {
    state.busy = busy;
    el.send.disabled = busy || !state.sid;
    el.input.disabled = busy || !state.sid;
    el.select.disabled = busy || !state.samples.length;
    el.newChat.disabled = busy;
    Array.prototype.forEach.call(el.quick.querySelectorAll("button"), function (b) { b.disabled = busy || !state.sid; });
  }

  function scrollTo(node, where) {
    var calm = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    node.scrollIntoView({ block: where, behavior: calm ? "auto" : "smooth" });
  }

  function clearTranscript() { el.transcript.innerHTML = helloHtml; el.hello = document.getElementById("hello"); }

  function dropHello() { var n = document.getElementById("hello"); if (n) n.remove(); }

  // ------------------------------------------------------------------ live / offline badge (GET /health)
  function showHealth(d) {
    el.badge.className = "live-badge";
    if (d && d.live) {
      el.badge.classList.add("live-on");
      el.badge.textContent = "Live agent";
      el.badge.title = "Answers come from the real Azure agent and index";
      el.banner.hidden = true;
    } else {
      el.badge.classList.add("live-off");
      el.badge.textContent = "Offline stand-in";
      el.badge.title = "Answers come from a keyword router, not the real agent";
      el.banner.hidden = false;
    }
  }

  function refreshHealth() {
    return api("/health").then(showHealth, function () {
      el.badge.className = "live-badge live-down";
      el.badge.textContent = "Server unreachable";
      el.banner.hidden = true;
    });
  }

  // ------------------------------------------------------------------ session and claim
  function startSession() {
    return api("/sessions", "POST").then(function (d) { state.sid = d.session_id; });
  }

  function money(n) { return typeof n === "number" ? "₹" + n.toLocaleString("en-IN") : ""; }

  function day(iso) {
    var d = new Date(iso);
    return isNaN(d) ? "" : d.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
  }

  function showClaim(c) {
    el.summary.textContent = "";
    if (!c) { el.summary.hidden = true; return; }
    var dl = h("dl");
    [["Claim", c.claim_id], ["Insured", c.insured], ["Plan", c.plan], ["Diagnosis", c.diagnosis], ["Procedure", c.procedure],
     ["Stay", day(c.admission) + " to " + day(c.discharge)], ["Claimed", money(c.claimed_amount)]].forEach(function (row) {
      if (!row[1]) return;
      dl.appendChild(h("dt", null, row[0]));
      dl.appendChild(h("dd", null, row[1]));
    });
    el.summary.appendChild(dl);
    el.summary.appendChild(h("p", "muted", "Synthetic demo claim."));
    el.summary.hidden = false;
  }

  function loadClaim(sampleId) {
    if (!sampleId) { showClaim(null); return Promise.resolve(); }
    return api("/sessions/" + state.sid + "/claim", "POST", { sample_id: sampleId }).then(function (d) { showClaim(d.claim); });
  }

  // a fresh session (and a fresh transcript) for the chosen claim, so an old conversation never mixes with a new claim
  function switchClaim(sampleId) {
    setBusy(true);
    setStatus(sampleId ? "Loading " + sampleId + "..." : "Starting a new conversation...");
    return startSession().then(function () { return loadClaim(sampleId); }).then(function () {
      state.claim = sampleId;
      el.select.value = sampleId;
      clearTranscript();
      renderQuick();
      setStatus(sampleId ? sampleId + " is loaded." : "No claim loaded. Policy questions only.");
    }).catch(function (e) {
      el.select.value = state.claim;
      setStatus(friendly(e));
      throw e;
    }).then(function () { setBusy(false); }, function (e) { setBusy(false); throw e; });
  }

  // ------------------------------------------------------------------ answers
  function withoutEvidence(md) {
    // fallback for an older server: the full answer carries its own "Evidence" section, which the citation chips replace
    return String(md || "").replace(/(^|\n)###\s+Evidence[^\n]*\n[\s\S]*?(?=\n>\s|\s*$)/, "$1").replace(/\n{3,}/g, "\n\n");
  }

  function sectionBlock(s) {
    var d = h("details", "sect");
    d.dataset.id = s.id;
    var sum = h("summary");
    var dot = h("span", "dot dot-" + (s.status || "info"));
    dot.setAttribute("aria-hidden", "true");
    sum.appendChild(dot);
    sum.appendChild(h("span", "sect-title", s.title));
    var hint = h("span", "sect-hint");
    hint.appendChild(h("span", "hint-closed", "Show details"));
    hint.appendChild(h("span", "hint-open", "Hide details"));
    sum.appendChild(hint);
    d.appendChild(sum);
    var body = h("div", "md sect-body");
    body.innerHTML = ARCMarkdown.render(s.markdown);
    d.appendChild(body);
    return d;
  }

  function sectionsBlock(sections) {
    // the evidence section repeats what the citation chips show, so the chips stand in for it
    var shown = (sections || []).filter(function (s) { return s && s.id !== "evidence"; });
    if (!shown.length) return null;
    var box = h("div", "sections");
    var tools = h("div", "answer-tools");
    tools.appendChild(h("h3", null, "Details"));
    var toggle = h("button", "link-toggle", "Expand all");
    toggle.type = "button";
    tools.appendChild(toggle);
    box.appendChild(tools);
    var blocks = shown.map(sectionBlock);
    blocks.forEach(function (b) { box.appendChild(b); });
    var sync = function () {
      var all = blocks.every(function (b) { return b.open; });
      toggle.textContent = all ? "Collapse all" : "Expand all";
      toggle.setAttribute("aria-pressed", all ? "true" : "false");
    };
    toggle.addEventListener("click", function () {
      var open = !blocks.every(function (b) { return b.open; });
      blocks.forEach(function (b) { b.open = open; });
      sync();
    });
    blocks.forEach(function (b) { b.addEventListener("toggle", sync); });
    return box;
  }

  function chipList(citations) {
    if (!citations || !citations.length) return null;
    var box = h("section", "citations");
    box.setAttribute("aria-label", "Citations");
    box.appendChild(h("h3", null, "Citations (" + citations.length + ")"));
    var row = h("ul", "chips");
    var quotes = h("div", "chip-quotes");
    citations.forEach(function (c, i) {
      var label = c.label || c.citation || c.clause || "Policy";
      var li = h("li");
      var chip = h("button", "chip", label);
      chip.type = "button";
      chip.setAttribute("aria-expanded", "false");
      chip.title = c.citation || label;
      var quote = h("blockquote", "chip-quote");
      quote.hidden = true;
      quote.id = "q" + Date.now().toString(36) + i;
      chip.setAttribute("aria-controls", quote.id);
      quote.appendChild(h("b", null, c.citation || label));
      if (c.excerpt) quote.appendChild(h("i", null, "“" + c.excerpt + "”"));
      chip.addEventListener("click", function () {
        var open = quote.hidden;
        quote.hidden = !open;
        chip.setAttribute("aria-expanded", open ? "true" : "false");
      });
      li.appendChild(chip);
      row.appendChild(li);
      quotes.appendChild(quote);
    });
    box.appendChild(row);
    box.appendChild(quotes);
    return box;
  }

  function howPanel(steps) {
    var d = h("details", "how");
    d.appendChild(h("summary", null, "How ARC got this answer"));
    var body = h("div", "how-body");
    body.appendChild(h("p", null, steps && steps.length ? "The tools ARC used, in order. Arguments are never shown." : "ARC used no tools for this answer."));
    if (steps && steps.length) {
      var ul = h("ol", "how-list");
      steps.forEach(function (s, i) {
        var li = h("li");
        li.appendChild(h("span", "how-step", String(i + 1) + "."));
        li.appendChild(h("span", "how-tool", String(s.tool)));
        li.appendChild(h("span", s.ok ? "how-ok" : "how-fail", s.ok ? "✓ ok" : "✗ failed"));
        li.appendChild(h("span", "how-ms", s.ms == null ? "" : s.ms + " ms"));
        ul.appendChild(li);
      });
      body.appendChild(ul);
    }
    d.appendChild(body);
    return d;
  }

  function retryButton(question) {
    var b = h("button", "btn retry", "Try again");
    b.type = "button";
    b.addEventListener("click", function () { if (!state.busy) ask(question, true); });
    return b;
  }

  function answerCard(data, question) {
    var notOk = data.status && data.status !== "ok";
    var card = h("article", "answer" + (notOk ? " warn" : ""));
    var head = h("div", "answer-head");
    head.appendChild(h("span", "badge", notOk ? "Please try again" : (TYPE_LABEL[data.answer_type] || "Answer")));
    head.appendChild(h("span", "who", "ARC"));
    card.appendChild(head);
    var hasCompact = typeof data.summary_markdown === "string" && data.summary_markdown.trim();
    var body = h("div", "md");
    body.innerHTML = ARCMarkdown.render(hasCompact ? data.summary_markdown : withoutEvidence(data.answer_markdown));
    card.appendChild(body);
    if (notOk) card.appendChild(retryButton(question));
    var sections = hasCompact ? sectionsBlock(data.sections) : null;
    if (sections) card.appendChild(sections);
    var chips = chipList(data.citations);
    if (chips) card.appendChild(chips);
    card.appendChild(howPanel(data.trace_summary));
    return card;
  }

  function errorCard(err, question) {
    var card = h("article", "answer error");
    var head = h("div", "answer-head");
    head.appendChild(h("span", "badge", "Problem"));
    head.appendChild(h("span", "who", "ARC"));
    card.appendChild(head);
    card.appendChild(h("p", null, friendly(err)));
    if (question) card.appendChild(retryButton(question));
    return card;
  }

  function loadingNode() {
    var box = h("div", "loading");
    box.setAttribute("role", "status");
    box.appendChild(h("div", "spinner"));
    var text = h("span", null, "ARC is working…");
    box.appendChild(text);
    var t0 = Date.now();
    var timer = setInterval(function () {
      var s = Math.round((Date.now() - t0) / 1000);
      text.textContent = "ARC is working… " + s + " s" + (s >= 15 ? " (answers can take up to a minute)" : "");
    }, 1000);
    box.stop = function () { clearInterval(timer); box.remove(); };
    return box;
  }

  function ask(question, isRetry) {
    question = String(question || "").trim();
    if (!question || state.busy || !state.sid) return Promise.resolve();
    state.last = question;
    dropHello();
    if (!isRetry) el.transcript.appendChild(h("div", "msg-user", question));
    var loading = loadingNode();
    el.transcript.appendChild(loading);
    scrollTo(loading, "nearest");
    setBusy(true);
    setStatus("");
    return api("/sessions/" + state.sid + "/chat", "POST", { message: question }).catch(function (e) {
      if (e.kind !== 404) throw e;
      // the server no longer knows this session (it was restarted): start a new one with the same claim, and ask again once
      return startSession().then(function () { return loadClaim(state.claim); }).then(function () {
        return api("/sessions/" + state.sid + "/chat", "POST", { message: question });
      });
    }).then(function (data) {
      loading.stop();
      var card = answerCard(data, question);
      el.transcript.appendChild(card);
      scrollTo(card, "start");
      setStatus(data.status && data.status !== "ok" ? "That did not finish. You can try again." : "");
    }, function (e) {
      loading.stop();
      var card = errorCard(e, question);
      el.transcript.appendChild(card);
      scrollTo(card, "nearest");
    }).then(function () { setBusy(false); if (!el.input.disabled) el.input.focus({ preventScroll: true }); });
  }

  function submit() {
    var q = el.input.value;
    if (!q.trim() || state.busy) return;
    el.input.value = "";
    ask(q);
  }

  // ------------------------------------------------------------------ wiring
  function renderQuick() {
    el.quick.textContent = "";
    var groups = ARCLabels.quickGroupsFor(state.claim, QUICK);
    groups.forEach(function (g) {
      var box = h("div", "q-group");
      box.appendChild(h("h3", null, g.group));
      g.items.forEach(function (it) {
        var b = h("button", "q-btn", it.label);
        b.type = "button";
        b.title = it.q;
        b.disabled = state.busy || !state.sid;
        b.addEventListener("click", function () { ask(it.q); });
        box.appendChild(b);
      });
      el.quick.appendChild(box);
    });
    if (!groups.length) el.quick.appendChild(h("p", "muted", "Pick a claim to see its quick questions."));
  }

  function init() {
    setStatus("Starting a session…");
    refreshHealth();
    return Promise.all([startSession(), api("/samples")]).then(function (r) {
      state.samples = r[1] || [];
      el.select.textContent = "";
      var none = h("option", null, "No claim (policy questions)");
      none.value = "";
      el.select.appendChild(none);
      state.samples.forEach(function (s) {
        var o = h("option", null, ARCLabels.claimOptionLabel(s.id, s.title));
        o.value = s.id;
        o.title = s.id + ": " + s.title + (s.purpose ? " — " + s.purpose : "");
        el.select.appendChild(o);
      });
      setBusy(false);
      renderQuick();
      setStatus("Ready. No claim loaded.");
      el.input.focus({ preventScroll: true });
    }).catch(function (e) {
      setBusy(true);
      var card = errorCard(e, null);
      var b = h("button", "btn retry", "Reconnect");
      b.type = "button";
      b.addEventListener("click", function () { card.remove(); init(); });
      card.appendChild(b);
      dropHello();
      el.transcript.appendChild(card);
      setStatus("");
    });
  }

  el.select.addEventListener("change", function () { switchClaim(el.select.value).catch(function () {}); });
  el.newChat.addEventListener("click", function () { switchClaim(state.claim).catch(function () {}); });
  el.form.addEventListener("submit", function (e) { e.preventDefault(); submit(); });
  el.input.addEventListener("keydown", function (e) {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); submit(); }
  });

  renderQuick();
  setBusy(true);
  init();
})();
