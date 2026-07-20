// Smooth-scroll for in-page nav links. Everything else on the landing
// page is static HTML/CSS on purpose — no framework needed for a page
// with no client state.
document.querySelectorAll('a[href^="#"]').forEach((link) => {
  link.addEventListener("click", (event) => {
    const target = document.querySelector(link.getAttribute("href"));
    if (!target) return;
    event.preventDefault();
    target.scrollIntoView({ behavior: "smooth", block: "start" });
  });
});
