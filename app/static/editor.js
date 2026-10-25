document.addEventListener("DOMContentLoaded", () => {
  const form = document.getElementById("article-form");
  const editor = document.getElementById("rich-editor");
  const body = document.getElementById("body");
  const status = document.getElementById("upload-status");
  const fileInput = document.getElementById("media-file-input");
  const uploadUrl = document.getElementById("media-upload-url");
  const readerSelection = document.getElementById("reader-selection");
  let savedRange = null;
  let requestedMedia = "file";
  let articleSavePromise = null;

  readerSelection?.addEventListener("change", () => {
    const selected = [...readerSelection.selectedOptions];
    const newlySelected = selected.filter((option) => !option.dataset.wasSelected);
    if (newlySelected.some((option) => option.value === "all")) {
      selected.forEach((option) => { if (option.value !== "all") option.selected = false; });
    } else if (newlySelected.length) {
      const all = [...readerSelection.options].find((option) => option.value === "all");
      if (all) all.selected = false;
    }
    [...readerSelection.options].forEach((option) => { option.dataset.wasSelected = option.selected ? "1" : ""; });
  });
  readerSelection?.dispatchEvent(new Event("change"));

  const syncBody = () => { if (editor && body) body.value = editor.innerHTML; };
  const saveSelection = () => {
    const selection = window.getSelection();
    if (selection && selection.rangeCount && editor?.contains(selection.anchorNode)) savedRange = selection.getRangeAt(0).cloneRange();
  };
  const insertNode = (node) => {
    editor.focus();
    const selection = window.getSelection();
    const range = savedRange?.cloneRange();
    if (range && editor.contains(range.commonAncestorContainer)) {
      selection.removeAllRanges(); selection.addRange(range); range.deleteContents(); range.insertNode(node); range.setStartAfter(node); range.collapse(true);
    } else {
      const end = document.createRange(); end.selectNodeContents(editor); end.collapse(false); end.insertNode(node); end.setStartAfter(node); end.collapse(true);
    }
    selection.removeAllRanges(); selection.addRange(range || endRangeAfter(node));
    savedRange = selection.getRangeAt(0).cloneRange();
    editor.dispatchEvent(new Event("input", {bubbles: true}));
  };
  const endRangeAfter = (node) => { const range = document.createRange(); range.setStartAfter(node); range.collapse(true); return range; };

  if (editor) {
    editor.addEventListener("keyup", saveSelection);
    editor.addEventListener("mouseup", saveSelection);
    editor.addEventListener("input", syncBody);
    document.addEventListener("selectionchange", saveSelection);
    form.addEventListener("submit", syncBody);
    form.addEventListener("submit", () => { if (status.value === "published") syncBody(); });

    editor.addEventListener("paste", (event) => {
      const files = [...(event.clipboardData?.items || [])].filter((item) => item.kind === "file").map((item) => item.getAsFile()).filter(Boolean);
      if (!files.length) return;
      event.preventDefault();
      files.forEach((file) => uploadFile(file));
    });
    editor.addEventListener("dragover", (event) => { if ([...(event.dataTransfer?.types || [])].includes("Files")) event.preventDefault(); });
    editor.addEventListener("drop", (event) => {
      const files = [...(event.dataTransfer?.files || [])];
      if (!files.length) return;
      event.preventDefault();
      if (document.caretRangeFromPoint) savedRange = document.caretRangeFromPoint(event.clientX, event.clientY);
      files.forEach((file) => uploadFile(file));
    });
  }

  document.querySelectorAll(".rich-toolbar [data-command], .rich-toolbar [data-block]").forEach((button) => {
    button.addEventListener("mousedown", (event) => event.preventDefault());
    button.addEventListener("click", () => {
      editor.focus();
      if (button.dataset.command) document.execCommand(button.dataset.command, false);
      if (button.dataset.block) document.execCommand("formatBlock", false, `<${button.dataset.block}>`);
      syncBody(); saveSelection();
    });
  });
  document.getElementById("insert-link")?.addEventListener("mousedown", (event) => event.preventDefault());
  document.getElementById("insert-link")?.addEventListener("click", () => {
    const url = prompt("输入链接地址（https://…）");
    if (!url) return;
    editor.focus();
    if (savedRange) { const selection = window.getSelection(); selection.removeAllRanges(); selection.addRange(savedRange); }
    document.execCommand("createLink", false, url); syncBody(); saveSelection();
  });

  document.querySelectorAll("[data-media]").forEach((button) => button.addEventListener("click", () => {
    requestedMedia = button.dataset.media;
    fileInput.accept = requestedMedia === "image" ? "image/*" : requestedMedia === "video" ? "video/*" : "image/*,video/*,.pdf,.txt,.zip,.docx,.odt";
    fileInput.click();
  }));
  fileInput?.addEventListener("change", () => {
    [...fileInput.files].forEach((file) => uploadFile(file));
    fileInput.value = "";
  });

  async function ensureArticleSaved() {
    if (uploadUrl.dataset.url) return;
    if (!articleSavePromise) articleSavePromise = (async () => {
      syncBody(); status.textContent = "正在保存草稿以便插入文件…";
      const response = await fetch(form.action, {method: "POST", body: new FormData(form), credentials: "same-origin", headers: {Accept: "text/html"}});
      const match = response.url.match(/\/articles\/(\d+)\/edit$/);
      if (!response.ok || !match) throw new Error("自动保存草稿失败，请先点击“保存草稿暂不发表”。");
      form.action = response.url;
      uploadUrl.dataset.url = `/articles/${match[1]}/attachments`;
      history.replaceState({}, "", response.url);
      status.textContent = "草稿已保存，正在上传…";
    })().catch((error) => { articleSavePromise = null; throw error; });
    await articleSavePromise;
  }

  async function uploadFile(file) {
    if (!file || !editor) return;
    try {
      await ensureArticleSaved();
      status.textContent = `正在上传 ${file.name}…`;
      const data = new FormData();
      data.set("file", file); data.set("csrf", document.querySelector('input[name="csrf"]').value);
      const response = await fetch(uploadUrl.dataset.url, {method: "POST", body: data, credentials: "same-origin", headers: {Accept: "application/json"}});
      const item = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(item.detail || "上传失败，请重试。");
      let node;
      if (item.content_type.startsWith("image/")) {
        node = document.createElement("img"); node.src = item.url; node.alt = item.name; node.className = "rich-image";
      } else if (item.content_type.startsWith("video/")) {
        node = document.createElement("video"); node.src = item.url; node.controls = true; node.preload = "metadata"; node.className = "rich-video";
      } else {
        node = document.createElement("a"); node.href = item.url; node.textContent = item.name; node.target = "_blank"; node.rel = "noopener";
      }
      insertNode(node);
      status.textContent = `${item.name} 已插入正文。`;
    } catch (error) { status.textContent = error.message; }
  }

  document.getElementById("preview-button")?.addEventListener("click", async () => {
    syncBody();
    const data = new FormData(); data.set("body", body.value); data.set("body_format", "html");
    data.set("csrf", document.querySelector('input[name="csrf"]').value);
    const button = document.getElementById("preview-button");
    button.disabled = true; button.textContent = "正在生成预览…";
    try {
      const response = await fetch("/markdown/preview", {method: "POST", body: data, credentials: "same-origin"});
      if (!response.ok) throw new Error("预览失败，请重试。");
      document.getElementById("markdown-preview").innerHTML = await response.text();
      document.getElementById("markdown-preview").hidden = false;
    } catch (error) { alert(error.message); }
    finally { button.disabled = false; button.textContent = "预览内容"; }
  });

  const statusField = document.getElementById("article-status");
  const date = document.getElementById("publish-date");
  if (form && statusField) {
    let choosingScheduleDate = false;
    document.getElementById("save-draft").addEventListener("click", () => { statusField.value = "draft"; form.requestSubmit(); });
    document.getElementById("publish-now").addEventListener("click", () => {
      statusField.value = "published"; date.value = ""; document.getElementById("publish-time").value = ""; form.requestSubmit();
    });
    document.getElementById("schedule-publish").addEventListener("click", () => {
      statusField.value = "scheduled";
      if (date.value) { form.requestSubmit(); return; }
      choosingScheduleDate = true;
      if (typeof date.showPicker === "function") date.showPicker(); else { date.focus(); date.click(); }
    });
    date.addEventListener("change", () => {
      if (!choosingScheduleDate || !date.value) return;
      choosingScheduleDate = false; statusField.value = "scheduled"; form.requestSubmit();
    });
  }
});
