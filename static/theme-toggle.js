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
//
// Bug this version fixes: navigating with the browser's Back/Forward
// buttons can restore a page from the browser's cache (bfcache) exactly
// as it looked when you left it — including a [data-theme] attribute
// that's now stale, if you changed the theme on a *different* page in
// the meantime. The previous version's click handler trusted that
// possibly-stale DOM attribute as "the current theme" when deciding
// which way to toggle, so clicking the button after a Back/Forward
// navigation could flip to the wrong theme, or need two clicks to
// "catch up." Now a `pageshow` listener resyncs the attribute (and the
// button labels) from localStorage — the actual source of truth — every
// time the page becomes visible, including cache restores, so the DOM
// attribute is never allowed to drift from what was last actually chosen.

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

  function currentPreferredTheme() {
    return getStoredTheme() || (systemPrefersDark() ? "dark" : "light");
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

  // Run immediately (before DOMContentLoaded) so there's no flash.
  applyTheme(currentPreferredTheme());

  document.addEventListener("DOMContentLoaded", () => {
    document.querySelectorAll("[data-theme-toggle]").forEach((btn) => {
      btn.addEventListener("click", () => {
        // Toggle relative to the stored preference (source of truth),
        // never the DOM attribute directly — the DOM can be stale right
        // after a bfcache restore, the stored value can't be.
        const current = currentPreferredTheme();
        setTheme(current === "dark" ? "light" : "dark");
      });
    });
    applyTheme(currentPreferredTheme());
  });

  // Re-apply on every page show, including Back/Forward navigations that
  // restore the page from the browser's cache rather than reloading it —
  // this is the actual fix for the theme "changing back" unexpectedly.
  window.addEventListener("pageshow", () => {
    applyTheme(currentPreferredTheme());
  });

  // Belt-and-suspenders: if the theme is changed in another tab/window
  // for the same site, keep this tab in sync too.
  window.addEventListener("storage", (event) => {
    if (event.key === STORAGE_KEY && event.newValue) {
      applyTheme(event.newValue);
    }
  });
})();
