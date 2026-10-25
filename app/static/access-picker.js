document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll(".reader-picker").forEach((picker) => {
    const form = picker.closest("form");
    if (!form) return;

    const update = (button, selected) => {
      const value = button.dataset.readerValue;
      button.classList.toggle("is-selected", selected);
      button.setAttribute("aria-pressed", String(selected));
      const existing = [...form.querySelectorAll('input[name="reader_selection"]')]
        .find((input) => input.value === value);
      if (selected && !existing) {
        const input = document.createElement("input");
        input.type = "hidden";
        input.hidden = true;
        input.name = "reader_selection";
        input.value = value;
        input.dataset.readerInput = "true";
        form.append(input);
      } else if (!selected && existing) {
        existing.remove();
      }
    };

    picker.querySelectorAll(".reader-option").forEach((button) => {
      update(button, button.getAttribute("aria-pressed") === "true");
      button.addEventListener("click", () => {
        update(button, button.getAttribute("aria-pressed") !== "true");
      });
    });
  });
});
