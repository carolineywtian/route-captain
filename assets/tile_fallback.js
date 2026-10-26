// If the light basemap ever refuses a tile (e.g. its provider starts requiring a key),
// swap in the standard OpenStreetMap tile, which never needs a key.
document.addEventListener("error", function (e) {
  var img = e.target;
  if (!img || img.tagName !== "IMG" || !img.classList.contains("leaflet-tile")) return;
  var m = img.src.match(/stadiamaps\.com\/tiles\/[^/]+\/(\d+)\/(\d+)\/(\d+)/);
  if (m) {
    img.src = "https://tile.openstreetmap.org/" + m[1] + "/" + m[2] + "/" + m[3] + ".png";
  }
}, true);
