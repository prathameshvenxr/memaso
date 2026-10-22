/* Shared behaviour: the "now" dot on the rail, copy buttons, docs scroll-spy. No dependencies. */
(function () {
  "use strict";

  // The pink dot rides the rail with scroll progress.
  var dot = document.querySelector(".rail i");
  function place() {
    if (!dot) return;
    var max = document.documentElement.scrollHeight - window.innerHeight;
    var f = max > 0 ? window.scrollY / max : 0;
    dot.style.top = Math.max(0, Math.min(1, f)) * dot.parentElement.clientHeight + "px";
  }
  window.addEventListener("scroll", place, { passive: true });
  window.addEventListener("resize", place);
  place();

  // Copy buttons: every <pre class="code"> and every .snippet gets one.
  function attach(btn, getText) {
    btn.addEventListener("click", function () {
      var text = getText().trim();
      var done = function () {
        btn.textContent = "Copied";
        btn.classList.add("done");
        setTimeout(function () { btn.textContent = "Copy"; btn.classList.remove("done"); }, 1600);
      };
      if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(text).then(done, function () {});
    });
  }
  document.querySelectorAll("pre.code").forEach(function (pre) {
    var btn = document.createElement("button");
    btn.className = "copy"; btn.type = "button"; btn.textContent = "Copy";
    btn.setAttribute("aria-label", "Copy code");
    pre.classList.add("has-copy");
    pre.appendChild(btn);
    attach(btn, function () { return pre.querySelector("code").innerText; });
  });
  document.querySelectorAll(".snippet .copy").forEach(function (btn) {
    attach(btn, function () { return btn.parentElement.querySelector("code").innerText; });
  });

  // Docs table of contents: the pink dot marks the last heading that has scrolled past the top.
  var toc = document.querySelector(".toc");
  if (toc) {
    var links = Array.prototype.slice.call(toc.querySelectorAll("a[href^='#']"));
    var targets = links.map(function (a) { return document.getElementById(a.getAttribute("href").slice(1)); });
    var mark = function () {
      var idx = 0;
      targets.forEach(function (el, i) { if (el && el.getBoundingClientRect().top < 150) idx = i; });
      links.forEach(function (a, i) { a.parentElement.classList.toggle("on", i === idx); });
    };
    window.addEventListener("scroll", mark, { passive: true });
    mark();
  }
})();
