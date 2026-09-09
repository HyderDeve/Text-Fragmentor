# Panel Script — Manhwa Prompt Studio

Turns a `.txt` or `.pdf` narrative into text fragments, breaks each fragment
into 3 sequential scenes, and generates 3 Whisk-ready, Korean-manhwa-style,
character-consistent image prompts per fragment — using the Groq API.

## Setup

```bash
pip install -r requirements.txt
python app.py
```

Then open **http://127.0.0.1:5050** in your browser.

## Using it

1. Paste your **Groq API key** (get one free at https://console.groq.com/keys).
2. Leave the model as-is, or swap in another Groq chat model that supports
   JSON responses (e.g. `llama-3.3-70b-versatile`, `llama-3.1-8b-instant`).
3. Upload your `.txt` or `.pdf` chapter/story.
4. Click **Generate Prompts**. Fragments stream in as they finish — click a
   fragment's header to expand it and see its 3 scenes and prompts.
5. Copy prompts individually with the **Copy prompt** button, or click
   **Export all prompts (.json)** once finished to download everything —
   including the full character bible — as one file.

## How it works

- **Fragments** are built from the document's own paragraph and scene-break
  structure (blank lines, and markers like `***`, `---`, `###`), grouped up
  to roughly 120–320 words each so fragments stay a sensible size without
  ever being cut mid-paragraph.
- **Character consistency**: the app keeps a running "character bible" as it
  works through the document. Each Groq call is given the bible built so far
  and is instructed to reuse a character's exact appearance description
  every time they reappear, rather than re-describing them differently in
  each fragment.
- **Scene pacing**: every fragment is broken into a fixed 3-beat structure —
  setup (wide shot) → development (medium shot) → turn/climax (close-up or
  dynamic shot) — so pacing stays steady across a whole document, fragment
  by fragment.
- **Whisk formatting**: prompts are written as dense, comma-separated visual
  attributes (style, character appearance, setting, lighting, framing, mood)
  rather than narrative sentences, and never contain character names, since
  Whisk needs visual descriptors rather than names it can't resolve.

## Notes

- Your Groq API key is only ever sent from your browser to your own local
  Flask server, and from there directly to Groq — it isn't logged or stored
  anywhere.
- This is a single-user local tool: job state lives in memory and is lost if
  you restart the server.
- PDF text extraction quality depends on the PDF (scanned/image-only PDFs
  won't extract text — OCR them first if needed).
