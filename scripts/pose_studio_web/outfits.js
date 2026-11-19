"use strict";

const $ = (id) => document.getElementById(id);
const state = { config: null, mode: "photo", sources: [], selected: new Set(), run: null, busy: false, cards: new Map(), detail: null, timer: null };
const STATES = { waiting: "Waiting", posing: "Posing on white…", extracting: "Extracting outfit…", saved: "Ready to review", failed: "Failed" };
const usd = (value) => `$${Number(value || 0).toFixed(3)}`;

async function api(url, options = {}) {
  const response = await fetch(url, { ...options, headers: { "X-Pose-Studio": "1", ...options.headers } });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || `Request failed (${response.status}).`);
  return data;
}
const post = (url, data = {}) => api(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) });
function notice(message = "") { $("notice").textContent = message; $("notice").hidden = !message; }
async function guarded(action) {
  if (state.busy) return;
  state.busy = true; refreshControls();
  try { notice(); await action(); } catch (error) { notice(error.message); } finally { state.busy = false; refreshControls(); }
}
function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

// Settings -------------------------------------------------------------------
const texts = () => $("outfit-texts").value.split("\n").map((line) => line.trim()).filter(Boolean);
const singleStep = (pose) => pose === "skip" || pose === "collage";
function stepsPerItem() { return state.mode === "photo" && !singleStep($("pose").value) ? 2 : 1; }
function selectedCount() { return state.mode === "photo" ? state.selected.size : texts().length; }
function refreshControls() {
  const provider = $("provider").value, ready = state.config?.providers?.[provider];
  const count = selectedCount(), requests = count * stepsPerItem();
  const locked = state.busy || !!state.run;
  $("spicy-caps").hidden = provider !== "spicy";
  $("pose-field").hidden = state.mode !== "photo";
  $("provider-note").textContent = !state.config ? "" : ready
    ? (provider === "openai" ? "Uses OPENAI_API_KEY. If OpenAI refuses an image, run it again with Seedream." : "Uses SPICY_API_KEY. Each request is quoted and held to the caps below.")
    : (provider === "openai" ? "Add OPENAI_API_KEY to the project .env to use OpenAI." : "Add SPICY_API_KEY to the project .env to use SpicyAPI.");
  $("ceiling").textContent = String(requests);
  $("budget-note").textContent = !count ? "Select photos or write descriptions to see the request count."
    : `${count} ${state.mode === "photo" ? "picture" : "description"}${count === 1 ? "" : "s"} × ${stepsPerItem()} step${stepsPerItem() === 1 ? "" : "s"}.` +
      (provider === "spicy" ? ` At most ${usd(requests * Number($("per-image").value))} if every quote hits the per-image cap.` : "");
  $("text-count").textContent = `${texts().length} outfit${texts().length === 1 ? "" : "s"}`;
  $("generate").disabled = locked || !ready || !count || count > (state.config?.max_items || 200);
  for (const id of ["tab-photo", "tab-text", "folder-button", "files-button", "outfit-texts", "provider", "pose", "use-style", "extra", "run-name", "per-image", "total-cap"]) $(id).disabled = locked;
  if (!state.config?.style_reference) { $("use-style").checked = false; $("use-style").disabled = true; }
  for (const id of ["new-run", "history"]) $(id).disabled = state.busy;
  const online = !!state.config, any = online && (state.config.providers.openai || state.config.providers.spicy);
  $("connection-label").textContent = !online ? "Server disconnected" : any ? "Image provider ready" : "No API key";
  $("connection-dot").classList.toggle("ready", !!any);
  if (!state.run) {
    $("gallery-count").textContent = state.mode === "photo" && state.sources.length ? `${state.selected.size} of ${state.sources.length} selected` : "";
    $("selection-tools").hidden = state.mode !== "photo" || !state.sources.length;
  }
}
function setMode(mode) {
  state.mode = mode;
  $("tab-photo").setAttribute("aria-selected", String(mode === "photo"));
  $("tab-text").setAttribute("aria-selected", String(mode === "text"));
  $("photo-mode").hidden = mode !== "photo";
  $("text-mode").hidden = mode !== "text";
  styleDefault();
  if (!state.run) renderSources();
  refreshControls();
}

// Sources (before a run) -----------------------------------------------------
function clearGallery() { state.cards.clear(); $("gallery").replaceChildren(); }
function renderSources() {
  clearGallery();
  const photos = state.mode === "photo" ? state.sources : [];
  $("empty-state").hidden = photos.length > 0;
  $("gallery-title").textContent = photos.length ? "Photos to extract" : "Your rack is empty";
  for (const source of photos) {
    const card = element("article", "card"), button = element("div", "card-image"), image = element("img");
    image.src = source.preview; image.alt = source.name; image.loading = "lazy";
    const check = element("label", "card-check"), box = element("input");
    box.type = "checkbox"; box.checked = state.selected.has(source.id); box.setAttribute("aria-label", `Select ${source.name}`);
    box.addEventListener("change", () => {
      if (box.checked) state.selected.add(source.id); else state.selected.delete(source.id);
      card.classList.toggle("selected", box.checked); refreshControls();
    });
    check.append(box); button.append(image);
    const info = element("div", "card-info");
    info.append(element("span", "card-name", source.name), element("span", "card-status", "Ready to queue"));
    card.classList.toggle("selected", box.checked);
    card.append(check, button, info);
    $("gallery").append(card);
  }
  refreshControls();
}
async function addPhotos(fileList, label, append = false) {
  const files = [...fileList].filter((file) => /\.(png|jpe?g|webp)$/i.test(file.name) || /^image\/(png|jpeg|webp)$/.test(file.type))
    .sort((a, b) => a.name.localeCompare(b.name, undefined, { numeric: true }));
  if (!files.length) throw new Error("No PNG, JPEG or WebP pictures found.");
  const limit = state.config?.max_items || 200;
  if (files.length + (append ? state.sources.length : 0) > limit) throw new Error(`Choose at most ${limit} pictures per run.`);
  const progress = $("upload-progress");
  progress.hidden = false; progress.max = files.length; progress.value = 0;
  const sources = append ? [...state.sources] : [], seen = new Set(sources.map((source) => source.id));
  const added = new Set();
  try {
    for (const file of files) {
      const saved = await api("/api/outfits/uploads", { method: "PUT", headers: { "Content-Type": "application/octet-stream" }, body: file });
      progress.value += 1;
      if (seen.has(saved.id)) continue;  // the same picture twice
      seen.add(saved.id); added.add(saved.id);
      sources.push({ id: saved.id, name: file.name || `Pasted ${sources.length + 1}`, preview: URL.createObjectURL(file) });
    }
  } finally { progress.hidden = true; }
  if (!append) for (const old of state.sources) URL.revokeObjectURL(old.preview);
  state.selected = append ? new Set([...state.selected, ...added]) : new Set(sources.map((source) => source.id));
  state.sources = sources;
  if (append) label = `${sources.length} picture${sources.length === 1 ? "" : "s"}`;
  $("folder-title").textContent = label;
  $("folder-subtitle").textContent = `${sources.length} ready · choose a folder to replace, drop or paste to add`;
  if (!$("run-name").value) $("run-name").value = label;
  newRun();
}

// Runs -----------------------------------------------------------------------
function newRun() {
  clearTimeout(state.timer);
  state.run = null;
  $("history").value = "";
  $("run-panel").hidden = true;
  $("workspace-title").textContent = "Build your outfit library.";
  $("workspace-subtitle").textContent = "Choose photos or write descriptions, then queue the run and watch each outfit come off the person.";
  notice(); renderSources();
}
function itemStatus(run, item) {
  if (item.deleted) return "Deleted";
  if (item.state === "extracting" && run.mode === "text") return "Rendering outfit…";
  if (item.state === "waiting" && item.posed && !item.outfit) return "Posed · waiting to extract";
  return STATES[item.state] || item.state;
}
function checkText(item) {
  const found = [item.checks?.logo && "possible logo", item.checks?.body && "visible skin or body"].filter(Boolean).join(" and ");
  return found + (item.checks?.note ? ` (${item.checks.note})` : "");
}
function retryStep(run, item) { return run.mode === "photo" && !singleStep(run.pose) && !item.posed ? "pose" : "extract"; }
function itemCard(run, item) {
  const card = element("article", "card"), button = element("button", "card-image");
  button.type = "button";
  const info = element("div", "card-info"), name = element("span", "card-name"), status = element("span", "card-status");
  const saved = element("span", "card-saved"), error = element("span", "card-error"), buttons = element("div", "card-buttons");
  const retry = element("button", "text-button", "Retry"), remove = element("button", "text-button card-delete");
  retry.type = remove.type = "button";
  buttons.append(retry, remove);
  info.append(name, status, saved, error, buttons);
  card.append(button, info);
  const entry = { card, button, name, status, saved, error, retry, remove, item, shown: null };
  button.addEventListener("click", () => openDetail(entry.item.index));
  retry.addEventListener("click", () => guarded(async () => showRun(await post(`/api/outfits/runs/${state.run.id}/items/${entry.item.index}/regenerate`, { step: retryStep(state.run, entry.item) }))));
  remove.addEventListener("click", () => guarded(async () => showRun(await post(`/api/outfits/runs/${state.run.id}/items/${entry.item.index}/${entry.item.deleted ? "restore" : "delete"}`))));
  return entry;
}
function updateCard(entry, run, item) {
  entry.item = item;
  const url = item.urls.outfit || item.urls.posed || item.urls.source;
  if (entry.shown !== (url || "text")) {
    entry.shown = url || "text";
    if (url) {
      const image = element("img"); image.src = url; image.alt = item.name; image.loading = "lazy";
      entry.button.replaceChildren(image);
    } else entry.button.replaceChildren(element("span", "card-text", item.text || item.name));
  }
  entry.card.dataset.state = item.state === "posing" || item.state === "extracting" ? "running" : item.state;
  entry.card.classList.toggle("deleted", item.deleted);
  entry.name.textContent = item.label || item.name;
  entry.status.textContent = itemStatus(run, item);
  entry.saved.textContent = item.saved_as ? `Saved as ${item.saved_as.id} · ${item.saved_as.gallery}` : "";
  entry.saved.hidden = !item.saved_as;
  const warning = item.error || (item.held ? `Held back: ${checkText(item)}` : "");
  entry.error.textContent = warning;
  entry.error.hidden = !warning;
  const active = item.state === "posing" || item.state === "extracting";
  entry.retry.hidden = item.state !== "failed" || item.deleted;
  entry.retry.disabled = entry.remove.disabled = state.busy || active;
  entry.remove.textContent = item.deleted ? "Restore" : "Delete";
}
function showRun(run) {
  const fresh = state.run?.id !== run.id;
  state.run = run;
  if (fresh) clearGallery();
  $("empty-state").hidden = true;
  $("selection-tools").hidden = true;
  $("run-panel").hidden = false;
  $("workspace-title").textContent = run.label;
  $("workspace-subtitle").textContent = run.mode === "photo"
    ? `${run.pose === "collage" ? "Assemble each flat lay or collage" : run.pose === "skip" ? "Extract straight from each photo" : `${run.pose === "i-pose" ? "I-pose" : "A-pose"} on white, then extract`} · ${run.provider === "openai" ? "gpt-image-2" : "Seedream 5.5"}`
    : `Text to outfit · ${run.provider === "openai" ? "gpt-image-2" : "Seedream 5.5"}`;
  const live = run.items.filter((item) => !item.deleted), done = live.filter((item) => item.outfit).length;
  const failed = live.filter((item) => item.state === "failed").length;
  const labels = { queued: run.queue_position ? `Queued · ${run.queue_position} run${run.queue_position === 1 ? "" : "s"} ahead` : "Queued", running: "Running", pausing: "Pausing after this outfit", paused: "Paused", completed: "Completed" };
  $("run-state").textContent = labels[run.status] || run.status;
  $("run-progress-label").textContent = `${done} of ${live.length} outfits ready`;
  $("run-progress").max = Math.max(live.length, 1); $("run-progress").value = done;
  $("requests").textContent = String(run.items.reduce((sum, item) => sum + item.requests, 0));
  $("failed").textContent = String(failed);
  const spicy = run.provider === "spicy";
  $("charged-metric").hidden = $("cap-metric").hidden = !spicy;
  $("charged").textContent = usd(run.items.reduce((sum, item) => sum + Number(item.charged), 0));
  $("run-cap").textContent = usd(run.total_cap);
  $("run-message").textContent = run.error || (run.status === "completed" && failed ? `${failed} outfit${failed === 1 ? "" : "s"} failed. Retry them one by one, or run them again with the other image model.` : "");
  $("destination").textContent = run.folder;
  $("pause").hidden = !["running", "queued"].includes(run.status);
  $("resume").hidden = run.status !== "paused";
  const unsaved = live.filter((item) => item.outfit && !item.saved_as && !item.held).length;
  const heldCount = live.filter((item) => item.outfit && !item.saved_as && item.held).length;
  $("curation").hidden = done === 0;
  $("save").textContent = unsaved ? `Save ${unsaved} outfit${unsaved === 1 ? "" : "s"} to gallery` : heldCount ? `${heldCount} held back by the check` : "All outfits saved";
  $("save").disabled = state.busy || !unsaved;
  const caption = run.caption || {}, uncaptioned = live.filter((item) => item.outfit && !item.label).length;
  $("caption").textContent = uncaptioned ? `Caption ${uncaptioned} outfit${uncaptioned === 1 ? "" : "s"}` : "All outfits captioned";
  $("caption").disabled = state.busy || caption.running || !uncaptioned || !state.config?.providers?.[$("caption-provider").value];
  $("caption-all").disabled = state.busy || caption.running || !done || !state.config?.providers?.[$("caption-provider").value];
  $("caption-status").textContent = caption.running ? `Captioning ${caption.done} of ${caption.total}…`
    : caption.error ? `Captions stopped: ${caption.error}` : caption.total ? `Captioned ${caption.done} of ${caption.total}.`
    : !state.config?.providers?.[$("caption-provider").value] ? "Add this vision model's API key to the project .env to caption." : "";
  $("gallery-title").textContent = "Outfits";
  $("gallery-count").textContent = `${live.length} in this run`;
  run.items.forEach((item) => {
    let entry = state.cards.get(item.index);
    if (!entry) { entry = itemCard(run, item); state.cards.set(item.index, entry); $("gallery").append(entry.card); }
    updateCard(entry, run, item);
  });
  if ($("detail").open && state.detail !== null) renderDetail(false);
  refreshControls();
  clearTimeout(state.timer);
  if (["queued", "running", "pausing"].includes(run.status) || caption.running) state.timer = setTimeout(poll, 2000);
}
async function poll() {
  if (!state.run) return;
  try { showRun(await api(`/api/outfits/runs/${state.run.id}`)); }
  catch (error) { notice(error.message); state.timer = setTimeout(poll, 5000); }
}
async function loadConfig() {
  state.config = await api("/api/outfits");
  const history = $("history"), current = state.run?.id || "";
  history.replaceChildren(Object.assign(element("option", "", "New run"), { value: "" }),
    ...state.config.runs.map((run) => Object.assign(element("option", "", `${run.label} · ${run.done}/${run.total} · ${run.status}`), { value: run.id })));
  history.value = current;
  $("gallery-list").replaceChildren(...state.config.galleries.map((name) => Object.assign(element("option"), { value: name })));
  $("output-size").textContent = `${state.config.size[0]} × ${state.config.size[1]} · 2:3`;
  refreshControls();
}

// Detail dialog --------------------------------------------------------------
function frame(id, url, placeholder) {
  const figure = $(id), image = figure.querySelector("img"), label = figure.querySelector("span");
  image.hidden = !url; label.hidden = !!url;
  if (url && image.getAttribute("src") !== url) image.src = url;
  if (!url) image.removeAttribute("src");
  label.textContent = placeholder;
}
function renderDetail(loadFields) {
  const run = state.run, item = run.items[state.detail];
  $("detail-title").textContent = item.label || item.name;
  $("detail-status").textContent = [itemStatus(run, item), item.error, item.held ? `Held back: ${checkText(item)}` : "",
    item.saved_as ? `Saved as ${item.saved_as.id} in ${item.saved_as.gallery}` : ""].filter(Boolean).join(" · ");
  $("cleared-field").hidden = !(item.checks?.logo || item.checks?.body);
  $("detail-source").hidden = $("detail-posed").hidden = run.mode !== "photo";
  $("detail-posed").hidden = run.mode !== "photo" || singleStep(run.pose);
  $("detail-source").querySelector("figcaption").textContent = run.pose === "collage" ? "Flat lay" : "Photo";
  frame("detail-source", item.urls.source, "No photo");
  frame("detail-posed", item.urls.posed, item.state === "posing" ? "Posing…" : "Not posed yet");
  frame("detail-outfit", item.urls.outfit, item.state === "extracting" ? "Working…" : run.mode === "text" ? item.text : "Not ready");
  const active = item.state === "posing" || item.state === "extracting";
  $("redo-pose").hidden = run.mode !== "photo" || singleStep(run.pose);
  $("redo-pose").disabled = $("redo-outfit").disabled = $("detail-delete").disabled = state.busy || active;
  $("redo-outfit").disabled = $("redo-outfit").disabled || (run.mode === "photo" && !singleStep(run.pose) && !item.posed);
  $("detail-delete").textContent = item.deleted ? "Restore" : "Delete";
  $("detail-download").hidden = !item.urls.outfit;
  if (item.urls.outfit) { $("detail-download").href = item.urls.outfit; $("detail-download").download = `${(item.label || item.name).replace(/[^\w.-]+/g, "_").slice(0, 60) || "outfit"}.png`; }
  if (loadFields) {
    $("edit-label").value = item.label; $("edit-prompt").value = item.prompt;
    $("edit-category").value = item.category; $("edit-tags").value = item.tags.join(", ");
    $("edit-cleared").checked = !!item.cleared;
    $("edit-status").textContent = "";
  }
}
function openDetail(index) {
  state.detail = index;
  renderDetail(true);
  if (!$("detail").open) $("detail").showModal();
}
const detailAction = (path, body) => guarded(async () => showRun(await post(`/api/outfits/runs/${state.run.id}/items/${state.detail}/${path}`, body)));
$("close-detail").addEventListener("click", () => $("detail").close());
$("redo-pose").addEventListener("click", () => detailAction("regenerate", { step: "pose" }));
$("redo-outfit").addEventListener("click", () => detailAction("regenerate", { step: "extract" }));
$("detail-delete").addEventListener("click", () => detailAction(state.run.items[state.detail].deleted ? "restore" : "delete"));
$("outfit-editor").addEventListener("submit", (event) => {
  event.preventDefault();
  guarded(async () => {
    showRun(await post(`/api/outfits/runs/${state.run.id}/items/${state.detail}/edit`, {
      label: $("edit-label").value, prompt: $("edit-prompt").value, category: $("edit-category").value, tags: $("edit-tags").value,
      cleared: $("edit-cleared").checked }));
    renderDetail(true);
    $("edit-status").textContent = "Saved.";
  });
});

// Wiring ---------------------------------------------------------------------
$("folder-button").addEventListener("click", () => $("folder-input").click());
$("files-button").addEventListener("click", () => $("files-input").click());
for (const id of ["folder-input", "files-input"]) $(id).addEventListener("change", () => guarded(async () => {
  const input = $(id), files = input.files;
  const label = id === "folder-input" && files[0]?.webkitRelativePath ? files[0].webkitRelativePath.split("/")[0] : `${files.length} photo${files.length === 1 ? "" : "s"}`;
  try { if (files.length) await addPhotos(files, label); } finally { input.value = ""; }
}));
// Drop images anywhere on the page, or paste them (for example "Copy image" on a Pinterest pin).
function incoming(files) {
  const images = [...files].filter((file) => /^image\/(png|jpeg|webp)$/.test(file.type));
  if (!images.length || state.run) return false;
  setMode("photo");
  guarded(() => addPhotos(images, "", true));
  return true;
}
document.addEventListener("dragover", (event) => { if (event.dataTransfer?.types?.includes("Files")) event.preventDefault(); });
document.addEventListener("drop", (event) => {
  if (!event.dataTransfer?.files?.length) return;
  event.preventDefault();
  if (!incoming(event.dataTransfer.files)) notice(state.run ? "Click New run to add pictures." : "Drop PNG, JPEG or WebP images.");
});
document.addEventListener("paste", (event) => {
  if (event.target.closest?.("input, textarea")) return;
  if (event.clipboardData?.files?.length && incoming(event.clipboardData.files)) event.preventDefault();
});
$("tab-photo").addEventListener("click", () => setMode("photo"));
$("tab-text").addEventListener("click", () => setMode("text"));
for (const id of ["provider", "pose", "per-image", "total-cap", "outfit-texts"]) $(id).addEventListener("input", refreshControls);
// Pictures already show the clothes; a style reference only tempts the model to borrow its pieces.
function styleDefault() { if (state.config?.style_reference) $("use-style").checked = state.mode === "text"; }
$("caption-provider").addEventListener("change", () => { if (state.run) showRun(state.run); });
$("select-all").addEventListener("click", () => { state.selected = new Set(state.sources.map((source) => source.id)); renderSources(); });
$("select-none").addEventListener("click", () => { state.selected.clear(); renderSources(); });
$("new-run").addEventListener("click", newRun);
$("history").addEventListener("change", () => guarded(async () => {
  const id = $("history").value;
  if (!id) return newRun();
  state.run = null;
  showRun(await api(`/api/outfits/runs/${id}`));
}));
$("generate").addEventListener("click", () => guarded(async () => {
  const body = { label: $("run-name").value, provider: $("provider").value, mode: state.mode, pose: $("pose").value,
    use_style: $("use-style").checked, extra: $("extra").value, max_charge: $("per-image").value, total_cap: $("total-cap").value };
  if (state.mode === "photo") body.uploads = state.sources.filter((source) => state.selected.has(source.id)).map(({ id, name }) => ({ id, name }));
  else body.texts = texts();
  const run = await post("/api/outfits/runs", body);
  state.run = null;
  showRun(run);
  await loadConfig();
  $("history").value = run.id;
}));
$("pause").addEventListener("click", () => guarded(async () => showRun(await post(`/api/outfits/runs/${state.run.id}/pause`))));
$("resume").addEventListener("click", () => guarded(async () => showRun(await post(`/api/outfits/runs/${state.run.id}/resume`))));
$("reuse").addEventListener("click", () => {
  const run = state.run;
  $("provider").value = run.provider; $("use-style").checked = run.use_style; $("extra").value = run.extra;
  if (run.pose) $("pose").value = run.pose;
  $("run-name").value = run.label;
  if (run.mode === "text") $("outfit-texts").value = run.items.map((item) => item.text).join("\n");
  newRun(); setMode(run.mode);
});
$("caption").addEventListener("click", () => guarded(async () => showRun(await post(`/api/outfits/runs/${state.run.id}/caption`, { provider: $("caption-provider").value }))));
$("caption-all").addEventListener("click", () => guarded(async () => showRun(await post(`/api/outfits/runs/${state.run.id}/caption`, { provider: $("caption-provider").value, all: true }))));
$("save").addEventListener("click", () => guarded(async () => {
  const result = await post(`/api/outfits/runs/${state.run.id}/save`, { gallery: $("gallery").value, category: $("category").value });
  showRun(result.run);
  $("save-status").textContent = `Saved ${result.saved} outfit${result.saved === 1 ? "" : "s"} to assets/${result.gallery} (${result.count} in the gallery).` +
    (result.held ? ` ${result.held} held back by the logo and skin check.` : "") + " Reload ComfyUI to see them in Browse outfits.";
  await loadConfig();
}));

// Remember the last image model and picture type between visits.
for (const id of ["provider", "pose"]) {
  try { const saved = localStorage.getItem(`outfits.${id}`); if (saved && [...$(id).options].some((option) => option.value === saved)) $(id).value = saved; } catch { /* storage blocked */ }
  $(id).addEventListener("change", () => { try { localStorage.setItem(`outfits.${id}`, $(id).value); } catch { /* storage blocked */ } });
}
setMode("photo");
loadConfig().then(styleDefault).catch((error) => { notice(error.message); refreshControls(); });
