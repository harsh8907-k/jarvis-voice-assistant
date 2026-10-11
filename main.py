#!/usr/bin/env python3
"""
Jarvis - a small voice assistant you can extend.

    python jarvis.py          voice mode: say "Jarvis, what time is it?"
    python jarvis.py --text   text mode: type commands (no microphone needed)

Optional: set ANTHROPIC_API_KEY to let Jarvis answer anything it has no
built-in command for (uses the Claude API).
"""
import argparse
import datetime
import os
import platform
import random
import re
import subprocess
import sys
import threading
import urllib.parse
import webbrowser
from pathlib import Path

import requests

ASSISTANT_NAME = "Jarvis"
WAKE_WORD = "jarvis"
NOTES_FILE = Path(__file__).with_name("notes.txt")
USER_AGENT = "JarvisAssistant/1.0 (personal project)"

# ---------------------------------------------------------------------------
# Things Jarvis knows how to open. Add your own here.
# ---------------------------------------------------------------------------
SITES = {
    "youtube": "https://www.youtube.com",
    "google": "https://www.google.com",
    "gmail": "https://mail.google.com",
    "github": "https://github.com",
    "linkedin": "https://www.linkedin.com",
    "stack overflow": "https://stackoverflow.com",
    "chat gpt": "https://chatgpt.com",
    "claude": "https://claude.ai",
    "forage": "https://www.theforage.com",
}

# Desktop apps, per operating system (platform.system()).
APPS = {
    "notepad": {"Windows": ["notepad"], "Darwin": ["open", "-a", "TextEdit"], "Linux": ["gedit"]},
    "text editor": {"Windows": ["notepad"], "Darwin": ["open", "-a", "TextEdit"], "Linux": ["gedit"]},
    "calculator": {"Windows": ["calc"], "Darwin": ["open", "-a", "Calculator"], "Linux": ["gnome-calculator"]},
    "vs code": {"Windows": ["code"], "Darwin": ["open", "-a", "Visual Studio Code"], "Linux": ["code"]},
    "code": {"Windows": ["code"], "Darwin": ["open", "-a", "Visual Studio Code"], "Linux": ["code"]},
    "terminal": {"Windows": ["cmd"], "Darwin": ["open", "-a", "Terminal"], "Linux": ["x-terminal-emulator"]},
}

JOKES = [
    "Why do programmers prefer dark mode? Because light attracts bugs.",
    "There are 10 kinds of people: those who understand binary and those who don't.",
    "A SQL query walks into a bar, walks up to two tables and asks: can I join you?",
    "Why did the developer go broke? Because he used up all his cache.",
]

# ---------------------------------------------------------------------------
# Speaking
# ---------------------------------------------------------------------------
STATE = {"speak_aloud": True}
_engine = None
_speak_lock = threading.Lock()


def speak(text: str) -> None:
    """Print the reply and, if possible, say it out loud."""
    print(f"{ASSISTANT_NAME}: {text}")
    if not STATE["speak_aloud"]:
        return
    global _engine
    with _speak_lock:
        try:
            if _engine is None:
                import pyttsx3

                _engine = pyttsx3.init()
                _engine.setProperty("rate", 175)
            _engine.say(text)
            _engine.runAndWait()
        except Exception as exc:  # no speech engine installed, etc.
            print(f"(speech output unavailable, using text only: {exc})")
            STATE["speak_aloud"] = False


# ---------------------------------------------------------------------------
# Skills
# ---------------------------------------------------------------------------
def tell_time() -> str:
    return "It's " + datetime.datetime.now().strftime("%I:%M %p").lstrip("0") + "."


def tell_date() -> str:
    return "Today is " + datetime.datetime.now().strftime("%A, %d %B %Y") + "."


def get_weather(city: str = "") -> str:
    url = "https://wttr.in/" + urllib.parse.quote(city)
    try:
        r = requests.get(url, params={"format": "%l: %C, %t"}, headers={"User-Agent": USER_AGENT}, timeout=8)
        r.raise_for_status()
        return r.text.strip()
    except requests.RequestException:
        return "I couldn't get the weather right now."


def wikipedia_summary(topic: str):
    """Return a two-sentence Wikipedia summary, or None if nothing is found."""
    try:
        r = requests.get(
            "https://en.wikipedia.org/w/api.php",
            params={
                "action": "query", "format": "json", "prop": "extracts", "exintro": 1,
                "explaintext": 1, "redirects": 1, "generator": "search",
                "gsrsearch": topic, "gsrlimit": 1,
            },
            headers={"User-Agent": USER_AGENT},
            timeout=8,
        )
        r.raise_for_status()
        pages = r.json().get("query", {}).get("pages", {})
        if not pages:
            return None
        extract = next(iter(pages.values())).get("extract", "").strip()
        if not extract:
            return None
        sentences = re.split(r"(?<=[.!?])\s+", extract)
        return " ".join(sentences[:2])
    except (requests.RequestException, ValueError):
        return None


_history = []


def ask_claude(question: str):
    """Optional: answer open questions with the Claude API. Returns None if not configured."""
    key = os.getenv("ANTHROPIC_API_KEY")
    if not key:
        return None
    try:
        import anthropic

        client = anthropic.Anthropic(api_key=key)
        _history.append({"role": "user", "content": question})
        del _history[:-10]
        while _history and _history[0]["role"] != "user":
            del _history[0]
        resp = client.messages.create(
            model=os.getenv("JARVIS_MODEL", "claude-haiku-5-5"),
            max_tokens=300,
            system=(
                "You are Jarvis, a concise voice assistant. Answer in one to three short "
                "sentences of plain text, with no markdown or lists."
            ),
            messages=_history,
        )
        answer = "".join(b.text for b in resp.content if b.type == "text").strip()
        _history.append({"role": "assistant", "content": answer})
        return answer or None
    except Exception as exc:
        return f"I couldn't reach the AI service: {exc}"


def start_timer(amount: int, unit: str) -> str:
    seconds = amount * {"second": 1, "minute": 60, "hour": 3600}[unit]
    label = f"{amount} {unit}{'s' if amount != 1 else ''}"
    threading.Timer(seconds, lambda: speak(f"Your {label} timer is done.")).start()
    return f"Timer set for {label}."


def save_note(text: str) -> str:
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    with NOTES_FILE.open("a", encoding="utf-8") as f:
        f.write(f"[{stamp}] {text}\n")
    return "Noted."


def read_notes() -> str:
    if not NOTES_FILE.exists():
        return "You have no notes yet."
    lines = NOTES_FILE.read_text(encoding="utf-8").strip().splitlines()[-5:]
    return "Your latest notes: " + " ... ".join(re.sub(r"^\[[^\]]*\]\s*", "", line) for line in lines)


def open_target(target: str) -> str:
    target = target.strip()
    if target in SITES:
        webbrowser.open(SITES[target])
        return f"Opening {target}."
    app = APPS.get(target)
    if app:
        command = app.get(platform.system())
        if command:
            try:
                subprocess.Popen(command)
                return f"Opening {target}."
            except (OSError, ValueError):
                return f"I couldn't open {target} on this computer."
    return f"I don't know how to open {target} yet. You can add it to SITES or APPS in jarvis.py."


# ---------------------------------------------------------------------------
# Command routing: returns (reply, should_exit)
# ---------------------------------------------------------------------------
def handle(command: str):
    c = command.lower().strip().rstrip("?.!").strip()
    if not c:
        return "Yes?", False

    if re.search(r"\b(exit|quit|goodbye|bye|stop listening|shut down)\b", c):
        return "Goodbye!", True

    if re.fullmatch(r"(hello|hi|hey)( there)?", c):
        return "Hello! How can I help?", False

    m = re.search(r"timer for (\d+)\s*(second|minute|hour)s?", c)
    if m:
        return start_timer(int(m.group(1)), m.group(2)), False

    if re.search(r"\bwhat('s| is)? the time\b|\bwhat time\b|^time$|\btell me the time\b", c):
        return tell_time(), False
    if re.search(r"\b(date|day) (is it|today)\b|\btoday'?s date\b|\bwhat day\b|^date$", c):
        return tell_date(), False

    if "weather" in c:
        m = re.search(r"weather (?:in|for|at) (.+)", c)
        return get_weather(m.group(1) if m else ""), False

    m = re.match(r"(?:take a note|make a note|note|remember(?: that)?)\s+(.+)", c)
    if m:
        return save_note(m.group(1)), False
    if re.search(r"\b(read|show) (my )?notes\b", c):
        return read_notes(), False

    m = re.match(r"open (.+)", c)
    if m:
        return open_target(m.group(1)), False

    m = re.match(r"play (.+?)(?: on youtube)?$", c)
    if m:
        webbrowser.open("https://www.youtube.com/results?search_query=" + urllib.parse.quote_plus(m.group(1)))
        return f"Searching YouTube for {m.group(1)}.", False

    m = re.match(r"(?:search|google)(?: for)? (.+)", c)
    if m:
        webbrowser.open("https://www.google.com/search?q=" + urllib.parse.quote_plus(m.group(1)))
        return f"Searching Google for {m.group(1)}.", False

    if "joke" in c:
        return random.choice(JOKES), False

    m = re.match(r"(?:who is|who was|what is|what are|tell me about|wikipedia)\s+(.+)", c)
    if m:
        summary = wikipedia_summary(m.group(1))
        if summary:
            return summary, False

    answer = ask_claude(command)
    if answer:
        return answer, False
    return ("I don't know how to do that yet. Add a skill in jarvis.py, or set "
            "ANTHROPIC_API_KEY so I can answer open questions."), False


# ---------------------------------------------------------------------------
# Listening
# ---------------------------------------------------------------------------
def listen_once(recognizer, mic, timeout=5, phrase_limit=8) -> str:
    import speech_recognition as sr

    with mic as source:
        try:
            audio = recognizer.listen(source, timeout=timeout, phrase_time_limit=phrase_limit)
        except sr.WaitTimeoutError:
            return ""
    try:
        return recognizer.recognize_google(audio)
    except sr.UnknownValueError:
        return ""
    except sr.RequestError as exc:
        print(f"(speech recognition service error: {exc})")
        return ""


def run_voice() -> None:
    import speech_recognition as sr

    recognizer = sr.Recognizer()
    try:
        mic = sr.Microphone()
    except (OSError, AttributeError) as exc:
        sys.exit(f"No microphone found ({exc}). Check your mic or run: python jarvis.py --text")

    print("Calibrating for background noise...")
    with mic as source:
        recognizer.adjust_for_ambient_noise(source, duration=1)
    speak(f"{ASSISTANT_NAME} online. Say '{WAKE_WORD}' followed by a command.")

    while True:
        heard = listen_once(recognizer, mic)
        if not heard:
            continue
        text = heard.lower()
        print(f"You: {heard}")
        if WAKE_WORD not in text:
            continue
        command = text.split(WAKE_WORD, 1)[1].strip(" ,.")
        if not command:
            speak("Yes?")
            command = listen_once(recognizer, mic, timeout=6, phrase_limit=10).lower()
            if not command:
                continue
            print(f"You: {command}")
        reply, done = handle(command)
        speak(reply)
        if done:
            break


def run_text() -> None:
    STATE["speak_aloud"] = False
    print(f"{ASSISTANT_NAME} (text mode). Type a command, or 'exit' to quit.")
    while True:
        try:
            command = input("You: ")
        except (EOFError, KeyboardInterrupt):
            print()
            break
        reply, done = handle(command)
        speak(reply)
        if done:
            break


def main() -> None:
    parser = argparse.ArgumentParser(description="Jarvis voice assistant")
    parser.add_argument("--text", action="store_true", help="type commands instead of speaking")
    args = parser.parse_args()
    run_text() if args.text else run_voice()


if __name__ == "__main__":
    main()