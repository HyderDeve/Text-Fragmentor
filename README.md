# Panel Script — Manhwa Prompt Studio

Turns a `.txt` or `.pdf` narrative into text fragments, breaks each fragment
into 3 sequential beats, and generates 3 Whisk-ready, character-consistent
prompts per fragment — using the Groq API. Each prompt describes **one
composite manhwa page image**: multiple panels divided by comic gutters
within a single picture (mixed panel sizes, diagonal cuts, inset close-ups,
optional speech-bubble dialogue) — the way real manhwa/webtoon pages are
laid out, not a single clean standalone shot.

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
3. Upload your `.txt` or `.pdf` chapter/story, pick **panels per page
   image** (2–4), and set the **art style tag** — either type your own, or
   click a preset chip ("Action / fantasy manhwa" for bold black gutters and
   dramatic linework, or "Slice-of-life webtoon" for soft pastel tones, thin
   white gutters, and cuter proportions). This is sent verbatim as the
   opening style tag of every generated prompt.
4. Click **Generate Prompts**. Fragments stream in as they finish — click a
   fragment's header to expand it and see its 3 beats, each with a
   panel-by-panel breakdown and its full page prompt.
5. Copy prompts individually with the **Copy page prompt** button, or click
   **Export all prompts (.json)** once finished to download everything —
   including the full character bible — as one file.

## How it works

- **Text cleaning**: before fragmenting, the document is run through a
  BeautifulSoup-based cleanup pass — any stray HTML tags/entities (common
  when text has been copied from a web page) are stripped, invisible
  whitespace characters are normalized, and excess blank lines are
  collapsed, so the fragment splitter always sees clean, consistent text.
- **Fragments** are built from the document's own paragraph and scene-break
  structure (blank lines, single line breaks, and markers like `***`, `---`,
  `###`), grouped up to roughly 120–320 words each so fragments stay a
  sensible size without ever being cut mid-line. A very short document (a
  few sentences) simply becomes one fragment, which still yields exactly 3
  beat-images — one per sentence/beat, as you'd expect for a short vignette.
- **Character consistency**: the app keeps a running "character bible" as it
  works through the document. Each Groq call is given the bible built so far
  and is instructed to reuse a character's exact appearance description
  every time they reappear, rather than re-describing them differently in
  each fragment.
- **Beat pacing**: every fragment is broken into a fixed 3-beat structure —
  setup → development → turn/climax — so pacing stays steady across a whole
  document, fragment by fragment.
- **Multi-panel pages**: each beat isn't one clean shot — it's one full page
  image made of several panels divided by bold comic gutters, mixing panel
  sizes and shot types (wide, medium, close-up, inset), the way real manhwa
  pages read. The model decides what each individual panel shows rather than
  repeating the same shot.
- **Speech bubbles**: when the source text actually has dialogue for a beat,
  one short line (≤8 words, quoted exactly) may be placed in a speech bubble
  in the relevant panel — never invented. Most panels carry no bubble.
- **Whisk formatting**: each page prompt is written as one dense,
  comma-separated visual-attribute description of the whole page (your art
  style tag, then panel layout, then panel-by-panel content using verbatim
  character descriptors, setting, lighting, framing, mood) rather than
  narrative sentences, and characters are never named in the prose — only
  described by their visual traits, since Whisk can't resolve names.

## Notes

- Your Groq API key is only ever sent from your browser to your own local
  Flask server, and from there directly to Groq — it isn't logged or stored
  anywhere.
- This is a single-user local tool: job state lives in memory and is lost if
  you restart the server.
- PDF text extraction quality depends on the PDF (scanned/image-only PDFs
  won't extract text — OCR them first if needed).
