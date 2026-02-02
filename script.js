const nav = document.querySelector("[data-nav]");
const toggle = document.querySelector("[data-nav-toggle]");

if (nav && toggle) {
  toggle.addEventListener("click", () => {
    nav.classList.toggle("is-open");
    const isOpen = nav.classList.contains("is-open");
    toggle.setAttribute("aria-label", isOpen ? "Закрити меню" : "Відкрити меню");
  });
}
