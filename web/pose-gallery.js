import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

const NODE = "PoseOutfitReference";
const NONE = "None";
const PAGE_SIZE = 60;
const nodeViews = new WeakMap();
let catalogPromise;

function element(tag, className, text) {
  const result = document.createElement(tag);
  if (className) result.className = className;
  if (text !== undefined) result.textContent = text;
  return result;
}

function button(text, action, className = "pg-button") {
  const result = element("button", className, text);
  result.type = "button";
  result.addEventListener("click", action);
  return result;
}

async function loadCatalog(refresh = false) {
  if (refresh || !catalogPromise) {
    catalogPromise = api.fetchApi("/pose-generator/catalog").then(async (response) => {
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || "Unable to read the local gallery.");
      return data;
    }).catch((error) => {
      catalogPromise = undefined;
      throw error;
    });
  }
  return catalogPromise;
}

function widget(node, name) {
  return node.widgets?.find((item) => item.name === name);
}

function imageUrl(kind, id, full = false) {
  return api.apiURL(`/pose-generator/image/${kind}/${encodeURIComponent(id)}${full ? "?full=1" : ""}`);
}

function imageElement(kind, entry, full = false) {
  const image = element("img");
  image.alt = entry.label;
  image.loading = "lazy";
  image.decoding = "async";
  image.src = imageUrl(kind, entry.id, full);
  image.addEventListener("error", () => {
    image.alt = `Missing image: ${entry.id}`;
    image.classList.add("pg-image-missing");
  }, { once: true });
  return image;
}

function updateSelection(node, kind, id) {
  const input = widget(node, kind);
  if (!input) return;
  if (!input.options.values.includes(id)) input.options.values.push(id);
  const graph = node.graph;
  graph?.beforeChange?.();
  input.value = id;
  input.callback?.(id);
  graph?.afterChange?.();
  node.setDirtyCanvas?.(true, true);
  refreshNode(node);
}

async function refreshNode(node) {
  const view = nodeViews.get(node);
  if (!view) return;
  const serial = ++view.serial;
  try {
    const data = await loadCatalog();
    if (serial !== view.serial) return;
    view.root.replaceChildren();
    for (const kind of ["pose", "outfit"]) {
      const id = widget(node, kind)?.value;
      const entry = data[kind].find((item) => item.id === id);
      const card = button("", () => openGallery(node, kind), "pg-mini-card");
      card.append(element("span", "pg-mini-label", kind === "pose" ? "POSE" : "OUTFIT"));
      if (entry) {
        card.append(imageElement(kind, entry));
        card.append(element("span", "pg-mini-name", entry.label));
        card.title = `${entry.id} · ${entry.label}`;
      } else {
        card.append(element("span", "pg-mini-empty", id && id !== NONE ? "Reference unavailable" : `Choose ${kind}`));
      }
      view.root.append(card);
    }
  } catch (error) {
    view.root.replaceChildren(element("p", "pg-error", error.message));
  }
}

function createDialog(title, className = "") {
  const dialog = element("dialog", `pg-dialog ${className}`);
  const heading = element("header", "pg-heading");
  heading.append(element("h2", "", title));
  const close = button("Close", () => dialog.close(), "pg-button pg-quiet");
  close.setAttribute("aria-label", "Close reference browser");
  heading.append(close);
  dialog.append(heading);
  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) {
      const rect = dialog.getBoundingClientRect();
      if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) dialog.close();
    }
  });
  dialog.addEventListener("close", () => dialog.remove(), { once: true });
  document.body.append(dialog);
  dialog.showModal();
  return dialog;
}

async function openGallery(node, initialKind) {
  const dialog = createDialog("Choose a reference");
  const status = element("p", "pg-status", "Loading your local gallery…");
  dialog.append(status);
  let data;
  try {
    data = await loadCatalog(true);
  } catch (error) {
    status.textContent = error.message;
    status.classList.add("pg-error");
    return;
  }
  if (!dialog.isConnected) return;
  status.remove();
  let kind = initialKind;
  let currentPage = 0;
  let selectedId = widget(node, kind)?.value || NONE;
  const tabs = element("nav", "pg-tabs");
  const poseTab = button(`Poses · ${data.pose.length.toLocaleString()}`, () => setKind("pose"), "pg-tab");
  const outfitTab = button(`Outfits · ${data.outfit.length.toLocaleString()}`, () => setKind("outfit"), "pg-tab");
  tabs.append(poseTab, outfitTab);
  const filters = element("div", "pg-filters");
  const search = element("input", "pg-search");
  search.type = "search";
  search.placeholder = "Search by pose, clothing, or ID…";
  search.setAttribute("aria-label", "Search references");
  const category = element("select", "pg-select");
  category.setAttribute("aria-label", "Reference category");
  const angle = element("select", "pg-select");
  angle.setAttribute("aria-label", "Camera angle");
  filters.append(search, category, angle);
  const layout = element("div", "pg-layout");
  const results = element("section", "pg-results");
  const resultCount = element("p", "pg-count");
  resultCount.setAttribute("aria-live", "polite");
  const grid = element("div", "pg-grid");
  const paging = element("div", "pg-paging");
  const pageLabel = element("span");
  const previous = button("Previous", () => { currentPage--; renderResults(); });
  const next = button("Next", () => { currentPage++; renderResults(); });
  paging.append(previous, pageLabel, next);
  results.append(resultCount, grid, paging);
  const detail = element("aside", "pg-detail");
  layout.append(results, detail);
  const footer = element("footer", "pg-footer");
  const clear = button("Clear selection", () => {
    updateSelection(node, kind, NONE);
    dialog.close();
  }, "pg-button pg-quiet");
  const use = button("Use reference", () => {
    if (selectedId === NONE) return;
    updateSelection(node, kind, selectedId);
    dialog.close();
  }, "pg-button pg-primary");
  footer.append(clear, element("span", "pg-footer-hint", "Images and descriptions stay local."), use);
  dialog.append(tabs, filters, layout, footer);

  function renderDetail() {
    detail.replaceChildren();
    const entry = data[kind].find((item) => item.id === selectedId);
    use.disabled = !entry;
    use.textContent = `Use ${kind}`;
    if (!entry) {
      detail.append(element("p", "pg-detail-empty", "Select a thumbnail to see its image and prompt text."));
      return;
    }
    detail.append(imageElement(kind, entry, true));
    detail.append(element("span", "pg-eyebrow", `${entry.category} · ${entry.id}`));
    detail.append(element("h3", "", entry.label));
    detail.append(element("p", "pg-prompt-label", "PROMPT TEXT"));
    detail.append(element("p", "pg-description", entry.prompt));
    if (entry.source_label) {
      const original = element("details", "pg-original");
      original.append(element("summary", "", "Original label"), element("p", "", entry.source_label));
      detail.append(original);
    }
  }

  function renderResults() {
    const terms = search.value.toLocaleLowerCase().trim().split(/\s+/).filter(Boolean);
    const matches = data[kind].filter((entry) => {
      if (category.value && category.value !== entry.category) return false;
      if (kind === "pose" && angle.value && angle.value !== entry.angle) return false;
      const text = `${entry.id} ${entry.label} ${entry.prompt} ${entry.source_label || ""}`.toLocaleLowerCase();
      return terms.every((term) => text.includes(term));
    });
    const pages = Math.max(1, Math.ceil(matches.length / PAGE_SIZE));
    currentPage = Math.min(Math.max(0, currentPage), pages - 1);
    resultCount.textContent = `${matches.length.toLocaleString()} ${matches.length === 1 ? "reference" : "references"}`;
    grid.replaceChildren();
    for (const entry of matches.slice(currentPage * PAGE_SIZE, (currentPage + 1) * PAGE_SIZE)) {
      const card = button("", () => {
        selectedId = entry.id;
        for (const other of grid.children) {
          other.classList.toggle("pg-selected", other.dataset.id === selectedId);
          other.setAttribute("aria-pressed", String(other.dataset.id === selectedId));
        }
        renderDetail();
      }, "pg-card");
      card.dataset.id = entry.id;
      card.classList.toggle("pg-selected", entry.id === selectedId);
      card.setAttribute("aria-pressed", String(entry.id === selectedId));
      card.title = `${entry.label}${entry.angle ? ` · ${entry.angle}` : ""}`;
      card.append(imageElement(kind, entry));
      card.append(element("span", "pg-card-title", entry.label));
      card.append(element("span", "pg-card-caption", entry.angle || entry.id));
      grid.append(card);
    }
    if (!matches.length) {
      grid.append(element("p", "pg-no-results", data[kind].length
        ? "No references match. Try another search or category."
        : "No local references yet. Add your own images and catalog.json to assets/gallery, or set POSE_GENERATOR_ASSETS to your library folder. See the library setup guide in the README."));
    }
    previous.disabled = currentPage === 0;
    next.disabled = currentPage + 1 >= pages;
    pageLabel.textContent = `${currentPage + 1} / ${pages}`;
    results.scrollTop = 0;
  }

  function setKind(value) {
    kind = value;
    selectedId = widget(node, kind)?.value || NONE;
    currentPage = 0;
    search.value = "";
    poseTab.classList.toggle("pg-active", kind === "pose");
    outfitTab.classList.toggle("pg-active", kind === "outfit");
    poseTab.setAttribute("aria-pressed", String(kind === "pose"));
    outfitTab.setAttribute("aria-pressed", String(kind === "outfit"));
    category.replaceChildren(new Option("All categories", ""));
    for (const value of [...new Set(data[kind].map((entry) => entry.category))]) category.add(new Option(value, value));
    angle.replaceChildren(new Option("All angles", ""));
    for (const value of [...new Set(data[kind].map((entry) => entry.angle).filter(Boolean))]) angle.add(new Option(value, value));
    angle.hidden = kind !== "pose";
    renderResults();
    renderDetail();
  }

  for (const filter of [search, category, angle]) {
    filter.addEventListener(filter === search ? "input" : "change", () => {
      currentPage = 0;
      renderResults();
    });
  }
  setKind(kind);
  search.focus();
}

async function previewPrompt(node) {
  const dialog = createDialog("Positive prompt", "pg-prompt-dialog");
  const output = element("textarea", "pg-prompt-output");
  output.readOnly = true;
  output.setAttribute("aria-label", "Composed positive prompt");
  output.value = "Composing…";
  dialog.append(output);
  try {
    const body = Object.fromEntries(["pose", "outfit", "subject", "prompt_template", "extra", "pose_override", "outfit_override"].map((name) => [name, widget(node, name)?.value ?? ""]));
    const response = await api.fetchApi("/pose-generator/preview", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Could not compose the prompt.");
    output.value = result.prompt;
    output.focus();
    output.select();
  } catch (error) {
    output.value = error.message;
    output.classList.add("pg-error");
  }
}

app.registerExtension({
  name: "artokun.PoseOutfitReference",
  setup() {
    const stylesheet = element("link");
    stylesheet.rel = "stylesheet";
    stylesheet.href = new URL("./pose-gallery.css", import.meta.url).href;
    document.head.append(stylesheet);
  },
  nodeCreated(node) {
    if (node.comfyClass !== NODE) return;
    node.addWidget("button", "Browse poses", null, () => openGallery(node, "pose"), { serialize: false });
    node.addWidget("button", "Browse outfits", null, () => openGallery(node, "outfit"), { serialize: false });
    node.addWidget("button", "Preview positive prompt", null, () => previewPrompt(node), { serialize: false });
    if (typeof node.addDOMWidget === "function") {
      const root = element("div", "pg-node-preview");
      node.addDOMWidget("reference_preview", "pose_preview", root, { serialize: false, getMinHeight: () => 180, getMaxHeight: () => 180 });
      nodeViews.set(node, { root, serial: 0 });
    }
    for (const kind of ["pose", "outfit"]) {
      const input = widget(node, kind);
      if (!input) continue;
      const previous = input.callback;
      input.callback = function (...args) {
        const result = previous?.apply(this, args);
        refreshNode(node);
        return result;
      };
    }
    refreshNode(node);
  },
  afterConfigureGraph() {
    for (const node of app.graph?._nodes || []) {
      if (node.comfyClass === NODE) refreshNode(node);
    }
  },
});
