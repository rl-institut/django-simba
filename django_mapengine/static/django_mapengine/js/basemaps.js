const basemaps = ["satellite"];
const defaultIcon = "/static/django_mapengine/images/layer_ctrl_default.svg";
const satelliteIcon = "/static/django_mapengine/images/layer_ctrl_satellite.svg";

function toggleBasemap() {
  const current = map_store.cold.basemap;
  const isSatellite = current === "satellite";

  const nextBasemap = isSatellite ? null : "satellite";
  map_store.cold.basemap = nextBasemap;
  map_store.cold.basemapFocusElement = "basemaps__toggle";

  const legend = document.getElementById("legend");
  for (const bm of basemaps) {
    map.setLayoutProperty(bm, "visibility", "none");
  }

  if (nextBasemap !== null) {
    map.setLayoutProperty(nextBasemap, "visibility", "visible");
    legend.hidden = false;
  } else {
    legend.hidden = true;
  }

  // Update basemap
  document.getElementById("basemap-icon").src = isSatellite ? satelliteIcon : defaultIcon;
  document.getElementById("basemap-label").innerText = isSatellite ? "Satellitenansicht" : "Kartenansicht";
}


// Toggle basemaps control
let toggleBasemapButton = document.getElementById("basemaps-control");

function toggleBasemapControl() {
  const basemapControl = document.getElementById("basemaps");

  if (basemapControl.style.display !== "none") {
    basemapControl.style.display = "none";
  }
  else {
    basemapControl.style.display = "flex";
    document.getElementById(map_store.cold.basemapFocusElement).focus();
  }
}

toggleBasemapButton.onclick = toggleBasemapControl;
