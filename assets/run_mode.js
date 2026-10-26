// Run mode: live turn-by-turn guidance from the phone's GPS, entirely in the browser.
// Your location never leaves the device. A simulation mode replays the route for demos.
(function () {
  var R = {
    route: null, watch: null, sim: null, timer: null, wantStart: false, simMode: false,
    paused: false, voice: true, t0: 0, pausedMs: 0, pauseAt: 0,
    progress: 0, announced: {}, lastMileSpoken: 0, offRoute: false, finished: false, lastFollow: 0, simAlong: 0,
  };
  var $ = function (id) { return document.getElementById(id); };
  var rad = function (d) { return d * Math.PI / 180; };

  function meters(a, b) {
    var dLat = rad(b[0] - a[0]), dLon = rad(b[1] - a[1]);
    var h = Math.sin(dLat / 2) * Math.sin(dLat / 2) +
      Math.cos(rad(a[0])) * Math.cos(rad(b[0])) * Math.sin(dLon / 2) * Math.sin(dLon / 2);
    return 2 * 6371000 * Math.asin(Math.sqrt(h));
  }

  // distance (m) from p to segment a-b, and how far along the segment (0-1) the closest point is
  function project(p, a, b) {
    var k = Math.cos(rad(p[0]));
    var ax = (a[1] - p[1]) * k * 111320, ay = (a[0] - p[0]) * 110540;
    var bx = (b[1] - p[1]) * k * 111320, by = (b[0] - p[0]) * 110540;
    var dx = bx - ax, dy = by - ay, len2 = dx * dx + dy * dy;
    var t = len2 ? Math.max(0, Math.min(1, -(ax * dx + ay * dy) / len2)) : 0;
    return { d: Math.hypot(ax + t * dx, ay + t * dy), t: t };
  }

  // where on the route is p? Searches near the last position first (so out-and-backs don't jump)
  function locate(p) {
    var c = R.route.coords, m = R.route.miles, best = null;
    function scan(lo, hi) {
      for (var i = 0; i < c.length - 1; i++) {
        if (m[i + 1] < lo || m[i] > hi) continue;
        var r = project(p, c[i], c[i + 1]);
        if (!best || r.d < best.dist) best = { dist: r.d, along: m[i] + r.t * (m[i + 1] - m[i]) };
      }
    }
    scan(R.progress - 0.25, R.progress + 0.75);
    if (!best || best.dist > 60) { best = null; scan(-1, 1e9); }
    return best;
  }

  function pointAt(along) {
    var c = R.route.coords, m = R.route.miles;
    for (var i = 0; i < c.length - 1; i++) {
      if (m[i + 1] >= along) {
        var t = m[i + 1] > m[i] ? (along - m[i]) / (m[i + 1] - m[i]) : 0;
        return [c[i][0] + t * (c[i + 1][0] - c[i][0]), c[i][1] + t * (c[i + 1][1] - c[i][1])];
      }
    }
    return c[c.length - 1];
  }

  function cueText(cue) { return cue.text.replace(/ for [\d.]+ mi$/, ""); }
  function arrow(text) {
    var t = text.toLowerCase();
    if (t.indexOf("turn around") >= 0) return "↩";
    if (t.indexOf("bear left") >= 0) return "↖";
    if (t.indexOf("bear right") >= 0) return "↗";
    if (t.indexOf("left") >= 0) return "←";
    if (t.indexOf("right") >= 0) return "→";
    return "↑";
  }
  function fmtDist(mi) {
    if (mi < 0.1) return Math.max(10, Math.round(mi * 5280 / 10) * 10) + " ft";
    return mi.toFixed(2) + " mi";
  }
  function fmtTime(sec) {
    sec = Math.max(0, Math.round(sec));
    var h = Math.floor(sec / 3600), m = Math.floor(sec % 3600 / 60), s = sec % 60;
    return (h ? h + ":" + String(m).padStart(2, "0") : m) + ":" + String(s).padStart(2, "0");
  }
  function say(text) {
    if (!R.voice || !window.speechSynthesis) return;
    var u = new SpeechSynthesisUtterance(text);
    u.rate = 1.05;
    window.speechSynthesis.speak(u);
  }
  function setProps(id, props) {
    if (window.dash_clientside && window.dash_clientside.set_props) window.dash_clientside.set_props(id, props);
  }
  function status(text) { var el = $("run-status"); if (el) el.textContent = text || ""; }

  function elapsedSec() {
    if (R.simMode) return R.simAlong * (R.route.pace || 9.5) * 60;
    if (!R.t0) return 0;
    var now = R.paused ? R.pauseAt : Date.now();
    return (now - R.t0 - R.pausedMs) / 1000;
  }

  function render() {
    if (!R.route) return;
    var total = R.route.total, done = Math.min(R.progress, total), el = elapsedSec();
    $("run-done").textContent = done.toFixed(2);
    $("run-left").textContent = Math.max(0, total - done).toFixed(2);
    $("run-elapsed").textContent = fmtTime(el);
    $("run-pace").textContent = done > 0.05 ? fmtTime(el / done) + "/mi" : "–";
    $("run-progress-fill").style.width = Math.min(100, done / total * 100) + "%";
  }

  function onPosition(lat, lon, accuracy) {
    if (!R.route || R.paused || R.finished) return;
    var p = [lat, lon], spot = locate(p);
    setProps("run-me", { children: [
      { namespace: "dash_leaflet", type: "Circle", props: { center: p, radius: Math.min(accuracy || 10, 80),
        color: "#286DC0", weight: 1, fillColor: "#63AAFF", fillOpacity: 0.15 } },
      { namespace: "dash_leaflet", type: "CircleMarker", props: { center: p, radius: 9, color: "#ffffff",
        weight: 3, fillColor: "#286DC0", fillOpacity: 1 } },
    ] });
    if (Date.now() - R.lastFollow > 4000) {  // keep the map centred on the runner
      R.lastFollow = Date.now();
      setProps("run-leaflet", { viewport: { center: p, zoom: 17, transition: "setView" } });
    }
    if (!spot) return;

    if (spot.dist > 45) {
      status("Off route by about " + Math.round(spot.dist) + " m. Head back toward the blue line.");
      if (!R.offRoute) say("You're off the route. Head back toward the blue line.");
      R.offRoute = true;
      render();
      return;
    }
    if (R.offRoute) say("Back on route.");
    R.offRoute = false;
    status(accuracy > 40 ? "Weak GPS signal (±" + Math.round(accuracy) + " m)." : "");
    if (spot.along > R.progress - 0.05) R.progress = Math.max(R.progress, spot.along);

    var total = R.route.total;
    if (total - R.progress < 0.03) { finish(); return; }

    var cues = R.route.cues, next = null, idx = -1;
    for (var i = 0; i < cues.length; i++) { if (cues[i].mile > R.progress + 0.005) { next = cues[i]; idx = i; break; } }
    if (next) {
      var dist = next.mile - R.progress, text = cueText(next);
      $("run-next").textContent = text;
      $("run-next-dist").textContent = "in " + fmtDist(dist);
      $("run-arrow").textContent = arrow(text);
      if (dist < 0.06 && !R.announced[idx]) {
        R.announced[idx] = true;
        say("In " + fmtDist(dist).replace("ft", "feet").replace("mi", "miles") + ", " + text);
      }
    } else {
      $("run-next").textContent = "Head to the finish";
      $("run-next-dist").textContent = "in " + fmtDist(total - R.progress);
      $("run-arrow").textContent = "🏁";
    }
    var mile = Math.floor(R.progress);
    if (mile > R.lastMileSpoken) {
      R.lastMileSpoken = mile;
      say("Mile " + mile + ". Average pace " + fmtTime(elapsedSec() / R.progress).replace(":", " ") + ".");
    }
    render();
  }

  function finish() {
    R.finished = true;
    R.progress = R.route.total;
    render();
    $("run-next").textContent = "You made it! 🎉";
    $("run-next-dist").textContent = R.route.total.toFixed(2) + " mi in " + fmtTime(elapsedSec());
    $("run-arrow").textContent = "🏁";
    say("You made it. " + R.route.total.toFixed(1) + " miles. Great run!");
    stopTracking();
    if (window.routeCaptainCelebrate) window.routeCaptainCelebrate();
  }

  function stopTracking() {
    if (R.watch !== null && navigator.geolocation) navigator.geolocation.clearWatch(R.watch);
    R.watch = null;
    clearInterval(R.sim); R.sim = null;
  }

  function begin(simulate) {
    stopTracking();
    R.simMode = !!simulate; R.t0 = Date.now(); R.pausedMs = 0; R.paused = false; R.finished = false;
    R.progress = 0; R.simAlong = 0; R.announced = {}; R.lastMileSpoken = 0; R.offRoute = false;
    $("run-pause").textContent = "⏸ Pause";
    clearInterval(R.timer);
    R.timer = setInterval(render, 1000);
    if (simulate) {
      status("Demo: replaying the route at high speed.");
      R.sim = setInterval(function () {
        if (R.paused) return;
        R.simAlong = Math.min(R.route.total, R.simAlong + 0.012);
        var p = pointAt(R.simAlong);
        onPosition(p[0], p[1], 6);
      }, 300);
      return;
    }
    if (!navigator.geolocation) { status("This browser can't share its location."); return; }
    status("Getting your location… allow location access if asked.");
    R.watch = navigator.geolocation.watchPosition(
      function (pos) { onPosition(pos.coords.latitude, pos.coords.longitude, pos.coords.accuracy); },
      function (err) {
        status(err.code === 1
          ? "Location is blocked. Allow it in your browser settings. On a phone, open the app in phone mode (https)."
          : "Waiting for a GPS signal…");
      },
      { enableHighAccuracy: true, maximumAge: 2000, timeout: 20000 });
  }

  window.RouteCaptainRun = {
    load: function (route) {
      R.route = route;
      var first = route.cues.length ? cueText(route.cues[0]).replace(/^Start on /, "") : "";
      var named = first && !/^(footpath|path|unnamed street|trail|steps)$/.test(first);
      $("run-next").textContent = named ? "Start on " + first : "Ready to run";
      $("run-next-dist").textContent = route.total.toFixed(2) + " mi · " + route.headline;
      $("run-arrow").textContent = "↑";
      R.progress = 0; render();
      if (R.wantStart) { R.wantStart = false; begin(false); }
    },
  };

  document.addEventListener("click", function (e) {
    var t = e.target.closest ? e.target.closest("button") : null;
    if (!t) return;
    if (t.id === "start-run-btn") {
      R.wantStart = true;  // tracking begins once the route arrives
      if (window.speechSynthesis) { window.speechSynthesis.cancel(); say("Starting your run."); }  // unlocks voice on phones
    } else if (t.id === "run-sim" && R.route) {
      if (window.speechSynthesis) window.speechSynthesis.cancel();
      begin(true);
    } else if (t.id === "run-pause" && R.route) {
      R.paused = !R.paused;
      if (R.paused) { R.pauseAt = Date.now(); t.textContent = "▶ Resume"; status("Paused."); }
      else { R.pausedMs += Date.now() - R.pauseAt; t.textContent = "⏸ Pause"; status(""); }
    } else if (t.id === "run-voice") {
      R.voice = !R.voice;
      t.textContent = R.voice ? "🔊 Voice on" : "🔇 Voice off";
      if (!R.voice && window.speechSynthesis) window.speechSynthesis.cancel();
    } else if (t.id === "run-end") {
      stopTracking(); clearInterval(R.timer);
      if (window.speechSynthesis) window.speechSynthesis.cancel();
    }
  }, true);
})();
