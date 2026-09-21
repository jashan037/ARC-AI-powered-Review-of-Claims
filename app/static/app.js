/* ARC customer page: upload documents, then chat. No framework, no build step, no network beyond this server.
 * Server text is inserted as text; only assistant replies go through ARCMarkdown, which escapes HTML first. */
(function () {
  "use strict";

  var DROP_LINE = "Drop your documents here, or click to upload";
  var BATCH = 5;                 // files per request, so the per-request file limit never shows
  var FADE_MS = 150;
  var CHAT_TIMEOUT_MS = 90000;
  var SLOW = "That took too long. Please try again.";
  var UPLOAD_FAILED = "couldn't be sent, please try again";

  var $ = function (id) { return document.getElementById(id); };
  var el = {
    upload: $("view-upload"), chat: $("view-chat"), drop: $("drop"), line: $("drop-line"), files: $("files"), reasons: $("reasons"),
    sample: $("sample"), sampleLink: $("sample-link"), picker: $("picker"), thread: $("thread"), form: $("bar"), ask: $("ask"), send: $("send"), overlay: $("overlay")
  };
  var state = { sid: null, view: "upload", pending: false, uploading: false };
  var reduced = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  el.line.textContent = DROP_LINE;

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

  function session() {
    if (state.sid) return Promise.resolve(state.sid);
    return api("POST", "/sessions").then(function (j) { state.sid = j.session_id; return state.sid; });
  }

  // ---------------------------------------------------------------- upload (shared by both views)
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
              row.result = r.status === "recognised" ? "ready" : (r.message || UPLOAD_FAILED);
              row.done();
            });
          }).catch(function (e) {
            brows.forEach(function (row) { row.result = (e && e.message && e.message !== "request failed" ? e.message : UPLOAD_FAILED); row.done(); });
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
      var li = h("li", null, f.name + ", reading…");
      if (!inChat) el.files.appendChild(li);
      return { name: f.name, li: li, result: "", done: function () { li.textContent = f.name + ", " + this.result; } };
    });
    uploadBatches(list, rows).then(function () {
      return api("POST", "/sessions/" + state.sid + "/intake");
    }).then(function (out) {
      if (out.status === "ready") {
        if (inChat) {
          var bad = rows.filter(function (r) { return r.result !== "ready"; }).map(function (r) { return r.name + ", " + r.result; });
          fillCard(wait, out.first_message + (bad.length ? "\n\n" + bad.join("\n") : ""));
          scrollToCard(wait, true);
        } else showChat(out.first_message);
      } else {
        var text = (out.reasons || []).map(function (r) { return r.message; }).join("\n");
        if (inChat) { fillCard(wait, text); scrollToCard(wait, true); } else el.reasons.textContent = text;
      }
    }).catch(function () {
      if (inChat) { fillCard(wait, SLOW); scrollToCard(wait, true); } else el.reasons.textContent = "Something went wrong. Please try again.";
    }).finally(function () { state.uploading = false; el.picker.value = ""; });
  }

  el.drop.addEventListener("click", function () { el.picker.click(); });
  el.drop.addEventListener("keydown", function (e) {
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); el.picker.click(); }
  });
  el.picker.addEventListener("change", function () { addFiles(el.picker.files); });

  // dragging anywhere on the page: the drop section (upload view) or the whole-page overlay (chat view)
  var depth = 0;
  function dragOn(on) {
    if (state.view === "chat") el.overlay.hidden = !on;
    else el.drop.classList.toggle("over", on);
  }
  window.addEventListener("dragenter", function (e) { if (!isFiles(e)) return; e.preventDefault(); depth++; dragOn(true); });
  window.addEventListener("dragover", function (e) { if (isFiles(e)) e.preventDefault(); });
  window.addEventListener("dragleave", function (e) { if (!isFiles(e)) return; depth = Math.max(0, depth - 1); if (!depth) dragOn(false); });
  window.addEventListener("drop", function (e) {
    if (!isFiles(e)) return;
    e.preventDefault(); depth = 0; dragOn(false);
    addFiles(e.dataTransfer.files);
  });

  if (/[?&]sample=1(&|$)/.test(location.search)) {
    el.sample.hidden = false;
    el.sampleLink.addEventListener("click", function (e) {
      e.preventDefault();
      if (state.uploading) return;
      state.uploading = true;
      var li = h("li", null, "Sample documents, reading…");
      el.files.appendChild(li);
      session().then(function (sid) { return api("POST", "/sessions/" + sid + "/documents/sample"); }).then(function () {
        li.textContent = "Sample documents, ready";
        return api("POST", "/sessions/" + state.sid + "/intake");
      }).then(function (out) {
        if (out.status === "ready") showChat(out.first_message);
        else el.reasons.textContent = (out.reasons || []).map(function (r) { return r.message; }).join("\n");
      }).catch(function () { li.textContent = "Sample documents, " + UPLOAD_FAILED; }).finally(function () { state.uploading = false; });
    });
  }

  // ---------------------------------------------------------------- views
  function showChat(firstMessage) {
    el.upload.classList.add("fade");
    setTimeout(function () {
      el.upload.hidden = true;
      el.upload.classList.remove("fade");
      el.chat.hidden = false;
      state.view = "chat";
      el.chat.classList.add("fade");
      el.thread.appendChild(assistantCard(firstMessage));
      void el.chat.offsetWidth;
      el.chat.classList.remove("fade");
      el.ask.focus();
      window.scrollTo(0, 0);
    }, reduced ? 0 : FADE_MS);
  }

  // ---------------------------------------------------------------- cards
  function assistantCard(markdown) {
    var c = h("article", "card");
    fillCard(c, markdown);
    return c;
  }

  function fillCard(card, markdown) {
    card.textContent = "";
    var text = String(markdown == null ? "" : markdown);
    try {   // the renderer escapes first; the result is parsed into nodes (no innerHTML), and a failure falls back to the plain text
      var doc = new DOMParser().parseFromString(ARCMarkdown.render(text), "text/html");
      while (doc.body.firstChild) card.appendChild(document.adoptNode(doc.body.firstChild));
    } catch (e) { card.textContent = text; }
    if (!reduced) { card.classList.remove("fresh"); void card.offsetWidth; card.classList.add("fresh"); }
  }

  function waitingCard() {
    var c = h("article", "card");
    var d = h("span", "dots");
    d.setAttribute("aria-label", "Working on it");
    d.appendChild(h("i")); d.appendChild(h("i")); d.appendChild(h("i"));
    c.appendChild(d);
    el.thread.appendChild(c);
    return c;
  }

  function scrollToCard(card, top) {
    card.scrollIntoView({ block: top ? "start" : "nearest", behavior: "auto" });
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
    scrollToCard(wait);
    el.ask.focus();
    api("POST", "/sessions/" + state.sid + "/chat", { message: q }, CHAT_TIMEOUT_MS).then(function (out) {
      fillCard(wait, typeof out.reply === "string" && out.reply ? out.reply : SLOW);
    }).catch(function (e) {
      fillCard(wait, e && (e.status === 404 || e.status === 429) ? e.message : SLOW);   // a session that ended, or too many requests: the server's own plain sentence
    }).finally(function () {
      state.pending = false;
      refresh();
      scrollToCard(wait, true);
      el.ask.focus();
    });
  });
})();
