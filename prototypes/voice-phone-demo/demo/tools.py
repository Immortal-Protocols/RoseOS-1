"""Tool definitions shared by the voice model (Design A) and the orchestrator."""

import os

# v1: the first, phone-focused prompt. v2: general companion that delegates
# more and keeps turns short. Pick with VOICE_PROMPT=v1|v2.
VOICE_PROMPTS = {
    "v1": (
        "You are Rose, a friendly voice assistant on the user's phone. "
        "Keep spoken replies short and natural. "
        "For anything on the phone (messages, contacts, alarms, calendar, settings, apps) "
        "call do_on_phone. For hard questions, facts you are unsure of, math or planning, "
        "call ask_brain. Call only one tool at a time, and only when the user asks for "
        "something that needs it. Before calling a tool, briefly tell the user you are on it."
    ),
    "v2": (
        "You are Rose, a friendly voice companion. Answer in one or two short sentences, "
        "then stop and listen. Do not ask a question back every turn. Never list what you "
        "can or cannot do. You have a smarter helper that can see the user's messages, "
        "contacts, calendar and alarms. Call ask_brain for anything beyond simple small "
        "talk: facts, advice, plans, math, or questions about the user's own life, people or "
        "schedule. Call do_on_phone to change or open something on the phone. Call one tool "
        "at a time. Before calling a tool, briefly tell the user you are on it."
    ),
}


def voice_system_message() -> str:
    return VOICE_PROMPTS[os.environ.get("VOICE_PROMPT", "v2")]


VOICE_TOOLS = [
    {
        "name": "ask_brain",
        "description": (
            "Ask a smarter assistant that can see the user's messages, contacts, calendar, "
            "alarms and settings. Use it for knowledge, advice, ideas, planning, math, and "
            "any question about the user's own plans, people or schedule. Returns a short "
            "answer to speak."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "The user's question, self-contained.",
                }
            },
            "required": ["question"],
        },
    },
    {
        "name": "do_on_phone",
        "description": (
            "Change or open something on the user's phone: send a text, set or switch an "
            "alarm, change settings such as Do Not Disturb, Wi-Fi or brightness, or open "
            "an app to show the user. Returns what happened."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "request": {
                    "type": "string",
                    "description": "What to do, in plain words, with all details the user gave.",
                }
            },
            "required": ["request"],
        },
    },
]
