const chatBox = document.getElementById("chat-box");
const historyBox = document.getElementById("history");
const chatForm = document.getElementById("chat-form");
const userInput = document.getElementById("user-input");
const sendBtn = document.getElementById("send-btn");
const newChatBtn = document.getElementById("new-chat-btn");
const errorBanner = document.getElementById("error-banner");

const GREETING_HTML = chatBox.innerHTML;

chatForm.addEventListener("submit", (event) => {
  event.preventDefault();
  sendMessage();
});

newChatBtn.addEventListener("click", startNewChat);

function setBusy(isBusy) {
  sendBtn.disabled = isBusy;
  userInput.disabled = isBusy;
}

function showError(message) {
  errorBanner.textContent = message;
  errorBanner.hidden = false;
}

function hideError() {
  errorBanner.hidden = true;
}

async function sendMessage() {
  const message = userInput.value.trim();
  if (!message) return;

  hideError();
  displayMessage("user", message);
  updateSidebar(message);
  userInput.value = "";
  setBusy(true);

  const typingEl = displayMessage("bot typing", "Thinking…");

  try {
    const response = await fetch("/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message }),
    });

    const data = await response.json().catch(() => ({}));
    typingEl.remove();

    if (!response.ok) {
      showError(data.error || "Something went wrong. Please try again.");
      return;
    }

    const variant = data.topic === "emergency" ? "bot emergency" : "bot";
    displayMessage(variant, data.reply || "Sorry, I couldn't process that.");
  } catch (err) {
    typingEl.remove();
    showError("Couldn't reach the server. Check your connection and try again.");
  } finally {
    setBusy(false);
    userInput.focus();
  }
}

function displayMessage(role, text) {
  const msg = document.createElement("div");
  msg.className = `message ${role}`;
  msg.textContent = text;
  chatBox.appendChild(msg);
  chatBox.scrollTop = chatBox.scrollHeight;
  return msg;
}

function updateSidebar(message) {
  const item = document.createElement("p");
  item.textContent = message.length > 36 ? message.slice(0, 36) + "…" : message;
  historyBox.insertBefore(item, historyBox.firstChild);
}

async function startNewChat() {
  hideError();
  try {
    await fetch("/clear_history", { method: "POST" });
  } catch (err) {
    showError("Couldn't clear the chat on the server, but the screen has been reset.");
  }
  chatBox.innerHTML = GREETING_HTML;
  historyBox.innerHTML = "";
  userInput.focus();
}
