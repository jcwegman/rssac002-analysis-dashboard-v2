// Applied before first paint so a chosen theme never flashes the other one.
(function () {
  try {
    var saved = localStorage.getItem("theme");
    if (saved === "light" || saved === "dark") document.documentElement.dataset.theme = saved;
  } catch (error) {
    /* storage unavailable: follow the system setting */
  }
})();
