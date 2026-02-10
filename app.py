from flask import Flask, request, jsonify, render_template, session
from flask_cors import CORS
from dotenv import load_dotenv
import os
import google.generativeai as genai

# Load environment variables
load_dotenv()

# Configure Gemini API
api_key = os.getenv("GEMINI_API_KEY")
if not api_key:
    raise RuntimeError("GEMINI_API_KEY not found in .env file!")

genai.configure(api_key=api_key)

# Create Flask app
app = Flask(__name__)
app.secret_key = "asad_secret_key"  # required for session-based chat history
CORS(app)

# Store chat history in session
@app.before_request
def ensure_chat_history():
    if "chat_history" not in session:
        session["chat_history"] = []
    if "preferred_lang" not in session:
        session["preferred_lang"] = None

@app.route('/')
def home():
    return render_template('index.html')


@app.route('/chat', methods=['POST'])
def chat():
    try:
        data = request.get_json()
        user_message = data.get("message", "").strip()
        lang = data.get("lang", "en")

        if not user_message:
            return jsonify({"error": "Message is required"}), 400

        # Detect or maintain the conversation language
        if session["preferred_lang"] is None:
            session["preferred_lang"] = lang
        else:
            lang = session["preferred_lang"]

        # Append user message to history
        session["chat_history"].append({"role": "user", "content": user_message})

        # Prepare full conversation for context
        conversation = "\n".join(
            [f"{msg['role'].capitalize()}: {msg['content']}" for msg in session["chat_history"]]
        )

        # Create Gemini model
        model = genai.GenerativeModel("gemini-2.0-flash")

        # Build prompt
        prompt = (
            "You are a polite, concise healthcare assistant. "
            "Stay consistent in language throughout the conversation. "
            "If the first message is in Hindi, continue in Hindi. "
            "If in English, continue in English.\n\n"
            f"Conversation so far:\n{conversation}\nAssistant:"
        )

        # Generate response
        response = model.generate_content(prompt)
        reply = response.text.strip()

        # Append assistant's reply
        session["chat_history"].append({"role": "assistant", "content": reply})
        session.modified = True

        return jsonify({"reply": reply})

    except Exception as e:
        return jsonify({"error": f"Server error: {str(e)}"}), 500


@app.route('/clear_history', methods=['POST'])
def clear_history():
    session.pop("chat_history", None)
    session.pop("preferred_lang", None)
    return jsonify({"message": "Chat history cleared successfully."})


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
