/* ARC: three views at three real paths ("/", "/upload", "/chat"), one page, no framework, no build step, no request off this server.
 * Server text is inserted as text; only assistant replies go through ARCMarkdown, which escapes HTML first and builds nodes (no innerHTML).
 */
(function () {
  "use strict";

  // ---------------------------------------------------------------- every string the customer reads
  var TEXT = {
    brand: "ARC",
    titles: { landing: "ARC", upload: "ARC - your documents", chat: "ARC - your claim" },
    landing: {
      h1: "Know where your claim stands before it's decided.",
      lead: "Upload your claim documents. ARC reads them, flags what's missing, and explains every deduction in plain words.",
      start: "Upload your documents",
      sample: "Try with sample documents",
      note: "Demo. Please upload only sample documents. Amounts are estimates; your insurer's team makes the final decision."
    },
    upload: {
      drop: "Drop your documents here, or click to upload",
      reading: "reading…",
      ready: "ready",
      failed: "couldn't be sent, please try again",
      wrong: "Something went wrong. Please try again.",
      sampleReading: "Sample documents, reading…",
      sampleReady: "Sample documents, ready"
    },
    chat: {
      placeholder: "Ask about your claim",
      send: "Send",
      working: "Working on it",
      assistant: "ARC",
      slow: "That took too long. Please try again.",
      dropHere: "Drop to add documents",
      report: "Download report",
      reportWorking: "Preparing…",
      reportFailed: "The report couldn't be prepared. Please try again.",
      restart: "Start over",
      restartConfirm: "Delete everything?",
      restartWorking: "Deleting…"
    }
  };

  var BATCH = 5;                 // files per request, so the per-request file limit never shows
  var CHAT_TIMEOUT_MS = 90000;
  var FADE_MS = 150;

  var $ = function (id) { return document.getElementById(id); };
  var el = {
    logoWord: $("logo-word"), main: $("main"),
    landing: $("view-landing"), h1: $("landing-title"), lead: $("landing-lead"), start: $("landing-start"), sample: $("landing-sample"), note: $("landing-note"),
    upload: $("view-upload"), drop: $("drop"), line: $("drop-line"), files: $("files"), reasons: $("reasons"), picker: $("picker"),
    chat: $("view-chat"), thread: $("thread"), form: $("bar"), ask: $("ask"), send: $("send"),
    tools: $("tools"), report: $("report"), reportLabel: $("report-label"), restart: $("restart"), restartLabel: $("restart-label"), toolNote: $("tool-note"),
    overlay: $("overlay"), overlayLine: $("overlay-line")
  };
  var state = { sid: null, view: null, ready: false, pending: false, uploading: false, restarting: false };
  var reduced = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  function fill() {
    el.logoWord.textContent = TEXT.brand;
    document.querySelector(".logo").setAttribute("aria-label", TEXT.brand + " home");
    el.h1.textContent = TEXT.landing.h1;
    el.lead.textContent = TEXT.landing.lead;
    el.start.textContent = TEXT.landing.start;
    el.sample.textContent = TEXT.landing.sample;
    el.note.textContent = TEXT.landing.note;
    el.line.textContent = TEXT.upload.drop;
    el.ask.placeholder = TEXT.chat.placeholder;
    el.ask.setAttribute("aria-label", TEXT.chat.placeholder);
    el.send.setAttribute("aria-label", TEXT.chat.send);
    el.reportLabel.textContent = TEXT.chat.report;
    el.restartLabel.textContent = TEXT.chat.restart;
    el.overlayLine.textContent = TEXT.chat.dropHere;
  }

  function h(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }

  // ---------------------------------------------------------------- API
  function api(method, path, body, timeout) {
    var ctl = new AbortController();
    var timer = timeout ? setTimeout(function () { ctl.abort(); }, timeout) : null;
    var opts = { method: method, signal: ctl.signal, headers: {} };
    if (body instanceof FormData) opts.body = body;
    else if (body) { opts.body = JSON.stringify(body); opts.headers["Content-Type"] = "application/json"; }
    return fetch(path, opts).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (j) {
        if (!r.ok) { var err = new Error((j && j.error && j.error.message) || "request failed"); err.status = r.status; throw err; }
        return j;
      });
    }).finally(function () { if (timer) clearTimeout(timer); });
  }

  function remember(sid) {
    state.sid = sid;
    try { sessionStorage.setItem("arc.sid", sid); } catch (e) { /* private mode: the visit simply does not survive a refresh */ }
  }

  function recall() {
    try { return sessionStorage.getItem("arc.sid"); } catch (e) { return null; }
  }

  function forget() {
    state.sid = null; state.ready = false;
    try { sessionStorage.removeItem("arc.sid"); } catch (e) { /* nothing to clear */ }
  }

  function session() {
    if (state.sid) return Promise.resolve(state.sid);
    return api("POST", "/sessions").then(function (j) { remember(j.session_id); return state.sid; });
  }

  // ---------------------------------------------------------------- the router: real paths, one page
  function go(path, replace) {
    if (location.pathname !== path) history[replace ? "replaceState" : "pushState"]({}, "", path);
    show(path);
  }

  function show(path) {
    var view = path === "/chat" ? "chat" : path === "/upload" ? "upload" : "landing";
    if (view === "chat" && !state.ready) { go("/upload", true); return; }
    state.view = view;
    el.landing.hidden = view !== "landing";
    el.upload.hidden = view !== "upload";
    el.chat.hidden = view !== "chat";
    el.tools.hidden = !(view === "chat" && state.ready);
    document.title = TEXT.titles[view];
    if (view === "chat") { el.ask.focus(); }
  }

  window.addEventListener("popstate", function () { show(location.pathname); });

  function link(node, path) {
    node.addEventListener("click", function (e) {
      if (e.metaKey || e.ctrlKey || e.shiftKey || e.button) return;   // let the customer open it in a new tab if they want to
      e.preventDefault();
      go(path);
    });
  }

  // ---------------------------------------------------------------- upload (shared by the upload and chat views)
  function isFiles(e) {
    var t = e.dataTransfer && e.dataTransfer.types;
    return !!t && Array.prototype.indexOf.call(t, "Files") >= 0;
  }

  function uploadBatches(list, rows) {
    var chain = Promise.resolve();
    for (var i = 0; i < list.length; i += BATCH) {
      (function (start) {
        chain = chain.then(function () {
          var batch = list.slice(start, start + BATCH), brows = rows.slice(start, start + BATCH);
          var fd = new FormData();
          batch.forEach(function (f) { fd.append("files", f, f.name); });
          return session().then(function (sid) { return api("POST", "/sessions/" + sid + "/documents", fd); }).then(function (res) {
            brows.forEach(function (row, k) {
              var r = (res.files || [])[k] || {};
              row.result = r.status === "recognised" ? TEXT.upload.ready : (r.message || TEXT.upload.failed);
              row.done();
            });
          }).catch(function (e) {
            brows.forEach(function (row) { row.result = (e && e.message && e.message !== "request failed" ? e.message : TEXT.upload.failed); row.done(); });
          });
        });
      })(i);
    }
    return chain;
  }

  function addFiles(fileList) {
    var list = Array.prototype.slice.call(fileList || []);
    if (!list.length || state.uploading) return;
    state.uploading = true;
    var inChat = state.view === "chat";
    var wait = null;
    if (inChat) { wait = waitingCard(); scrollToCard(wait); }
    el.reasons.textContent = "";
    var rows = list.map(function (f) {
      var li = h("li", null, f.name + ", " + TEXT.upload.reading);
      if (!inChat) el.files.appendChild(li);
      return { name: f.name, li: li, result: "", done: function () { li.textContent = f.name + ", " + this.result; } };
    });
    uploadBatches(list, rows).then(function () {
      return api("POST", "/sessions/" + state.sid + "/intake");
    }).then(function (out) {
      if (out.status === "ready") {
        state.ready = true;
        if (inChat) {
          var bad = rows.filter(function (r) { return r.result !== TEXT.upload.ready; }).map(function (r) { return r.name + ", " + r.result; });
          fillCard(wait, out.first_message + (bad.length ? "\n\n" + bad.join("\n") : ""));
          el.tools.hidden = false;
          scrollToCard(wait, true);
        } else enterChat(out.first_message);
      } else {
        var text = (out.reasons || []).map(function (r) { return r.message; }).join("\n");
        if (inChat) { fillCard(wait, text); scrollToCard(wait, true); } else el.reasons.textContent = text;
      }
    }).catch(function () {
      if (inChat) { fillCard(wait, TEXT.chat.slow); scrollToCard(wait, true); } else el.reasons.textContent = TEXT.upload.wrong;
    }).finally(function () { state.uploading = false; el.picker.value = ""; });
  }

  el.drop.addEventListener("click", function () { el.picker.click(); });
  el.drop.addEventListener("keydown", function (e) {
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); el.picker.click(); }
  });
  el.picker.addEventListener("change", function () { addFiles(el.picker.files); });

  var depth = 0;
  function dragOn(on) {
    if (state.view === "chat") el.overlay.hidden = !on;
    else if (state.view === "upload") el.drop.classList.toggle("over", on);
  }
  window.addEventListener("dragenter", function (e) { if (!isFiles(e)) return; e.preventDefault(); depth++; dragOn(true); });
  window.addEventListener("dragover", function (e) { if (isFiles(e)) e.preventDefault(); });
  window.addEventListener("dragleave", function (e) { if (!isFiles(e)) return; depth = Math.max(0, depth - 1); if (!depth) dragOn(false); });
  window.addEventListener("drop", function (e) {
    if (!isFiles(e)) return;
    e.preventDefault(); depth = 0; dragOn(false);
    if (state.view === "landing") { go("/upload"); }
    addFiles(e.dataTransfer.files);
  });

  // the sample link on the landing view: the same intake pipeline, with the set the query asks for (on_time by default)
  var sampleSet = (location.search.match(/[?&]sample=([a-z_0-9]+)/i) || [])[1] || "on_time";
  el.sample.addEventListener("click", function (e) {
    e.preventDefault();
    if (state.uploading) return;
    state.uploading = true;
    go("/upload");
    var li = h("li", null, TEXT.upload.sampleReading);
    el.files.appendChild(li);
    session().then(function (sid) { return api("POST", "/sessions/" + sid + "/documents/sample?set=" + encodeURIComponent(sampleSet)); }).then(function () {
      li.textContent = TEXT.upload.sampleReady;
      return api("POST", "/sessions/" + state.sid + "/intake");
    }).then(function (out) {
      if (out.status === "ready") { state.ready = true; enterChat(out.first_message); }
      else el.reasons.textContent = (out.reasons || []).map(function (r) { return r.message; }).join("\n");
    }).catch(function () { li.textContent = "Sample documents, " + TEXT.upload.failed; }).finally(function () { state.uploading = false; });
  });

  // ---------------------------------------------------------------- cards
  function assistantCard(markdown) {
    var c = h("article", "card");
    c.setAttribute("aria-label", TEXT.chat.assistant);
    fillCard(c, markdown);
    return c;
  }

  function fillCard(card, markdown) {
    card.textContent = "";
    var text = String(markdown == null ? "" : markdown);
    try {   // the renderer escapes first; the result is parsed into nodes, and a failure falls back to the plain text
      var doc = new DOMParser().parseFromString(ARCMarkdown.render(text), "text/html");
      while (doc.body.firstChild) card.appendChild(document.adoptNode(doc.body.firstChild));
    } catch (e) { card.textContent = text; }
    if (!reduced) { card.classList.remove("fresh"); void card.offsetWidth; card.classList.add("fresh"); }
  }

  function waitingCard() {
    var c = h("article", "card");
    c.setAttribute("aria-label", TEXT.chat.assistant);
    var d = h("span", "dots");
    d.setAttribute("aria-label", TEXT.chat.working);
    d.appendChild(h("i")); d.appendChild(h("i")); d.appendChild(h("i"));
    c.appendChild(d);
    el.thread.appendChild(c);
    return c;
  }

  function scrollToCard(card, top) {
    card.scrollIntoView({ block: top ? "start" : "nearest", behavior: "auto" });
  }

  function enterChat(firstMessage) {
    el.thread.textContent = "";
    el.thread.appendChild(assistantCard(firstMessage));
    go("/chat");
    window.scrollTo(0, 0);
  }

  // ---------------------------------------------------------------- chat
  function refresh() { el.send.disabled = state.pending || !el.ask.value.trim(); }
  el.ask.addEventListener("input", refresh);

  el.form.addEventListener("submit", function (e) {
    e.preventDefault();
    var q = el.ask.value.trim();
    if (!q || state.pending) return;
    state.pending = true;
    var me = h("article", "card me", q);
    el.thread.appendChild(me);
    el.ask.value = "";
    var wait = waitingCard();
    refresh();
    scrollToCard(me);
    el.ask.focus();
    api("POST", "/sessions/" + state.sid + "/chat", { message: q }, CHAT_TIMEOUT_MS).then(function (out) {
      fillCard(wait, typeof out.reply === "string" && out.reply ? out.reply : TEXT.chat.slow);
    }).catch(function (e) {
      fillCard(wait, e && (e.status === 404 || e.status === 429) ? e.message : TEXT.chat.slow);   // a session that ended, or too many requests: the server's own plain sentence
    }).finally(function () {
      state.pending = false;
      refresh();
      scrollToCard(wait, true);
      el.ask.focus();
    });
  });

  // ---------------------------------------------------------------- the report and starting over
  el.report.addEventListener("click", function (e) {
    e.preventDefault();
    if (!state.sid || el.report.hasAttribute("disabled")) return;
    el.report.setAttribute("disabled", "disabled");
    el.reportLabel.textContent = TEXT.chat.reportWorking;
    el.toolNote.textContent = "";
    fetch("/sessions/" + state.sid + "/report.pdf").then(function (r) {
      if (!r.ok) throw new Error("report");
      var name = (r.headers.get("content-disposition") || "").match(/filename="([^"]+)"/);
      return r.blob().then(function (blob) {
        var url = URL.createObjectURL(blob);
        var a = h("a");
        a.href = url;
        a.download = name ? name[1] : "ARC-claim-report.pdf";
        document.body.appendChild(a);
        a.click();
        a.remove();
        setTimeout(function () { URL.revokeObjectURL(url); }, 10000);
      });
    }).catch(function () {
      el.toolNote.textContent = TEXT.chat.reportFailed;
    }).finally(function () {
      el.reportLabel.textContent = TEXT.chat.report;
      el.report.removeAttribute("disabled");
    });
  });

  el.restart.addEventListener("click", function () {
    if (!state.restarting) {                       // two steps: the second click confirms
      state.restarting = true;
      el.restartLabel.textContent = TEXT.chat.restartConfirm;
      setTimeout(function () {
        if (state.restarting) { state.restarting = false; el.restartLabel.textContent = TEXT.chat.restart; }
      }, 5000);
      return;
    }
    state.restarting = false;
    el.restartLabel.textContent = TEXT.chat.restartWorking;
    var sid = state.sid;
    forget();
    (sid ? api("DELETE", "/sessions/" + sid).catch(function () { return null; }) : Promise.resolve()).then(function () {
      el.thread.textContent = "";
      el.files.textContent = "";
      el.reasons.textContent = "";
      el.toolNote.textContent = "";
      el.restartLabel.textContent = TEXT.chat.restart;
      el.tools.hidden = true;
      go("/upload");
    });
  });

  // ---------------------------------------------------------------- boot: a refresh on /chat brings the conversation back
  fill();
  link(el.start, "/upload");
  link(document.querySelector(".logo"), "/");

  var sid = recall();
  if (sid) {
    state.sid = sid;
    api("GET", "/sessions/" + sid + "/messages").then(function (out) {
      state.ready = !!out.ready;
      (out.messages || []).forEach(function (m) {
        if (m.who === "you") el.thread.appendChild(h("article", "card me", m.text));
        else el.thread.appendChild(assistantCard(m.text));
      });
      show(location.pathname);
    }).catch(function () {
      forget();
      show(location.pathname);
    });
  } else {
    show(location.pathname);
  }
})();
