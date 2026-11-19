"use strict";

const $ = (id) => document.getElementById(id);
const state = { config: null, source: null, selected: new Set(), styleId: null, run: null, busy: false, cards: new Map(), connected: false, historyVersion: "" };
const labels = { waiting: "Waiting", preparing: "Uploading / preparing", submitted: "Submitted", queued: "Queued", running: "Generating", saving: "Saving result", saved: "Ready to review", failed: "Failed", expired: "Expired", canceled: "Canceled", regenerating: "Regenerating…", "retry-queued": "Queued", "retry-error": "Retry interrupted · previous image kept", "retry-paused": "Retry paused · previous image kept" };
const usd = (value) => `$${Number(value || 0).toFixed(3)}`;
const regenerationRequests = new Map();
const regenerationSubmitting = new Set();
const captionDrafts = new Map();
const captionSubmitting = new Map();
const captionRequests = new Map();
let captionPosts = Promise.resolve();
let comparisonFile = null;
let drawingEditor = null;

async function api(url, options = {}) {
  const response = await fetch(url, { ...options, headers: { "X-Pose-Studio": "1", ...options.headers } });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || `Request failed (${response.status}).`);
  return data;
}
const post = (url, data = {}) => api(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) });
function notice(message = "") { $("notice").textContent = message; $("notice").hidden = !message; }
function connection(online) {
  state.connected = online;
  $("connection-label").textContent = online ? (state.config?.key_ready ? "API key configured" : "API key missing") : "Server disconnected";
  $("connection-dot").classList.toggle("ready", online && state.config?.key_ready);
  updateBudget();
}
function updateBudget() {
  const count = state.selected.size;
  const cap = Number($("per-image").value), total = Number($("total-cap").value);
  const ceiling = count * cap;
  $("ceiling").textContent = usd(ceiling);
  $("budget-note").textContent = total < ceiling ? `The ${usd(total)} total cap may stop this run before all ${count} images finish.` : "Quotes are checked against both caps before each submission.";
  $("generate").disabled = state.busy || !!state.run || !state.connected || !state.config?.key_ready || !count || !(cap > 0 && total >= cap);
  for (const id of ["new-run", "history", "reuse"]) $(id).disabled = state.busy;
  for (const item of state.cards.values()) {
    configureRegenerate(item.regenerate, item.file);
    configureCaption(item.caption, item.file);
  }
  configureRegenerate($("compare-regenerate"), comparisonFile);
  if ($("comparison").open) refreshDrawingControls();
  if (!state.run) $("gallery-count").textContent = state.source ? `${count} of ${state.source.files.length} selected` : "";
}
function promptCount() { $("prompt-count").textContent = `${$("prompt").value.length} / 5000`; }
function applyProfile(id) {
  const profile = state.config.profiles.find((p) => p.id === id);
  if (!profile) return;
  $("prompt").value = profile.prompt;
  $("image-order").value = profile.image_order;
  promptCount();
}
function lockForm(locked) {
  for (const id of ["folder-button", "staged-button", "style-button", "clear-style", "style-uri", "profile", "prompt", "image-order", "resolution", "run-name", "per-image", "total-cap"]) $(id).disabled = locked;
  $("style-uri").disabled = locked || !!state.styleId;
  updateBudget();
}
function resetCanvas() { state.cards.clear(); $("gallery").replaceChildren(); }
function newRun() {
  state.run = null;
  $("grid-gallery-picker").hidden = true;
  $("tag-filters").replaceChildren();
  activeFilters.clear();
  $("history").value = "";
  $("run-panel").hidden = true;
  $("regeneration-note").hidden = true;
  $("workspace-title").textContent = "Build your pose library.";
  $("workspace-subtitle").textContent = "Choose a folder, refine the prompt, and watch each reference take shape.";
  lockForm(false); notice(); renderSources();
}
function sourceLoaded(source) {
  state.source = source;
  state.selected = new Set(source.files.map((file) => file.name));
  $("folder-title").textContent = source.label;
  $("folder-subtitle").textContent = `${source.files.length} images ready · choose another folder`;
  $("run-name").value = source.label;
  newRun();
}
function attachWipe(surface, onChange = () => {}) {
  const original = document.createElement("img");
  original.className = "wipe-source";
  original.alt = "";
  original.draggable = false;
  original.setAttribute("aria-hidden", "true");
  const divider = document.createElement("span");
  divider.className = "wipe-divider";
  divider.setAttribute("aria-hidden", "true");
  surface.append(original, divider);
  let enabled = false;
  let sourceUrl = null;
  let position = 0;

  function reveal(value) {
    position = enabled ? Math.max(0, Math.min(100, value)) : 0;
    surface.style.setProperty("--wipe-position", `${position}%`);
    surface.classList.toggle("wipe-active", enabled && position > 0);
    onChange(position);
  }
  function move(event) {
    if (!enabled || event.pointerType === "touch") return;
    const bounds = surface.getBoundingClientRect();
    reveal(100 * (event.clientX - bounds.left) / Math.max(1, bounds.width));
  }
  original.addEventListener("load", () => {
    enabled = !!sourceUrl && original.naturalWidth > 0;
    surface.classList.toggle("has-wipe", enabled);
    reveal(position);
  });
  original.addEventListener("error", () => {
    enabled = false;
    surface.classList.remove("has-wipe");
    reveal(0);
  });
  surface.addEventListener("pointerenter", move);
  surface.addEventListener("pointermove", move);
  surface.addEventListener("pointerleave", () => reveal(0));
  return {
    reveal,
    setSource(url) {
      if (sourceUrl === url) return; // Polling must not reset an active wipe.
      sourceUrl = url;
      enabled = false;
      surface.classList.remove("has-wipe");
      reveal(0);
      if (url) original.src = url;
      else original.removeAttribute("src");
    },
  };
}
const comparisonWipe = attachWipe($("compare-surface"), (value) => {
  $("compare-reveal").value = value;
});
$("compare-reveal").addEventListener("input", () => comparisonWipe.reveal(Number($("compare-reveal").value)));

function openComparison(file) {
  comparisonFile = file;
  loadDrawingEditor(file);
  updateComparisonImage(file);
  $("comparison").showModal();
}

function updateComparisonImage(file) {
  configureDepthLink(file);
  configureRegenerate($("compare-regenerate"), file);
  configureDelete($("compare-delete"), file);
  $("compare-title").textContent = file.metadata?.label || file.name;
  if (file.source_url || file.url) $("compare-source").src = file.source_url || file.url;
  else $("compare-source").removeAttribute("src");
  $("compare-source").hidden = !!file.output_url;
  $("compare-surface").hidden = !file.output_url;
  $("compare-controls").hidden = !file.output_url || !file.source_url;
  $("compare-hint").textContent = file.imported ? "Imported mannequin drawing" : file.output_url ? "Move across the image to reveal the original. Original on the left, drawing on the right." : "Source photograph";
  comparisonWipe.setSource(file.output_url ? file.source_url : null);
  comparisonWipe.reveal(0);
  if (file.output_url) {
    $("compare-output").src = file.output_url;
  } else $("compare-output").removeAttribute("src");
  $("download").hidden = !file.output_url;
  if (file.output_url) {
    $("download").href = file.output_url;
    $("download").download = downloadName(file);
  } else $("download").removeAttribute("href");
}

function configureDepthLink(file) {
  const link = $("download-depth");
  link.hidden = !file.has_depth;
  if (file.has_depth) {
    const url = new URL(file.output_url, location.href);
    url.searchParams.set("depth", "1");
    link.href = url.href;
    link.download = downloadName(file).replace(/\.png$/, "-depth.png");
  } else link.removeAttribute("href");
}

function configureCaption(button, file) {
  button.hidden = !file?.output_url || !state.config?.drawing_editor;
  if (button.hidden) return;
  const run = state.run;
  const progress = run?.metadata_status || {};
  const submitting = captionSubmitting.get(file.run_id)?.has(file.name);
  const position = (run?.caption_queue || []).indexOf(file.name) + 1;
  const pending = run?.caption_active === file.name || (progress.running && progress.target === file.name);
  button.textContent = pending ? "Captioning…" : position ? `Caption queued · #${position}` : submitting ? "Queuing caption…" : "Caption";
  button.disabled = !state.connected || !state.config.metadata_providers[$("metadata-provider").value]
    || !!run?.generation_busy || !!(progress.running && (!state.config.caption_queue || !progress.target))
    || !!(submitting || position || pending) || !$("caption-prompt").value.trim();
  button.title = run?.caption_errors?.[file.name] || "Queue one paid caption request. Manually selected tag rows are preserved.";
}

async function captionFile(file) {
  const id = file.run_id;
  const submitting = captionSubmitting.get(id) || new Set();
  if (submitting.has(file.name)) return;
  submitting.add(file.name);
  captionSubmitting.set(id, submitting);
  const key = JSON.stringify([id, file.name]);
  if (!captionRequests.has(key)) captionRequests.set(key, crypto.randomUUID().replaceAll("-", ""));
  const body = { name: file.name, request_id: captionRequests.get(key), provider: $("metadata-provider").value, caption_prompt: $("caption-prompt").value };
  renderCuration(state.run);
  updateBudget();
  try {
    const queued = captionPosts.then(() => curate(id, "caption", body));
    captionPosts = queued.catch(() => {});
    await queued;
    captionRequests.delete(key);
  } finally {
    submitting.delete(file.name);
    if (!submitting.size) captionSubmitting.delete(id);
    if (state.run?.id === id) renderCuration(state.run);
    updateBudget();
  }
}

function loadDrawingEditor(file) {
  const enabled = !!(file.output_url && state.config?.drawing_editor);
  $("drawing-editor").hidden = !enabled;
  $("comparison").classList.toggle("has-editor", enabled);
  drawingEditor = null;
  $("drawing-tags").replaceChildren();
  if (enabled) {
    const label = file.metadata?.label || "";
    $("drawing-label").value = label;
    const groups = Object.entries(state.run.tag_groups);
    const values = Object.fromEntries(groups.map(([row]) => [row, [...(file.metadata?.tag_groups?.[row] || [])]]));
    drawingEditor = { version: file.edit_version, label, values, initialTags: JSON.stringify(values), dirty: false, saving: false, message: "" };
    for (const [row, tags] of groups) {
      if (!tags.length) continue;
      const field = document.createElement("fieldset"), legend = document.createElement("legend");
      legend.textContent = row; field.append(legend);
      for (const tag of tags) {
        const button = document.createElement("button");
        button.type = "button"; button.className = "tag-filter"; button.textContent = tag;
        button.setAttribute("aria-pressed", String(values[row].includes(tag)));
        button.addEventListener("click", () => {
          const selected = new Set(values[row]);
          if (selected.has(tag)) selected.delete(tag); else selected.add(tag);
          values[row] = tags.filter((value) => selected.has(value));
          button.setAttribute("aria-pressed", String(selected.has(tag)));
          drawingChanged();
        });
        field.append(button);
      }
      $("drawing-tags").append(field);
    }
  }
  refreshDrawingControls();
}

function drawingChanged() {
  drawingEditor.dirty = $("drawing-label").value !== drawingEditor.label
    || JSON.stringify(drawingEditor.values) !== drawingEditor.initialTags;
  drawingEditor.message = "";
  refreshDrawingControls();
}

function refreshDrawingControls() {
  configureCaption($("compare-caption"), comparisonFile);
  if (!drawingEditor) return;
  const progress = state.run.metadata_status || {};
  const stale = drawingEditor.version !== comparisonFile.edit_version;
  const locked = !!(drawingEditor.saving || state.run.generation_busy || progress.running || captionSubmitting.has(state.run.id));
  $("drawing-label").disabled = locked;
  for (const field of $("drawing-tags").children) field.disabled = locked;
  $("save-drawing").disabled = !state.connected || locked || stale || !drawingEditor.dirty;
  $("reset-drawing").hidden = !drawingEditor.dirty && !stale;
  $("reset-drawing").disabled = drawingEditor.saving;
  $("compare-caption").disabled ||= drawingEditor.dirty || drawingEditor.saving;
  const position = (state.run.caption_queue || []).indexOf(comparisonFile.name) + 1;
  const targetStatus = state.run.caption_errors?.[comparisonFile.name] || (position ? `Caption queued · #${position}` : progress.target === comparisonFile.name
    ? progress.error || (progress.running ? "Captioning this drawing…" : progress.done ? "Caption request complete." : "") : "");
  $("drawing-status").textContent = stale && !drawingEditor.saving ? "Saved values changed. Reload them before saving."
    : drawingEditor.message || (drawingEditor.dirty ? "Unsaved changes" : targetStatus);
}

$("drawing-label").addEventListener("input", drawingChanged);
$("reset-drawing").addEventListener("click", () => loadDrawingEditor(comparisonFile));
$("drawing-editor").addEventListener("submit", async (event) => {
  event.preventDefault();
  if ($("save-drawing").disabled) return;
  const editor = drawingEditor, file = comparisonFile;
  editor.saving = true; refreshDrawingControls();
  try {
    await curate(file.run_id, "edit-drawing", { name: file.name, version: editor.version,
      label: $("drawing-label").value, tag_groups: editor.values });
    if (drawingEditor === editor) {
      loadDrawingEditor(comparisonFile);
      drawingEditor.message = "Changes saved.";
    }
  } catch (error) { editor.message = error.message; }
  finally { editor.saving = false; if ($("comparison").open) refreshDrawingControls(); }
});

function detailAction(action) {
  const file = comparisonFile;
  if (drawingEditor) drawingEditor.message = "";
  action(file).catch((error) => {
    if ($("comparison").open && drawingEditor && comparisonFile.name === file.name && comparisonFile.run_id === file.run_id) {
      drawingEditor.message = error.message;
      refreshDrawingControls();
    } else notice(error.message);
  });
}
$("compare-caption").addEventListener("click", () => detailAction(captionFile));
function configureRegenerate(button, file) {
  if (!button) return;
  button.hidden = !file?.output_url || !!file.imported;
  const submitting = file && regenerationSubmitting.has(`${file.run_id}:${file.name}`);
  button.textContent = submitting ? "Adding to queue…" : file?.queue_position ? `Queued · #${file.queue_position}`
    : file?.retry_pending ? (file.state === "regenerating" ? "Regenerating…" : "Resume retry") : `Regenerate · max ${usd(file?.per_image)}`;
  button.disabled = state.busy || !state.connected || !!state.run?.metadata_status?.running || !state.config?.regeneration_queue || !!submitting
    || !!file?.queue_position || file?.state === "regenerating";
  button.title = !state.config?.regeneration_queue ? "Restart Pose Studio to enable queued regeneration."
    : file?.retry_pending ? "Recover the existing attempt; do not submit a replacement job."
    : "Queue one paid attempt. It replaces this image when ready; the previous version is backed up.";
}
async function regenerateFile(file) {
  if (!file?.output_url || state.busy) return;
  const key = `${file.run_id}:${file.name}`;
  if (regenerationSubmitting.has(key) || file.queue_position || file.state === "regenerating") return;
  // Keep this identifier after an interrupted response, so another click can
  // recover the same retry instead of paying for an accidental duplicate.
  if (!regenerationRequests.has(key)) regenerationRequests.set(key, crypto.randomUUID().replaceAll("-", ""));
  regenerationSubmitting.add(key); updateBudget();
  try {
    const run = await post(`/api/runs/${file.run_id}/regenerate`, {
      name: file.name, request_id: regenerationRequests.get(key),
    });
    regenerationRequests.delete(key);
    if (state.run?.id === file.run_id) renderRun(run);
    await refreshHistory();
  } finally { regenerationSubmitting.delete(key); updateBudget(); }
}
$("compare-regenerate").addEventListener("click", () => guarded(() => regenerateFile(comparisonFile)));
$("compare-delete").addEventListener("click", () => detailAction((file) => curate(file.run_id, "delete", { name: file.name })));

function configureDelete(button, file) {
  button.hidden = !file?.output_url || !state.config?.metadata_providers;
  button.disabled = !!state.run?.generation_busy || !!state.run?.metadata_status?.running;
}

function downloadName(file) {
  return file.metadata?.id ? `${file.metadata.id}.png` : `${file.name.replace(/\.[^.]+$/, "")}-pose.png`;
}

async function curate(runId, action, body) {
  const run = await post(`/api/runs/${runId}/${action}`, body);
  if (state.run?.id === runId) renderRun(run);
}

function renderCuration(run) {
  $("curation").hidden = !state.config?.metadata_providers;
  const progress = run.metadata_status || {};
  const prompt = captionDrafts.get(run.id) ?? run.caption_prompt ?? "";
  if ($("caption-prompt").value !== prompt) $("caption-prompt").value = prompt;
  const changed = prompt.trim() !== run.caption_prompt;
  if (prompt === run.caption_prompt) captionDrafts.delete(run.id);
  $("caption-state").textContent = changed ? "Unsaved changes" : "";
  $("caption-prompt").disabled = !!progress.running;
  $("save-caption").disabled = !changed || !prompt.trim() || !!progress.running;
  $("default-caption").disabled = prompt === state.config?.default_caption_prompt || !!progress.running;
  if (progress.running) $("metadata-provider").value = progress.provider;
  const provider = $("metadata-provider").value;
  const model = state.config?.metadata_models?.[provider];
  const saved = run.files.filter((file) => file.output_url);
  const ready = changed ? 0 : saved.filter((file) => file.metadata?.model === model
    && file.metadata?.caption_prompt_sha256 === run.caption_prompt_sha256
    && file.metadata?.tag_version === run.tag_version).length;
  const remaining = saved.length - ready;
  const reviews = saved.filter((file) => file.metadata?.review).length;
  const configured = state.config?.metadata_providers?.[provider];
  const locked = run.generation_busy || progress.running;
  $("restore").hidden = !run.last_deleted;
  $("restore").disabled = !!locked;
  $("restore").textContent = `Undo last delete (${run.deleted_count || 0})`;
  $("restore").title = run.last_deleted || "";
  $("metadata-provider").disabled = !!progress.running;
  $("metadata-one").disabled = $("metadata-all").disabled = !!locked || captionSubmitting.has(run.id) || !configured || !remaining || !prompt.trim();
  $("metadata-all").textContent = `Tag remaining (${remaining})`;
  const parts = [`${ready} of ${saved.length} processed with this prompt and model`];
  if (reviews) parts.push(`${reviews} need review`);
  if (progress.running) parts.push(`${progress.done} of ${progress.total} requests finished`);
  if (progress.last_label) parts.push(`Last result: ${progress.last_label}`);
  if (progress.error) parts.push(progress.error);
  if (run.caption_queue?.length) parts.push(`${run.caption_queue.length} captions queued`);
  const failed = Object.keys(run.caption_errors || {}).length;
  if (failed) parts.push(`${failed} caption attempts need attention`);
  $("resume-captions").hidden = !run.captions_paused;
  if (run.generation_busy) parts.push("Available when this folder's generation queue finishes.");
  if (!configured) parts.push(`Set ${provider === "xai" ? "GROK_API_KEY" : "SPICY_API_KEY"} in .env.`);
  $("metadata-status").textContent = parts.join(" · ");
  const depth = run.depth_status || {}, maps = saved.filter((file) => file.has_depth).length;
  $("depth-one").disabled = $("depth-all").disabled = !!(locked || depth.running || maps === saved.length);
  $("depth-status").textContent = [`${maps} of ${saved.length} depth maps saved`, depth.running && `Generating · ${depth.done}/${depth.total}`, depth.error].filter(Boolean).join(" · ");
}
for (const [id, limit] of [["depth-one", 1], ["depth-all", null]]) {
  $(id).addEventListener("click", () => guarded(() => curate(state.run.id, "depth", {limit})));
}
$("caption-prompt").addEventListener("input", () => {
  captionDrafts.set(state.run.id, $("caption-prompt").value);
  renderCuration(state.run);
  updateBudget();
});
$("default-caption").addEventListener("click", () => {
  captionDrafts.set(state.run.id, state.config.default_caption_prompt);
  renderCuration(state.run);
  updateBudget();
});
$("save-caption").addEventListener("click", () => guarded(() =>
  curate(state.run.id, "caption-prompt", { caption_prompt: $("caption-prompt").value })));
$("metadata-provider").addEventListener("change", () => { if (state.run) { renderCuration(state.run); updateBudget(); } });
for (const [id, limit] of [["metadata-one", 1], ["metadata-all", null]]) {
  $(id).addEventListener("click", () => guarded(async () => {
    $("metadata-one").disabled = $("metadata-all").disabled = true;
    try { await curate(state.run.id, "metadata", { provider: $("metadata-provider").value, limit, caption_prompt: $("caption-prompt").value }); }
    finally { if (state.run) renderCuration(state.run); }
  }));
}
$("restore").addEventListener("click", () => guarded(() => curate(state.run.id, "restore", { name: state.run.last_deleted })));
$("resume-captions").addEventListener("click", () => guarded(() => curate(state.run.id, "resume-captions", {})));

function card(file, selectable) {
  const article = document.createElement("article"); article.className = "card";
  const imageButton = document.createElement("button"); imageButton.className = "card-image"; imageButton.type = "button";
  imageButton.setAttribute("aria-label", `Inspect ${file.name}`);
  const img = document.createElement("img"); img.loading = "lazy"; img.alt = file.name;
  imageButton.append(img); article.append(imageButton);
  const wipe = attachWipe(imageButton);
  const info = document.createElement("div"); info.className = "card-info";
  const name = document.createElement("span"); name.className = "card-name"; name.textContent = file.name; name.title = file.name;
  const status = document.createElement("span"); status.className = "card-status";
  info.append(name, status); article.append(info);
  const regenerate = document.createElement("button");
  regenerate.className = "secondary card-regenerate"; regenerate.type = "button";
  const metadata = document.createElement("small"); metadata.className = "card-metadata";
  const actions = document.createElement("div"); actions.className = "card-curation";
  const caption = document.createElement("button"); caption.className = "text-button"; caption.type = "button"; caption.textContent = "Caption";
  const remove = document.createElement("button");
  remove.className = "text-button card-delete"; remove.type = "button"; remove.textContent = "Delete";
  actions.append(caption, remove);
  info.append(metadata, regenerate, actions);
  const item = { article, img, name, metadata, status, file, wipe, regenerate, remove, caption };
  caption.addEventListener("click", () => guarded(() => captionFile(item.file)));
  remove.addEventListener("click", () => guarded(() => curate(item.file.run_id, "delete", { name: item.file.name })));
  regenerate.addEventListener("click", () => guarded(() => regenerateFile(item.file)));
  imageButton.addEventListener("click", () => openComparison(item.file));
  if (selectable) {
    const label = document.createElement("label"); label.className = "card-check";
    const checkbox = document.createElement("input"); checkbox.type = "checkbox"; checkbox.checked = state.selected.has(file.name);
    checkbox.setAttribute("aria-label", `Select ${file.name}`);
    checkbox.addEventListener("change", () => {
      if (checkbox.checked) state.selected.add(file.name); else state.selected.delete(file.name);
      article.classList.toggle("selected", checkbox.checked); updateBudget();
    });
    label.append(checkbox); article.append(label); article.classList.toggle("selected", checkbox.checked);
    item.checkbox = checkbox;
  }
  $("gallery").append(article); state.cards.set(file.name, item); return item;
}
function updateCard(item, file, selectable = false) {
  item.file = file;
  item.name.textContent = file.metadata?.label || file.name;
  item.metadata.textContent = file.metadata?.review ? "Needs pose review"
    : [file.metadata?.id, ...(file.metadata?.tags || [])].filter(Boolean).join(" · ");
  item.metadata.hidden = !item.metadata.textContent;
  configureDelete(item.remove, file);
  const url = file.output_url || file.source_url || file.url;
  if (item.img.getAttribute("src") !== url) item.img.src = url;
  item.wipe.setSource(file.output_url ? file.source_url : null);
  item.article.dataset.state = file.state || "source";
  item.status.textContent = selectable ? "Source image" : file.queue_position
    ? `${file.queue_paused ? "Queue paused" : "Queued"} · #${file.queue_position}` : (labels[file.state] || file.state);
  item.status.title = file.retry_error || "";
  const captionError = state.run?.caption_errors?.[file.name];
  if (captionError) {
    item.status.textContent += " · Caption failed";
    item.status.title = captionError;
  }
  configureRegenerate(item.regenerate, file);
}
function renderSources() {
  resetCanvas();
  const files = state.source?.files || [];
  $("empty-state").hidden = !!files.length;
  $("selection-tools").hidden = !files.length;
  $("gallery-title").textContent = files.length ? "Source images" : "Your canvas is ready";
  for (const file of files) updateCard(card(file, true), file, true);
  updateBudget();
}
function renderRun(run) {
  // Concurrent clicks can return out of order; keep the latest queue snapshot.
  if (state.run?.id === run.id && run.queue_revision < state.run.queue_revision) return;
  const changed = state.run?.id !== run.id;
  state.run = run;
  $("grid-gallery-picker").hidden = false;
  $("grid-gallery").value = run.id;
  if (changed) activeFilters.clear();
  $("folder-actions").hidden = !state.config?.metadata_providers;
  $("add-images").disabled = !!(run.generation_busy || run.metadata_status?.running);
  $("run-panel").hidden = false; $("empty-state").hidden = true; $("selection-tools").hidden = true;
  $("workspace-title").textContent = run.label;
  $("workspace-subtitle").textContent = "Hover and move across a finished image to reveal the original. Click for a larger wipe comparison.";
  $("gallery-title").textContent = "Generation queue";
  $("gallery-count").textContent = `${run.count} images`;
  $("run-state").textContent = run.status;
  $("run-progress-label").textContent = `${run.completed} / ${run.count} saved`;
  $("run-progress").max = run.count; $("run-progress").value = run.completed;
  $("settled").textContent = usd(run.settled); $("reserved").textContent = usd(run.reserved); $("run-cap").textContent = usd(run.total);
  $("destination").textContent = run.destination;
  renderCuration(run);
  $("regeneration-note").hidden = !run.files.some((file) => file.output_url);
  $("regeneration-note").textContent = state.config?.regeneration_queue
    ? "Click Regenerate on several images to queue them. Results replace each image here; previous versions are backed up. Retry charges and caps are included in the totals."
    : "Restart the Pose Studio server, then refresh to enable queued regeneration.";
  $("pause").hidden = !["running", "pausing", "regenerating", "queued"].includes(run.status);
  $("pause").disabled = run.status === "pausing";
  $("resume").hidden = !["paused", "error"].includes(run.status) || !!run.retry_failed;
  $("resume").textContent = run.queue_paused ? "Resume queue" : "Resume run";
  $("run-message").textContent = run.error || ({running: "You can leave this tab open or close it. Keep the server running to finish the queue.", regenerating: "Replacing images one at a time. Keep the server running; you can close this browser tab.", queued: "Waiting for the current work to finish. This queue will continue automatically.", pausing: "The current image will finish before the queue pauses. Accepted jobs cannot be canceled.", paused: "Resume continues saved work in queue order without duplicate submissions.", completed: "All selected images are saved. Review pose fidelity, hands, and unwanted shading."}[run.status] || "");
  const regenerating = run.files.some((file) => file.state === "regenerating");
  $("queue-status").hidden = !run.queued_retries && !regenerating;
  $("queue-status").textContent = `${run.queue_paused ? "Queue paused · " : ""}${regenerating ? "1 regenerating · " : ""}${run.queued_retries || 0} retries queued`;
  if (changed) {
    resetCanvas(); $("history").value = run.id;
    $("prompt").value = run.recipe.prompt; $("image-order").value = run.recipe.image_order || "style-first";
    $("resolution").value = run.recipe.resolution; $("run-name").value = run.label;
    $("per-image").value = run.per_image; $("total-cap").value = run.total;
    $("style-uri").value = run.style_id ? "" : run.recipe.style;
    state.styleId = run.style_id; $("style-preview").hidden = !run.style_id;
    $("clear-style").hidden = !run.style_id || run.style_id === state.config.builtin_style?.id;
    if (run.style_id) { $("style-image").src = `/media/style/${run.style_id}`; $("style-name").textContent = run.style_id === state.config.builtin_style?.id ? state.config.builtin_style.label : "Saved style image"; }
    promptCount(); lockForm(true);
  }
  const visible = new Set(run.files.map((file) => file.name));
  for (const [name, item] of state.cards) {
    if (!visible.has(name)) { item.article.remove(); state.cards.delete(name); }
  }
  for (const [index, file] of run.files.entries()) {
    file.run_id = run.id; file.per_image = run.per_image;
    const item = state.cards.get(file.name) || card(file, false);
    updateCard(item, file);
    const slot = $("gallery").children[index];
    if (slot !== item.article) $("gallery").insertBefore(item.article, slot || null);
  }
  if (comparisonFile?.run_id === run.id && $("comparison").open) {
    const updated = run.files.find((file) => file.name === comparisonFile.name);
    if (updated) {
      const imageChanged = updated.output_url !== comparisonFile.output_url;
      comparisonFile = updated;
      configureDepthLink(updated);
      if (imageChanged) updateComparisonImage(updated);
      if (updated.output_url && !drawingEditor?.dirty && !drawingEditor?.saving && drawingEditor?.version !== updated.edit_version) {
        loadDrawingEditor(updated);
      }
      configureDelete($("compare-delete"), updated);
      $("compare-title").textContent = updated.metadata?.label || updated.name;
      $("download").download = downloadName(updated);
    } else $("comparison").close();
  }
  filterCards(run);
  updateBudget();
}
async function refreshHistory() {
  const runs = await api("/api/runs");
  updateBudget();
  const version = JSON.stringify(runs.map((run) => [run.id, run.label, run.status, run.completed, run.count]));
  if (version === state.historyVersion) return runs;
  state.historyVersion = version;
  $("history").replaceChildren(new Option("New run", ""));
  for (const run of runs) $("history").add(new Option(`${run.label} · ${run.completed}/${run.count} · ${run.status}`, run.id));
  $("history").value = state.run?.id || "";
  $("grid-gallery").replaceChildren(...runs.map((run) => new Option(run.label, run.id)));
  $("grid-gallery").value = state.run?.id || "";
  return runs;
}
async function guarded(action) {
  try { notice(); await action(); }
  catch (error) { notice(error.message); }
}

$("folder-button").addEventListener("click", () => $("folder-input").click());
$("folder-input").addEventListener("change", () => guarded(async () => {
  const files = [...$("folder-input").files].filter((file) => /\.(png|jpe?g|webp)$/i.test(file.name) && !(file.webkitRelativePath || file.name).split("/").some((part) => part.startsWith(".")));
  if (!files.length) throw new Error("The folder contains no PNG, JPEG, or WebP images.");
  if (files.length > 500 || files.some((file) => file.size > 10 * 1024 * 1024)) throw new Error("Choose up to 500 images, each no larger than 10 MiB.");
  const folder = files[0].webkitRelativePath.split("/")[0] || "Uploaded folder";
  state.busy = true; lockForm(true); $("upload-progress").hidden = false; $("upload-progress").max = files.length;
  try {
    const source = await post("/api/sources", { label: folder });
    for (let index = 0; index < files.length; index++) {
      const file = files[index];
      const relative = file.webkitRelativePath ? file.webkitRelativePath.split("/").slice(1).join("/") : file.name;
      $("folder-title").textContent = `Copying ${index + 1} of ${files.length}…`;
      await api(`/api/sources/${source.id}/files?name=${encodeURIComponent(relative)}`, { method: "PUT", body: file, headers: { "Content-Type": "application/octet-stream" } });
      $("upload-progress").value = index + 1;
    }
    sourceLoaded(await api(`/api/sources/${source.id}`));
  } finally { state.busy = false; lockForm(!!state.run); $("upload-progress").hidden = true; $("folder-input").value = ""; }
}));
$("staged-button").addEventListener("click", () => guarded(async () => sourceLoaded(await post("/api/sources", { preset: "staged" }))));
$("style-button").addEventListener("click", () => $("style-input").click());
$("style-input").addEventListener("change", () => guarded(async () => {
  const file = $("style-input").files[0]; if (!file) return;
  state.busy = true; updateBudget();
  try {
    const style = await api("/api/style", { method: "POST", body: file });
    state.styleId = style.id; $("style-image").src = style.url; $("style-name").textContent = file.name;
    $("style-preview").hidden = false; $("clear-style").hidden = false; $("style-uri").disabled = true;
  } finally { state.busy = false; $("style-input").value = ""; updateBudget(); }
}));
function useBuiltinStyle() {
  const builtin = state.config?.builtin_style;
  state.styleId = builtin ? builtin.id : null;
  $("style-preview").hidden = !builtin; $("clear-style").hidden = true; $("style-uri").disabled = !!builtin;
  if (builtin) { $("style-image").src = builtin.url; $("style-name").textContent = builtin.label; $("style-uri").value = ""; }
  else $("style-uri").value ||= state.config?.style_uri || "";
  updateBudget();
}
$("clear-style").addEventListener("click", useBuiltinStyle);
$("profile").addEventListener("change", () => applyProfile($("profile").value));
$("prompt").addEventListener("input", promptCount);
for (const id of ["per-image", "total-cap"]) $(id).addEventListener("input", updateBudget);
$("select-all").addEventListener("click", () => { state.selected = new Set(state.source.files.map((file) => file.name)); renderSources(); });
$("select-none").addEventListener("click", () => { state.selected.clear(); renderSources(); });
// "New run" starts from the defaults (built-in style, current prompt preset); "Use settings for
// new run" keeps the open run's settings instead.
$("new-run").addEventListener("click", () => { newRun(); applyProfile($("profile").value); useBuiltinStyle(); });
$("grid-gallery").addEventListener("change", () => {
  $("history").value = $("grid-gallery").value;
  $("history").dispatchEvent(new Event("change"));
});
$("history").addEventListener("change", () => guarded(async () => {
  const id = $("history").value;
  if (!id) newRun();
  else {
    const run = await api(`/api/runs/${id}`);
    if ($("history").value === id) renderRun(run);
  }
}));
$("reuse").addEventListener("click", () => guarded(async () => {
  const run = state.run;
  const source = await api(`/api/sources/${run.source_id}`);
  sourceLoaded(source); $("run-name").value = `${run.label} · next`;
  notice("New run: editing these settings creates new generations with a separate budget.");
}));
$("generate").addEventListener("click", () => guarded(async () => {
  if (state.busy || state.run || !state.selected.size) return;
  state.busy = true; updateBudget();
  try {
    const run = await post("/api/runs", { source_id: state.source.id, files: [...state.selected], style_id: state.styleId,
      style_uri: $("style-uri").value.trim(), prompt: $("prompt").value, image_order: $("image-order").value,
      resolution: $("resolution").value, label: $("run-name").value.trim(), per_image: $("per-image").value, total: $("total-cap").value });
    renderRun(run); await refreshHistory();
  } finally { state.busy = false; updateBudget(); }
}));
for (const action of ["pause", "resume"]) $(action).addEventListener("click", () => guarded(async () => {
  $(action).disabled = true;
  try { renderRun(await post(`/api/runs/${state.run.control_run_id || state.run.id}/${action}`)); await refreshHistory(); }
  finally { $(action).disabled = false; }
}));
$("close-comparison").addEventListener("click", () => $("comparison").close());

async function poll() {
  try {
    const id = state.run?.id;
    if (id) {
      const run = await api(`/api/runs/${id}`);
      if (state.run?.id === id) renderRun(run);
    }
    await refreshHistory(); connection(true);
  } catch { connection(false); }
  setTimeout(poll, 2000);
}
async function boot() {
  try {
    state.config = await api("/api/config");
    if (!state.config.metadata_providers?.xai && state.config.metadata_providers?.spicy) $("metadata-provider").value = "spicy";
    for (const profile of state.config.profiles) $("profile").add(new Option(profile.label, profile.id));
    applyProfile(state.config.profiles[0]?.id);
    useBuiltinStyle();
    $("staged-button").hidden = !state.config.staged_available;
    connection(true);
    if (!state.config.key_ready) notice("Add SPICY_API_KEY or SAI_API_TOKEN to the project .env, then restart Pose Studio. The key stays on the server.");
    const runs = await refreshHistory();
    const current = runs.find((run) => ["running", "pausing", "regenerating", "queued", "paused", "error"].includes(run.status)) || runs[0];
    if (current) renderRun(await api(`/api/runs/${current.id}`));
    poll();
  } catch (error) { connection(false); notice(`Could not connect to Pose Studio: ${error.message} Reload after starting the server.`); }
}

const activeFilters = new Map();
let filterVersion = "";
function filterCards(run) {
  const groups = run.tag_groups || {};
  const visibleGroups = Object.entries(groups).map(([row, tags]) => {
    const present = new Set(run.files.flatMap((file) => file.metadata?.tag_groups?.[row] || []));
    return [row, tags.filter((tag) => present.has(tag))];
  }).filter(([, tags]) => tags.length);
  const available = new Map(visibleGroups);
  for (const [row, selected] of activeFilters) {
    for (const tag of selected) if (!available.get(row)?.includes(tag)) selected.delete(tag);
    if (!selected.size) activeFilters.delete(row);
  }
  const version = JSON.stringify([run.id, visibleGroups, [...activeFilters].map(([row, tags]) => [row, [...tags]])]);
  if (version !== filterVersion || !$("tag-filters").childElementCount) {
    filterVersion = version;
    $("tag-filters").replaceChildren();
    for (const [row, tags] of visibleGroups) {
      const field = document.createElement("fieldset"), legend = document.createElement("legend");
      legend.textContent = row; field.append(legend);
      for (const tag of tags) {
        const button = document.createElement("button"); button.type = "button";
        button.textContent = tag; button.className = "tag-filter";
        button.setAttribute("aria-pressed", String(activeFilters.get(row)?.has(tag) || false));
        button.addEventListener("click", () => {
          const selected = activeFilters.get(row) || new Set();
          if (selected.has(tag)) selected.delete(tag); else selected.add(tag);
          if (selected.size) activeFilters.set(row, selected); else activeFilters.delete(row);
          filterCards(state.run);
        });
        field.append(button);
      }
      $("tag-filters").append(field);
    }
  }
  let visible = 0;
  for (const file of run.files) {
    const matches = [...activeFilters].every(([row, tags]) => (file.metadata?.tag_groups?.[row] || []).some((tag) => tags.has(tag)));
    state.cards.get(file.name).article.hidden = !matches;
    if (matches) visible++;
  }
  $("gallery-count").textContent = activeFilters.size ? `${visible} of ${run.count} images` : `${run.count} images`;
}
for (const button of document.querySelectorAll("[data-close]")) {
  button.addEventListener("click", () => $(button.dataset.close).close());
}
const dialogMessage = (id, text) => { $(id).querySelector('[role="status"], [role="alert"]').textContent = text; };
let editingRun;
$("rename-folder").addEventListener("click", () => {
  editingRun = state.run.id;
  $("folder-label").value = state.run.label; dialogMessage("folder-editor", "");
  $("folder-editor").showModal(); $("folder-label").select();
});
$("rename-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    await curate(editingRun, "rename", { label: $("folder-label").value }); $("folder-editor").close();
    await refreshHistory();
  } catch (error) { dialogMessage("folder-editor", error.message); }
});

let stagedAddition = null;
function updateAdd() {
  const photos = $("add-kind").value === "photo", count = $("add-files").files.length;
  $("add-files").accept = photos ? "image/png,image/jpeg,image/webp" : "image/png";
  $("add-submit").disabled = !count || (photos && !state.config.key_ready);
  $("add-submit").textContent = photos ? `Queue ${count} photos · max ${usd(count * Number(state.run.per_image))}` : `Import ${count} drawings`;
}
$("add-images").addEventListener("click", () => {
  editingRun = state.run.id; stagedAddition = null;
  $("add-files").value = ""; $("add-kind").value = "drawing"; dialogMessage("addition", "");
  updateAdd(); $("addition").showModal();
});
$("add-kind").addEventListener("change", () => { stagedAddition = null; $("add-files").value = ""; updateAdd(); });
$("add-files").addEventListener("change", () => { stagedAddition = null; updateAdd(); });
$("add-submit").addEventListener("click", async () => {
  const files = [...$("add-files").files], kind = $("add-kind").value, id = editingRun;
  for (const el of $("addition").querySelectorAll("button, input, select")) el.disabled = true;
  try {
    if (!stagedAddition) {
      const source = await post("/api/sources", { label: state.run.label });
      for (const [i, file] of files.entries()) {
        dialogMessage("addition", `Copying ${i + 1} of ${files.length}…`);
        await api(`/api/sources/${source.id}/files?name=${encodeURIComponent(file.name)}`, { method: "PUT", body: file });
      }
      stagedAddition = source.id;
    }
    await curate(id, "add", { source_id: stagedAddition, kind });
    $("addition").close(); await refreshHistory();
  } catch (error) { dialogMessage("addition", error.message); }
  finally {
    for (const el of $("addition").querySelectorAll("button, input, select")) el.disabled = false;
    updateAdd();
  }
});

let editingTagVersion;
function tagRow(name = "", tags = []) {
  const row = document.createElement("div"); row.className = "tag-edit-row";
  const key = document.createElement("input"); key.value = name; key.maxLength = 48;
  key.placeholder = "Row name"; key.setAttribute("aria-label", "Tag row name");
  const editor = document.createElement("div"), chips = document.createElement("div");
  const values = document.createElement("input");
  values.placeholder = "Add a tag"; values.setAttribute("aria-label", "New tags");
  function addTag(value) {
    const tag = value.trim().toLowerCase();
    if (!tag || [...chips.children].some((button) => button.dataset.tag === tag)) return;
    const chip = document.createElement("button"); chip.type = "button"; chip.className = "tag-filter";
    chip.dataset.tag = tag; chip.textContent = tag + " ×"; chip.setAttribute("aria-label", `Remove ${tag}`);
    chip.addEventListener("click", () => chip.remove()); chips.append(chip);
  }
  function acceptTags() { values.value.split(",").forEach(addTag); values.value = ""; }
  values.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === ",") { event.preventDefault(); acceptTags(); }
  });
  const add = document.createElement("button"); add.type = "button"; add.className = "text-button"; add.textContent = "Add tag";
  add.addEventListener("click", acceptTags);
  tags.forEach(addTag); editor.append(chips, values, add);
  const remove = document.createElement("button"); remove.type = "button"; remove.className = "text-button"; remove.textContent = "Remove row";
  remove.addEventListener("click", () => row.remove());
  row.append(key, editor, remove); $("tag-rows").append(row);
}
$("edit-tags").addEventListener("click", () => {
  editingTagVersion = state.run.tag_version; $("tag-rows").replaceChildren();
  for (const [row, values] of Object.entries(state.run.tag_groups)) tagRow(row, values);
  dialogMessage("tag-editor", ""); $("tag-editor").showModal();
});
$("add-tag-row").addEventListener("click", () => tagRow());
$("save-tags").addEventListener("click", async () => {
  $("save-tags").disabled = true;
  try {
    const groups = Object.create(null);
    for (const row of $("tag-rows").children) {
      const name = row.querySelector("input").value.trim();
      if (!name || Object.hasOwn(groups, name)) throw new Error("Each row needs a unique name.");
      groups[name] = [...row.querySelectorAll("[data-tag]")].map((chip) => chip.dataset.tag)
        .concat(row.querySelectorAll("input")[1].value.split(",").map((s) => s.trim()).filter(Boolean));
    }
    await post("/api/tag-groups", { groups, version: editingTagVersion });
    $("tag-editor").close();
    const id = state.run.id, run = await api(`/api/runs/${id}`);
    if (state.run?.id === id) renderRun(run);
  } catch (error) { dialogMessage("tag-editor", error.message); }
  finally { $("save-tags").disabled = false; }
});
boot();
