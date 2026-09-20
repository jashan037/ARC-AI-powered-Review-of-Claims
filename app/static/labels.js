/* Small pure helpers for ARC's labels. No DOM here, so they can be tested under Node (tests/test_web_ui.py). */
(function (root) {
  "use strict";

  var MAX_OPTION = 36;

  // "TC07: DEMO CLAIM: prescription missing" -> "TC07 · Prescription missing" (short enough for the dropdown; the full title goes in the tooltip)
  function claimOptionLabel(id, title) {
    var t = String(title || "").replace(/^\s*(demo claim|demo|claim)\s*[:\-]\s*/i, "").replace(/\s+/g, " ").trim();
    if (t) t = t.charAt(0).toUpperCase() + t.slice(1);
    var label = id + (t ? " \u00B7 " + t : "");
    if (label.length > MAX_OPTION) {
      var cut = label.slice(0, MAX_OPTION - 1);
      // end on a whole word: if the cut lands inside a word, drop that partial word
      if (label.charAt(MAX_OPTION - 1) !== " " && cut.lastIndexOf(" ") > id.length + 3) cut = cut.slice(0, cut.lastIndexOf(" "));
      label = cut.replace(/[\s,;:(\-]+$/, "") + "\u2026";
    }
    return label;
  }

  // The quick questions that make sense for the claim that is loaded now: the policy questions when no claim is loaded, the group written for
  // that claim if there is one, otherwise the generic ones ("Assess this claim", "Which documents are missing?").
  function quickGroupsFor(claimId, groups) {
    var own = groups.filter(function (g) { return g.claim === claimId; });
    if (own.length) return own;
    if (!claimId) return [];
    var generic = groups.filter(function (g) { return g.claim === "*"; });
    return generic;
  }

  var api = { claimOptionLabel: claimOptionLabel, quickGroupsFor: quickGroupsFor };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.ARCLabels = api;
})(typeof window !== "undefined" ? window : this);
