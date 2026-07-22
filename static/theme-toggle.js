// Theme toggle, shared across landing/auth/chat pages.
//
// Applied as early as possible (this script is loaded in <head>, not at
// the end of <body>) specifically to avoid a "flash of wrong theme" —
// if this ran after the page painted, a dark-mode user would see a
// bright white flash on every load before JS caught up.

(function () {
  const STORAGE_KEY = "sibbu-theme";

  function getStoredTheme() {
    try {
      return localStorage.getItem(STORAGE_KEY);
    } catch (e) {
      return null; // localStorage can throw in some privacy modes
    }
  }

  function systemPrefersDark() {
    return window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
  }

  function applyTheme(theme) {
    document.documentElement.setAttribute("data-theme", theme);
    document.querySelectorAll("[data-theme-toggle]").forEach((btn) => {
      btn.setAttribute("aria-pressed", String(theme === "dark"));
      btn.textContent = theme === "dark" ? "☀️ Light" : "🌙 Dark";
    });
  }

  function setTheme(theme) {
    try {
      localStorage.setItem(STORAGE_KEY, theme);
    } catch (e) {
      /* ignore */
    }
    applyTheme(theme);
  }

  // Run immediately (before DOMContentLoaded) so there's no flash.
  const initial = getStoredTheme() || (systemPrefersDark() ? "dark" : "light");
  applyTheme(initial);

  // Wire up any toggle buttons once the DOM is ready.
  document.addEventListener("DOMContentLoaded", () => {
    document.querySelectorAll("[data-theme-toggle]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const current = document.documentElement.getAttribute("data-theme");
        setTheme(current === "dark" ? "light" : "dark");
      });
    });
    // Sync label/aria-pressed now that buttons exist in the DOM.
    applyTheme(document.documentElement.getAttribute("data-theme") || initial);
  });
})();
