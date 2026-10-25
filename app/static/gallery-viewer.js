document.addEventListener("DOMContentLoaded", () => {
  const dialog = document.getElementById("gallery-viewer");
  const previews = [...document.querySelectorAll("[data-gallery-preview]")];
  if (!dialog || !previews.length) return;

  const image = document.getElementById("viewer-image");
  const name = document.getElementById("viewer-name");
  const count = document.getElementById("viewer-count");
  const download = document.getElementById("viewer-download");
  let currentIndex = 0;

  const show = (index) => {
    currentIndex = (index + previews.length) % previews.length;
    const selected = previews[currentIndex];
    image.src = selected.dataset.src;
    image.alt = selected.dataset.name;
    name.textContent = selected.dataset.name;
    count.textContent = `${currentIndex + 1} / ${previews.length}`;
    download.href = selected.dataset.src;
    download.download = selected.dataset.name;
  };

  previews.forEach((preview, index) => preview.addEventListener("click", () => {
    show(index);
    dialog.showModal();
  }));

  dialog.querySelector("[data-viewer-close]").addEventListener("click", () => dialog.close());
  dialog.querySelector("[data-viewer-prev]").addEventListener("click", () => show(currentIndex - 1));
  dialog.querySelector("[data-viewer-next]").addEventListener("click", () => show(currentIndex + 1));
  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) dialog.close();
  });
  dialog.addEventListener("keydown", (event) => {
    if (event.key === "ArrowLeft") { event.preventDefault(); show(currentIndex - 1); }
    if (event.key === "ArrowRight") { event.preventDefault(); show(currentIndex + 1); }
  });
});
