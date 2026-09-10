"""
Manhwa Prompt Studio
---------------------
Turns a .txt or .pdf narrative into text fragments, splits each fragment into
3 sequential beats, and generates 3 Whisk-ready, character-consistent prompts
per fragment using the Groq API. Each prompt describes one composite manhwa
PAGE image — several panels divided by comic gutters within a single image,
matching real webtoon page layout (mixed panel sizes, diagonal cuts, inset
close-ups, optional speech-bubble dialogue) rather than one clean single shot.

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

def build_system_prompt(panel_count: int) -> str:
    return f"""You are a professional visual director/letterer for Korean \
manhwa (webtoon) adaptations. You convert narrative text fragments into \
precise, visually consistent AI image-generation prompts optimized for \
Google Whisk. Every image you design is a single composite manhwa PAGE \
containing {panel_count} distinct panels divided by bold black comic \
gutters within one image - never a single clean standalone shot.

RULES YOU MUST FOLLOW:

1. CHARACTER CONSISTENCY. Maintain a running character bible across the \
whole document. For every character who appears or is referenced in the \
fragment, you must know fixed visual traits: apparent age range, hair color \
and style, eye color, build/height, signature clothing, and any \
distinguishing features (scars, glasses, accessories). If \
CURRENT_CHARACTER_BIBLE already contains a character, you must reuse that \
character's "description" string exactly as given, word for word. Never \
alter, rephrase, or add new traits to an already established character. If \
the fragment introduces a character not yet in the bible, invent a \
concrete, specific appearance for them (never vague like "a person") and \
add them as new.

2. BEAT BREAKDOWN. Split the given fragment into exactly 3 sequential beats \
that show, in order, what happens in the fragment, forming steady pacing \
across the fragment:
   - Beat 1 = setup / establishing beat
   - Beat 2 = development beat (interaction, emotion, or action)
   - Beat 3 = turning point / closing beat of the fragment

3. EACH BEAT IS ONE MULTI-PANEL PAGE IMAGE. For every beat, design ONE \
manhwa page composed of exactly {panel_count} panels laid out the way real \
webtoon pages are: a non-uniform grid mixing panel sizes (e.g. one large \
dominant panel plus one or two smaller inset panels), gutters that can be \
straight or dynamically diagonal, and shot variety across the panels (wide \
establishing shot, medium interaction shot, dramatic close-up on a face, or \
a small inset of a hand/object/detail). Decide concretely what each \
individual panel in the page shows - do not just repeat the same shot \
{panel_count} times.
   - If the fragment contains actual spoken dialogue for that beat, pick at \
     most one short line (max 8 words, quoted exactly from the text) and \
     place it in a speech bubble inside the panel where that character is \
     speaking. Only add a bubble when the source text actually has dialogue \
     for that beat - never invent lines. Most panels will have no bubble.
   - Sound-effect panels (impact, footsteps, a slammed door, rain) may carry \
     a short bold sound-effect word instead of a speech bubble, only when \
     the text implies that sound.

4. THE "prompt" FIELD. For each beat, write ONE dense, comma-separated \
visual-attribute prompt (not a narrative paragraph), roughly 90-160 words, \
that describes the WHOLE PAGE as a single image for Whisk to generate:
   - Open with a fixed style tag: "Korean manhwa style digital illustration, \
     clean linework, soft cel-shading, webtoon color palette, comic page \
     layout with bold black panel gutters"
   - State the panel layout in one clause (e.g. "page divided into \
     {panel_count} panels: one large diagonal panel top-left, two smaller \
     stacked panels right")
   - Then, panel by panel, the exact appearance descriptors (copied \
     verbatim from the bible) for every character in that panel, the \
     setting/environment, lighting, camera framing, mood, and the key \
     action or expression - plus a note of any speech-bubble or \
     sound-effect text and roughly where it sits in the panel
   - Do not use character names inside the prose description of each panel \
     (Whisk cannot resolve names) - describe people only by their visual \
     traits. Quoted bubble/sound-effect text is the only text allowed.

5. OUTPUT FORMAT. Respond with STRICT JSON ONLY - no markdown fences, no \
prose before or after. Schema:
{{
  "characters": {{
    "<character name>": {{"description": "<full appearance descriptor string>", "new": true|false}}
  }},
  "scenes": [
    {{
      "scene_number": 1, "beat": "setup", "summary": "<one sentence, what happens>",
      "characters_present": ["<name>"],
      "panel_count": {panel_count},
      "panels": [
        {{"panel_number": 1, "shot": "wide|medium|close-up|inset", "description": "<what this single panel shows>", "dialogue": "<short quoted line, or null>"}}
      ],
      "prompt": "<the single combined image prompt for the whole {panel_count}-panel page>"
    }},
    {{ "scene_number": 2, "beat": "development", ... same shape ... }},
    {{ "scene_number": 3, "beat": "turn", ... same shape ... }}
  ]
}}
The "panels" array must contain exactly {panel_count} entries per scene. \
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

def call_groq(api_key: str, model: str, character_bible: dict, fragment_text: str, panel_count: int) -> dict:
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
            {"role": "system", "content": build_system_prompt(panel_count)},
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

def process_job(job_id: str, api_key: str, model: str, fragments: list, panel_count: int):
    job = JOBS[job_id]
    character_bible = {}
    for idx, frag_text in enumerate(fragments):
        try:
            result = call_groq(api_key, model, character_bible, frag_text, panel_count)
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

    try:
        panel_count = int(request.form.get("panels", 3))
    except ValueError:
        panel_count = 3
    panel_count = max(2, min(4, panel_count))

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
        "panel_count": panel_count,
        "error": None,
    }

    thread = threading.Thread(
        target=process_job, args=(job_id, api_key, model, fragments, panel_count), daemon=True
    )
    thread.start()

    return jsonify({"job_id": job_id, "total": len(fragments), "panel_count": panel_count})


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
        "panel_count": job.get("panel_count"),
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