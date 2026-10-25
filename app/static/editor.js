document.addEventListener("DOMContentLoaded", () => {
  const previewButton = document.getElementById("preview-button");
  if (previewButton) previewButton.addEventListener("click", async () => {
    const body = document.getElementById("body").value;
    const csrf = document.querySelector('input[name="csrf"]').value;
    const data = new FormData(); data.set("body", body); data.set("csrf", csrf);
    previewButton.disabled = true; previewButton.textContent = "正在预览…";
    try {
      const response = await fetch("/markdown/preview", {method: "POST", body: data, credentials: "same-origin"});
      if (!response.ok) throw new Error("预览失败，请刷新页面后重试。");
      document.getElementById("markdown-preview").innerHTML = await response.text();
      document.getElementById("markdown-preview").hidden = false;
    } catch (error) { alert(error.message); }
    finally { previewButton.disabled = false; previewButton.textContent = "预览 Markdown"; }
  });

  const form = document.getElementById("article-form");
  if (form) {
    const status = document.getElementById("article-status");
    const date = document.getElementById("publish-date");
    let choosingScheduleDate = false;
    document.getElementById("save-draft").addEventListener("click", () => { status.value = "draft"; form.requestSubmit(); });
    document.getElementById("publish-now").addEventListener("click", () => {
      status.value = "published";
      date.value = "";
      document.getElementById("publish-time").value = "";
      form.requestSubmit();
    });
    document.getElementById("schedule-publish").addEventListener("click", () => {
      status.value = "scheduled";
      if (date.value) { form.requestSubmit(); return; }
      choosingScheduleDate = true;
      if (typeof date.showPicker === "function") date.showPicker();
      else { date.focus(); date.click(); }
    });
    date.addEventListener("change", () => {
      if (!choosingScheduleDate || !date.value) return;
      choosingScheduleDate = false;
      status.value = "scheduled";
      form.requestSubmit();
    });
  }

  const uploadForm = document.getElementById("attachment-upload-form");
  if (uploadForm) uploadForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const input = uploadForm.querySelector('input[type="file"]');
    const status = document.getElementById("upload-status");
    if (!input.files.length) return;
    const submit = uploadForm.querySelector('button[type="submit"]');
    submit.disabled = true; status.textContent = "正在上传…";
    try {
      const response = await fetch(uploadForm.action, {method: "POST", body: new FormData(uploadForm), headers: {Accept: "application/json"}, credentials: "same-origin"});
      if (!response.ok) {
        const result = await response.json().catch(() => ({}));
        throw new Error(result.detail || "上传失败，请重试。");
      }
      const item = await response.json();
      const textarea = document.getElementById("body");
      const safeName = item.name.replace(/[\]\\]/g, "\\$&");
      let snippet;
      if (item.content_type.startsWith("image/")) snippet = `![${safeName}](${item.url})`;
      else if (item.content_type.startsWith("video/")) snippet = `<video controls src="${item.url}"></video>`;
      else snippet = `[${safeName}](${item.url})`;
      const start = textarea.selectionStart, end = textarea.selectionEnd;
      textarea.setRangeText(`\n${snippet}\n`, start, end, "end");
      textarea.focus();
      input.value = "";
      status.textContent = "上传完成，已插入正文。";
    } catch (error) { status.textContent = error.message; }
    finally { submit.disabled = false; }
  });
});
