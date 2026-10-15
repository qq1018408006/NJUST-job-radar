(() => {
  const form = document.querySelector("[data-filter-form]");
  if (!form) return;
  form.querySelectorAll("select[name='sort'], select[name='mode'], select[name='source']").forEach((field) => {
    field.addEventListener("change", () => form.requestSubmit());
  });
})();

(() => {
  document.querySelectorAll("[data-read-form]").forEach((form) => {
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const button = event.submitter || form.querySelector("button[type='submit'], button:not([type])");
      const body = new FormData(form);
      body.set("action", button?.value || "read");
      if (button) button.disabled = true;

      try {
        const response = await fetch(form.action, {
          method: "POST",
          body,
          headers: {
            "Accept": "application/json",
            "X-Requested-With": "XMLHttpRequest",
          },
        });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const result = await response.json();
        if (!result.ok) throw new Error("状态未更新");

        const row = form.closest(".job-row");
        row?.classList.remove("job-row--unread");
        const pill = row?.querySelector(".status-pill");
        if (pill) {
          pill.textContent = "已读";
          pill.classList.remove("status-pill--new", "status-pill--updated");
          pill.classList.add("status-pill--read");
        }
        const confirmation = document.createElement("span");
        confirmation.className = "read-confirmation";
        confirmation.textContent = "已标记";
        form.replaceWith(confirmation);
      } catch (error) {
        if (button) button.disabled = false;
        form.submit();
      }
    });
  });
})();

(() => {
  const form = document.querySelector("[data-bulk-read-form]");
  if (!form) return;
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const count = Number(form.dataset.count || 0);
    if (!count) return;
    const firstConfirmed = window.confirm(
      `将昨天及以前的 ${count} 条未读信息批量标记为已读。是否继续？`
    );
    if (!firstConfirmed) return;
    const secondConfirmed = window.confirm(
      "请再次确认：这些条目将从未读和内容更新视图中移除。确定执行吗？"
    );
    if (!secondConfirmed) return;
    form.querySelector("[name='confirmed_twice']").value = "yes";
    form.submit();
  });
})();
