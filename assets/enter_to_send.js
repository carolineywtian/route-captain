// Typeform-style keyboard flow.
// Enter: picks the top address suggestion if the list is open, otherwise goes to the next
// question (or plans the run on the last one). Shift+Enter still makes a new line in notes.
document.addEventListener("keydown", function (e) {
  if (e.key !== "Enter" || e.shiftKey || e.isComposing) return;
  var welcome = document.getElementById("welcome");
  if (welcome && welcome.style.display !== "none") {  // welcome screen: Enter starts
    e.preventDefault();
    document.getElementById("start-btn").click();
    return;
  }
  var wizard = document.getElementById("wizard");
  if (!wizard || wizard.style.display === "none") return;
  // tile-only questions leave focus on the page itself: Enter should still move on
  if (!wizard.contains(e.target) && e.target !== document.body) return;
  if (e.target.tagName === "BUTTON") return;  // let buttons handle their own Enter
  e.preventDefault();
  var wrap = e.target.closest(".field-wrap");
  var first = wrap && wrap.querySelector(".suggest-box .s-item");
  if (first) { first.click(); return; }
  var next = document.getElementById("next-btn");
  var send = document.getElementById("send");
  if (next && next.style.display !== "none") next.click();
  else if (send) send.click();
});
