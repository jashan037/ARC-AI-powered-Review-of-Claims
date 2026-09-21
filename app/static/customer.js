/* ARC customer page: two screens in one page. Screen 1 uploads and checks documents; screen 2 is the chat about the claim.
 * Talks to the same origin: POST /sessions, POST /sessions/{id}/documents (and /documents/sample), POST /sessions/{id}/intake, POST /sessions/{id}/chat.
 * Everything from the server is inserted as text; only answer text goes through ARCMarkdown, which escapes HTML first.
 * Developer extras (?dev=1) live in dev.js, which is only loaded in that mode. */
(function () {
  "use strict";

  var MAX_FILES = 15;
  var MAX_BYTES = 5 * 1024 * 1024;
  var TIMEOUT_MS = 80000;   // the server stops a turn after 60 s and answers with a "try again" message; this is the safety net

  // The documents we ask for. tests/test_web_ui.py fails if this list and the server's list drift apart.
  var EXPECTED = [
    { id: "policy_schedule", label: "Policy schedule" },
    { id: "claim_form", label: "Claim form" },
    { id: "photo_id_age_proof", label: "Photo ID and age proof" },
    { id: "discharge_summary", label: "Discharge summary" },
    { id: "final_bill_receipts", label: "Final hospital bill with receipts" },
    { id: "diagnostic_reports_bills", label: "Lab and imaging reports" },
    { id: "previous_consultation_papers", label: "Consultation papers" },
    { id: "pharmacy_bills_prescription", label: "Pharmacy bills with prescription" },
    { id: "kyc", label: "KYC form (claims above ₹1 lakh)" },
    { id: "neft_form", label: "NEFT form with cancelled cheque" }
  ];

  var $ = function (id) { return document.getElementById(id); };
  var el = {
    upload: $("screen-upload"), chat: $("screen-chat"), drop: $("dropzone"), input: $("file-input"), progress: $("progress"), bar: document.querySelector(".bar"),
    fill: $("bar-fill"), progressText: $("progress-text"), files: $("file-list"), attention: $("attention"), reasons: $("reasons"), addMore: $("add-more"),
    filesBox: $("files-box"), filesSummary: $("files-summary"), ready: $("ready"), readyText: $("ready-text"), cont: $("continue"), checklist: $("checklist"), sample: $("use-sample"),
    thread: $("thread"), form: $("composer"), q: $("chat-input"), send: $("send"), addDoc: $("add-doc"), addInput: $("add-input"), note: $("note")
  };
  var state = { sid: null, busy: false, intake: null, checklist: null, replaceFor: null, last: "" };

  // ------------------------------------------------------------------ helpers
  function h(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }

  function ApiError(kind, message) { this.kind = kind; this.message = message || ""; }

  function request(method, path, body, isForm, onProgress) {
    return new Promise(function (resolve, reject) {
      var x = new XMLHttpRequest();
      x.open(method, path);
      x.timeout = TIMEOUT_MS;
      if (body && !isForm) x.setRequestHeader("Content-Type", "application/json");
      if (onProgress && x.upload) x.upload.onprogress = function (e) { if (e.lengthComputable) onProgress(e.loaded / e.total); };
      x.onload = function () {
        var data = null;
        try { data = JSON.parse(x.responseText); } catch (e) { /* not JSON */ }
        if (x.status >= 200 && x.status < 300) resolve(data);
        else reject(new ApiError(x.status, data && data.error && data.error.message));
      };
      x.onerror = function () { reject(new ApiError("network")); };
      x.ontimeout = function () { reject(new ApiError("timeout")); };
      x.send(body ? (isForm ? body : JSON.stringify(body)) : null);
    });
  }

  function friendly(err) {
    var k = err && err.kind;
    if (k === "timeout") return "That took too long, so nothing was changed. Please try again.";
    if (k === "network") return "We couldn't reach the server. Please check your connection and try again.";
    if (k === 413) return "That is too much to send at once. Please add fewer or smaller files.";
    if (k === 422) return err.message || "We couldn't use that. Please check it and try again.";
    if (k === 404) return isLostSession(err) ? "The server was restarted, so your visit ended. We've started a new one." : "This service is out of date, so we can't do that yet. Please try again later.";
    if (k === "lost") return err.message;
    if (k === "outdated") return "This ARC service is out of date, so uploads aren't available yet. If you run ARC yourself, restart it with scripts/run_demo.sh and reload this page.";
    return "Something went wrong on our side. Please try again in a moment.";
  }

  // the server answers 404 "Unknown session" when it no longer knows this visit (it was restarted); any other 404 means the route itself is missing (an old server)
  function isLostSession(err) { return !!err && err.kind === 404 && /unknown session/i.test(err.message || ""); }

  function hasDocuments() { return (state.checklist || []).some(function (c) { return c.state !== "missing"; }); }

  function newSession() {
    return request("POST", "/sessions", { audience: "customer" }).then(function (d) {
      state.sid = d.session_id;
      return d;
    });
  }

  // A call for this visit. If the server has forgotten the visit and nothing was uploaded yet, start a new one and try again without bothering the customer.
  // If documents had already been uploaded they are gone with the visit: start over, say so, and stop.
  function visit(method, suffix, body, isForm, onProgress) {
    return request(method, "/sessions/" + state.sid + suffix, body, isForm, onProgress).catch(function (e) {
      if (!isLostSession(e)) throw e;
      var had = hasDocuments();
      return newSession().then(function () {
        if (had) { startOver(); throw new ApiError("lost", friendly(e)); }
        return request(method, "/sessions/" + state.sid + suffix, body, isForm, onProgress);
      });
    });
  }

  function visitless(method, suffix, body) { return request(method, "/sessions/" + state.sid + suffix, body); }

  function startOver() {
    state.intake = null;
    state.checklist = null;
    renderChecklist(blankChecklist(), null);
    el.files.textContent = "";
    updateFiles();
    show(el.ready, false);
    show(el.chat, false);
    show(el.upload, true);
  }

  function show(node, on) { node.hidden = !on; }
  function scrollTo(node, where) {
    var calm = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    node.scrollIntoView({ block: where || "nearest", behavior: calm ? "auto" : "smooth" });
  }

  // ------------------------------------------------------------------ screen 1: checklist, files, progress
  function renderChecklist(list, flagged) {
    var before = {};
    (state.checklist || []).forEach(function (c) { before[c.id] = c.state; });
    state.checklist = list;
    el.checklist.textContent = "";
    list.forEach(function (c) {
      var li = h("li", "check-item " + c.state + (flagged && flagged.indexOf(c.id) >= 0 ? " flag" : "") + (before[c.id] && before[c.id] !== c.state && c.state !== "missing" ? " just" : ""));
      li.dataset.id = c.id;
      li.appendChild(h("span", "tick"));
      var text = h("span");
      text.appendChild(h("span", "check-label", c.label));
      var flag = flagged && flagged.indexOf(c.id) >= 0;
      var words = flag ? "needs a look" : { received: "received", partial: "partly received", missing: "not received yet" }[c.state];
      text.appendChild(h("span", "sr", " (" + words + ")"));
      if (flag) text.appendChild(h("span", "check-note", "Please check this document" + (c.filename ? " (" + c.filename + ")" : "") + "."));
      else if (c.note) text.appendChild(h("span", "check-note", c.note));
      else if (c.filename) text.appendChild(h("span", "check-note", c.filename));
      li.appendChild(text);
      el.checklist.appendChild(li);
    });
  }

  function blankChecklist() {
    return EXPECTED.map(function (d) { return { id: d.id, label: d.label, state: "missing", note: null, filename: null }; });
  }

  function progress(text, fraction) {
    show(el.progress, true);
    el.progressText.textContent = text;
    el.bar.classList.toggle("indeterminate", fraction == null);
    var pct = fraction == null ? 0 : Math.round(fraction * 100);
    el.fill.style.width = fraction == null ? "" : pct + "%";
    el.bar.setAttribute("aria-valuenow", String(pct));
  }

  function busy(on) {
    state.busy = on;
    el.drop.classList.toggle("busy", on);
    el.sample.setAttribute("aria-disabled", on ? "true" : "false");
    [el.addMore, el.cont, el.send, el.addDoc].forEach(function (b) { if (b) b.disabled = on; });
    Array.prototype.forEach.call(document.querySelectorAll(".chip, .retry"), function (b) { b.disabled = on; });
  }

  function updateFiles() {
    var rows = el.files.children.length;
    var bad = el.files.querySelectorAll(".bad, .check").length;
    el.filesSummary.textContent = "Your files (" + rows + ")" + (bad ? " · " + bad + " need a look" : "");
    show(el.filesBox, rows > 0);
  }

  function fileRow(name) {
    var li = h("li", "file-row");
    li.appendChild(h("span", "spin"));
    li.appendChild(h("span", "file-name", name));
    li.appendChild(h("span", "file-state", "Waiting…"));
    el.files.appendChild(li);
    el.filesBox.open = true;
    updateFiles();
    return li;
  }

  function setRow(li, kind, text) {
    li.className = "file-row" + (kind ? " " + kind : "");
    var spin = li.querySelector(".spin");
    if (spin && kind) spin.remove();
    li.querySelector(".file-state").textContent = text;
  }

  function applyFileResults(resp, rows) {
    (resp.files || []).forEach(function (f, i) {
      var li = rows && rows[i] ? rows[i] : fileRow(f.filename);
      li.querySelector(".file-name").textContent = f.filename;
      setRow(li, f.status === "recognised" ? "ok" : "bad", f.status === "recognised" ? "✓ " + f.message : f.message);
    });
    renderChecklist(resp.checklist, null);
    updateFiles();
  }

  // a file that was read fine but that the claim check found a problem with is not a success: mark it, and fold the list away when nothing needs a look
  function markFiles(flagged) {
    Array.prototype.forEach.call(el.files.children, function (li) {
      var name = li.querySelector(".file-name").textContent;
      var hit = (state.checklist || []).some(function (c) { return c.filename === name && flagged.indexOf(c.id) >= 0; });
      if (hit) { li.className = "file-row check"; li.querySelector(".file-state").textContent = "Needs a look: see above"; }
    });
    updateFiles();
    el.filesBox.open = el.files.querySelectorAll(".bad, .check").length > 0;
  }

  function received(list) { return list.filter(function (c) { return c.state === "received"; }).length; }

  function showResult(out) {
    state.intake = out;
    show(el.progress, false);
    show(el.attention, false);
    show(el.ready, false);
    if (out.status === "ready") {
      var n = received(out.checklist);
      el.readyText.textContent = "We recognised " + n + " of " + out.checklist.length + " documents." +
        (out.missing && out.missing.length ? " Still missing: " + out.missing.join("; ") + ". You can continue now and add it later." : " That's everything we asked for.");
      show(el.ready, true);
      renderChecklist(out.checklist, null);
      markFiles([]);
      scrollTo(el.ready, "center");
    } else {
      el.reasons.textContent = "";
      var flagged = [];
      out.reasons.forEach(function (r) {
        var li = h("li");
        li.appendChild(h("span", null, r.message));
        var actions = h("div", "reason-actions");
        (r.documents || []).forEach(function (id) {
          flagged.push(id);
          var label = (EXPECTED.filter(function (d) { return d.id === id; })[0] || {}).label || id;
          var b = h("button", "link-btn", "Replace: " + label);
          b.type = "button";
          b.addEventListener("click", function () { state.replaceFor = id; el.input.click(); });
          actions.appendChild(b);
        });
        if (actions.children.length) li.appendChild(actions);
        el.reasons.appendChild(li);
      });
      show(el.attention, true);
      renderChecklist(out.checklist, flagged);
      markFiles(flagged);
      scrollTo(el.attention, "center");
    }
  }

  function runIntake() {
    progress("Checking your claim…", null);
    return visit("POST", "/intake").then(showResult);
  }

  function failed(e, rows) {
    show(el.progress, false);
    (rows || []).forEach(function (li) { if (li.querySelector(".spin")) setRow(li, "bad", "Not sent"); });
    show(el.attention, true);
    el.reasons.textContent = "";
    var li = h("li", null, friendly(e));
    el.reasons.appendChild(li);
  }

  function pickFiles(fileList) {
    var files = Array.prototype.slice.call(fileList || []);
    state.replaceFor = null;
    if (!files.length || state.busy) return;
    if (files.length > MAX_FILES) { failed(new ApiError(422, "Please add up to " + MAX_FILES + " files at a time."), []); return; }
    show(el.attention, false);
    show(el.ready, false);
    var rows = files.map(function (f) { return fileRow(f.name); });
    var sendable = [], sendRows = [];
    files.forEach(function (f, i) {
      if (f.size > MAX_BYTES) setRow(rows[i], "bad", "This file is larger than 5 MB. Please add a smaller copy.");
      else { sendable.push(f); sendRows.push(rows[i]); }
    });
    if (!sendable.length) return;
    sendRows.forEach(function (li) { li.querySelector(".file-state").textContent = "Uploading…"; });
    var form = new FormData();
    sendable.forEach(function (f) { form.append("files", f, f.name); });
    busy(true);
    progress("Uploading…", 0);
    visit("POST", "/documents", form, true, function (p) {
      progress(p < 1 ? "Uploading… " + Math.round(p * 100) + "%" : "Reading your documents…", p < 1 ? p : null);
    }).then(function (resp) {
      applyFileResults(resp, sendRows);
      return runIntake();
    }).catch(function (e) { failed(e, sendRows); }).then(function () { busy(false); });
  }

  function useSample() {
    if (state.busy) return;
    show(el.attention, false);
    show(el.ready, false);
    el.files.textContent = "";
    updateFiles();
    busy(true);
    progress("Reading the sample documents…", null);
    visit("POST", "/documents/sample").then(function (resp) {
      applyFileResults(resp, null);
      return runIntake();
    }).catch(function (e) { failed(e, []); }).then(function () { busy(false); });
  }

  // ------------------------------------------------------------------ screen 2: chat
  function enterChat() {
    show(el.upload, false);
    show(el.chat, true);
    window.scrollTo(0, 0);
    el.q.focus({ preventScroll: true });
  }

  function backToUpload() {
    show(el.chat, false);
    show(el.upload, true);
    window.scrollTo(0, 0);
  }

  // The policy sections the assistant looked at, collapsed under the reply. No sources are shown when no policy section was used.
  function sourcesBlock(sources) {
    if (!sources || !sources.length) return null;
    var d = h("details", "sources");
    d.appendChild(h("summary", null, "Sources"));
    var ul = h("ul");
    sources.forEach(function (x) { ul.appendChild(h("li", null, x.title + (x.page ? ", page " + x.page : ""))); });
    d.appendChild(ul);
    return d;
  }

  function arcMessage(markdown, extra) {
    var m = h("article", "msg msg-arc" + (extra || ""));
    m.appendChild(h("p", "who", "ARC"));
    var body = h("div", "md");
    body.innerHTML = ARCMarkdown.render(markdown);
    m.appendChild(body);
    return m;
  }

  function note(text) {
    var n = h("div", "msg msg-note", text);
    el.thread.appendChild(n);
    return n;
  }

  function typing() {
    var t = h("div", "msg msg-arc typing");
    t.setAttribute("role", "status");
    t.appendChild(h("span", "spin"));
    var label = h("span", null, "ARC is looking at your claim…");
    t.appendChild(label);
    var t0 = Date.now();
    var timer = setInterval(function () {
      var s = Math.round((Date.now() - t0) / 1000);
      if (s >= 10) label.textContent = "ARC is looking at your claim… " + s + " s. Answers can take up to a minute.";
    }, 1000);
    t.stop = function () { clearInterval(timer); t.remove(); };
    return t;
  }

  function answerCard(data, question) {
    var notOk = data.status && data.status !== "ok";
    var text = typeof data.reply === "string" ? data.reply : "";
    var card = arcMessage(text, notOk ? " warn" : "");
    if (notOk) {
      var retry = h("button", "btn retry", "Try again");
      retry.type = "button";
      retry.addEventListener("click", function () { if (!state.busy) ask(question, true); });
      card.appendChild(retry);
    } else {
      var more = sourcesBlock(data.sources);
      if (more) card.appendChild(more);
    }
    return card;
  }

  function problemCard(err, question) {
    var card = h("article", "msg msg-arc problem");
    card.appendChild(h("p", "who", "ARC"));
    card.appendChild(h("p", null, friendly(err)));
    if (question) {
      var retry = h("button", "btn retry", "Try again");
      retry.type = "button";
      retry.addEventListener("click", function () { if (!state.busy) ask(question, true); });
      card.appendChild(retry);
    }
    return card;
  }

  function ask(question, isRetry) {
    question = String(question || "").trim();
    if (!question || state.busy || !state.sid) return;
    state.last = question;
    if (!isRetry) el.thread.appendChild(h("div", "msg msg-me", question));
    var wait = typing();
    el.thread.appendChild(wait);
    scrollTo(wait, "nearest");
    busy(true);
    visitless("POST", "/chat", { message: question }).then(function (data) {
      wait.stop();
      var card = answerCard(data, question);
      el.thread.appendChild(card);
      card.scrollIntoView({ block: "start" });
      document.dispatchEvent(new CustomEvent("arc:answer", { detail: { data: data, card: card } }));
    }, function (e) {
      wait.stop();
      if (isLostSession(e)) {   // the claim was kept only in the old visit: start again from the upload screen and say why
        newSession().then(function () {
          startOver();
          window.scrollTo(0, 0);
          failed(new ApiError(404, "Unknown session"), []);
          el.reasons.textContent = "";
          el.reasons.appendChild(h("li", null, "The server was restarted, so your visit ended and we've started a new one. Please add your documents again."));
        }, function () {});
        return;
      }
      var card = problemCard(e, question);
      el.thread.appendChild(card);
      scrollTo(card, "nearest");
    }).then(function () { busy(false); el.q.focus({ preventScroll: true }); });
  }

  // "Add a document": upload, then run intake again
  function addDocuments(fileList) {
    var files = Array.prototype.slice.call(fileList || []);
    if (!files.length || state.busy) return;
    if (files.length > MAX_FILES) { el.note.textContent = "Please add up to " + MAX_FILES + " files at a time."; return; }
    var big = files.filter(function (f) { return f.size > MAX_BYTES; });
    files = files.filter(function (f) { return f.size <= MAX_BYTES; });
    el.note.textContent = "";
    var names = files.map(function (f) { return f.name; }).join(", ");
    var wait = note(files.length ? "Reading " + names + "…" : "");
    big.forEach(function (f) { note("We couldn't use " + f.name + ": it is larger than 5 MB."); });
    if (!files.length) { wait.remove(); return; }
    var form = new FormData(), added = "";
    files.forEach(function (f) { form.append("files", f, f.name); });
    busy(true);
    scrollTo(wait, "nearest");
    visit("POST", "/documents", form, true).then(function (resp) {
      var bad = resp.files.filter(function (f) { return f.status !== "recognised"; });
      var good = resp.files.filter(function (f) { return f.status === "recognised"; });
      bad.forEach(function (f) { note("We couldn't use " + f.filename + ": " + f.message); });
      added = good.map(function (f) { return f.label; }).join(", ");
      wait.textContent = good.length ? "Added: " + added + ". Checking your claim again…" : "Nothing was added.";
      renderChecklist(resp.checklist, null);
      if (!good.length) return null;
      return visit("POST", "/intake");
    }).then(function (out) {
      if (!out) return;
      state.intake = out;
      if (out.status === "ready") {
        wait.textContent = "Added: " + added + ". I checked your claim again.";
        var card = arcMessage(out.first_message);
        el.thread.appendChild(card);
        card.scrollIntoView({ block: "start" });
      } else {
        wait.textContent = "Added: " + added + ". Something needs a look before I can go on.";
        backToUpload();
        showResult(out);
      }
    }).catch(function (e) {
      wait.textContent = friendly(e);
    }).then(function () { busy(false); });
  }

  // ------------------------------------------------------------------ start
  function begin() {
    renderChecklist(blankChecklist(), null);
    busy(true);
    newSession().then(function (d) {
      if (!d.intake) {   // an older server: it can make a visit but has no document routes. Say so now, not after a failed upload.
        failed(new ApiError("outdated"), []);
        el.drop.setAttribute("aria-disabled", "true");
        return;
      }
      busy(false);
    }, function (e) {
      failed(e, []);
      var retry = h("button", "btn btn-secondary", "Try again");
      retry.type = "button";
      retry.addEventListener("click", function () { el.reasons.textContent = ""; show(el.attention, false); begin(); });
      el.attention.appendChild(retry);
      show(el.attention, true);
    });
  }

  el.drop.addEventListener("click", function () { if (!state.busy) el.input.click(); });
  el.drop.addEventListener("keydown", function (e) { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); if (!state.busy) el.input.click(); } });
  ["dragenter", "dragover"].forEach(function (t) { el.drop.addEventListener(t, function (e) { e.preventDefault(); el.drop.classList.add("over"); }); });
  ["dragleave", "drop"].forEach(function (t) { el.drop.addEventListener(t, function (e) { e.preventDefault(); el.drop.classList.remove("over"); }); });
  el.drop.addEventListener("drop", function (e) { pickFiles(e.dataTransfer && e.dataTransfer.files); });
  el.input.addEventListener("change", function () { pickFiles(el.input.files); el.input.value = ""; });
  el.addMore.addEventListener("click", function () { el.input.click(); });
  el.sample.addEventListener("click", function (e) { e.preventDefault(); useSample(); });
  el.cont.addEventListener("click", function () {
    var out = state.intake;
    if (!out || out.status !== "ready" || state.busy) return;
    el.thread.textContent = "";
    enterChat();
    el.thread.appendChild(arcMessage(out.first_message));
  });
  el.form.addEventListener("submit", function (e) { e.preventDefault(); var q = el.q.value; if (!q.trim() || state.busy) return; el.q.value = ""; el.q.style.height = ""; ask(q); });
  el.q.addEventListener("keydown", function (e) { if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); el.form.requestSubmit(); } });
  el.q.addEventListener("input", function () { el.q.style.height = "auto"; el.q.style.height = Math.min(el.q.scrollHeight, 128) + "px"; });
  el.addDoc.addEventListener("click", function () { if (!state.busy) el.addInput.click(); });
  el.addInput.addEventListener("change", function () { addDocuments(el.addInput.files); el.addInput.value = ""; });

  begin();

  if (new URLSearchParams(window.location.search).get("dev") === "1") {   // developer extras are a separate file, loaded only in this mode
    var s = document.createElement("script");
    s.src = "/static/dev.js";
    document.head.appendChild(s);
  }
})();
