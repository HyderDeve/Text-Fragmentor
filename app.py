"""
Manhwa Prompt Studio
---------------------
Turns a .txt or .pdf narrative into text fragments, splits each fragment into
3 sequential scenes, and generates 3 Whisk-ready, character-consistent,
Korean-manhwa-style image prompts per fragment using the Groq API.

Run:
    pip install -r requirements.txt
    python app.py
Then open http://127.0.0.1:5050 in your browser.
"""

import io
import json
import re
import threading
import uuid

from flask import Flask, request, jsonify, render_template
import requests

try:
    from pypdf import PdfReader
except ImportError:  # pragma: no cover
    from PyPDF2 import PdfReader  # type: ignore

app = Flask(__name__)

# ---------------------------------------------------------------------------
# In-memory job store. Fine for a local single-user tool; not for production.
# ---------------------------------------------------------------------------
JOBS = {}
JOBS_LOCK = threading.Lock()

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
DEFAULT_MODEL = "llama-3.3-70b-versatile"

SYSTEM_PROMPT = """You are a professional visual director for Korean manhwa \
(webtoon) adaptations. You convert narrative text fragments into precise, \
visually consistent AI image-generation prompts optimized for Google Whisk.

RULES YOU MUST FOLLOW:

1. CHARACTER CONSISTENCY. Maintain a running character bible across the whole \
document. For every character who appears or is referenced in the fragment, \
you must know fixed visual traits: apparent age range, hair color and style, \
eye color, build/height, signature clothing, and any distinguishing features \
(scars, glasses, accessories). If CURRENT_CHARACTER_BIBLE already contains a \
character, you must reuse that character's "description" string exactly as \
given, word for word. Never alter, rephrase, or add new traits to an already \
established character. If the fragment introduces a character not yet in the \
bible, invent a concrete, specific appearance for them (never vague like \
"a person") and add them as new.

2. SCENE BREAKDOWN. Split the given fragment into exactly 3 sequential scenes \
that show, in order, what happens in the fragment, forming steady visual \
pacing:
   - Scene 1 = setup / establishing beat (wide shot, setting and mood)
   - Scene 2 = development beat (medium shot, character interaction, emotion \
     or action)
   - Scene 3 = turning point / closing beat of the fragment (close-up or a \
     more dynamic shot)

3. IMAGE PROMPTS. For each scene write exactly one dense, comma-separated \
visual-attribute prompt (not a narrative sentence), 40-80 words, in this \
shape:
   - Open with a fixed style tag: "Korean manhwa style digital illustration, \
     clean linework, soft cel-shading, webtoon color palette"
   - Then the exact appearance descriptors (copied verbatim from the bible) \
     for every character present in that scene
   - Then setting/environment, lighting, camera framing (wide shot / medium \
     shot / close-up matching the scene's beat), mood/atmosphere, and the key \
     action or expression happening
   - Do not use character names inside the prompt text itself (Whisk cannot \
     resolve names) - describe people only by their visual traits
   - No dialogue, no quotation marks, no camera-brand or artist names

4. OUTPUT FORMAT. Respond with STRICT JSON ONLY - no markdown fences, no \
prose before or after. Schema:
{
  "characters": {
    "<character name>": {"description": "<full appearance descriptor string>", "new": true|false}
  },
  "scenes": [
    {"scene_number": 1, "beat": "setup", "summary": "<one sentence, what happens>", "characters_present": ["<name>"], "prompt": "<the image prompt>"},
    {"scene_number": 2, "beat": "development", "summary": "...", "characters_present": [...], "prompt": "..."},
    {"scene_number": 3, "beat": "turn", "summary": "...", "characters_present": [...], "prompt": "..."}
  ]
}
Only include characters relevant to this fragment in "characters"."""


# ---------------------------------------------------------------------------
# Text extraction & fragment splitting
# ---------------------------------------------------------------------------

def extract_text_from_pdf(file_bytes: bytes) -> str:
    reader = PdfReader(io.BytesIO(file_bytes))
    pages = []
    for page in reader.pages:
        try:
            pages.append(page.extract_text() or "")
        except Exception:
            pages.append("")
    return "\n\n".join(pages)


SCENE_BREAK_RE = re.compile(r"\n[ \t]*(?:\*[ \t]*\*[ \t]*\*|-{3,}|_{3,}|#{2,}|~{3,})[ \t]*\n")
PARA_SPLIT_RE = re.compile(r"\n\s*\n")


def split_into_fragments(text: str, target_min: int = 120, target_max: int = 320):
    """Split text into fragments along paragraph and scene-break boundaries.

    Explicit scene-break markers (***, ---, etc.) always force a fragment
    boundary. Within a section, consecutive paragraphs are grouped together
    until the soft target_max word count is reached, so fragments stay
    reasonably sized while never being cut mid-paragraph.
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    sections = SCENE_BREAK_RE.split(text)

    fragments = []
    for section in sections:
        section = section.strip()
        if not section:
            continue
        paragraphs = [p.strip() for p in PARA_SPLIT_RE.split(section) if p.strip()]
        current, current_words = [], 0
        for para in paragraphs:
            wc = len(para.split())
            if current and current_words + wc > target_max and current_words >= target_min:
                fragments.append("\n\n".join(current))
                current, current_words = [para], wc
            else:
                current.append(para)
                current_words += wc
        if current:
            fragments.append("\n\n".join(current))
    return fragments


# ---------------------------------------------------------------------------
# Groq call
# ---------------------------------------------------------------------------

def call_groq(api_key: str, model: str, character_bible: dict, fragment_text: str) -> dict:
    user_prompt = (
        "CURRENT_CHARACTER_BIBLE (reuse these descriptions verbatim for "
        "characters already listed here):\n"
        f"{json.dumps(character_bible, ensure_ascii=False, indent=2)}\n\n"
        "FRAGMENT TEXT:\n"
        f"{fragment_text}"
    )
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.6,
        "max_tokens": 1600,
        "response_format": {"type": "json_object"},
    }
    resp = requests.post(
        GROQ_URL,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=payload,
        timeout=90,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Groq API error {resp.status_code}: {resp.text[:500]}")

    content = resp.json()["choices"][0]["message"]["content"].strip()
    content = re.sub(r"^```(json)?|```$", "", content, flags=re.MULTILINE).strip()
    return json.loads(content)


# ---------------------------------------------------------------------------
# Background job processing
# ---------------------------------------------------------------------------

def process_job(job_id: str, api_key: str, model: str, fragments: list):
    job = JOBS[job_id]
    character_bible = {}
    for idx, frag_text in enumerate(fragments):
        try:
            result = call_groq(api_key, model, character_bible, frag_text)
        except Exception as exc:
            with JOBS_LOCK:
                job["status"] = "error"
                job["error"] = str(exc)
            return

        for name, info in (result.get("characters") or {}).items():
            if name not in character_bible:
                character_bible[name] = {"description": info.get("description", "")}

        with JOBS_LOCK:
            job["fragments"].append({
                "index": idx,
                "text": frag_text,
                "scenes": result.get("scenes", []),
            })
            job["character_bible"] = character_bible
            job["progress"] = idx + 1

    with JOBS_LOCK:
        job["status"] = "done"


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html", default_model=DEFAULT_MODEL)


@app.route("/api/start", methods=["POST"])
def api_start():
    api_key = request.form.get("api_key", "").strip()
    model = request.form.get("model", "").strip() or DEFAULT_MODEL
    upload = request.files.get("file")

    if not api_key:
        return jsonify({"error": "Missing Groq API key."}), 400
    if not upload:
        return jsonify({"error": "No file uploaded."}), 400

    filename = upload.filename or ""
    raw = upload.read()

    try:
        if filename.lower().endswith(".pdf"):
            text = extract_text_from_pdf(raw)
        else:
            text = raw.decode("utf-8", errors="ignore")
    except Exception as exc:
        return jsonify({"error": f"Could not read file: {exc}"}), 400

    fragments = split_into_fragments(text)
    if not fragments:
        return jsonify({"error": "No text could be extracted from the file."}), 400

    job_id = uuid.uuid4().hex
    JOBS[job_id] = {
        "status": "running",
        "progress": 0,
        "total": len(fragments),
        "fragments": [],
        "character_bible": {},
        "error": None,
    }

    thread = threading.Thread(
        target=process_job, args=(job_id, api_key, model, fragments), daemon=True
    )
    thread.start()

    return jsonify({"job_id": job_id, "total": len(fragments)})


@app.route("/api/status/<job_id>")
def api_status(job_id):
    job = JOBS.get(job_id)
    if not job:
        return jsonify({"error": "Unknown job id."}), 404
    with JOBS_LOCK:
        return jsonify({
            "status": job["status"],
            "progress": job["progress"],
            "total": job["total"],
            "fragments": job["fragments"],
            "character_bible": job["character_bible"],
            "error": job["error"],
        })


@app.route("/api/export/<job_id>")
def api_export(job_id):
    job = JOBS.get(job_id)
    if not job:
        return jsonify({"error": "Unknown job id."}), 404
    export = {
        "character_bible": job["character_bible"],
        "fragments": job["fragments"],
    }
    return app.response_class(
        response=json.dumps(export, ensure_ascii=False, indent=2),
        mimetype="application/json",
        headers={"Content-Disposition": "attachment; filename=manhwa_prompts.json"},
    )


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5050, debug=True)
