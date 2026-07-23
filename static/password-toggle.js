// Password visibility toggle for auth forms. Each toggle button sits
// inside a `.password-field` wrapper alongside its password `<input>`;
// clicking it flips the input's type between "password" and "text" and
// swaps the eye/eye-off icon via a CSS class (see auth.css).
document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll("[data-password-toggle]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const field = btn.closest(".password-field");
      const input = field ? field.querySelector("input") : null;
      if (!input) return;

      const isNowVisible = input.type === "password";
      input.type = isNowVisible ? "text" : "password";
      btn.classList.toggle("is-visible", isNowVisible);
      btn.setAttribute("aria-pressed", String(isNowVisible));
      btn.setAttribute("aria-label", isNowVisible ? "Hide password" : "Show password");
    });
  });
});
