# AI Healthcare Chatbot (Flask + Google Gemini)

A web-based AI healthcare chatbot built using Flask and Google Gemini API.  
The application provides intelligent, context-aware responses through a browser-based chat interface while maintaining session-level conversation history and language consistency.

---

## Overview

This project implements an AI-powered healthcare assistant that interacts with users in real time via a web interface.  
The backend is developed using Flask and integrates Google’s Gemini generative AI model to produce responses.  

Key design goals of the project include:
- Maintaining conversational context across multiple messages
- Ensuring consistent response language throughout a session
- Secure handling of sensitive API credentials
- Clean separation between frontend and backend logic

The chatbot currently supports English and Hindi conversations and continues in the language detected from the user’s first message.

---

## Application Functionality

### Core Functionality
- Accepts user input through a web-based chat interface
- Sends user queries to the Flask backend using REST APIs
- Generates AI-based responses using Google Gemini
- Maintains conversation history using Flask sessions
- Preserves language consistency across the session
- Allows users to clear chat history at any time

### Backend Responsibilities
- Request validation and error handling
- Session management for chat history and language preference
- Prompt construction using conversation context
- Communication with the Gemini API
- Secure environment variable handling

### Frontend Responsibilities
- User interface rendering
- Sending messages asynchronously using Fetch API
- Displaying chatbot responses in real time
- Maintaining a simple message history sidebar
- Handling chat reset functionality

---

## Technology Stack

### Backend
- Python 3
- Flask (Web framework)
- Flask-CORS (Cross-origin request handling)
- Google Generative AI SDK (Gemini)
- python-dotenv (Environment variable management)

### Frontend
- HTML5
- CSS3
- JavaScript (Vanilla JS with Fetch API)

### AI Model
- Google Gemini (`gemini-2.0-flash`)

---

## Project Structure
ai_chatbot/
│── app.py # Flask application entry point
│── templates/
│ └── index.html # Frontend UI template
│── static/
│ ├── style.css # Application styling
│ └── script.js # Frontend interaction logic
│── .env # Environment variables (ignored in Git)
│── requirements.txt # Python dependencies
│── README.md # Project documentation
│── venv/ # Virtual environment (ignored in Git)
