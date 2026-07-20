// Sibbu chat frontend.
//
// Talks to the Flask backend over two endpoints:
//   POST /api/chat/stream  — Server-Sent Events, used for every real send
//   POST /api/chat         — classic JSON, used only as an automatic
//                             fallback if streaming fails outright (e.g.
//                             a corporate proxy that buffers responses)
//
// State lives in three places: `state.conversationId` (which thread is
// active), the DOM (message list), and the server (conversation history,
// source of truth). There is deliberately no client-side duplication of
// message history beyond what's rendered — a page refresh re-fetches
// from GET /api/conversations/<id>.

const csrfToken = document.querySelector('meta[name="csrf-token"]').content;

const chatBox = document.getElementById("chat-box");
const emptyState = document.getElementById("empty-state");
const conversationList = document.getElementById("conversation-list");
const chatForm = document.getElementById("chat-form");
const userInput = document.getElementById("user-input");
const sendBtn = document.getElementById("send-btn");
const newChatBtn = document.getElementById("new-chat-btn");
const clearAllBtn = document.getElementById("clear-all-btn");
const errorBanner = document.getElementById("error-banner");
const sidebar = document.getElementById("sidebar");
const sidebarToggle = document.getElementById("sidebar-toggle");

const GREETING_HTML = chatBox.innerHTML;

const state = {
  conversationId: null,
  isStreaming: false,
};

function api(path, options = {}) {
  const headers = Object.assign({ "Content-Type": "application/json" }, options.headers || {});
  if (options.method && options.method !== "GET") {
    headers["X-CSRF-Token"] = csrfToken;
  }
  return fetch(path, Object.assign({}, options, { headers }));
}

// ---------- UI helpers ----------

function setBusy(isBusy) {
  sendBtn.disabled = isBusy;
  userInput.disabled = isBusy;
  sendBtn.classList.toggle("is-busy", isBusy);
}

function showError(message) {
  errorBanner.textContent = message;
  errorBanner.hidden = false;
}

function hideError() {
  errorBanner.hidden = true;
}

function toggleEmptyState() {
  const hasUserTurn = chatBox.querySelector('.message[data-role="user"]');
  if (emptyState) emptyState.hidden = Boolean(hasUserTurn);
}

function autoResize() {
  userInput.style.height = "auto";
  userInput.style.height = Math.min(userInput.scrollHeight, 200) + "px";
}

function attachCodeCopyButtons(scope) {
  scope.querySelectorAll("pre").forEach((pre) => {
    if (pre.querySelector(".code-copy-btn")) return;
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "code-copy-btn";
    btn.textContent = "Copy";
    btn.addEventListener("click", () => {
      const code = pre.querySelector("code");
      navigator.clipboard.writeText(code ? code.textContent : "").then(() => {
        btn.textContent = "Copied";
        setTimeout(() => (btn.textContent = "Copy"), 1500);
      });
    });
    pre.appendChild(btn);
  });
}

function createMessageEl(role) {
  const wrap = document.createElement("div");
  wrap.className = `message ${role === "user" ? "user" : "bot"}`;
  wrap.dataset.role = role;
  const bubble = document.createElement("div");
  bubble.className = "message-bubble";
  wrap.appendChild(bubble);
  chatBox.appendChild(wrap);
  chatBox.scrollTop = chatBox.scrollHeight;
  return { wrap, bubble };
}

function renderUserMessage(text) {
  const { bubble } = createMessageEl("user");
  bubble.textContent = text; // user text is never markdown-rendered — no need, and one less place to worry about.
  toggleEmptyState();
}

function renderBotMessage(initialText, variant) {
  const { wrap, bubble } = createMessageEl("bot");
  if (variant === "emergency") wrap.classList.add("emergency");
  bubble.innerHTML = renderMarkdown(initialText);
  return { wrap, bubble, buffer: initialText };
}

// ---------- Conversation sidebar ----------

function setActiveConversationInSidebar(convId) {
  conversationList.querySelectorAll(".conversation-item").forEach((el) => {
    el.classList.toggle("active", el.dataset.conversationId === convId);
  });
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}

async function refreshConversationList() {
  try {
    const res = await api("/api/conversations");
    const data = await res.json();
    conversationList.innerHTML = "";
    (data.conversations || []).forEach((conv) => {
      const el = document.createElement("button");
      el.type = "button";
      el.className = "conversation-item";
      el.dataset.conversationId = conv.id;
      el.innerHTML = `<span class="conversation-title">${escapeHtml(conv.title)}</span>`;
      el.addEventListener("click", () => loadConversation(conv.id));
      conversationList.appendChild(el);
    });
    setActiveConversationInSidebar(state.conversationId);
  } catch (err) {
    // Non-fatal: the sidebar just won't refresh until the next send.
  }
}

async function loadConversation(convId) {
  if (state.isStreaming) return;
  hideError();
  try {
    const res = await api(`/api/conversations/${convId}`);
    if (!res.ok) throw new Error("not found");
    const data = await res.json();
    state.conversationId = convId;
    chatBox.innerHTML = GREETING_HTML;
    (data.history || []).forEach((turn) => {
      if (turn.role === "user") {
        renderUserMessage(turn.content);
      } else {
        renderBotMessage(turn.content);
      }
    });
    attachCodeCopyButtons(chatBox);
    setActiveConversationInSidebar(convId);
    closeSidebarOnMobile();
  } catch (err) {
    showError("Couldn't load that conversation.");
  }
}

async function startNewChat() {
  if (state.isStreaming) return;
  hideError();
  state.conversationId = null;
  chatBox.innerHTML = GREETING_HTML;
  toggleEmptyState();
  setActiveConversationInSidebar(null);
  closeSidebarOnMobile();
  userInput.focus();
}

async function clearAllChats() {
  if (state.isStreaming) return;
  hideError();
  try {
    await api("/api/session/reset", { method: "POST" });
  } catch (err) {
    showError("Couldn't clear chats on the server, but the screen has been reset.");
  }
  conversationList.innerHTML = "";
  startNewChat();
}

// ---------- Sending messages (streaming) ----------

async function sendMessage(rawMessage) {
  const message = (rawMessage ?? userInput.value).trim();
  if (!message || state.isStreaming) return;

  hideError();
  renderUserMessage(message);
  userInput.value = "";
  autoResize();
  setBusy(true);
  state.isStreaming = true;

  const botMsg = renderBotMessage("");
  botMsg.bubble.classList.add("is-typing");

  try {
    const response = await api("/api/chat/stream", {
      method: "POST",
      body: JSON.stringify({ message, conversation_id: state.conversationId, lang: "en" }),
    });

    if (!response.ok || !response.body) {
      throw new Error("stream-unavailable");
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let sawAnyToken = false;

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      let boundary;
      while ((boundary = buffer.indexOf("\n\n")) !== -1) {
        const rawEvent = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);
        const eventMatch = rawEvent.match(/^event:\s*(\S+)/m);
        const dataMatch = rawEvent.match(/^data:\s*([\s\S]*)$/m);
        if (!eventMatch || !dataMatch) continue;
        const eventName = eventMatch[1];
        const rawData = dataMatch[1];

        if (eventName === "meta") {
          const meta = JSON.parse(rawData);
          if (meta.conversation_id) {
            state.conversationId = meta.conversation_id;
          }
          if (meta.topic === "emergency") botMsg.wrap.classList.add("emergency");
        } else if (eventName === "token") {
          let token;
          try {
            token = JSON.parse(rawData);
          } catch (e) {
            token = rawData;
          }
          sawAnyToken = true;
          botMsg.buffer += token;
          botMsg.bubble.classList.remove("is-typing");
          botMsg.bubble.innerHTML = renderMarkdown(botMsg.buffer);
          chatBox.scrollTop = chatBox.scrollHeight;
        } else if (eventName === "error") {
          let msg;
          try {
            msg = JSON.parse(rawData);
          } catch (e) {
            msg = rawData;
          }
          throw new Error(msg || "stream-error");
        } else if (eventName === "done") {
          attachCodeCopyButtons(botMsg.wrap);
        }
      }
    }

    if (!sawAnyToken) {
      throw new Error("stream-empty");
    }

    refreshConversationList();
  } catch (err) {
    // Fall back to the classic non-streaming endpoint once, so a proxy
    // that mangles SSE doesn't leave the user with a dead chat.
    botMsg.wrap.remove();
    try {
      const res = await api("/api/chat", {
        method: "POST",
        body: JSON.stringify({ message, conversation_id: state.conversationId, lang: "en" }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        showError(data.error || "Something went wrong. Please try again.");
      } else {
        if (data.conversation_id) state.conversationId = data.conversation_id;
        const fallbackMsg = renderBotMessage(data.reply || "Sorry, I couldn't process that.", data.topic);
        attachCodeCopyButtons(fallbackMsg.wrap);
        refreshConversationList();
      }
    } catch (fallbackErr) {
      showError("Couldn't reach the server. Check your connection and try again.");
    }
  } finally {
    setBusy(false);
    state.isStreaming = false;
    userInput.focus();
  }
}

// ---------- Sidebar toggle (mobile) ----------

function closeSidebarOnMobile() {
  if (window.matchMedia("(max-width: 860px)").matches) {
    sidebar.classList.remove("open");
    sidebarToggle.setAttribute("aria-expanded", "false");
  }
}

sidebarToggle.addEventListener("click", () => {
  const isOpen = sidebar.classList.toggle("open");
  sidebarToggle.setAttribute("aria-expanded", String(isOpen));
});

// ---------- Wiring ----------

chatForm.addEventListener("submit", (event) => {
  event.preventDefault();
  sendMessage();
});

userInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    sendMessage();
  }
});

userInput.addEventListener("input", autoResize);

newChatBtn.addEventListener("click", startNewChat);
clearAllBtn.addEventListener("click", clearAllChats);

conversationList.querySelectorAll(".conversation-item").forEach((el) => {
  el.addEventListener("click", () => loadConversation(el.dataset.conversationId));
});

document.querySelectorAll(".chip-btn").forEach((chip) => {
  chip.addEventListener("click", () => sendMessage(chip.dataset.prompt));
});

// Prefill from a landing-page chip: /app?prompt=...
const params = new URLSearchParams(window.location.search);
const prefill = params.get("prompt");
if (prefill) {
  userInput.value = prefill;
  autoResize();
  window.history.replaceState({}, "", "/app");
}

toggleEmptyState();
userInput.focus();
