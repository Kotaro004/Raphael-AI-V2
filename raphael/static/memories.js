const csrf = document.querySelector('meta[name="csrf-token"]')?.content || "";
const list = document.getElementById("memory-list");
const filter = document.getElementById("memory-filter");
const statusEl = document.getElementById("memory-status");
const proposalList = document.getElementById("proposal-list");
let memories = [];

const labels = { personal: "Personal", project: "Proyecto", contextual: "Contextual", decision: "Decisión", general: "General" };

async function api(url, options = {}) {
  const headers = { ...(options.headers || {}), "X-CSRFToken": csrf };
  if (options.body) headers["Content-Type"] = "application/json";
  const response = await fetch(url, { ...options, headers });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || `Error HTTP ${response.status}`);
  return data;
}

function showStatus(message, isError = false) {
  statusEl.textContent = message;
  statusEl.classList.toggle("is-error", isError);
}

function updateCounts() {
  document.getElementById("count-active").textContent = memories.filter(m => m.status === "active").length;
  document.getElementById("count-pending").textContent = memories.filter(m => m.status === "pending").length;
  document.getElementById("count-total").textContent = memories.length;
}

function makeButton(label, className, action) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = className;
  button.textContent = label;
  button.addEventListener("click", action);
  return button;
}

function render() {
  updateCounts();
  const selected = filter.value;
  const shown = selected === "all" ? memories : memories.filter(m => m.status === selected);
  list.replaceChildren();
  if (!shown.length) {
    const empty = document.createElement("div");
    empty.className = "memory-empty";
    empty.textContent = selected === "pending" ? "No tienes recuerdos pendientes de aprobación." : "Todavía no hay recuerdos en esta categoría.";
    list.append(empty);
    return;
  }
  for (const memory of shown) {
    const card = document.createElement("article");
    card.className = "memory-card";
    const top = document.createElement("div"); top.className = "memory-card-top";
    const category = document.createElement("span"); category.className = "memory-category"; category.textContent = labels[memory.category] || memory.category;
    const state = document.createElement("span"); state.className = "memory-state " + memory.status; state.textContent = memory.status === "active" ? "Activo" : "Pendiente";
    top.append(category, state);
    const fact = document.createElement("p"); fact.className = "memory-fact"; fact.textContent = memory.fact;
    const meta = document.createElement("p"); meta.className = "memory-meta";
    const date = memory.updated_at ? new Date(memory.updated_at).toLocaleString("es-ES") : "Fecha no disponible";
    meta.textContent = `Origen: ${memory.source} · Actualizado: ${date} · Confianza: ${Math.round(memory.confidence * 100)}%`;
    const actions = document.createElement("div"); actions.className = "memory-actions";
    actions.append(makeButton("Editar", "memory-quiet", () => editMemory(memory)));
    actions.append(makeButton("Proponer actualización", "memory-quiet", () => proposeReplacement(memory)));
    actions.append(makeButton("Historial", "memory-quiet", () => showHistory(memory)));
    actions.append(makeButton("Relacionar", "memory-quiet", () => relateMemory(memory)));
    if (memory.status === "pending") actions.append(makeButton("Aprobar", "memory-primary small", () => changeStatus(memory, "active")));
    else actions.append(makeButton("Poner en revisión", "memory-quiet", () => changeStatus(memory, "pending")));
    actions.append(makeButton("Eliminar", "memory-danger", () => deleteMemory(memory)));
    card.append(top, fact, meta, actions);
    list.append(card);
  }
}

async function loadMemories() {
  try {
    const data = await api("/api/memories");
    memories = data.memories || [];
    render();
  } catch (error) { showStatus(error.message, true); }
}

async function changeStatus(memory, status) {
  try {
    await api(`/api/memories/${memory.id}`, { method: "PATCH", body: JSON.stringify({ status }) });
    showStatus(status === "active" ? "Recuerdo aprobado." : "Recuerdo devuelto a revisión.");
    await loadMemories();
  } catch (error) { showStatus(error.message, true); }
}

async function editMemory(memory) {
  const fact = window.prompt("Editar recuerdo:", memory.fact);
  if (fact === null) return;
  const clean = fact.trim();
  if (!clean) { showStatus("El contenido no puede estar vacío.", true); return; }
  try {
    await api(`/api/memories/${memory.id}`, { method: "PATCH", body: JSON.stringify({ fact: clean }) });
    showStatus("Recuerdo actualizado.");
    await loadMemories();
  } catch (error) { showStatus(error.message, true); }
}

async function deleteMemory(memory) {
  if (!window.confirm("¿Eliminar este recuerdo de forma permanente?")) return;
  try {
    await api(`/api/memories/${memory.id}`, { method: "DELETE" });
    showStatus("Recuerdo eliminado.");
    await loadMemories();
  } catch (error) { showStatus(error.message, true); }
}



async function proposeReplacement(memory) {
  const proposed = window.prompt("Nueva versión del recuerdo:", memory.fact);
  if (proposed === null) return;
  const clean = proposed.trim();
  if (!clean || clean === memory.fact) return;
  const reason = window.prompt("¿Por qué debería actualizarse?", "El usuario ha actualizado esta información.") || "Actualización propuesta por el propietario";
  try {
    await api(`/api/memories/${memory.id}/proposals`, {
      method: "POST",
      body: JSON.stringify({ proposed_fact: clean, reason })
    });
    showStatus("Propuesta creada. El recuerdo actual sigue activo hasta que apruebes el cambio.");
    await loadProposals();
  } catch (error) { showStatus(error.message, true); }
}

async function decideProposal(proposal, decision) {
  const message = decision === "approved"
    ? "¿Aprobar la nueva versión? La anterior pasará a revisión y no se borrará."
    : "¿Rechazar esta propuesta?";
  if (!window.confirm(message)) return;
  try {
    await api(`/api/memory-proposals/${proposal.id}`, {
      method: "PATCH",
      body: JSON.stringify({ decision })
    });
    showStatus(decision === "approved" ? "Actualización aprobada. La versión anterior se conserva." : "Propuesta rechazada.");
    await loadMemories();
    await loadProposals();
  } catch (error) { showStatus(error.message, true); }
}

async function loadProposals() {
  if (!proposalList) return;
  try {
    const data = await api("/api/memory-proposals?status=pending");
    proposalList.replaceChildren();
    const proposals = data.proposals || [];
    if (!proposals.length) {
      const empty = document.createElement("div");
      empty.className = "memory-empty";
      empty.textContent = "No hay actualizaciones pendientes de aprobación.";
      proposalList.append(empty);
      return;
    }
    for (const proposal of proposals) {
      const card = document.createElement("article");
      card.className = "memory-card";
      const oldFact = document.createElement("p");
      oldFact.className = "memory-meta";
      oldFact.textContent = "Recuerdo actual";
      const oldText = document.createElement("p");
      oldText.className = "memory-fact";
      oldText.textContent = proposal.existing_fact;
      const newFact = document.createElement("p");
      newFact.className = "memory-meta";
      newFact.textContent = "Propuesta nueva";
      const newText = document.createElement("p");
      newText.className = "memory-fact";
      newText.textContent = proposal.proposed_fact;
      const reason = document.createElement("p");
      reason.className = "memory-meta";
      reason.textContent = `Motivo: ${proposal.reason} · Coincidencia léxica: ${Math.round(proposal.similarity * 100)}%`;
      const actions = document.createElement("div");
      actions.className = "memory-actions";
      actions.append(
        makeButton("Aprobar sustitución", "memory-primary small", () => decideProposal(proposal, "approved")),
        makeButton("Rechazar", "memory-danger", () => decideProposal(proposal, "rejected"))
      );
      card.append(oldFact, oldText, newFact, newText, reason, actions);
      proposalList.append(card);
    }
  } catch (error) { showStatus(error.message, true); }
}

async function showHistory(memory) {
  try {
    const data = await api("/api/memories/" + memory.id + "/history");
    const summary = (data.history || []).map(item => item.created_at + ": " + item.action).join("\n");
    window.alert(summary || "Todavía no hay eventos de auditoría para este recuerdo.");
  } catch (error) { showStatus(error.message, true); }
}
async function relateMemory(memory) {
  const target = Number(window.prompt("ID del recuerdo relacionado:"));
  if (!Number.isInteger(target) || target < 1) return;
  const relation = window.prompt("Relación: related_to, supports, contradicts, derived_from, same_topic", "related_to");
  if (!relation) return;
  try { await api("/api/memories/" + memory.id + "/relationships", { method: "POST", body: JSON.stringify({ target_memory_id: target, relation }) }); showStatus("Relación guardada."); }
  catch (error) { showStatus(error.message, true); }
}
async function reviewSemantics() {
  const fact = window.prompt("Escribe el hecho nuevo que quieres contrastar con tu memoria:");
  if (!fact || !fact.trim()) return;
  showStatus("Claude está analizando los recuerdos relacionados…");
  try { const data = await api("/api/memories/semantic-review", { method: "POST", body: JSON.stringify({ fact: fact.trim() }) }); window.alert(JSON.stringify(data.results || data, null, 2)); showStatus("Análisis terminado. Ningún recuerdo ha sido modificado."); }
  catch (error) { showStatus(error.message, true); }
}
document.getElementById("memory-form").addEventListener("submit", async event => {
  event.preventDefault();
  const fact = document.getElementById("memory-fact").value.trim();
  const category = document.getElementById("memory-category").value;
  if (!fact) return;
  try {
    await api("/api/memories", { method: "POST", body: JSON.stringify({ fact, category }) });
    document.getElementById("memory-fact").value = "";
    showStatus("Recuerdo guardado y activado.");
    filter.value = "all";
    await loadMemories();
  } catch (error) { showStatus(error.message, true); }
});
document.getElementById("semantic-review-button")?.addEventListener("click", reviewSemantics);
filter.addEventListener("change", render);
loadMemories();
