document.addEventListener("DOMContentLoaded", () => {
  const button = document.getElementById("preview-button");
  if (!button) return;
  button.addEventListener("click", async () => {
    const body = document.getElementById("body").value;
    const csrf = document.querySelector('input[name="csrf"]').value;
    const data = new FormData(); data.set("body", body); data.set("csrf", csrf);
    button.disabled = true; button.textContent = "正在预览…";
    try {
      const response = await fetch("/markdown/preview", {method: "POST", body: data, credentials: "same-origin"});
      if (!response.ok) throw new Error("预览失败，请刷新页面后重试。");
      document.getElementById("markdown-preview").innerHTML = await response.text();
      document.getElementById("markdown-preview").hidden = false;
    } catch (error) { alert(error.message); }
    finally { button.disabled = false; button.textContent = "预览 Markdown"; }
  });
});
