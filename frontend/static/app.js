async function createRubric() {
  const name = document.getElementById("newRubricName").value.trim();
  const target_use_case = document.getElementById("newRubricUse").value.trim();
  const description = document.getElementById("newRubricDesc").value.trim();

  if (!name) {
    alert("name is required");
    return;
  }

  const resp = await fetch("/api/rubrics", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({name, target_use_case, description})
  });

  if (!resp.ok) {
    alert("Create rubric failed");
    return;
  }
  location.reload();
}

async function setActive(versionId) {
  const ok = confirm("Set version này làm Active?");
  if (!ok) return;

  const resp = await fetch(`/api/rubric_versions/${versionId}/set_active`, {method: "POST"});
  if (!resp.ok) {
    alert("Set active failed");
    return;
  }
  location.reload();
}

async function openEditor(rubricId, versionId) {
  const box = document.getElementById(`editor-${rubricId}`);
  const ta = document.getElementById(`editorText-${rubricId}`);
  const warn = document.getElementById(`warn-${rubricId}`);
  warn.classList.add("hidden");
  warn.textContent = "";

  const resp = await fetch(`/api/rubric_versions/${versionId}/export`);
  if (!resp.ok) {
    alert("Load rubric JSON failed");
    return;
  }
  const cfg = await resp.json();
  ta.value = JSON.stringify(cfg, null, 2);
  box.classList.remove("hidden");
  box.dataset.versionId = String(versionId);
}

function closeEditor(rubricId) {
  const box = document.getElementById(`editor-${rubricId}`);
  box.classList.add("hidden");
}

async function saveNewVersion(rubricId) {
  const box = document.getElementById(`editor-${rubricId}`);
  const ta = document.getElementById(`editorText-${rubricId}`);
  const makeActive = document.getElementById(`makeActive-${rubricId}`).checked;
  const warn = document.getElementById(`warn-${rubricId}`);

  let cfg;
  try {
    cfg = JSON.parse(ta.value);
  } catch (e) {
    warn.textContent = "JSON không hợp lệ: " + e;
    warn.classList.remove("hidden");
    return;
  }

  // soft warning
  const maxTotal = Number(cfg.max_total);
  if (!Number.isNaN(maxTotal) && maxTotal > 9.0) {
    warn.textContent = "Cảnh báo: max_total > 9.0 (UI yêu cầu cảnh báo). Bạn vẫn có thể lưu.";
    warn.classList.remove("hidden");
  } else {
    warn.classList.add("hidden");
  }

  const resp = await fetch(`/api/rubrics/${rubricId}/versions`, {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({config: cfg, make_active: makeActive})
  });

  if (!resp.ok) {
    const t = await resp.text();
    warn.textContent = "Save failed: " + t;
    warn.classList.remove("hidden");
    return;
  }

  location.reload();
}
