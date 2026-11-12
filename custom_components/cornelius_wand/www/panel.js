// Panneau « Baguette » de Home Assistant : placer les cibles en cliquant sur la photo de
// la caméra au plafond, et vérifier la visée. Parle à l'intégration par les commandes
// websocket de panel.py.
//
// Une cible hors champ : cliquer dans la marge hachurée autour de la photo, là où serait
// l'objet. Au bord de la photo, la direction ne serait juste que depuis un seul endroit.

const SVG = "http://www.w3.org/2000/svg";
const COUNTDOWN = 3; // s pour viser une cible avant l'analyse
const PHOTO_STALE_MS = 300; // PHOTO_MAX_GAP_MS de wand.py : plus vieille, la caméra ne range plus de photo

// Icônes et libellés des surfaces ; "" : n'importe quelle surface
const LEVELS = {
  "": { label: "toutes", chip: "mdi:arrow-expand-vertical", icon: "mdi:lightbulb-outline" },
  plafond: { label: "plafond", chip: "mdi:arrow-collapse-up", icon: "mdi:ceiling-light" },
  mur: { label: "mur", chip: "mdi:wall", icon: "mdi:lightbulb-outline" },
  sol: { label: "sol", chip: "mdi:arrow-collapse-down", icon: "mdi:floor-lamp" },
};
const level = (key) => LEVELS[key || ""] || { label: key, chip: "mdi:help", icon: "mdi:lightbulb-outline" };

// Réponse arrivée après un changement de baguette : elle concerne l'autre, on l'ignore
class StaleAnswer extends Error {}

class CorneliusWandPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this.state = null;
    this.entryId = undefined; // baguette affichée ; la première tant qu'aucune n'est choisie
    this.selected = null;
    this.analysis = null;
    this.busy = false;
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (first) {
      this.render();
      this.refreshPhoto();
    }
  }

  set narrow(narrow) {
    this._narrow = narrow;
    const menu = this.$("menu");
    if (menu) menu.hidden = !narrow;
  }

  async ws(type, data = {}) {
    const entryId = this.entryId;
    const result = await this._hass.callWS({ type: `cornelius_wand/${type}`, entry_id: entryId, ...data });
    if (entryId !== this.entryId) throw new StaleAnswer();
    return result;
  }

  // Consigne sous la photo ; tone : "info", "error" ou "success"
  say(text, tone = "info") {
    const hint = this.$("hint");
    hint.className = `hint ${tone}`;
    this.$("status").textContent = text;
    this.$("hint-icon").setAttribute(
      "icon",
      { error: "mdi:alert-circle-outline", success: "mdi:check-circle-outline" }[tone] || "mdi:cursor-default-click-outline",
    );
  }

  $(id) {
    return this.shadowRoot.getElementById(id);
  }

  target(key) {
    return this.state?.targets.find((t) => t.key === key);
  }

  async load() {
    try {
      this.state = await this.ws("state");
      this.entryId = this.state.entry_id;
      if (!this.target(this.selected)) this.selected = this.state.targets[0]?.key ?? null;
      this.renderEntries();
      this.renderLevels();
      this.renderTargets();
      this.updateAnalyze();
      this.draw();
      return true;
    } catch (err) {
      if (!(err instanceof StaleAnswer)) this.say(`Baguette introuvable : ${err.message}`, "error");
      return false;
    }
  }

  // État chargé, ou rechargé si le premier chargement a échoué (baguette pas encore prête)
  async ready() {
    return this.state !== null || (await this.load());
  }

  async chooseEntry(entryId) {
    this.entryId = entryId;
    this.selected = null;
    this.analysis = null;
    // Les cibles et la photo de l'autre baguette ne doivent plus servir, même si le chargement échoue
    this.state = null;
    this.$("targets").replaceChildren();
    this.$("count-label").textContent = "";
    this.$("no-target").hidden = true;
    this.$("age").textContent = "";
    this.$("photo").removeAttribute("src");
    this.$("empty").hidden = false;
    this.renderResult();
    this.draw();
    await this.refreshPhoto();
  }

  // Consigne de placement : la cible choisie, ou quoi faire d'abord
  placeHint() {
    const t = this.target(this.selected);
    if (!t) return "Ajoute une cible, puis clique dessus dans la photo.";
    return t.x === null ? `Clique sur ${t.name} dans la photo.` : `Clique dans la photo pour déplacer ${t.name}.`;
  }

  async refreshPhoto() {
    this.analysis = null;
    this.renderResult();
    this.$("wrap").classList.add("loading");
    this.say("Photo de la caméra…");
    try {
      // Les options et les cibles peuvent avoir changé depuis un autre onglet.
      // « Photo » reste accessible quand la vision est coupée et permet de la réactiver.
      if (!(await this.load())) return;
      const { image, age_ms } = await this.ws("photo");
      this.showPhoto(image, age_ms);
      this.say(this.placeHint());
    } catch (err) {
      if (!(err instanceof StaleAnswer)) this.say(`Caméra indisponible : ${err.message}`, "error");
    } finally {
      this.$("wrap").classList.remove("loading");
    }
  }

  // ageMs : âge de la photo quand la caméra l'a rendue (null si elle ne le donne pas) ;
  // waitedMs : attente depuis (Jev), pour la dater de sa prise
  showPhoto(base64, ageMs, waitedMs = 0) {
    const img = this.$("photo");
    img.onload = () => this.draw();
    img.src = `data:image/jpeg;base64,${base64}`;
    const taken = new Date(Date.now() - (ageMs ?? 0) - waitedMs);
    const time = taken.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
    const old = ageMs > PHOTO_STALE_MS;
    const age = this.$("age");
    age.textContent = `photo de ${time}${old ? " (photo ancienne : la caméra ne range plus de photo)" : ""}`;
    age.classList.toggle("old", old);
    this.$("empty").hidden = true;
  }

  // Pas de vérification sans vision, ni de vérification ou de photo pendant une analyse
  updateAnalyze() {
    const button = this.$("analyze");
    const off = this.state !== null && !this.state.vision_enabled;
    button.disabled = this.busy || off;
    this.$("refresh").disabled = this.busy; // sa photo remplacerait celle de l'analyse sous ses cadres
    button.title = off ? "Vision non configurée : adresse de la caméra et clé TypeSafe, dans les options de l'intégration" : "";
  }

  overlay(count, text) {
    const overlay = this.$("overlay");
    overlay.hidden = text === null;
    this.$("count").textContent = count ?? "";
    this.$("count").classList.toggle("spin", count === null);
    this.$("overlay-text").textContent = text ?? "";
  }

  async analyze() {
    if (this.busy) return;
    this.busy = true;
    this.updateAnalyze();
    try {
      if (!(await this.load())) return;
      if (!this.state.vision_enabled) {
        this.say("Vision non configurée : renseigne l'adresse de la caméra et la clé TypeSafe dans les options de l'intégration.", "error");
        return;
      }
      const entryId = this.entryId;
      this.analysis = null;
      this.renderResult();
      this.draw();
      for (let left = COUNTDOWN; left > 0; left--) {
        this.overlay(left, "Pointe une cible, bras tendu");
        await new Promise((resolve) => setTimeout(resolve, 1000));
      }
      if (entryId !== this.entryId || !this.state) throw new StaleAnswer(); // baguette changée pendant le compte
      this.overlay(null, "Analyse par Jev…");
      this.analysis = await this.ws("analyze");
      this.showPhoto(this.analysis.image, this.analysis.age_ms, this.analysis.latency_ms);
      this.renderResult();
      this.say(this.placeHint());
    } catch (err) {
      if (!(err instanceof StaleAnswer)) this.say(`Analyse impossible : ${err.message}`, "error");
    } finally {
      this.overlay(null, null);
      this.busy = false;
      this.updateAnalyze();
    }
  }

  async place(event) {
    if (!this.selected) {
      this.say("Choisis d'abord une cible dans la liste.", "error");
      return;
    }
    const img = this.$("photo");
    if (!img.naturalWidth) {
      this.say("Attends la photo de la caméra.", "error");
      return;
    }
    // Par rapport à la photo : hors de [0, 1] dans la marge, pour un objet hors champ
    const box = img.getBoundingClientRect();
    const x = (event.clientX - box.left) / box.width;
    const y = (event.clientY - box.top) / box.height;
    try {
      const placed = this.selected;
      ({ targets: this.state.targets } = await this.ws("update_target", { key: placed, x, y }));
      const outside = x < 0 || x > 1 || y < 0 || y > 1;
      const name = this.target(placed)?.name ?? placed;
      // Cible suivante à placer, pour enchaîner les clics
      const next = this.state.targets.find((t) => t.x === null);
      if (next) this.selected = next.key;
      this.renderTargets();
      this.draw();
      this.say(`${name} placée${outside ? " hors champ" : ""}.${next ? ` Clique maintenant sur ${next.name}.` : ""}`, "success");
    } catch (err) {
      if (!(err instanceof StaleAnswer)) this.say(err.message, "error");
    }
  }

  async addTarget() {
    const input = this.$("name");
    if (!input.value.trim() || !(await this.ready())) {
      input.focus();
      return;
    }
    try {
      const result = await this.ws("add_target", { name: input.value, level: this.$("level").value || null });
      this.state.targets = result.targets;
      this.selected = result.key;
      input.value = "";
      this.renderTargets();
      this.draw();
      this.say(`${this.target(result.key).name} ajoutée : clique dessus dans la photo.`);
    } catch (err) {
      if (!(err instanceof StaleAnswer)) this.say(err.message, "error");
    }
  }

  async setLevel(key, value) {
    try {
      ({ targets: this.state.targets } = await this.ws("update_target", { key, level: value || null }));
      this.renderTargets();
      this.draw();
    } catch (err) {
      if (err instanceof StaleAnswer) return;
      this.say(err.message, "error");
      await this.load();
    }
  }

  async deleteTarget(key) {
    const target = this.target(key);
    if (!target) {
      await this.load();
      return;
    }
    if (!confirm(`Supprimer la cible ${target.name} ?`)) return;
    try {
      ({ targets: this.state.targets } = await this.ws("delete_target", { key }));
    } catch (err) {
      if (err instanceof StaleAnswer) return;
      this.say(err.message, "error");
      await this.load();
      return;
    }
    if (this.selected === key) this.selected = this.state.targets[0]?.key ?? null;
    this.renderTargets();
    this.draw();
    this.say(this.placeHint());
  }

  select(key) {
    this.selected = key;
    this.renderTargets();
    this.draw();
    this.say(this.placeHint());
  }

  // Surfaces proposées : celles de l'intégration, ou aucune en particulier
  levels() {
    return ["", ...this.state.levels];
  }

  renderEntries() {
    const select = this.$("entry");
    select.hidden = this.state.entries.length < 2;
    select.replaceChildren(...this.state.entries.map((e) => new Option(e.title, e.entry_id, false, e.entry_id === this.entryId)));
  }

  renderLevels() {
    const select = this.$("level");
    const kept = select.value;
    select.replaceChildren(...this.levels().map((value) => new Option(level(value).label, value, false, value === kept)));
  }

  renderTargets() {
    const list = this.$("targets");
    const targets = this.state.targets;
    const placed = targets.filter((t) => t.x !== null).length;
    this.$("count-label").textContent = targets.length ? `${placed} / ${targets.length} placées` : "";
    this.$("no-target").hidden = targets.length > 0;
    list.replaceChildren(
      ...targets.map((t) => {
        const row = document.createElement("li");
        const lvl = level(t.level);
        row.className = `target${t.key === this.selected ? " selected" : ""}${t.x === null ? " todo" : ""}`;
        row.dataset.key = t.key;
        row.innerHTML = `
          <span class="avatar"><ha-icon></ha-icon></span>
          <span class="names"><span class="name"></span>
            <span class="sub"><code class="key" title="Attribut « cible » de l'événement Sort"></code><span class="badge">à placer</span></span></span>
          <label class="chip" title="Surface visée, d'après l'inclinaison de la baguette"><ha-icon></ha-icon><select></select></label>
          <button class="icon delete" title="Supprimer"><ha-icon icon="mdi:delete-outline"></ha-icon></button>`;
        row.querySelector(".avatar ha-icon").setAttribute("icon", t.x === null ? "mdi:map-marker-question-outline" : lvl.icon);
        // Au clavier, le nom choisit la cible (la surface et la corbeille ont leurs propres contrôles)
        const names = row.querySelector(".names");
        names.tabIndex = 0;
        names.setAttribute("role", "button");
        if (t.key === this.selected) names.setAttribute("aria-current", "true");
        row.querySelector(".name").textContent = t.name;
        row.querySelector(".key").textContent = t.key;
        const chip = row.querySelector(".chip");
        chip.dataset.level = t.level || "";
        chip.querySelector("ha-icon").setAttribute("icon", lvl.chip);
        const select = chip.querySelector("select");
        for (const value of this.levels()) select.add(new Option(level(value).label, value, false, (t.level || "") === value));
        chip.onclick = (e) => e.stopPropagation();
        select.onchange = () => this.setLevel(t.key, select.value);
        row.querySelector(".delete").onclick = (e) => {
          e.stopPropagation();
          this.deleteTarget(t.key);
        };
        row.onclick = () => this.select(t.key);
        names.onkeydown = (e) => {
          if (e.key !== "Enter" && e.key !== " ") return;
          e.preventDefault(); // Espace ferait défiler la page
          this.select(t.key);
          // La liste est redessinée : le focus revient sur la même cible
          list.querySelector(`li[data-key="${CSS.escape(t.key)}"] .names`)?.focus();
        };
        return row;
      }),
    );
  }

  // Carte du résultat de « Vérifier la visée »
  renderResult() {
    const card = this.$("result");
    const a = this.analysis;
    card.hidden = !a;
    if (!a) return;
    const name = (key) => this.target(key)?.name ?? key;
    card.className = `card result ${a.target ? "ok" : "none"}`;
    this.$("result-icon").setAttribute("icon", a.target ? "mdi:target" : "mdi:crosshairs-question");
    this.$("result-title").textContent = a.target ? name(a.target) : "Aucune cible";
    const details = [];
    if (a.target && a.gaps?.[a.target] !== undefined) details.push(`écart ${Math.round(a.gaps[a.target])}°`);
    if (a.angle !== null) details.push(`bras à ${Math.round(a.angle)}°`);
    if (!a.target && a.refusal) details.push(a.refusal);
    // L'inclinaison vient de la baguette : ici, toutes les cibles sont comparées, quelle que soit leur surface
    details.push("sans la baguette : toutes surfaces");
    this.$("result-sub").textContent = details.join(" · ");
    const percent = (v) => (v === null || v === undefined ? "" : ` ${Math.round(v * 100)} %`);
    const chips = [];
    if (a.shoulders) chips.push(["shoulders", `épaules${percent(a.shoulders_confidence)}`]);
    if (a.head) chips.push(["head", `tête${percent(a.head_confidence)}`]);
    if (a.hand) chips.push(["hand", `main${percent(a.hand_confidence)}`]);
    if (a.latency_ms !== undefined) chips.push(["", `Jev ${a.latency_ms} ms`]);
    for (const [key, gap] of Object.entries(a.gaps || {}).sort((x, y) => x[1] - y[1])) {
      if (key !== a.target) chips.push(["", `${name(key)} ${Math.round(gap)}°`]);
    }
    this.$("result-chips").replaceChildren(
      ...chips.map(([kind, text]) => {
        const chip = document.createElement("span");
        chip.className = `pill ${kind}`;
        chip.textContent = text;
        return chip;
      }),
    );
  }

  draw() {
    const img = this.$("photo");
    const svg = this.$("layer");
    // Le calque couvre la photo et sa marge : (ox, oy) est le coin de la photo dans le calque
    const [frame, photo] = [svg.getBoundingClientRect(), img.getBoundingClientRect()];
    const [W, H] = [frame.width, frame.height];
    const [ox, oy, w, h] = [photo.left - frame.left, photo.top - frame.top, photo.width, photo.height];
    svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
    svg.replaceChildren();
    if (!this.state || !w || !img.naturalWidth) return;
    const el = (name, attrs, parent = svg) => {
      const node = document.createElementNS(SVG, name);
      for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
      parent.appendChild(node);
      return node;
    };

    // Analyse dessous : épaules, main, bras et cône de tolérance
    const a = this.analysis;
    if (a) {
      for (const [box, kind] of [[a.shoulders, "shoulders"], [a.head, "head"], [a.hand, "hand"]]) {
        if (box) {
          el("rect", { class: `box ${kind}`, x: ox + box.left * w, y: oy + box.top * h, width: (box.right - box.left) * w, height: (box.bottom - box.top) * h, rx: 4 });
        }
      }
      // Le bras part de la tête, sinon du centre des épaules (VisionResult.origin)
      const from = a.head || a.shoulders;
      if (from && a.angle !== null) {
        const sx = ox + ((from.left + from.right) / 2) * w;
        const sy = oy + ((from.top + from.bottom) / 2) * h;
        const len = Math.hypot(W, H);
        const tol = a.tolerance ?? this.state.tolerance;
        const point = (deg, r = len) => {
          const rad = (deg * Math.PI) / 180;
          return `${sx + Math.cos(rad) * r} ${sy + Math.sin(rad) * r}`;
        };
        // Bords du cône allongés pour que sa base passe à len dans l'axe : il couvre tout le calque
        // (tolérance de 60° au plus, donc cos >= 0,5) ; le calque coupe ce qui dépasse
        const edge = len / Math.cos((tol * Math.PI) / 180);
        el("path", { class: "cone", d: `M ${sx} ${sy} L ${point(a.angle - tol, edge)} L ${point(a.angle + tol, edge)} Z` });
        el("path", { class: "arm", d: `M ${sx} ${sy} L ${point(a.angle)}` });
        el("circle", { class: "origin", cx: sx, cy: sy, r: 4 });
      }
    }

    // Cibles par-dessus, la choisie en dernier pour rester lisible
    const targets = this.state.targets.filter((t) => t.x !== null);
    targets.sort((t, u) => (t.key === this.selected) - (u.key === this.selected));
    for (const t of targets) {
      const outside = t.x < 0 || t.x > 1 || t.y < 0 || t.y > 1;
      const state = t.key === a?.target ? "chosen" : t.key === this.selected ? "selected" : "";
      const g = el("g", { class: `marker ${state}${outside ? " outside" : ""}` });
      // Au-delà de la marge (placée hors du panneau) : au bord du calque, dans sa direction
      const [x, y] = [Math.min(Math.max(ox + t.x * w, 7), W - 7), Math.min(Math.max(oy + t.y * h, 7), H - 7)];
      el("circle", { class: "dot", cx: x, cy: y, r: 7 }, g);
      const pill = el("rect", { class: "pill", height: 24, rx: 12 }, g);
      const text = el("text", { y: y + 4.5 }, g);
      text.textContent = t.name;
      // Étiquette à droite du point, ou à gauche si elle sortirait du calque
      const width = text.getComputedTextLength() + 20;
      const right = x + 12 + width <= W;
      const left = right ? x + 12 : x - 12 - width;
      const top = Math.min(Math.max(y - 12, 2), H - 26);
      pill.setAttribute("x", left);
      pill.setAttribute("y", top);
      pill.setAttribute("width", width);
      text.setAttribute("x", left + 10);
      text.setAttribute("y", top + 16.5);
    }
  }

  render() {
    this.shadowRoot.innerHTML = `
      <style>
        :host { display:block; min-height:100vh; background:var(--primary-background-color); color:var(--primary-text-color);
          font-family:var(--ha-font-family-body, var(--paper-font-body1_-_font-family, Roboto, sans-serif)); font-size:14px;
          --radius:var(--ha-card-border-radius, 12px); --accent:var(--primary-color, #03a9f4); --ok:var(--success-color, #43a047);
          --warn:var(--warning-color, #ffa600); --bad:var(--error-color, #db4437); --muted:var(--secondary-text-color, #727272);
          --line:var(--divider-color, rgba(127,127,127,.25)); --card:var(--card-background-color, #fff);
          --soft:var(--secondary-background-color, rgba(127,127,127,.08)); }
        * { box-sizing:border-box; }
        [hidden] { display:none !important; }
        ha-icon { --mdc-icon-size:20px; display:inline-flex; }
        .toolbar { height:56px; display:flex; align-items:center; gap:12px; padding:0 16px; border-bottom:1px solid var(--line);
          background:var(--app-header-background-color, var(--card)); color:var(--app-header-text-color, var(--primary-text-color)); }
        .toolbar h1 { margin:0; font-size:20px; font-weight:400; flex:1; display:flex; align-items:center; gap:10px; }
        .toolbar h1 ha-icon { color:var(--accent); --mdc-icon-size:24px; }
        main { max-width:1400px; margin:0 auto; padding:16px; display:grid; grid-template-columns:minmax(0, 1fr) 360px; gap:16px; align-items:start; }
        @media (max-width: 960px) { main { grid-template-columns:minmax(0, 1fr); } }
        .column { display:grid; gap:16px; min-width:0; }
        .card { background:var(--card); border-radius:var(--radius); border:var(--ha-card-border-width, 1px) solid var(--ha-card-border-color, var(--line));
          box-shadow:var(--ha-card-box-shadow, none); padding:16px; min-width:0; }
        .head { display:flex; align-items:center; gap:10px; margin-bottom:12px; flex-wrap:wrap; }
        .head .title { flex:1; display:flex; align-items:center; gap:8px; font-size:16px; font-weight:500; min-width:0; }
        .head .title ha-icon { color:var(--muted); }
        @media (max-width: 600px) { .head .title { flex-basis:100%; } main { padding:8px; gap:8px; } .card { padding:12px; } }
        .muted { color:var(--muted); font-size:13px; font-weight:400; }
        button, select, input { font:inherit; color:inherit; }
        button { display:inline-flex; align-items:center; gap:6px; height:36px; padding:0 14px; border-radius:18px; cursor:pointer;
          border:1px solid var(--line); background:transparent; white-space:nowrap; }
        button:hover:not(:disabled) { background:var(--soft); }
        button:disabled { opacity:.45; cursor:default; }
        button.primary { background:var(--accent); border-color:var(--accent); color:var(--text-primary-color, #fff); }
        button.primary:hover:not(:disabled) { filter:brightness(1.08); background:var(--accent); }
        button.icon { width:36px; padding:0; justify-content:center; border-color:transparent; color:var(--muted); }
        input, select { height:36px; padding:0 12px; border-radius:8px; border:1px solid var(--line); background:var(--soft); min-width:0; }
        input:focus, select:focus, button:focus-visible, .names:focus-visible { outline:2px solid var(--accent); outline-offset:1px; }
        #menu { width:40px; height:40px; padding:0; justify-content:center; border:none; }

        /* Photo et marge hachurée « hors champ » (bornes des cibles : MARGIN_X et MARGIN_Y de panel.py) */
        #wrap { position:relative; line-height:0; padding:9%; border-radius:10px; border:1px dashed var(--line); overflow:hidden;
          background:repeating-linear-gradient(45deg, transparent 0 9px, var(--line) 9px 10px), var(--soft); }
        .margin-label { position:absolute; top:8px; left:12px; line-height:1; font-size:12px; color:var(--muted); letter-spacing:.02em; }
        #age.old { color:var(--bad); }
        /* Cadre de la taille exacte de la photo : le cadre vide et le compte à rebours la couvrent, sans la marge */
        #frame { position:relative; }
        #photo { display:block; width:100%; aspect-ratio:auto 4 / 3; border-radius:6px; background:#111;
          box-shadow:0 0 0 1px rgba(0,0,0,.25), 0 4px 18px rgba(0,0,0,.25); }
        #photo:not([src]) { visibility:hidden; }
        .empty { position:absolute; inset:0; display:flex; flex-direction:column; align-items:center; justify-content:center; gap:10px;
          line-height:1.4; color:var(--muted); border-radius:6px; background:var(--card); }
        .empty ha-icon { --mdc-icon-size:40px; opacity:.6; }
        #wrap.loading #photo { filter:saturate(.4) brightness(.7); transition:filter .2s; }
        #layer { position:absolute; inset:0; width:100%; height:100%; cursor:crosshair; }
        #overlay { position:absolute; inset:0; z-index:1; display:flex; flex-direction:column; align-items:center; justify-content:center; gap:14px;
          line-height:1.2; border-radius:6px; background:rgba(0,0,0,.45); color:#fff; pointer-events:none; }
        #count { width:84px; height:84px; border-radius:50%; display:flex; align-items:center; justify-content:center; font-size:42px;
          font-weight:300; border:3px solid rgba(255,255,255,.85); }
        #count.spin { border-color:rgba(255,255,255,.25); border-top-color:#fff; animation:spin .8s linear infinite; }
        @keyframes spin { to { transform:rotate(360deg); } }
        #overlay-text { font-size:16px; text-shadow:0 1px 3px rgba(0,0,0,.6); }

        /* Calque : cibles et analyse */
        .marker .dot { fill:#fff; stroke:rgba(0,0,0,.55); stroke-width:2; }
        .marker .pill { fill:rgba(20,20,20,.72); }
        .marker text { fill:#fff; font-size:13px; font-weight:500; }
        .marker.outside .dot { fill:none; stroke:#fff; stroke-width:2; stroke-dasharray:3 3; }
        .marker.selected .dot { fill:var(--accent); stroke:#fff; }
        .marker.selected .pill { fill:var(--accent); }
        .marker.selected text { fill:var(--text-primary-color, #fff); }
        .marker.chosen .dot { fill:var(--ok); stroke:#fff; }
        .marker.chosen .pill { fill:var(--ok); }
        .box { fill:none; stroke-width:2.5; }
        .box.shoulders { stroke:#3fb7a3; }
        .box.head { stroke:#78aaff; }
        .box.hand { stroke:#ff6b6b; }
        .cone { fill:rgba(255,107,107,.16); stroke:rgba(255,107,107,.5); stroke-width:1; stroke-dasharray:5 5; }
        .arm { stroke:#ff6b6b; stroke-width:3; stroke-linecap:round; }
        .origin { fill:#ff6b6b; stroke:#fff; stroke-width:2; }

        .hint { display:flex; align-items:center; gap:8px; margin-top:12px; padding:10px 12px; border-radius:8px; background:var(--soft);
          color:var(--muted); line-height:1.4; }
        .hint ha-icon { flex:none; }
        .hint.error { color:var(--bad); background:color-mix(in srgb, var(--bad) 10%, transparent); }
        .hint.success { color:var(--ok); background:color-mix(in srgb, var(--ok) 10%, transparent); }

        /* Résultat de la vérification */
        .result { display:flex; align-items:center; gap:14px; flex-wrap:wrap; }
        .result .badge-icon { width:44px; height:44px; flex:none; border-radius:50%; display:flex; align-items:center; justify-content:center; }
        .result.ok .badge-icon { color:var(--ok); background:color-mix(in srgb, var(--ok) 15%, transparent); }
        .result.none .badge-icon { color:var(--warn); background:color-mix(in srgb, var(--warn) 15%, transparent); }
        .result .text { flex:1; min-width:160px; }
        #result-title { font-size:16px; font-weight:500; }
        #result-sub { color:var(--muted); font-size:13px; margin-top:2px; }
        #result-chips { display:flex; flex-wrap:wrap; gap:6px; flex-basis:100%; }
        .pill { font-size:12px; padding:4px 10px; border-radius:12px; background:var(--soft); color:var(--muted); white-space:nowrap; }
        .pill.shoulders { color:#2e9c8a; background:rgba(63,183,163,.14); }
        .pill.head { color:#4f86e0; background:rgba(120,170,255,.14); }
        .pill.hand { color:#e05555; background:rgba(255,107,107,.14); }

        /* Liste des cibles */
        ul { list-style:none; padding:0; margin:0; display:grid; gap:6px; }
        li.target { display:grid; grid-template-columns:auto minmax(0, 1fr) auto auto; align-items:center; gap:8px; padding:8px 6px 8px 8px;
          border-radius:10px; border:1px solid var(--line); cursor:pointer; transition:background .15s, border-color .15s; }
        li.target:hover { background:var(--soft); }
        li.target.selected { border-color:var(--accent); background:color-mix(in srgb, var(--accent) 9%, transparent); }
        .avatar { width:34px; height:34px; border-radius:50%; display:flex; align-items:center; justify-content:center; background:var(--soft); color:var(--muted); }
        li.selected .avatar { background:var(--accent); color:var(--text-primary-color, #fff); }
        li.todo .avatar { color:var(--warn); }
        .names { min-width:0; display:flex; flex-direction:column; }
        .name { font-weight:500; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
        .sub { display:flex; align-items:center; gap:6px; min-width:0; }
        .key { font-family:var(--code-font-family, monospace); font-size:11px; color:var(--muted); overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
        .badge { display:none; flex:none; font-size:10px; line-height:16px; padding:0 6px; border-radius:10px; color:var(--warn); background:color-mix(in srgb, var(--warn) 14%, transparent); }
        li.todo .badge { display:inline; }
        .chip { position:relative; display:inline-flex; align-items:center; gap:4px; height:28px; padding:0 4px 0 8px; border-radius:14px;
          background:var(--soft); color:var(--muted); font-size:12px; }
        .chip ha-icon { --mdc-icon-size:16px; }
        .chip select { appearance:none; -webkit-appearance:none; height:28px; border:none; background:transparent; padding:0 6px 0 0; font-size:12px; cursor:pointer; color:inherit; }
        .chip[data-level="plafond"] { color:#7b61c9; background:rgba(123,97,201,.14); }
        .chip[data-level="mur"] { color:#1d8f84; background:rgba(29,143,132,.14); }
        .chip[data-level="sol"] { color:#b26a10; background:rgba(178,106,16,.14); }
        .delete:hover { color:var(--bad); }
        .empty-list { text-align:center; color:var(--muted); padding:18px 8px; line-height:1.5; }
        .add { display:grid; grid-template-columns:minmax(0, 1fr) auto auto; gap:6px; margin-top:12px; }
        .add button { padding:0 12px; }
        details { margin-top:14px; color:var(--muted); font-size:13px; line-height:1.55; }
        summary { cursor:pointer; display:flex; align-items:center; gap:6px; list-style:none; }
        summary::-webkit-details-marker { display:none; }
        details p { margin:8px 0 0; }
        code { font-family:var(--code-font-family, monospace); }
      </style>
      <div class="toolbar">
        <button id="menu" title="Menu" hidden><ha-icon icon="mdi:menu"></ha-icon></button>
        <h1><ha-icon icon="mdi:magic-staff"></ha-icon>Baguette</h1>
        <select id="entry" title="Baguette" hidden></select>
      </div>
      <main>
        <div class="column">
          <section class="card">
            <div class="head">
              <div class="title"><ha-icon icon="mdi:cctv"></ha-icon>Caméra du plafond <span id="age" class="muted"></span></div>
              <button id="refresh" title="Nouvelle photo"><ha-icon icon="mdi:refresh"></ha-icon>Photo</button>
              <button id="analyze" class="primary"><ha-icon icon="mdi:magic-staff"></ha-icon>Vérifier la visée</button>
            </div>
            <div id="wrap">
              <span class="margin-label">hors champ</span>
              <div id="frame">
                <img id="photo" alt="Photo de la caméra au plafond">
                <div id="empty" class="empty"><ha-icon icon="mdi:camera-outline"></ha-icon>Photo de la caméra…</div>
                <div id="overlay" hidden><div id="count"></div><div id="overlay-text"></div></div>
              </div>
              <svg id="layer"></svg>
            </div>
            <div id="hint" class="hint"><ha-icon id="hint-icon" icon="mdi:cursor-default-click-outline"></ha-icon><span id="status" aria-live="polite"></span></div>
          </section>
        </div>
        <div class="column">
          <section id="result" class="card result" hidden>
            <span class="badge-icon"><ha-icon id="result-icon"></ha-icon></span>
            <div class="text"><div id="result-title"></div><div id="result-sub"></div></div>
            <div id="result-chips"></div>
          </section>
          <aside class="card">
            <div class="head"><div class="title">Cibles <span id="count-label" class="muted"></span></div></div>
            <ul id="targets" aria-label="Cibles"></ul>
            <div id="no-target" class="empty-list" hidden>Ajoute ta première cible : son nom ci-dessous, puis un clic sur l'objet dans la photo.</div>
            <div class="add">
              <input id="name" placeholder="Prise TV" aria-label="Nom de la nouvelle cible">
              <select id="level" title="Surface visée : toutes, ou celle qui départage deux cibles alignées"></select>
              <button id="add" class="primary" title="Ajouter la cible"><ha-icon icon="mdi:plus"></ha-icon></button>
            </div>
            <details>
              <summary><ha-icon icon="mdi:help-circle-outline"></ha-icon>Aide</summary>
              <p>Choisis une cible dans la liste, puis clique sur l'objet dans la photo. La cible suivante à placer est choisie toute seule.</p>
              <p>Hors champ : clique dans la marge hachurée, là où serait l'objet ; elle prolonge la photo d'environ 11 % sur les côtés et 15 % en haut et en bas. Caché derrière un meuble : clique là où il se trouve sur la photo.</p>
              <p>La surface (sol, mur, plafond) départage deux cibles l'une au-dessus de l'autre, d'après l'inclinaison de la baguette. Bras levé vers la caméra, seules les cibles « plafond » comptent. « Vérifier la visée » se fait sans la baguette : toutes les surfaces y comptent.</p>
              <p>La clé sous le nom devient l'attribut <code>cible</code> de l'événement Sort : le nom en minuscules, sans accents, espaces et ponctuation remplacés par _ (« Prise TV » → <code>prise_tv</code>).</p>
            </details>
          </aside>
        </div>
      </main>`;
    this.$("menu").hidden = !this._narrow;
    this.$("menu").onclick = () => this.dispatchEvent(new Event("hass-toggle-menu", { bubbles: true, composed: true }));
    this.$("entry").onchange = (e) => this.chooseEntry(e.target.value);
    this.$("layer").addEventListener("click", (e) => this.place(e));
    this.$("refresh").onclick = () => this.refreshPhoto();
    this.$("analyze").onclick = () => this.analyze();
    this.$("add").onclick = () => this.addTarget();
    this.$("name").addEventListener("keydown", (e) => e.key === "Enter" && this.addTarget());
    new ResizeObserver(() => this.draw()).observe(this.$("photo"));
  }
}

customElements.define("cornelius-wand-panel", CorneliusWandPanel);
