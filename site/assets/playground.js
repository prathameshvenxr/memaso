/* The hero playground: a browser port of the engine's rules path (extraction, judge, supersession, redaction,
   packing). No model, no network. The Python engine is the real thing; this shows its behaviour on your sentences. */
(function () {
  "use strict";
  var root = document.getElementById("pg");
  if (!root) return;

  var DAYS = ["jan 5", "feb 1", "mar 3", "apr 9", "may 14", "jun 2", "jul 8", "aug 19"];
  var MAX = 5;
  var reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  var MARK = /^(?:(?:actually|so|ok|okay|well|now|update|also|and|but|then|btw|fyi|note|anyway)\b[,:]?\s+)+/i;
  var PV = "prefers?|likes?|dislikes?|hates?|wants?";
  var KEY = new RegExp("^(?<k>.{1,80}?)\\s+(?:(?<be>is|are|was|were|will be|should be|must be|set to|=)|(?<pv>" + PV + "))\\s+(?<v>.{1,120})$", "i");
  var DECISION = /\b(decided|agreed|approved|we will|we'll|going forward)\b/i;
  var HEDGE = /\b(maybe|might|perhaps|not sure|i think|probably|could be)\b/i;
  var COMPOUND = new RegExp("(?:[;,]|\\s+(?:and|but|while|plus|&)\\s+).*\\b(?:is|are|was|were|" + PV + ")\\b", "i");
  var PRON = new Set(["it", "this", "that", "there", "he", "she", "they", "you"]);
  var DET = new Set(["the", "a", "an", "our", "your", "my", "their", "his", "her", "its", "this", "that", "current", "new"]);
  var POSITIVE = new Set(["prefer", "like", "want"]);

  // updates phrased as events: "we raised our monthly budget to 80k" is the statement "monthly budget is 80k"
  var CHANGE = "(?:raised|increased|bumped|boosted|lifted|upped|lowered|reduced|cut|dropped|decreased|changed|updated|revised|adjusted|moved|switched|set|pushed)";
  var UNAMBIGUOUS = "(?:raised|increased|bumped|boosted|lifted|upped|lowered|reduced|dropped|decreased|changed|updated|revised|adjusted|moved|switched|pushed)";
  var ADV = "(?:(?:just|now|already|recently|finally)\\s+)?";
  var TO = "(?:from\\s+[^,;]{1,30}?\\s+)?to\\s+(?<v>[^\\s,;:!?]+(?:\\s[^\\s,;:!?]+){0,2})";
  var SUBJ = /^(?:we|i|they|he|she|you|the\s+team|everyone|someone)(?:'ve|\s+have|\s+had)?\s+/i;
  var ACTIVE_REST = new RegExp("^" + ADV + CHANGE + "\\s+(?:up\\s+|down\\s+)?(?<k>.{2,60}?)\\s+" + TO + "$", "i");
  var NO_SUBJECT = new RegExp("^" + ADV + UNAMBIGUOUS + "\\s+(?:up\\s+|down\\s+)?(?<k>.{2,60}?)\\s+" + TO + "$", "i");
  var PASSIVE = new RegExp("^(?<k>.{2,60}?)\\s+(?:has\\s+been|have\\s+been|was|were|got)\\s+" + ADV + CHANGE + "\\s+(?:up\\s+|down\\s+)?" + TO + "$", "i");
  var INTRANS = new RegExp("^(?<k>.{2,60}?)\\s+(?:went|has\\s+gone|have\\s+gone|rose|has\\s+risen|climbed|has\\s+climbed|fell|has\\s+fallen|dropped|has\\s+dropped|jumped|slipped)\\s+(?:up\\s+|down\\s+)?" + TO + "$", "i");
  var VALUE_WORDS = new Set(["daily", "weekly", "monthly", "quarterly", "yearly", "inr", "usd", "eur", "gbp", "on", "off", "enabled", "disabled", "paused", "active", "manual", "automatic"]);
  var UNIT_WORDS = new Set(["percent", "%", "usd", "inr", "eur", "gbp", "dollars", "rupees", "euros", "pounds", "per", "daily", "weekly", "monthly", "yearly"]);
  var BAD_KEY = new Set(["it", "this", "that", "there", "he", "she", "they", "you", "we", "i", "him", "her", "them", "us", "me", "in", "on", "at", "by", "into", "out", "up", "down", "off", "half"]);
  // a real value is a number (with an optional unit) or a setting word; "this month" is dropped, the future is not a fact
  function cleanValue(v) {
    v = v.replace(/\.+$/, "").replace(/\s+(?:today|yesterday|now|(?:this|last)\s+(?:week|month|quarter|year))$/i, "");
    if (/\b(?:next|tomorrow|soon|later|tonight)\b/i.test(v)) return null;
    var t = v.toLowerCase().split(/\s+/).filter(Boolean);
    if (!t.length || t.length > 2 || !(/\d/.test(t[0]) || VALUE_WORDS.has(t[0]))) return null;
    if (t.length === 2 && !UNIT_WORDS.has(t[1])) return null;
    return v;
  }
  function keyOk(k) {
    var t = Array.from(tokens(norm(k)));
    return t.length > 0 && t.length <= 4 && !t.some(function (x) { return BAD_KEY.has(x); });
  }
  function rewriteChange(text) {
    var s = text.trim().replace(/[.!]+$/, "").trim();
    var sm = SUBJ.exec(s);
    var cands = [sm ? ACTIVE_REST.exec(s.slice(sm[0].length)) : null, NO_SUBJECT.exec(s), PASSIVE.exec(s), INTRANS.exec(s)];
    for (var i = 0; i < cands.length; i++) {
      var m = cands[i];
      if (!m) continue;
      var v = cleanValue(m.groups.v), k = m.groups.k.trim().replace(/^(?:our|my|their|your|its)\s+/i, "");
      if (v !== null && keyOk(k)) return k + " is " + v;
    }
    return null;
  }

  // "monthly budget" is "budget" only when the extra word is a period and the numbers are the same size
  var PERIODS = new Set(["daily", "weekly", "monthly", "quarterly", "yearly", "annual", "hourly"]);
  var EQUIV = new Set(["total", "overall", "main", "updated", "latest"]);
  var UNITS = { k: 1e3, thousand: 1e3, m: 1e6, million: 1e6, b: 1e9, billion: 1e9 };
  function parseNumber(v) {
    var m = /(\d[\d,]*(?:\.\d+)?)\s*(%|thousand|million|billion|[kmb]\b)?/.exec(v.toLowerCase());
    return m ? { n: parseFloat(m[1].replace(/,/g, "")) * (UNITS[m[2]] || 1), pct: m[2] === "%" } : null;
  }
  function refinedSame(a, b, va, vb) {
    var A = tokens(a), B = tokens(b), small = subset(A, B) ? A : subset(B, A) ? B : null;
    if (!small) return false;
    var large = small === A ? B : A, extra = Array.from(large).filter(function (t) { return !small.has(t); });
    if (!extra.every(function (t) { return PERIODS.has(t) || EQUIV.has(t); })) return false;
    if (!extra.some(function (t) { return PERIODS.has(t); })) return true;
    var na = parseNumber(va), nb = parseNumber(vb);
    if (!na || !nb || na.pct !== nb.pct || Math.min(na.n, nb.n) <= 0) return false;
    return Math.max(na.n, nb.n) / Math.min(na.n, nb.n) <= 4;
  }

  var norm = function (s) { return s.toLowerCase().replace(/\s+/g, " ").trim(); };
  var stripMarkers = function (s) { return s.trim().replace(MARK, "").trim(); };
  var subjectOf = function (k) { k = norm(k); return k === "i" || k === "we" ? "user" : k; };
  var tokens = function (k) { return new Set((k.match(/\w+/g) || []).filter(function (t) { return !DET.has(t); })); };
  var sameSet = function (a, b) { return a.size === b.size && Array.from(a).every(function (x) { return b.has(x); }); };
  var subset = function (a, b) { return a.size < b.size && Array.from(a).every(function (x) { return b.has(x); }); };
  function relation(a, b) {
    var A = tokens(a), B = tokens(b);
    if (sameSet(A, B)) return "same";
    return subset(A, B) || subset(B, A) ? "related" : "different";
  }
  function match(text) { return KEY.exec(stripMarkers(text).replace(/[.!?]+$/, "")); }
  function keyOf(text) {
    var m = match(text);
    if (!m) return null;
    var be = (m.groups.be || "").toLowerCase();
    if (be === "was" || be === "were") return null;
    var k = subjectOf(m.groups.k);
    if (!m.groups.pv) {
      var tk = tokens(k);
      if (k === "user" || tk.size === 0 || Array.from(tk).every(function (t) { return PRON.has(t); })) return null;
    }
    if (m.groups.pv) k += " " + m.groups.pv.toLowerCase().replace(/s$/, "");
    return { k: k, v: norm(m.groups.v).replace(/^(?:now\s+|currently\s+)?(?:set\s+to\s+)?/, "") };
  }
  function prefParts(text) {
    var m = match(text);
    if (!m || !m.groups.pv) return null;
    return { subj: Array.from(tokens(subjectOf(m.groups.k))).sort().join(" "), verb: m.groups.pv.toLowerCase().replace(/s$/, ""), val: norm(m.groups.v) };
  }

  // Layer B input: conservative rules, exactly the cases the engine reads.
  function extract(text) {
    var out = [], skipped = [];
    text.split(/(?<=[.!?])\s+|\n+/).forEach(function (raw) {
      var s = raw.trim();
      if (!s) return;
      if (s.indexOf("?") >= 0) { skipped.push("a question states nothing"); return; }
      s = stripMarkers(s.replace(/[.!]+$/, "").trim());
      if (s.length < 6 || s.length > 200) return;
      if (HEDGE.test(s)) { skipped.push("a hedge (“" + s + "”) is not a fact"); return; }
      s = rewriteChange(s) || s;
      var m = KEY.exec(s);
      if (m && /^(was|were)$/i.test(m.groups.be || "")) { skipped.push("a past-tense remark is history, not a current fact"); return; }
      if (m && COMPOUND.test(m.groups.v)) { skipped.push("a compound sentence stays verbatim-only"); return; }
      var kind = m && m.groups.pv ? "preference" : DECISION.test(s) ? "decision" : m ? "fact" : null;
      if (kind) out.push({ text: s, kind: kind });
    });
    return { cands: out, skipped: skipped };
  }

  function judge(cand, facts) {
    var live = facts.filter(function (f) { return f.to === null; });
    var dup = live.find(function (f) { return norm(f.text) === norm(cand.text); });
    if (dup) return { v: "duplicate", target: dup };
    var cp = prefParts(cand.text);
    if (cp) {
      var opp = live.find(function (f) {
        var np = prefParts(f.text);
        return np && np.subj === cp.subj && np.val === cp.val && POSITIVE.has(np.verb) !== POSITIVE.has(cp.verb);
      });
      return opp ? { v: "contradicts", target: opp } : { v: "new" };
    }
    var ck = keyOf(cand.text);
    if (!ck) return { v: "new" };
    var amb = null;
    for (var i = 0; i < live.length; i++) {
      var f = live[i];
      if (prefParts(f.text)) continue;
      var nk = keyOf(f.text);
      if (!nk) continue;
      var rel = relation(ck.k, nk.k);
      if (rel === "same") return nk.v === ck.v ? { v: "duplicate", target: f } : { v: "updates", target: f };
      if (rel === "related") {
        if (refinedSame(ck.k, nk.k, ck.v, nk.v)) return { v: "updates", target: f };
        amb = f;
      }
    }
    return amb ? { v: "new", ambiguous: "overlaps “" + amb.text + "”, so a model would decide; without one both stay" } : { v: "new" };
  }

  function replay(stmts) {
    var facts = [], events = [];
    stmts.forEach(function (st, i) {
      var ev = [];
      var ex = extract(st.text);
      ex.skipped.forEach(function (r) { ev.push("No fact: " + r + "."); });
      ex.cands.forEach(function (c) {
        var j = judge(c, facts);
        if (j.v === "duplicate") { ev.push("“" + c.text + "” repeats a current fact, so confidence goes up."); return; }
        var fact = { text: c.text, kind: c.kind, from: i, to: null, amb: j.ambiguous || null };
        if (j.target) {
          j.target.to = i;
          ev.push((j.v === "contradicts" ? "Opposite preference: " : "") + "“" + c.text + "” closes “" + j.target.text + "”.");
        } else ev.push("New fact: “" + c.text + "”" + (j.ambiguous ? " (" + j.ambiguous + ")" : "") + ".");
        facts.push(fact);
      });
      if (!ex.cands.length && !ex.skipped.length) ev.push("No plain statement to turn into a fact. The words are still stored and searchable.");
      events.push(ev);
    });
    return { facts: facts, events: events };
  }

  var esc = function (s) { return s.replace(/[&<>"]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; }); };
  var rx = function (s) { return s.trim().split(/\s+/).map(function (t) { return t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"); }).join("\\s+"); };
  function redact(text, spans) {
    var plain = text, html = esc(text), used = false;
    spans.forEach(function (sp) {
      var re = new RegExp(rx(sp), "i");
      if (re.test(plain)) { used = true; plain = plain.replace(re, "[superseded]"); html = html.replace(new RegExp(rx(esc(sp)), "i"), '<span class="red">[superseded]</span>'); }
    });
    var rest = plain.replace(/\[superseded\]|user:|\W+/gi, "");
    return { html: html, gone: used && rest.length < 8, plain: plain };
  }
  var hash8 = function (s) { var h = 2166136261; for (var i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 16777619); } return (h >>> 0).toString(16).padStart(8, "0"); };

  // ---------- state and rendering ----------
  var all = [];                 // every statement ever added; the view replays the last MAX
  var lanes = document.getElementById("pg-lanes");
  var promptEl = document.getElementById("pg-prompt");
  var tokEl = document.getElementById("pg-tokens");
  var statusEl = document.getElementById("pg-status");
  var input = document.getElementById("pg-input");
  var playing = false;
  var animate = false;        // only the newest statement and its facts animate in

  function render() {
    var stmts = all.slice(-MAX);
    var offset = all.length - stmts.length;
    var res = replay(stmts);
    var n = Math.max(stmts.length, 3);
    lanes.style.setProperty("--n", n);
    var h = "";
    var col = function (i) { return i + 2; };
    h += '<div class="lbl said" style="grid-row:1">said</div>';
    for (var i = 0; i < n; i++) {
      var st = stmts[i];
      h += '<div class="cell" style="grid-row:1;grid-column:' + col(i) + '">' +
        (st ? '<span class="date">' + DAYS[(offset + i) % DAYS.length] + '</span><div class="note' + (animate && i === stmts.length - 1 ? " pop" : "") + '">' + esc(st.text) + "</div>" : "") + "</div>";
    }
    h += '<div class="lane-sep" style="grid-row:2"></div>';
    h += '<div class="lbl verbatim" style="grid-row:3">verbatim</div>';
    var chunks = [];
    for (i = 0; i < n; i++) {
      var s = stmts[i], cell = "";
      if (s) {
        var spans = res.facts.filter(function (f) { return f.to !== null && f.from === this; }, i).map(function (f) { return f.text; });
        var r = redact(s.text, spans);
        chunks.push({ i: i, r: r, text: s.text });
        cell = '<div class="chunk' + (r.gone ? " gone" : "") + (animate && i === stmts.length - 1 ? " pop" : "") + '">' + r.html + "</div>";
      }
      h += '<div class="cell" style="grid-row:3;grid-column:' + col(i) + '">' + cell + "</div>";
    }
    h += '<div class="lane-sep" style="grid-row:4"></div>';
    h += '<div class="lbl facts" style="grid-row:5">facts</div>';
    if (!res.facts.length) h += '<div class="empty" style="grid-row:5">' + (stmts.length ? "No plain statements yet. Try “Budget is 50k”." : "Say something below.") + "</div>";
    res.facts.forEach(function (f, k) {
      var end = f.to !== null ? f.to + 2 : n + 3;
      var cls = "factrow" + (f.to !== null ? " closed" : " cur") + (f.amb ? " amb" : "") + (f.kind === "preference" ? " pref" : "") + (animate && f.from === stmts.length - 1 ? " pop" : "");
      h += '<div class="' + cls + '" style="grid-row:' + (5 + k) + ";grid-column:" + col(f.from) + " / " + end + '"><div class="t">' + esc(f.text) +
        '</div><div class="bar"></div>' + (f.amb ? '<span class="tag">' + esc(f.amb) + "</span>" : "") + "</div>";
    });
    h += '<div class="pg-now" aria-hidden="true" style="grid-column:' + (n + 3) + ";grid-row:1 / span " + (4 + Math.max(res.facts.length, 1)) + '"></div>';
    lanes.innerHTML = h;

    // The prompt block, in the engine's own format and order: current facts, then surviving verbatim text.
    var last = stmts.length - 1, lines = [], plain = [];
    res.facts.filter(function (f) { return f.to === null; }).forEach(function (f) {
      var t = '<memory id="' + hash8(f.text) + '" kind="' + f.kind + '">' + f.text + "</memory>";
      plain.push(t);
      lines.push('<span class="' + (f.from === last ? "hot" : "m") + '">' + esc(t) + "</span>");
    });
    chunks.forEach(function (c) {
      if (c.r.gone) return;
      var t = '<chunk observed="' + DAYS[(offset + c.i) % DAYS.length] + '">' + c.r.plain + "</chunk>";
      plain.push(t);
      lines.push('<span class="m">' + esc(t) + "</span>");
    });
    promptEl.innerHTML = lines.length ? lines.join("\n") : '<span class="m">(nothing to recall yet)</span>';
    var bytes = new TextEncoder().encode(plain.join("\n")).length;
    tokEl.textContent = "about " + Math.ceil(bytes / 3) + " tokens, 0 model calls";
    statusEl.textContent = stmts.length ? res.events[last].join(" ") : "";
  }

  function add(text) {
    text = text.trim();
    if (!text) return;
    all.push({ text: text });
    animate = !reduce;
    render();
  }
  function reset(seed) {
    all = [];
    (seed || []).forEach(function (t) { all.push({ text: t }); });
    animate = false;
    render();
  }
  function play(seq) {
    if (playing) return;
    reset([]);
    if (reduce) { seq.forEach(add); return; }
    playing = true;
    seq.forEach(function (t, k) {
      setTimeout(function () { add(t); if (k === seq.length - 1) playing = false; }, 500 + k * 1500);
    });
  }

  var SEED = ["Budget is 50k. I want weekly reports in INR.", "Actually the budget is 80k."];
  var EXAMPLE = ["Budget is 50k. I want weekly reports in INR.", "I hate pie charts.", "Actually the budget is 80k.", "I like pie charts."];

  document.getElementById("pg-form").addEventListener("submit", function (e) {
    e.preventDefault();
    add(input.value);
    input.value = "";
    input.focus();
  });
  document.getElementById("pg-play").addEventListener("click", function () { play(EXAMPLE); });
  document.getElementById("pg-clear").addEventListener("click", function () { playing = false; reset([]); input.focus(); });
  root.querySelectorAll(".chips button").forEach(function (b) {
    b.addEventListener("click", function () { add(b.dataset.say || b.textContent); });
  });

  reset(SEED);
})();
