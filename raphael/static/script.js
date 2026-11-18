const sidebarToggle = document.getElementById("sidebar-toggle");
const sidebar = document.getElementById("sidebar");
const sidebarOverlay = document.getElementById("sidebar-overlay");
const newChatBtn = document.getElementById("new-chat");
const conversationListEl = document.getElementById("conversation-list");
const form = document.getElementById("chat-form");
const input = document.getElementById("chat-input");
const messagesEl = document.getElementById("messages");
const voiceToggleBtn = document.getElementById("voice-toggle");
const attachmentInput = document.getElementById("attachment-input");
const attachmentToggle = document.getElementById("attachment-toggle");
const attachmentPreview = document.getElementById("attachment-preview");
let selectedAttachments = [];

let currentConversationId = null;
let conversations = [];
let voiceMode = false;
let recognition = null;
let recognitionActive = false;
let suspendAutoListen = false;
let assistantSpeaking = false;

const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || "";

const GREETING = "Hola, soy Raphael. Cuéntame qué tienes entre manos hoy y vemos cómo resolverlo.";
const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
const canRecognizeSpeech = Boolean(SpeechRecognition);
const canSpeak = "speechSynthesis" in window;

if (window.lucide) {
  window.lucide.createIcons();
  document.body.classList.add("icons-ready");
}

/* ---------- Sidebar (móvil) ---------- */

function openSidebar() {
  sidebar.classList.add("open");
  sidebarOverlay.classList.add("open");
  sidebarToggle.setAttribute("aria-expanded", "true");
}

function closeSidebar() {
  sidebar.classList.remove("open");
  sidebarOverlay.classList.remove("open");
  sidebarToggle.setAttribute("aria-expanded", "false");
}

sidebarToggle.addEventListener("click", () => {
  sidebar.classList.contains("open") ? closeSidebar() : openSidebar();
});
sidebarOverlay.addEventListener("click", closeSidebar);

/* ---------- Mensajes ---------- */

function addMessage(role, text) {
  const div = document.createElement("div");
  div.className = `msg msg--${role}`;
  if (role === "assistant") {
    const html = DOMPurify.sanitize(marked.parse(text));
    div.innerHTML = html;
  } else {
    const p = document.createElement("p");
    p.textContent = text;
    div.appendChild(p);
  }
  messagesEl.appendChild(div);
  messagesEl.scrollTop = messagesEl.scrollHeight;
  return div;
}

function addLoadingMessage() {
  const div = document.createElement("div");
  div.className = "msg msg--assistant msg--loading";
  div.innerHTML =
    '<div class="tesseract-loader" aria-label="Raphael está pensando">' +
    '<div class="tess-ring tess-ring--a"></div>' +
    '<div class="tess-ring tess-ring--b"></div>' +
    '<div class="tess-ring tess-ring--c"></div>' +
    "</div>";
  messagesEl.appendChild(div);
  messagesEl.scrollTop = messagesEl.scrollHeight;
  return div;
}

function renderGreeting() {
  messagesEl.innerHTML = "";
  addMessage("assistant", GREETING);
}

/* ---------- Voz ---------- */

function updateVoiceUi(state = "idle") {
  document.body.classList.toggle("voice-mode-on", voiceMode);
  document.body.classList.toggle("voice-listening", voiceMode && state === "listening");
  document.body.classList.toggle("voice-speaking", voiceMode && state === "speaking");

  voiceToggleBtn.classList.toggle("active", voiceMode);
  voiceToggleBtn.setAttribute("aria-pressed", String(voiceMode));
  voiceToggleBtn.setAttribute("aria-label", voiceMode ? "Desactivar voz" : "Activar voz");
  voiceToggleBtn.title = voiceMode ? "Desactivar voz" : "Activar voz";
}

function plainTextForSpeech(markdownText) {
  const div = document.createElement("div");
  div.innerHTML = DOMPurify.sanitize(marked.parse(markdownText));
  return div.textContent.replace(/\s+/g, " ").trim();
}

function chooseSpanishVoice() {
  if (!canSpeak) return null;
  return window.speechSynthesis
    .getVoices()
    .find((voice) => voice.lang.toLowerCase().startsWith("es"));
}

function stopListening() {
  if (!recognition || !recognitionActive) return;
  try {
    recognition.stop();
  } catch (err) {
    recognitionActive = false;
  }
}

function startListening() {
  if (!voiceMode || !canRecognizeSpeech || recognitionActive) return;
  if (suspendAutoListen || assistantSpeaking || input.disabled) return;

  if (!recognition) {
    recognition = new SpeechRecognition();
    recognition.lang = "es-ES";
    recognition.continuous = false;
    recognition.interimResults = true;

    recognition.onstart = () => {
      recognitionActive = true;
      updateVoiceUi("listening");
    };

    recognition.onresult = (event) => {
      let finalTranscript = "";
      let interimTranscript = "";

      for (let i = event.resultIndex; i < event.results.length; i += 1) {
        const transcript = event.results[i][0].transcript.trim();
        if (event.results[i].isFinal) {
          finalTranscript += `${transcript} `;
        } else {
          interimTranscript += transcript;
        }
      }

      if (interimTranscript) {
        input.value = interimTranscript;
      }

      const cleanTranscript = finalTranscript.trim();
      if (cleanTranscript) {
        input.value = "";
        suspendAutoListen = true;
        stopListening();
        sendMessage(cleanTranscript, { fromVoice: true });
      }
    };

    recognition.onerror = (event) => {
      recognitionActive = false;
      if (event.error === "not-allowed" || event.error === "service-not-allowed") {
        voiceMode = false;
        addMessage("error", "El navegador no tiene permiso para usar el micrófono.");
      } else if (voiceMode && event.error !== "no-speech" && event.error !== "aborted") {
        addMessage("error", `No pude escuchar bien: ${event.error}.`);
      }
      updateVoiceUi(voiceMode ? "idle" : "off");
    };

    recognition.onend = () => {
      recognitionActive = false;
      if (voiceMode && !suspendAutoListen && !assistantSpeaking && !input.disabled) {
        window.setTimeout(startListening, 250);
      } else {
        updateVoiceUi(assistantSpeaking ? "speaking" : "idle");
      }
    };
  }

  try {
    recognition.start();
  } catch (err) {
    recognitionActive = false;
  }
}

function speakAssistantReply(text) {
  if (!voiceMode || !canSpeak) return;

  const cleanText = plainTextForSpeech(text);
  if (!cleanText) return;

  window.speechSynthesis.cancel();
  const utterance = new SpeechSynthesisUtterance(cleanText);
  const voice = chooseSpanishVoice();
  utterance.lang = "es-ES";
  utterance.rate = 0.98;
  utterance.pitch = 1.02;
  if (voice) utterance.voice = voice;

  assistantSpeaking = true;
  suspendAutoListen = true;
  updateVoiceUi("speaking");

  utterance.onstart = () => {
    assistantSpeaking = true;
    suspendAutoListen = true;
    updateVoiceUi("speaking");
  };

  utterance.onend = () => {
    assistantSpeaking = false;
    suspendAutoListen = false;
    updateVoiceUi("idle");
    startListening();
  };

  utterance.onerror = () => {
    assistantSpeaking = false;
    suspendAutoListen = false;
    updateVoiceUi("idle");
    startListening();
  };

  window.speechSynthesis.speak(utterance);
}

function activateVoiceMode() {
  if (!canRecognizeSpeech) {
    addMessage("error", "Tu navegador no soporta reconocimiento de voz en esta vista.");
    return;
  }

  voiceMode = true;
  suspendAutoListen = false;
  updateVoiceUi("idle");
  startListening();
}

function deactivateVoiceMode() {
  voiceMode = false;
  suspendAutoListen = true;
  assistantSpeaking = false;
  stopListening();
  if (canSpeak) window.speechSynthesis.cancel();
  input.value = "";
  updateVoiceUi("off");
}

voiceToggleBtn.addEventListener("click", () => {
  if (voiceMode) {
    deactivateVoiceMode();
  } else {
    activateVoiceMode();
  }
});

/* ---------- Conversaciones ---------- */

function renderConversationList() {
  conversationListEl.innerHTML = "";
  conversations.forEach((conv) => {
    const btn = document.createElement("button");
    btn.className =
      "conversation-item" + (conv.id === currentConversationId ? " active" : "");
    btn.textContent = conv.title;
    btn.addEventListener("click", () => selectConversation(conv.id));
    conversationListEl.appendChild(btn);
  });
}

async function loadConversations() {
  const res = await fetch("/api/conversations");
  const data = await res.json();
  conversations = data.conversations || [];

  if (conversations.length === 0) {
    const created = await createConversationOnServer();
    conversations = [{ id: created.id, title: created.title }];
  }

  currentConversationId = conversations[0].id;
  renderConversationList();
  await loadConversationMessages(currentConversationId);
}

async function createConversationOnServer() {
  const res = await fetch("/api/conversations", { method: "POST", headers: { "X-CSRFToken": csrfToken } });
  return res.json();
}

async function loadConversationMessages(conversationId) {
  const res = await fetch(`/api/history?conversation_id=${conversationId}`);
  const data = await res.json();
  if (data.history && data.history.length > 0) {
    messagesEl.innerHTML = "";
    data.history.forEach((m) => addMessage(m.role, m.content));
  } else {
    renderGreeting();
  }
}

async function selectConversation(conversationId) {
  currentConversationId = conversationId;
  renderConversationList();
  await loadConversationMessages(conversationId);
  closeSidebar();
}

newChatBtn.addEventListener("click", async () => {
  const created = await createConversationOnServer();
  conversations.unshift({ id: created.id, title: created.title });
  currentConversationId = created.id;
  renderConversationList();
  renderGreeting();
  closeSidebar();
  input.focus();
});

async function refreshConversationTitle() {
  const res = await fetch("/api/conversations");
  const data = await res.json();
  const updated = (data.conversations || []).find((c) => c.id === currentConversationId);
  if (updated) {
    const local = conversations.find((c) => c.id === currentConversationId);
    if (local) local.title = updated.title;
    renderConversationList();
  }
}

/* ---------- Envío de mensajes ---------- */

async function sendMessage(text, options = {}) {
  if ((!text && selectedAttachments.length === 0) || !currentConversationId) return;

  let sentSuccessfully = false;
  const attachmentNames = selectedAttachments.map((file) => file.name);
  addMessage("user", [text, ...attachmentNames.map((name) => `📎 ${name}`)].filter(Boolean).join("\n"));
  input.value = "";
  input.disabled = true;
  const loadingEl = addLoadingMessage();

  try {
    let res;
    if (selectedAttachments.length) {
      const payload = new FormData();
      payload.append("conversation_id", String(currentConversationId));
      payload.append("message", text);
      selectedAttachments.forEach((file) => payload.append("files", file, file.name));
      res = await fetch("/api/chat-with-files", {
        method: "POST",
        headers: { "X-CSRFToken": csrfToken },
        body: payload,
      });
    } else {
      res = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken },
        body: JSON.stringify({ conversation_id: currentConversationId, message: text }),
      });
    }
    const data = await res.json();
    loadingEl.remove();

    if (!res.ok) {
      addMessage("error", data.error || "Algo salió mal. Los archivos siguen adjuntos para que puedas reintentarlo.");
    } else {
      sentSuccessfully = true;
      addMessage("assistant", data.reply);
      refreshConversationTitle();
      if (options.fromVoice || voiceMode) {
        speakAssistantReply(data.reply);
      }
    }
  } catch (err) {
    loadingEl.remove();
    addMessage("error", "No se pudo conectar con Raphael. Revisa que el servidor esté corriendo.");
  } finally {
    if (sentSuccessfully) {
      selectedAttachments = [];
      attachmentInput.value = "";
      renderAttachmentPreview();
    }
    input.disabled = false;
    if (voiceMode && !assistantSpeaking) {
      suspendAutoListen = false;
      startListening();
    } else {
      input.focus();
    }
  }
}

function renderAttachmentPreview() {
  attachmentPreview.innerHTML = "";
  attachmentPreview.classList.toggle("has-files", selectedAttachments.length > 0);
  selectedAttachments.forEach((file, index) => {
    const item = document.createElement("span");
    item.className = "attachment-chip";
    const name = document.createElement("span");
    name.textContent = file.name;
    const remove = document.createElement("button");
    remove.type = "button";
    remove.textContent = "×";
    remove.setAttribute("aria-label", `Quitar ${file.name}`);
    remove.addEventListener("click", () => {
      selectedAttachments.splice(index, 1);
      renderAttachmentPreview();
    });
    item.append(name, remove);
    attachmentPreview.appendChild(item);
  });
}
attachmentToggle.addEventListener("click", () => attachmentInput.click());
attachmentInput.addEventListener("change", () => {
  const allowed = /\.(pdf|docx|txt|jpe?g|png|webp|zip|pptx|csv|xlsx)$/i;
  const incoming = Array.from(attachmentInput.files || []);
  const rejected = [];
  for (const file of incoming) {
    if (!allowed.test(file.name)) {
      rejected.push(file.name + ": formato no admitido");
      continue;
    }
    if (selectedAttachments.length >= 5) {
      rejected.push(file.name + ": máximo 5 archivos");
      continue;
    }
    if (!selectedAttachments.some((item) => item.name === file.name && item.size === file.size)) {
      selectedAttachments.push(file);
    }
  }
  attachmentInput.value = "";
  renderAttachmentPreview();
  if (rejected.length) {
    addMessage("error", "No se añadieron estos archivos: " + rejected.join("; "));
  }
});

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  const text = input.value.trim();
  if (!text && selectedAttachments.length === 0) return;
  if (voiceMode) {
    suspendAutoListen = true;
    stopListening();
  }
  await sendMessage(text);
});

loadConversations();
