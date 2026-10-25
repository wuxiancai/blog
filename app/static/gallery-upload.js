document.addEventListener("DOMContentLoaded", () => {
  const folderInput = document.querySelector('input[name="folder_files"]');
  const status = document.getElementById("folder-filter-status");
  if (!folderInput || !status) return;

  const imageExtensions = new Set([
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic", ".heif", ".dng", ".raw",
  ]);

  folderInput.addEventListener("change", () => {
    const originalFiles = [...folderInput.files];
    const imageFiles = originalFiles.filter((file) => {
      const dot = file.name.lastIndexOf(".");
      return dot >= 0 && imageExtensions.has(file.name.slice(dot).toLowerCase());
    });

    try {
      const filteredFiles = new DataTransfer();
      imageFiles.forEach((file) => filteredFiles.items.add(file));
      folderInput.files = filteredFiles.files;
    } catch {
      // The server applies the same extension filter if FileList cannot be replaced.
    }

    const skippedCount = originalFiles.length - imageFiles.length;
    status.textContent = imageFiles.length
      ? `已筛选出 ${imageFiles.length} 张图片${skippedCount ? `，已跳过 ${skippedCount} 个非图片文件` : ""}。`
      : "所选文件夹中没有支持的图片格式。";
  });
});
