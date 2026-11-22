/* Theme switch and non-sending chat preview. No network or Telegram calls. */
(() => {
  const isSpace = document.documentElement.dataset.siteTemplate === "space";
  const storageKey = isSpace ? "telegram-broadcaster-space-color" : "telegram-broadcaster-theme";
  let requested = null;
  try { requested = window.localStorage.getItem(storageKey); } catch (_) {}
  const prefersDark = !!window.matchMedia?.("(prefers-color-scheme: dark)")?.matches;
  const initial = (requested === "dark" || requested === "light")
    ? requested : (isSpace || prefersDark ? "dark" : "light");
  document.documentElement.dataset.theme = initial;

  document.addEventListener("DOMContentLoaded", () => {
    const toggles = [...document.querySelectorAll("[data-theme-toggle]")];
    const paint = () => {
      const isDark = document.documentElement.dataset.theme === "dark";
      for (const button of toggles) {
        button.textContent = isDark ? "☀" : "☾";
        button.title = isSpace
          ? (isDark ? "Switch to Cosmic day appearance" : "Switch to Cosmic night appearance")
          : (isDark ? "Switch to Telegram day theme" : "Switch to Telegram night theme");
        button.setAttribute("aria-label", button.title);
        button.setAttribute("aria-pressed", isDark ? "true" : "false");
      }
    };
    paint();
    for (const button of toggles) {
      button.addEventListener("click", () => {
        const current = document.documentElement.dataset.theme;
        const next = current === "dark" ? "light" : "dark";
        document.documentElement.dataset.theme = next;
        try { window.localStorage.setItem(storageKey, next); } catch (_) {}
        paint();
      });
    }

    const textarea = document.getElementById("compose-message");
    const preview = document.getElementById("tg-message-preview-text");
    if (textarea && preview) {
      const syncPreview = () => {
        const value = textarea.value.trim();
        preview.textContent = value || "Your message will appear here as you type…";
        preview.classList.toggle("tg-message-placeholder", !value);
      };
      textarea.addEventListener("input", syncPreview);
      syncPreview();
    }
    const senderSelect = document.getElementById("account_id");
    const senderLabel = document.getElementById("tg-preview-sender");
    if (senderSelect && senderLabel) {
      const syncSender = () => {
        const selected = senderSelect.options[senderSelect.selectedIndex];
        senderLabel.textContent = selected && selected.value ? selected.textContent.split(" - ")[0] : "Private message preview";
      };
      senderSelect.addEventListener("change", syncSender);
      syncSender();
    }
  });
})();
