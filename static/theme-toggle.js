// Theme toggle, shared across landing/auth/chat/settings pages.
//
// Applied as early as possible (this script is loaded in <head>, not at
// the end of <body>) specifically to avoid a "flash of wrong theme" —
// if this ran after the page painted, a dark-mode user would see a
// bright white flash on every load before JS caught up.
//
// Icon swap (sun/moon) is handled entirely by CSS keyed off the
// [data-theme] attribute on <html> — see theme.css. This script only
// ever sets that attribute, aria-pressed, and the text of the button's
// `.toggle-label` span. It deliberately never touches a button's
// innerHTML/textContent wholesale, because doing that on every theme
// change is what previously wiped out the button's icon markup.

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

  function syncButtons(theme) {
    document.querySelectorAll("[data-theme-toggle]").forEach((btn) => {
      const isDark = theme === "dark";
      btn.setAttribute("aria-pressed", String(isDark));
      const label = btn.querySelector(".toggle-label");
      if (label) {
        label.textContent = isDark ? "Light" : "Dark";
      }
    });
  }

  function applyTheme(theme) {
    document.documentElement.setAttribute("data-theme", theme);
    syncButtons(theme);
  }

  function setTheme(theme) {
    try {
      localStorage.setItem(STORAGE_KEY, theme);
    } catch (e) {
      /* ignore */
    }
    applyTheme(theme);
  }

  // Run immediately (before DOMContentLoaded) so there's no flash. Buttons
  // may not exist in the DOM yet at this point — that's fine, syncButtons
  // is called again below once they do.
  const initial = getStoredTheme() || (systemPrefersDark() ? "dark" : "light");
  document.documentElement.setAttribute("data-theme", initial);

  document.addEventListener("DOMContentLoaded", () => {
    document.querySelectorAll("[data-theme-toggle]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const current = document.documentElement.getAttribute("data-theme");
        setTheme(current === "dark" ? "light" : "dark");
      });
    });
    syncButtons(document.documentElement.getAttribute("data-theme") || initial);
  });
})();
