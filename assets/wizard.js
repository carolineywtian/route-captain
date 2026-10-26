// Focus the input of each new question, and show the "planning" screen while agents work.
(function () {
  var lastActive = null, lastMap = null, wasVisible = false, planStamp = null;
  function historyStamp() {  // changes whenever a new plan or error message is shown
    var h = document.querySelector("#history");
    if (!h) return "";
    var card = h.querySelector("[data-plan]");
    return h.childElementCount + "|" + (card ? card.getAttribute("data-plan") : "") + "|" +
           (h.lastElementChild ? h.lastElementChild.textContent.slice(0, 120) : "");
  }
  new MutationObserver(function () {
    var active = document.querySelector(".q.active");
    if (active && active !== lastActive) {
      lastActive = active;
      var input = active.querySelector("input[type=text], input[type=number], textarea");
      if (input) setTimeout(function () { input.focus({ preventScroll: true }); }, 60);
    }
    var results = document.querySelector("#results");
    var visible = results && results.style.visibility !== "hidden" && results.style.display !== "none";
    // hide the planning screen when results first appear, or when a new plan lands while they're
    // already showing (re-planning from a dropped pin)
    var stamp = historyStamp();
    if (document.body.classList.contains("planning") &&
        ((visible && !wasVisible) || (stamp !== planStamp && planStamp !== null))) {
      document.body.classList.remove("planning");
    }
    if (!document.body.classList.contains("planning")) planStamp = null;
    // The map may be built while the results screen is still hidden; once it shows (or the
    // map is rebuilt), tell Leaflet to re-measure so every tile draws.
    var map = document.querySelector("#mapwrap .leaflet-container");
    if (visible && map && (map !== lastMap || !wasVisible)) {
      lastMap = map;
      [60, 300, 800].forEach(function (ms) {
        setTimeout(function () { window.dispatchEvent(new Event("resize")); }, ms);
      });
    }
    wasVisible = visible;
  }).observe(document.documentElement, { childList: true, subtree: true, attributes: true,
                                         attributeFilter: ["class", "style"] });
  var planningTimer = null;
  document.addEventListener("click", function (e) {
    if (e.target.closest && e.target.closest("#send")) {
      document.body.classList.add("planning");
      planStamp = historyStamp();
      // safety net: never leave the overlay up if the server doesn't answer
      clearTimeout(planningTimer);
      planningTimer = setTimeout(function () { document.body.classList.remove("planning"); }, 180000);
    }
  }, true);
})();
