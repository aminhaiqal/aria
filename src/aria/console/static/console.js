document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll("[data-dismiss-message]").forEach((button) => {
    button.addEventListener("click", () => button.closest(".message")?.remove());
  });

  document.querySelectorAll("[data-confirm]").forEach((form) => {
    form.addEventListener("submit", (event) => {
      if (!window.confirm(form.dataset.confirm)) event.preventDefault();
    });
  });

  document.querySelectorAll(".click-row[data-href]").forEach((row) => {
    row.addEventListener("click", (event) => {
      if (event.target.closest("a, button, input, select, textarea")) return;
      window.location.assign(row.dataset.href);
    });
  });
});
