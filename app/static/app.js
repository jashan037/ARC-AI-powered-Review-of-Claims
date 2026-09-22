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
      h1: "Know where your claim stands\nbefore it's decided.",
      lead: "Upload your claim documents. ARC reads them, flags what's missing,\nand explains every deduction in plain words.",
      start: "Upload your documents",
      sample: "Try with sample documents",
      note: "Demo. Please upload only sample documents. Amounts are estimates; your insurer's team makes the final decision.",
      navUpload: "Upload documents",
      navSample: "Try a sample claim",
      navCta: "Get started",
      menuOpen: "Open menu",
      menuClose: "Close menu"
    },
    // the product picture on the landing view: the on_time sample claim, figures as the engine gives them
    mock: {
      eyebrow: "# CLM-20260910-0001 / Optima Secure",
      title: "Claim review",
      meta: ["Read in seconds", "9 of 10 documents", "Estimate ready"],
      tabs: ["Outcome", "Deductions", "Documents", "Questions"],
      heading: "What ARC found",
      row1: "Likely payable: about \u20b91,22,125 of \u20b91,84,500",
      row1Note: "An estimate. Your insurer's team makes the decision.",
      row2: "Prescription missing, \u20b920,500 on hold",
      row2Note: "Upload it to release the held amount."
    },
    // the second landing screen: three real figures (the eight answer checks G1-G8, the 186 indexed policy clauses, the 68 Annexure B items)
    metrics: {
      line1: "Built for ",
      dots: "Intelligent",
      line2: "Claim Review",
      intro: "Every answer is checked against your documents, the policy wording and a rules engine, so ARC explains your claim in plain words and never guesses.",
      more: "Learn more",
      less: "Close",
      cards: [
        { title: "Answer Checks\nBefore Every Reply", num: "8", unit: "checks", caption: "Checks every reply\nmust pass",
          detail: "Figures, dates and verdicts in a reply must match the rules engine, and every policy statement must match a clause found for that question. A reply that fails is corrected before you see it." },
        { title: "Policy Knowledge\nClause-Level Search", num: "186", unit: "clauses", caption: "Policy clauses searched\nfor each answer",
          detail: "The policy wording is split into clauses and indexed in Azure AI Search. Each question finds the clauses that apply, and ARC names them as its sources." },
        { title: "Rules Engine\nExact Dates and Money", num: "68", unit: "items", caption: "Non-payable items\nchecked on every bill",
          detail: "Waiting periods, the room-rent limit, filing time and the non-payable items are worked out in Python, not by the model, so the amounts are exact." }
      ]
    },
    loader: {
      reading: "Reading your documents",
      sample: "Reading the sample documents",
      building: "Building your claim",
      count: "{done} of {total} read",
      wait: "This takes a few seconds",
      almost: "Checking the policy and the bill"
    },
    upload: {
      title: "Add your claim documents",
      lead: "Your bill, discharge summary, prescriptions and claim form. ARC reads them and builds your claim.",
      drop: "Drop your documents here, or click to upload",
      hint: "PDF files, as many as you have",
      sampleLead: "No documents at hand?",
      reading: "reading…",
      ready: "ready",
      failed: "couldn't be sent, please try again",
      wrong: "Something went wrong. Please try again.",
      sampleName: "Sample documents"
    },
    chat: {
      placeholder: "Ask about your claim",
      send: "Send",
      working: "Working on it",
      assistant: "ARC",
      attach: "Add documents",
      chips: ["How much will be paid?", "What's still missing?", "Explain the deductions"],
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
    logoWord: $("logo-word"), appLogoWord: $("app-logo-word"), main: $("main"), appbar: $("appbar"),
    landing: $("view-landing"), h1: $("landing-title"), lead: $("landing-lead"), start: $("landing-start"), sample: $("landing-sample"), note: $("landing-note"),
    stage: $("stage"), nav: $("l-nav"), burger: $("burger"), menu: $("nav-menu"), mock: document.querySelector(".mock"),
    upload: $("view-upload"), drop: $("drop"), line: $("drop-line"), files: $("files"), reasons: $("reasons"), picker: $("picker"),
    chat: $("view-chat"), thread: $("thread"), form: $("bar"), ask: $("ask"), send: $("send"), attach: $("attach"),
    tools: $("tools"), report: $("report"), reportLabel: $("report-label"), restart: $("restart"), restartLabel: $("restart-label"), toolNote: $("tool-note"),
    overlay: $("overlay"), overlayLine: $("overlay-line"),
    loader: $("loader"), loaderTitle: $("loader-title"), loaderSub: $("loader-sub"), loaderFill: $("loader-fill"), metrics: $("metrics")
  };
  var state = { sid: null, view: null, ready: false, pending: false, uploading: false, restarting: false };
  var reduced = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  function fill() {
    el.logoWord.textContent = TEXT.brand;
    el.appLogoWord.textContent = TEXT.brand;
    $("logo").setAttribute("aria-label", TEXT.brand + " home");
    $("app-logo").setAttribute("aria-label", TEXT.brand + " home");
    el.h1.textContent = TEXT.landing.h1;
    el.lead.textContent = TEXT.landing.lead;
    el.start.textContent = TEXT.landing.start;
    el.sample.textContent = TEXT.landing.sample;
    el.note.textContent = TEXT.landing.note;
    $("nav-upload").textContent = $("menu-upload").textContent = TEXT.landing.navUpload;
    $("nav-sample").textContent = $("menu-sample").textContent = TEXT.landing.navSample;
    $("nav-cta").textContent = $("menu-cta").textContent = TEXT.landing.navCta;
    el.burger.setAttribute("aria-label", TEXT.landing.menuOpen);
    $("m-eyebrow").textContent = TEXT.mock.eyebrow;
    $("m-title").textContent = TEXT.mock.title;
    TEXT.mock.meta.forEach(function (part, i) {
      if (i) $("m-meta").appendChild(h("span", "sep", "\u2022"));
      $("m-meta").appendChild(document.createTextNode(part));
    });
    TEXT.mock.tabs.forEach(function (tab, i) { $("m-tabs").appendChild(h("span", "m-tab " + (i ? "t" + (i + 1) : "is-active"), tab)); });
    $("m-heading").textContent = TEXT.mock.heading;
    $("m-r1t").textContent = TEXT.mock.row1; $("m-r1o").textContent = TEXT.mock.row1Note;
    $("m-r2t").textContent = TEXT.mock.row2; $("m-r2o").textContent = TEXT.mock.row2Note;
    $("upload-title").textContent = TEXT.upload.title;
    $("upload-lead").textContent = TEXT.upload.lead;
    $("drop-hint").textContent = TEXT.upload.hint;
    $("upload-sample-lead").textContent = TEXT.upload.sampleLead;
    $("upload-sample").textContent = TEXT.landing.sample;
    el.attach.setAttribute("aria-label", TEXT.chat.attach);
    $("mx-line1").textContent = TEXT.metrics.line1;
    $("mx-line2").textContent = TEXT.metrics.line2;
    $("mx-intro").textContent = TEXT.metrics.intro;
    dots($("mx-dots"), TEXT.metrics.dots, 4, 1.8);
    TEXT.metrics.cards.forEach(function (c, i) {
      var n = i + 1, card = $("mx-c" + n + "-title").parentNode, btn = card.querySelector(".learn-more"), detail = $("mx-c" + n + "-detail");
      $("mx-c" + n + "-title").textContent = c.title;
      dots($("mx-c" + n + "-num"), c.num, 5, 2.05);
      $("mx-c" + n + "-unit").textContent = c.unit;
      $("mx-c" + n + "-cap").textContent = c.caption;
      detail.textContent = c.detail;
      btn.textContent = TEXT.metrics.more;
      btn.addEventListener("click", function () {
        var open = detail.hidden;
        detail.hidden = !open;
        btn.setAttribute("aria-expanded", String(open));
        btn.textContent = open ? TEXT.metrics.less : TEXT.metrics.more;
      });
    });
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

  // ---------------------------------------------------------------- LED-dot type: 7-row bitmap glyphs drawn as SVG circles
  var GLYPHS = {
    "0": "01110 10001 10011 10101 11001 10001 01110", "1": "010 110 010 010 010 010 111", "2": "01110 10001 00001 00010 00100 01000 11111",
    "3": "11110 00001 00001 01110 00001 00001 11110", "4": "00010 00110 01010 10010 11111 00010 00010", "5": "11111 10000 10000 11110 00001 00001 11110",
    "6": "01110 10000 10000 11110 10001 10001 01110", "7": "11111 00001 00010 00100 01000 01000 01000", "8": "01110 10001 10001 01110 10001 10001 01110",
    "9": "01110 10001 10001 01111 00001 00001 01110", ".": "0 0 0 0 0 0 1", "I": "111 010 010 010 010 010 111",
    "a": "00000 00000 01110 00001 01111 10001 01111", "e": "00000 00000 01110 10001 11111 10000 01110", "g": "00000 00000 01111 10001 01111 00001 01110",
    "i": "1 0 1 1 1 1 1", "l": "10 10 10 10 10 10 01", "n": "00000 00000 11110 10001 10001 10001 10001", "t": "010 010 111 010 010 010 001",
    "r": "00000 00000 10110 11001 10000 10000 10000"
  };
  var SVGNS = "http://www.w3.org/2000/svg";
  function dots(node, text, pitchX, radius) {
    var svg = document.createElementNS(SVGNS, "svg"), x = 0;
    svg.setAttribute("class", "dot-svg");
    svg.setAttribute("aria-hidden", "true");
    svg.setAttribute("fill", "currentColor");
    Array.prototype.forEach.call(text, function (ch) {
      var rows = (GLYPHS[ch] || "0 0 0 0 0 0 0").split(" "), cols = rows[0].length;
      rows.forEach(function (row, y) {
        for (var c = 0; c < cols; c++) {
          if (row[c] !== "1") continue;
          var dot = document.createElementNS(SVGNS, "circle");
          dot.setAttribute("cx", String(x + c * pitchX + 1.55));
          dot.setAttribute("cy", String(y * 4 + 1.55));
          dot.setAttribute("r", String(radius));
          svg.appendChild(dot);
        }
      });
      x += cols * pitchX + pitchX;
    });
    svg.setAttribute("viewBox", "0 0 " + (x - pitchX + 3.1) + " 28");
    svg.setAttribute("preserveAspectRatio", "xMinYMid meet");
    node.textContent = "";
    node.appendChild(svg);
    node.setAttribute("aria-label", text);
  }

  function ticks() {   // the gauge's scale: 23 ticks, every fifth a long one
    var g = $("gaugeTicks");
    for (var i = 0; i <= 22; i++) {
      var a = (190 + i * 5) * Math.PI / 180, inner = i % 5 === 0 ? 129 : 133, line = document.createElementNS(SVGNS, "line");
      line.setAttribute("x1", String(163 + 142 * Math.cos(a)));
      line.setAttribute("y1", String(163 + 142 * Math.sin(a)));
      line.setAttribute("x2", String(163 + inner * Math.cos(a)));
      line.setAttribute("y2", String(163 + inner * Math.sin(a)));
      line.setAttribute("class", "tick");
      line.setAttribute("stroke-width", i % 5 === 0 ? "1.5" : "1");
      g.appendChild(line);
    }
  }

  // ---------------------------------------------------------------- the loading card while documents are read
  function loading(title, done, total, sub) {
    el.loader.hidden = false;
    el.loaderTitle.textContent = title;
    el.loaderSub.textContent = sub || (total ? TEXT.loader.count.replace("{done}", done).replace("{total}", total) : TEXT.loader.almost);
    el.loaderFill.style.setProperty("--p", String(total ? Math.max(0.06, 0.85 * done / total) : 0.92));
  }
  function loaded() { el.loader.hidden = true; el.loaderFill.style.setProperty("--p", "0.06"); }

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
    el.appbar.hidden = view === "landing";
    el.tools.hidden = !(view === "chat" && state.ready);
    document.documentElement.classList.toggle("on-landing", view === "landing");
    document.title = TEXT.titles[view];
    if (view === "landing") fit(); else closeMenu();
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
    var finished = 0;
    if (!inChat) loading(TEXT.loader.reading, 0, list.length);
    var rows = list.map(function (f) {
      var li = h("li"), status = h("span", "fstatus", TEXT.upload.reading);
      li.appendChild(h("span", "fname", f.name));
      li.appendChild(status);
      if (!inChat) el.files.appendChild(li);
      return { name: f.name, li: li, result: "", done: function () {
        finished++;
        if (!inChat) loading(finished < list.length ? TEXT.loader.reading : TEXT.loader.building, finished, finished < list.length ? list.length : 0);
        status.textContent = this.result;
        status.className = "fstatus " + (this.result === TEXT.upload.ready ? "is-ready" : "is-bad");
      } };
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
    }).finally(function () { state.uploading = false; el.picker.value = ""; loaded(); });
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
  function startSample(e) {
    e.preventDefault();
    if (state.uploading) return;
    state.uploading = true;
    go("/upload");
    var li = h("li"), status = h("span", "fstatus", TEXT.upload.reading);
    li.appendChild(h("span", "fname", TEXT.upload.sampleName));
    li.appendChild(status);
    el.files.appendChild(li);
    loading(TEXT.loader.sample, 0, 1, TEXT.loader.wait);
    session().then(function (sid) { return api("POST", "/sessions/" + sid + "/documents/sample?set=" + encodeURIComponent(sampleSet)); }).then(function () {
      status.textContent = TEXT.upload.ready;
      status.className = "fstatus is-ready";
      loading(TEXT.loader.building, 1, 0);
      return api("POST", "/sessions/" + state.sid + "/intake");
    }).then(function (out) {
      if (out.status === "ready") { state.ready = true; enterChat(out.first_message); }
      else el.reasons.textContent = (out.reasons || []).map(function (r) { return r.message; }).join("\n");
    }).catch(function () { status.textContent = TEXT.upload.failed; status.className = "fstatus is-bad"; }).finally(function () { state.uploading = false; loaded(); });
  }
  ["landing-sample", "nav-sample", "menu-sample", "upload-sample"].forEach(function (id) { $(id).addEventListener("click", startSample); });

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
    el.thread.appendChild(chips());
    go("/chat");
    window.scrollTo(0, 0);
  }

  // ---------------------------------------------------------------- chat
  function refresh() { el.send.disabled = state.pending || !el.ask.value.trim(); }
  el.ask.addEventListener("input", refresh);

  // questions to start with: shown under the first message, gone once the customer asks anything
  function chips() {
    var box = h("div", "chips");
    box.id = "chips";
    TEXT.chat.chips.forEach(function (q) {
      var b = h("button", "chip", q);
      b.type = "button";
      b.addEventListener("click", function () { el.ask.value = q; submit(); });
      box.appendChild(b);
    });
    return box;
  }

  el.form.addEventListener("submit", function (e) { e.preventDefault(); submit(); });
  el.attach.addEventListener("click", function () { el.picker.click(); });

  function submit() {
    var q = el.ask.value.trim();
    if (!q || state.pending) return;
    state.pending = true;
    el.form.classList.add("is-pending");
    if ($("chips")) $("chips").remove();
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
      el.form.classList.remove("is-pending");
      refresh();
      scrollToCard(wait, true);
      el.ask.focus();
    });
  }

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

  // ---------------------------------------------------------------- the landing view: fit the 1290x860 stage to the window, the menu, the entrance
  var DW = 1290, DH = 860, CARD_TOP = 549, CARD_H = 340, NAV_W = 880, MINW = 960, HERO_SHARE = 0.55;
  function fit() {
    if (state.view !== "landing") return;
    var st = el.stage.style, root = document.documentElement.style;
    if (window.innerWidth < 940) {                    // below 940px the CSS grid owns the layout
      ["transform", "width", "height"].forEach(function (p) { st.removeProperty(p); });
      ["--dsk-card-s", "--dsk-band-dy", "--dsk-hero-dy"].forEach(function (p) { root.removeProperty(p); });
      return;
    }
    closeMenu();
    var vw = window.innerWidth, vh = window.innerHeight;
    var s = Math.min(vh / DH, vw / MINW), W = vw / s, H = vh / s;
    var cs = Math.max(1, Math.min((H - CARD_TOP) / CARD_H, Math.min(NAV_W, 0.8 * W) / 548));
    var dy = Math.max(0, H - CARD_TOP - CARD_H * cs);
    st.setProperty("width", W + "px");
    st.setProperty("height", H + "px");
    st.setProperty("transform", "scale(" + s + ")");
    root.setProperty("--dsk-card-s", String(cs));
    root.setProperty("--dsk-band-dy", dy + "px");
    root.setProperty("--dsk-hero-dy", HERO_SHARE * dy + "px");
  }
  ["resize", "orientationchange", "pageshow"].forEach(function (ev) { window.addEventListener(ev, fit); });
  if (window.visualViewport) window.visualViewport.addEventListener("resize", fit);
  if (document.fonts && document.fonts.ready) document.fonts.ready.then(fit);

  function setMenu(open) {
    el.nav.classList.toggle("is-open", open);
    el.menu.hidden = !open;
    el.burger.setAttribute("aria-expanded", String(open));
    el.burger.setAttribute("aria-label", open ? TEXT.landing.menuClose : TEXT.landing.menuOpen);
  }
  function closeMenu() { if (el.nav.classList.contains("is-open")) setMenu(false); }
  el.burger.addEventListener("click", function (e) { e.stopPropagation(); setMenu(!el.nav.classList.contains("is-open")); });
  document.addEventListener("click", function (e) { if (!el.nav.contains(e.target)) closeMenu(); });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && el.nav.classList.contains("is-open")) { setMenu(false); el.burger.focus(); }
  });
  el.menu.addEventListener("click", function (e) { if (e.target.closest("a")) closeMenu(); });

  function enter() {            // played once: pending (set in enter.js) -> run -> done, which leaves only the authored CSS
    var html = document.documentElement;
    if (html.getAttribute("data-enter") !== "pending") return;
    var started = false;
    var done = function () { html.setAttribute("data-enter", "done"); };
    var run = function () {
      if (started) return;
      started = true;
      html.setAttribute("data-enter", "run");
      el.mock.addEventListener("animationend", done, { once: true });
      setTimeout(done, 3000);
    };
    (document.fonts && document.fonts.ready ? document.fonts.ready : Promise.resolve()).then(function () {
      requestAnimationFrame(function () { requestAnimationFrame(run); });
    });
    setTimeout(run, 1200);
  }

  function metricsEntrance() {   // played once, when the second screen first comes into view
    if (reduced || !("IntersectionObserver" in window)) return;
    el.metrics.classList.add("mx-armed");
    var io = new IntersectionObserver(function (entries) {
      if (!entries.some(function (e) { return e.isIntersecting; })) return;
      io.disconnect();
      el.metrics.classList.add("mx-play");
      el.metrics.classList.remove("mx-armed");
    }, { rootMargin: "0px 0px -20% 0px", threshold: 0 });
    io.observe(el.metrics);
  }

  // ---------------------------------------------------------------- boot: a refresh on /chat brings the conversation back
  fill();
  link(el.start, "/upload");
  ["nav-upload", "nav-cta", "menu-upload", "menu-cta"].forEach(function (id) { link($(id), "/upload"); });
  link($("logo"), "/");
  link($("app-logo"), "/");
  enter();
  ticks();
  metricsEntrance();

  var sid = recall();
  if (sid) {
    state.sid = sid;
    api("GET", "/sessions/" + sid + "/messages").then(function (out) {
      state.ready = !!out.ready;
      (out.messages || []).forEach(function (m) {
        if (m.who === "you") el.thread.appendChild(h("article", "card me", m.text));
        else el.thread.appendChild(assistantCard(m.text));
      });
      if ((out.messages || []).length === 1) el.thread.appendChild(chips());   // nothing asked yet: offer the starting questions again
      show(location.pathname);
    }).catch(function () {
      forget();
      show(location.pathname);
    });
  } else {
    show(location.pathname);
  }
})();
