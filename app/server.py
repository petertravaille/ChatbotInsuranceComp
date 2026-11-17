"""Small web server for the chatbot.  Run:  python app/server.py  then open http://localhost:5000"""

import os
import secrets
import sys
from pathlib import Path

import anthropic
from flask import Flask, jsonify, request, send_from_directory, session

sys.path.insert(0, str(Path(__file__).resolve().parent))
from chatbot import ChatSession  # noqa: E402
from tools import list_members_for_login  # noqa: E402

STATIC = Path(__file__).resolve().parent / "static"
app = Flask(__name__, static_folder=str(STATIC), static_url_path="/static")
app.secret_key = os.environ.get("FLASK_SECRET_KEY", secrets.token_hex(16))

# Conversations live in memory; fine for a local prototype.
CHATS: dict[str, ChatSession] = {}


@app.get("/")
def index():
    return send_from_directory(STATIC, "index.html")


@app.get("/api/members")
def members():
    return jsonify(list_members_for_login())


@app.post("/api/login")
def login():
    data = request.get_json(force=True)
    role = data.get("role")
    if role not in ("member", "employee"):
        return jsonify({"error": "role must be member or employee"}), 400
    member_id = name = None
    if role == "member":
        member_id = data.get("member_id")
        match = [m for m in list_members_for_login(1000) if m["member_id"] == member_id]
        if not match:
            return jsonify({"error": "Unknown member"}), 400
        name = f"{match[0]['first_name']} {match[0]['last_name']}"
    chat_id = secrets.token_urlsafe(16)
    CHATS[chat_id] = ChatSession(role, member_id, name)
    session["chat_id"] = chat_id
    return jsonify({"role": role, "member_id": member_id, "name": name or "Employee"})


@app.post("/api/chat")
def chat():
    chat = CHATS.get(session.get("chat_id", ""))
    if not chat:
        return jsonify({"error": "Please sign in first."}), 401
    question = (request.get_json(force=True).get("message") or "").strip()
    if not question:
        return jsonify({"error": "Empty message"}), 400
    try:
        answer, tools_used = chat.ask(question)
    except anthropic.AuthenticationError:
        return jsonify({"error": "The Anthropic API key is invalid. Check ANTHROPIC_API_KEY."}), 500
    except TypeError as e:
        if "authentication" not in str(e):
            raise
        return jsonify({"error": "No Anthropic API key found. Set ANTHROPIC_API_KEY and restart the server."}), 500
    except anthropic.RateLimitError:
        return jsonify({"error": "Too many requests right now, please try again in a minute."}), 429
    except anthropic.APIStatusError as e:
        return jsonify({"error": f"Claude API error ({e.status_code}): {e.message}"}), 502
    except anthropic.APIConnectionError:
        return jsonify({"error": "Could not reach the Claude API. Check your internet connection."}), 502
    return jsonify({"answer": answer, "tools_used": tools_used})


@app.post("/api/logout")
def logout():
    CHATS.pop(session.pop("chat_id", ""), None)
    return jsonify({"ok": True})


if __name__ == "__main__":
    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        print("Warning: ANTHROPIC_API_KEY is not set, so the chatbot can't answer yet "
              "(unless you signed in with `ant auth login`).")
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", 5000)), debug=False)
