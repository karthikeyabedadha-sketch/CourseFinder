(function () {
  const toggle = document.querySelector(".nav-toggle");
  const nav = document.getElementById("site-nav");
  if (toggle && nav) {
    toggle.addEventListener("click", function () {
      const open = nav.classList.toggle("is-open");
      toggle.setAttribute("aria-expanded", open ? "true" : "false");
    });
  }

  const account = document.querySelector(".account-trigger");
  const panel = document.querySelector(".account-panel");
  if (account && panel) {
    account.addEventListener("click", function () {
      const open = panel.classList.toggle("is-open");
      account.setAttribute("aria-expanded", open ? "true" : "false");
    });
    document.addEventListener("click", function (event) {
      if (!event.target.closest(".account-menu")) {
        panel.classList.remove("is-open");
        account.setAttribute("aria-expanded", "false");
      }
    });
  }
})();

// Confirmation dialogs and loading states for form submissions (strict CSP compatible).
(function () {
  document.addEventListener("submit", function (event) {
    const form = event.target;
    const message = form.getAttribute("data-confirm");
    if (message && !window.confirm(message)) {
      event.preventDefault();
      return;
    }
    const submitter = event.submitter || form.querySelector('button[type="submit"]');
    if (submitter && form.method && form.method.toLowerCase() === "post") {
      submitter.setAttribute("aria-busy", "true");
    }
  });
})();

// Owner Studio AI Content Assistant inside the Course Editor.
(function () {
  const panel = document.getElementById("ai-assistant-panel");
  const form = document.getElementById("course-form");
  if (!panel || !form) return;

  const endpoint = panel.getAttribute("data-endpoint");
  const genBtn = document.getElementById("ai-generate-btn");
  const impBtn = document.getElementById("ai-improve-btn");
  const statusMsg = document.getElementById("ai-status-msg");
  const previewBox = document.getElementById("ai-preview-box");
  const applyAllBtn = document.getElementById("ai-apply-all-btn");
  const applyTagsBtn = document.getElementById("ai-apply-tags-btn");
  let lastSuggestions = null;

  function val(name) {
    const el = form.elements[name];
    return el ? el.value : "";
  }

  function setVal(name, value) {
    const el = form.elements[name];
    if (el && value !== undefined && value !== null) {
      el.value = value;
    }
  }

  async function runAssistant(mode) {
    const csrfInput = form.querySelector('input[name="csrf_token"]');
    const csrfToken = csrfInput ? csrfInput.value : "";
    const rawNotesEl = document.getElementById("ai-raw-notes");
    statusMsg.textContent = "Generating editorial suggestions…";

    const payload = {
      title: val("title"),
      provider_id: val("provider_id"),
      new_provider_name: val("new_provider_name"),
      category_id: val("category_id"),
      difficulty: val("difficulty"),
      short_description: val("short_description"),
      description: val("description"),
      skills_taught: val("skills_taught"),
      learning_outcomes: val("learning_outcomes"),
      prerequisites: val("prerequisites"),
      intended_audience: val("intended_audience"),
      raw_notes: rawNotesEl ? rawNotesEl.value : "",
      mode: mode,
    };

    try {
      const response = await fetch(endpoint, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": csrfToken,
        },
        body: JSON.stringify(payload),
      });
      const data = await response.json();
      if (!response.ok) {
        statusMsg.textContent = data.error || "Could not generate suggestions.";
        return;
      }
      lastSuggestions = data;
      document.getElementById("ai-prev-short").textContent = data.short_description || "";
      document.getElementById("ai-prev-desc").textContent = data.description || "";
      document.getElementById("ai-prev-skills").textContent = (data.skills_taught || "").replace(/\n/g, " · ");
      document.getElementById("ai-prev-outcomes").textContent = (data.learning_outcomes || "").replace(/\n/g, " · ");
      document.getElementById("ai-prev-keywords").textContent = data.keywords || "";
      document.getElementById("ai-prev-category").textContent = data.category_suggestion || "No suggestion";
      previewBox.hidden = false;
      statusMsg.textContent = data.notice || "Suggestions ready.";
    } catch (_err) {
      statusMsg.textContent = "Network error while contacting AI Content Assistant.";
    }
  }

  if (genBtn) genBtn.addEventListener("click", function () { runAssistant("all"); });
  if (impBtn) impBtn.addEventListener("click", function () { runAssistant("improve"); });

  if (applyAllBtn) {
    applyAllBtn.addEventListener("click", function () {
      if (!lastSuggestions) return;
      setVal("short_description", lastSuggestions.short_description);
      setVal("description", lastSuggestions.description);
      setVal("skills_taught", lastSuggestions.skills_taught);
      setVal("learning_outcomes", lastSuggestions.learning_outcomes);
      setVal("keywords", lastSuggestions.keywords);
      if (!val("prerequisites")) setVal("prerequisites", lastSuggestions.prerequisites);
      if (!val("intended_audience")) setVal("intended_audience", lastSuggestions.intended_audience);
      if (!val("editorial_assessment")) setVal("editorial_assessment", lastSuggestions.editorial_assessment);
      if (!val("editorial_who_should_take")) setVal("editorial_who_should_take", lastSuggestions.editorial_who_should_take);
      if (!val("editorial_who_should_avoid")) setVal("editorial_who_should_avoid", lastSuggestions.editorial_who_should_avoid);
      statusMsg.textContent = "Applied suggestions to editor fields. Review and click Save below.";
    });
  }

  if (applyTagsBtn) {
    applyTagsBtn.addEventListener("click", function () {
      if (!lastSuggestions) return;
      setVal("skills_taught", lastSuggestions.skills_taught);
      setVal("keywords", lastSuggestions.keywords);
      statusMsg.textContent = "Applied skills and search tags to editor fields.";
    });
  }
})();
