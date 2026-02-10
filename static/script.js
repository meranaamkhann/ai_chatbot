const chatBox = document.getElementById("chat-box");
const historyBox = document.getElementById("history");
let chatHistory = [];

async function sendMessage() {
  const input = document.getElementById("user-input");
  const message = input.value.trim();
  if (!message) return;

  displayMessage("user", message);
  input.value = "";

  const response = await fetch("/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message: message, lang: "en" })
  });

  const data = await response.json();
  const reply = data.reply || "Sorry, I couldn’t process that.";

  displayMessage("bot", reply);
  updateSidebar(message);
}

function displayMessage(role, text) {
  const msg = document.createElement("div");
  msg.classList.add("message", role);
  msg.textContent = text;
  chatBox.appendChild(msg);
  chatBox.scrollTop = chatBox.scrollHeight;
}

function updateSidebar(message) {
  const item = document.createElement("p");
  item.textContent = message.slice(0, 30) + "...";
  historyBox.appendChild(item);
}

async function clearHistory() {
  await fetch("/clear", { method: "POST" });
  chatBox.innerHTML = '<div class="message bot">👩‍⚕️ Hello! I\'m your healthcare assistant. How can I help you today?</div>';
  historyBox.innerHTML = "";
}
