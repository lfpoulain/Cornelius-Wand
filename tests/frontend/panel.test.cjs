// Régressions du panneau sans navigateur ni dépendance npm : les échanges websocket
// sont contrôlés, et seuls le dessin et la construction des listes sont remplacés.
const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { join } = require("node:path");
const { test } = require("node:test");
const vm = require("node:vm");

const source = readFileSync(join(__dirname, "../../custom_components/cornelius_wand/www/panel.js"), "utf8");

function element() {
  const classes = new Set();
  return {
    classList: {
      add: (value) => classes.add(value),
      remove: (value) => classes.delete(value),
      toggle: (value, enabled) => enabled ? classes.add(value) : classes.delete(value),
    },
    setAttribute(name, value) { this[name] = value; },
    removeAttribute(name) { delete this[name]; },
    replaceChildren() {},
  };
}

function state(entryId, visionEnabled, targets = []) {
  return { entry_id: entryId, vision_enabled: visionEnabled, targets, entries: [], levels: [], tolerance: 25 };
}

function panel(callWS) {
  let Panel;
  const elements = new Map();
  const context = vm.createContext({
    HTMLElement: class {
      attachShadow() {
        this.shadowRoot = {
          getElementById(id) {
            if (!elements.has(id)) elements.set(id, element());
            return elements.get(id);
          },
        };
      }
    },
    customElements: { define(name, constructor) { Panel = constructor; } },
    setTimeout: (resolve) => resolve(), // le compte à rebours n'est pas l'objet de ces tests
  });
  vm.runInContext(source, context);
  const instance = new Panel();
  for (const method of ["renderEntries", "renderLevels", "renderTargets", "draw"]) instance[method] = () => {};
  instance._hass = { callWS };
  instance.entryId = "first";
  return instance;
}

function deferred() {
  let resolve;
  const promise = new Promise((done) => { resolve = done; });
  return { promise, resolve };
}

test("Photo actualise les cibles et réactive la vision configurée dans un autre onglet", async () => {
  const calls = [];
  const target = { key: "new", name: "Nouvelle lampe", x: 0.5, y: 0.5 };
  const current = state("first", true, [target]);
  const view = panel(async (message) => {
    calls.push([message.type, message.entry_id]);
    return message.type.endsWith("/state") ? current : { image: "fresh", age_ms: 10 };
  });
  view.state = state("first", false, [{ key: "removed", name: "Ancienne lampe" }]);
  view.selected = "removed";
  view.updateAnalyze();
  assert.equal(view.$("analyze").disabled, true);

  await view.refreshPhoto();

  assert.equal(view.state, current);
  assert.equal(view.selected, "new");
  assert.equal(view.$("analyze").disabled, false);
  assert.equal(view.$("photo").src, "data:image/jpeg;base64,fresh");
  assert.deepEqual(calls, [["cornelius_wand/state", "first"], ["cornelius_wand/photo", "first"]]);
});

test("Analyser détecte une vision désactivée ailleurs avant le compte à rebours et l'appel Jev", async () => {
  const calls = [];
  const view = panel(async (message) => {
    calls.push(message.type);
    assert.equal(message.type, "cornelius_wand/state");
    return state("first", false);
  });
  view.state = state("first", true);

  await view.analyze();

  assert.deepEqual(calls, ["cornelius_wand/state"]);
  assert.equal(view.$("analyze").disabled, true);
  assert.equal(view.$("refresh").disabled, false);
  assert.match(view.$("status").textContent, /Vision non configurée/);
});

test("Analyser resynchronise les cibles tout en gardant la sélection encore présente", async () => {
  const calls = [];
  const current = state("first", true, [{ key: "lamp", name: "Lampe", x: 0.7, y: 0.5 }]);
  const view = panel(async (message) => {
    calls.push(message.type);
    return message.type.endsWith("/state") ? current : { image: "analysis", age_ms: 0, latency_ms: 100, target: null, angle: null };
  });
  view.state = state("first", true, [{ key: "lamp", name: "Lampe", x: null, y: null }]);
  view.selected = "lamp";

  await view.analyze();

  assert.equal(view.state, current);
  assert.equal(view.selected, "lamp");
  assert.deepEqual(calls, ["cornelius_wand/state", "cornelius_wand/analyze"]);
  assert.equal(view.$("photo").src, "data:image/jpeg;base64,analysis");
});

test("Un état tardif de la première baguette ne remplace pas la deuxième", async () => {
  const oldState = deferred();
  const current = state("second", true);
  const calls = [];
  const view = panel(async (message) => {
    calls.push([message.type, message.entry_id]);
    if (message.entry_id === "first") return oldState.promise;
    return message.type.endsWith("/state") ? current : { image: "second", age_ms: 0 };
  });
  view.state = state("first", false);
  const firstRefresh = view.refreshPhoto();
  await view.chooseEntry("second");
  oldState.resolve(state("first", false));
  await firstRefresh;

  assert.equal(view.state, current);
  assert.equal(view.entryId, "second");
  assert.equal(view.$("analyze").disabled, false);
  assert.equal(view.$("photo").src, "data:image/jpeg;base64,second");
  assert.deepEqual(calls, [["cornelius_wand/state", "first"], ["cornelius_wand/state", "second"], ["cornelius_wand/photo", "second"]]);
});

test("Une photo tardive de la première baguette ne remplace pas celle de la deuxième", async () => {
  const oldPhoto = deferred();
  const photoRequested = deferred();
  const view = panel(async (message) => {
    if (message.type.endsWith("/state")) return state(message.entry_id, true);
    if (message.entry_id === "first") {
      photoRequested.resolve();
      return oldPhoto.promise;
    }
    return { image: "second", age_ms: 0 };
  });
  view.state = state("first", true);
  const firstRefresh = view.refreshPhoto();
  await photoRequested.promise;
  await view.chooseEntry("second");
  oldPhoto.resolve({ image: "first", age_ms: 0 });
  await firstRefresh;

  assert.equal(view.entryId, "second");
  assert.equal(view.$("photo").src, "data:image/jpeg;base64,second");
});

test("Un rechargement d'état en échec ne lance pas d'analyse sur les options périmées", async () => {
  const calls = [];
  const view = panel(async (message) => {
    calls.push(message.type);
    throw new Error("baguette en cours de rechargement");
  });
  view.state = state("first", true);

  await view.analyze();

  assert.deepEqual(calls, ["cornelius_wand/state"]);
  assert.match(view.$("status").textContent, /Baguette introuvable/);
  assert.equal(view.busy, false);
});
