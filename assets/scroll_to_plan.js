// When a new result appears (plan or message), scroll the side panel to the top of it.
(function () {
  var last = null;
  new MutationObserver(function () {
    var card = document.querySelector("#history .msg");
    if (!card) return;
    var id = card.getAttribute("data-plan") || card.textContent.slice(0, 80);
    if (id !== last) {
      last = id;
      card.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  }).observe(document.documentElement, { childList: true, subtree: true, attributes: true,
                                         attributeFilter: ["data-plan"] });
})();
