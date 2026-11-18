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
    const galleries = data.galleries || [];
    const current = data.pose.find((entry) => entry.id === widget(node, "pose")?.value);
    view.currentGallery = current?.gallery;
    let scope = node.properties?.pose_gallery ?? current?.gallery ?? "";
    if (scope && !galleries.some((group) => group.id === scope)) scope = "";
    if (scope && current && current.gallery !== scope) scope = current.gallery;
    view.gallery.replaceChildren(new Option("All pose galleries", ""),
      ...galleries.map((group) => new Option(group.label, group.id)));
    view.gallery.value = scope;
    const pose = widget(node, "pose");
    if (pose?.options) {
      const visible = data.pose.filter((entry) => !scope || entry.gallery === scope);
      const names = new Map(visible.map((entry) => [entry.id, entry.catalog_id || entry.id]));
      pose.options = { ...pose.options, values: [NONE, ...names.keys()],
        getOptionLabel: (value) => names.get(value) || value || NONE };
    }
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
  const gallery = element("select", "pg-select");
  gallery.setAttribute("aria-label", "Pose gallery");
  gallery.add(new Option("All pose galleries", ""));
  for (const group of data.galleries || []) gallery.add(new Option(group.label, group.id));
  gallery.value = nodeViews.get(node)?.gallery.value ?? data.pose.find((entry) => entry.id === widget(node, "pose")?.value)?.gallery ?? "";
  const tagFilters = element("div", "pg-tag-filters");
  const selectedTags = new Map();
  const search = element("input", "pg-search");
  search.type = "search";
  search.placeholder = "Search by pose, clothing, or ID…";
  search.setAttribute("aria-label", "Search references");
  const category = element("select", "pg-select");
  category.setAttribute("aria-label", "Reference category");
  const angle = element("select", "pg-select");
  angle.setAttribute("aria-label", "Camera angle");
  filters.append(gallery, search, category, angle);
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
    if (kind === "pose") {
      node.properties ||= {};
      node.properties.pose_gallery = gallery.value;
    }
    updateSelection(node, kind, selectedId);
    dialog.close();
  }, "pg-button pg-primary");
  footer.append(clear, element("span", "pg-footer-hint", "Images and descriptions stay local."), use);
  dialog.append(tabs, filters, tagFilters, layout, footer);

  function inGallery(entry) {
    return kind !== "pose" || !gallery.value || entry.gallery === gallery.value;
  }

  function tagsByRow(entry) {
    const rows = new Map(Object.entries(entry.tag_groups || {}).filter(([, tags]) => Array.isArray(tags)));
    const assigned = new Set([...rows.values()].flat());
    const flat = (Array.isArray(entry.tags) ? entry.tags : []).filter((tag) => !assigned.has(tag));
    if (flat.length) rows.set("Tags", [...(rows.get("Tags") || []), ...flat]);
    return rows;
  }
  // Grouped tags per tab: poses and outfits both filter by their tag_groups rows.
  const tagIndex = Object.fromEntries(["pose", "outfit"].map((name) =>
    [name, new Map((data[name] || []).map((entry) => [entry.id, tagsByRow(entry)]))]));

  function resetFilters() {
    const entries = data[kind].filter(inGallery);
    category.replaceChildren(new Option("All categories", ""));
    for (const value of new Set(entries.map((entry) => entry.category))) category.add(new Option(value, value));
    angle.replaceChildren(new Option("All angles", ""));
    for (const value of new Set(entries.map((entry) => entry.angle).filter(Boolean))) angle.add(new Option(value, value));
    selectedTags.clear();
    tagFilters.replaceChildren();
    const vocabulary = new Map();
    for (const entry of entries) {
      for (const [row, tags] of tagIndex[kind].get(entry.id) || []) {
        if (!vocabulary.has(row)) vocabulary.set(row, new Set());
        for (const tag of tags) vocabulary.get(row).add(tag);
      }
    }
    for (const [row, tags] of vocabulary) {
      if (!tags.size) continue;
      const field = element("fieldset");
      field.append(element("legend", "", row));
      for (const tag of tags) {
        const chip = button(tag, () => {
          const values = selectedTags.get(row) || new Set();
          if (values.has(tag)) values.delete(tag); else values.add(tag);
          if (values.size) selectedTags.set(row, values); else selectedTags.delete(row);
          chip.setAttribute("aria-pressed", String(values.has(tag)));
          currentPage = 0;
          renderResults();
        }, "pg-tag");
        chip.setAttribute("aria-pressed", "false");
        field.append(chip);
      }
      tagFilters.append(field);
    }
    tagFilters.hidden = !tagFilters.childElementCount;
    currentPage = 0;
    renderResults();
  }

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
    const galleryLabel = data.galleries?.find((group) => group.id === entry.gallery)?.label;
    detail.append(element("span", "pg-eyebrow", [galleryLabel, entry.category, entry.catalog_id || entry.id].filter(Boolean).join(" · ")));
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
      if (!inGallery(entry)) return false;
      if (category.value && category.value !== entry.category) return false;
      if (kind === "pose" && angle.value && angle.value !== entry.angle) return false;
      for (const [row, tags] of selectedTags) {
        if (!tagIndex[kind].get(entry.id)?.get(row)?.some((value) => tags.has(value))) return false;
      }
      const tags = Array.isArray(entry.tags) ? entry.tags.join(" ") : "";
      const text = `${entry.catalog_id || entry.id} ${entry.label} ${entry.prompt} ${entry.source_label || ""} ${tags}`.toLocaleLowerCase();
      return terms.every((term) => text.includes(term));
    });
    if (selectedId !== NONE && !matches.some((entry) => entry.id === selectedId)) {
      selectedId = NONE;
      renderDetail();
    }
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
      card.append(element("span", "pg-card-caption", entry.angle || entry.catalog_id || entry.id));
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
    gallery.hidden = kind !== "pose";
    angle.hidden = kind !== "pose";
    resetFilters();
    renderDetail();
  }

  gallery.addEventListener("change", resetFilters);
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
    const body = Object.fromEntries(["pose", "outfit", "subject", "prompt_template", "extra", "pose_override", "outfit_override",
      "pose_image_mode", "picture_tag", "edit_target", "reference_text", "location"].map((name) => [name, widget(node, name)?.value ?? ""]));
    // Picture numbers depend on whether subject or location photos are wired in.
    body.has_subject_image = node.inputs?.some((input) => input.name === "subject_image" && input.link != null) ?? false;
    body.has_location_image = node.inputs?.some((input) => input.name === "location_image" && input.link != null) ?? false;
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
  beforeRegisterNodeDef(nodeType, nodeData) {
    if (nodeData.name !== NODE) return;
    // Workflows saved before 0.1.4 have no values for the widgets added after pose_image_mode,
    // and widget values are stored by position. Give those workflows the old behaviour:
    // no picture references, no resizing.
    const configure = nodeType.prototype.configure;
    nodeType.prototype.configure = function (info) {
      let values = info?.widgets_values;
      // 0.1.0 had no pose_image_mode either.
      if (Array.isArray(values) && values.length === 11) values = [...values.slice(0, 7), "Drawing", ...values.slice(7)];
      if (Array.isArray(values) && values.length === 12) {
        info = { ...info, widgets_values: [...values.slice(0, 8), "", "Subject", 0, "Image and description", "Custom", ...values.slice(8)] };
      }
      return configure.call(this, info);
    };
  },
  nodeCreated(node) {
    if (node.comfyClass !== NODE) return;
    node.addWidget("button", "Browse poses", null, () => openGallery(node, "pose"), { serialize: false });
    node.addWidget("button", "Browse outfits", null, () => openGallery(node, "outfit"), { serialize: false });
    node.addWidget("button", "Preview positive prompt", null, () => previewPrompt(node), { serialize: false });
    if (typeof node.addDOMWidget === "function") {
      const panel = element("div", "pg-node-panel"), root = element("div", "pg-node-preview");
      const label = element("label", "pg-gallery-label", "Pose gallery");
      const gallery = element("select");
      label.append(gallery);
      panel.append(label, root);
      node.addDOMWidget("reference_preview", "pose_preview", panel, { serialize: false, getMinHeight: () => 210, getMaxHeight: () => 210 });
      nodeViews.set(node, { root, gallery, serial: 0 });
      gallery.addEventListener("change", () => {
        node.properties ||= {};
        node.properties.pose_gallery = gallery.value;
        const current = widget(node, "pose")?.value;
        if (gallery.value && current && current !== NONE && gallery.value !== nodeViews.get(node).currentGallery) updateSelection(node, "pose", NONE);
        else refreshNode(node);
        node.setDirtyCanvas?.(true, true);
      });
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
