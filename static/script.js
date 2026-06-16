const chatBox = document.getElementById("chat-box");
const historyBox = document.getElementById("history");
const chatForm = document.getElementById("chat-form");
const userInput = document.getElementById("user-input");
const sendBtn = document.getElementById("send-btn");
const clearBtn = document.getElementById("clear-btn");
const errorBanner = document.getElementById("error-banner");

chatForm.addEventListener("submit", (event) => {
  event.preventDefault();
  sendMessage();
});

clearBtn.addEventListener("click", clearHistory);

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

    displayMessage("bot", data.reply || "Sorry, I couldn't process that.");
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
  item.textContent = message.length > 30 ? message.slice(0, 30) + "…" : message;
  historyBox.appendChild(item);
}

async function clearHistory() {
  hideError();
  try {
    await fetch("/clear_history", { method: "POST" });
  } catch (err) {
    showError("Couldn't clear the chat on the server, but the screen has been reset.");
  }
  chatBox.innerHTML =
    '<div class="message bot">👩\u200d⚕️ Hello! I\'m your healthcare assistant. How can I help you today?</div>';
  historyBox.innerHTML = "";
  userInput.focus();
}
