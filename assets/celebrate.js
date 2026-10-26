// Fun layer: confetti + the sneaker mascot when a route is ready, sparkles on click.
// Skipped for people who prefer reduced motion.
(function () {
  var COLORS = ["#00356B", "#286DC0", "#63AAFF", "#BDD5EA", "#ffffff"];
  var reduced = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  var MASCOT = '<img src="/assets/mascot.svg" alt="" aria-hidden="true">';

  function confetti() {
    if (reduced) return;
    var canvas = document.createElement("canvas");
    canvas.className = "confetti-canvas";
    document.body.appendChild(canvas);
    var ctx = canvas.getContext("2d");
    var w = (canvas.width = window.innerWidth), h = (canvas.height = window.innerHeight);
    var pieces = [];
    for (var i = 0; i < 170; i++) {
      var fromLeft = i % 2 === 0;
      pieces.push({
        x: fromLeft ? 0 : w, y: h * 0.75,
        vx: (fromLeft ? 1 : -1) * (4 + Math.random() * 9), vy: -(9 + Math.random() * 11),
        size: 5 + Math.random() * 6, rot: Math.random() * 6, vr: (Math.random() - 0.5) * 0.4,
        color: COLORS[i % COLORS.length], star: i % 7 === 0,
      });
    }
    var start = performance.now();
    function frame(t) {
      var elapsed = t - start;
      ctx.clearRect(0, 0, w, h);
      pieces.forEach(function (p) {
        p.vy += 0.32; p.vx *= 0.99; p.x += p.vx; p.y += p.vy; p.rot += p.vr;
        ctx.save();
        ctx.translate(p.x, p.y); ctx.rotate(p.rot);
        ctx.globalAlpha = Math.max(0, 1 - elapsed / 3200);
        ctx.fillStyle = p.color; ctx.strokeStyle = "#00356B"; ctx.lineWidth = p.color === "#ffffff" ? 1 : 0;
        if (p.star) {
          ctx.font = (p.size * 2.2) + "px sans-serif"; ctx.fillText("★", -p.size, p.size);
        } else {
          ctx.fillRect(-p.size / 2, -p.size / 4, p.size, p.size / 2);
          if (ctx.lineWidth) ctx.strokeRect(-p.size / 2, -p.size / 4, p.size, p.size / 2);
        }
        ctx.restore();
      });
      if (elapsed < 3300) requestAnimationFrame(frame); else canvas.remove();
    }
    requestAnimationFrame(frame);
  }

  function mascot(message) {
    var old = document.querySelector(".mascot-pop");
    if (old) old.remove();
    var el = document.createElement("div");
    el.className = "mascot-pop" + (reduced ? " still" : "");
    el.innerHTML = '<div class="mascot-bubble">' + message + "</div>" + MASCOT;
    document.body.appendChild(el);
    setTimeout(function () { el.classList.add("leaving"); }, 3600);
    setTimeout(function () { el.remove(); }, 4300);
  }

  window.routeCaptainCelebrate = function () {
    confetti();
    mascot("Laced up! Your route is ready 👟");
  };

  // Celebrate each time a new plan appears (each plan card has a unique data-plan id).
  var lastPlan = null;
  new MutationObserver(function () {
    var card = document.querySelector("#history .msg.plan[data-plan]");
    var id = card && card.getAttribute("data-plan");
    if (id && id !== lastPlan) window.routeCaptainCelebrate();
    lastPlan = id || lastPlan;
  }).observe(document.documentElement, { childList: true, subtree: true, attributes: true,
                                         attributeFilter: ["data-plan"] });

  // Sparkles from the wand on every click.
  document.addEventListener("click", function (e) {
    if (reduced) return;
    for (var i = 0; i < 7; i++) {
      var s = document.createElement("span");
      s.className = "sparkle";
      s.textContent = i % 2 ? "✦" : "★";
      var angle = (Math.PI * 2 * i) / 7 + Math.random() * 0.5;
      var dist = 18 + Math.random() * 22;
      s.style.left = e.clientX + "px";
      s.style.top = e.clientY + "px";
      s.style.setProperty("--dx", Math.cos(angle) * dist + "px");
      s.style.setProperty("--dy", Math.sin(angle) * dist + "px");
      s.style.color = COLORS[i % 3 + 1];
      document.body.appendChild(s);
      setTimeout(function (n) { return function () { n.remove(); }; }(s), 700);
    }
  }, true);
})();
