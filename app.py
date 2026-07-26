from __future__ import annotations

import os
import json
import re
from io import BytesIO
from pathlib import Path

import streamlit as st
from google import genai
from google.genai import types

import pandas as pd

# For local development compatibility
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

st.set_page_config(page_title="BodyBuddies Adventures", page_icon="🧬")

# ========== 1. API Key & Model Configuration ==========

def get_config_value(name: str, default: str | None = None) -> str | None:
    """Read deployment config from environment first, then Streamlit secrets."""
    value = os.environ.get(name)
    if value:
        return value
    try:
        value = st.secrets.get(name)
    except Exception:
        value = None
    return value or default


def get_int_config(name: str, default: int) -> int:
    value = get_config_value(name, str(default))
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def get_bool_config(name: str, default: bool) -> bool:
    value = get_config_value(name, str(default))
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


api_key = get_config_value("GEMINI_API_KEY")

if not api_key:
    st.title("🧬 BodyBuddies Adventures")
    st.error("GEMINI_API_KEY is not configured on the server.")
    st.info(
        "For a public website, set GEMINI_API_KEY as a deployment secret. "
        "Visitors should not need to enter their own API key."
    )
    st.stop()

# Model Configurations
TEXT_MODEL = get_config_value("TEXT_MODEL", "gemini-2.5-flash")
IMAGE_MODEL = get_config_value("IMAGE_MODEL", "gemini-3.1-flash-image")
IMAGE_ASPECT_RATIO = get_config_value("IMAGE_ASPECT_RATIO", "16:9")
IMAGE_SIZE = get_config_value("IMAGE_SIZE", "1K")
MAX_PUBLIC_IMAGES = get_int_config("MAX_PUBLIC_IMAGES", 4)
MAX_INPUT_CHARS = get_int_config("MAX_INPUT_CHARS", 800)
SESSION_GENERATION_LIMIT = get_int_config("SESSION_GENERATION_LIMIT", 5)
ENABLE_IMAGE_GENERATION = get_bool_config("ENABLE_IMAGE_GENERATION", True)
SHOW_DEBUG_ERRORS = get_bool_config("SHOW_DEBUG_ERRORS", False)

gemini_client = genai.Client(api_key=api_key)


# ========== 2. Ontology Retrieval ==========

APP_DIR = Path(__file__).resolve().parent
csv_filename = "ontology_multi_organ_steps.csv"
possible_paths = [
    APP_DIR / "data" / csv_filename,
    APP_DIR / csv_filename,
]

csv_path = None
for path in possible_paths:
    if path.exists():
        csv_path = path
        break

if not csv_path:
    st.error(f"❌ Data file '{csv_filename}' not found. Please place it in a 'data' folder.")
    st.stop()

@st.cache_data
def load_ontology():
    df_onto = pd.read_csv(csv_path)
    required_columns = {"activity", "organ", "mechanism", "step"}
    missing_columns = required_columns - set(df_onto.columns)
    if missing_columns:
        raise ValueError(
            f"Ontology CSV is missing required column(s): {', '.join(sorted(missing_columns))}"
        )
    for optional_col in ["superclass", "class", "keywords"]:
        if optional_col not in df_onto.columns:
            df_onto[optional_col] = ""
    return df_onto.fillna("")


try:
    df_onto = load_ontology()
except Exception as exc:
    st.error("Ontology CSV could not be loaded.")
    if SHOW_DEBUG_ERRORS:
        st.caption(str(exc))
    st.stop()

SUGGESTED_ACTIVITIES = df_onto["activity"].drop_duplicates().head(12).tolist()


def tokenize_for_match(text: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-zA-Z][a-zA-Z0-9_+-]*", str(text).lower())
        if len(token) > 2
    }


def score_activity_match(event_phrase: str, group: pd.DataFrame) -> float:
    event_lower = event_phrase.lower()
    event_tokens = tokenize_for_match(event_phrase)
    first = group.iloc[0]
    searchable = " ".join(
        str(first.get(col, ""))
        for col in ["activity", "superclass", "class", "keywords"]
    ).lower()
    mechanism_text = " ".join(group["mechanism"].astype(str)).lower()
    searchable_tokens = tokenize_for_match(searchable)
    mechanism_tokens = tokenize_for_match(mechanism_text)

    score = 0.0
    activity = str(first.get("activity", "")).lower()
    keywords = str(first.get("keywords", "")).lower()

    if activity and activity in event_lower:
        score += 8
    if event_lower and event_lower in searchable:
        score += 6
    if event_lower and event_lower in mechanism_text:
        score += 2

    keyword_hits = [kw.strip().lower() for kw in keywords.split(",") if kw.strip()]
    score += sum(4 for kw in keyword_hits if kw in event_lower)
    score += len(event_tokens & searchable_tokens) * 2
    score += len(event_tokens & mechanism_tokens) * 0.5

    return score


def find_best_activity_steps(event_phrase: str) -> pd.DataFrame:
    best_activity = None
    best_score = 0.0

    for activity, group in df_onto.groupby("activity", sort=False):
        score = score_activity_match(event_phrase, group)
        if score > best_score:
            best_activity = activity
            best_score = score

    if best_activity is None or best_score <= 0:
        return pd.DataFrame()

    filtered = df_onto[df_onto["activity"] == best_activity].copy()
    filtered["_step_sort"] = pd.to_numeric(filtered["step"], errors="coerce").fillna(0)
    return filtered.sort_values("_step_sort")


def find_activity_matches(text: str, limit: int = 4) -> list[str]:
    """Return ontology activity names that directly match the user's raw text."""
    scored_matches = []
    for activity, group in df_onto.groupby("activity", sort=False):
        score = score_activity_match(text, group)
        if score > 0:
            scored_matches.append((score, str(activity)))

    scored_matches.sort(key=lambda item: item[0], reverse=True)
    return [activity for _, activity in scored_matches[:limit]]


def unique_preserve_order(items: list[str]) -> list[str]:
    seen = set()
    unique_items = []
    for item in items:
        key = item.strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        unique_items.append(item.strip())
    return unique_items


def has_ontology_match(journey: str) -> bool:
    return journey.strip().startswith("Activity ")


# ========== 3. Story Generation Functions ==========

def extract_events_gemini(user_input: str):
    """
    Extracts 1–4 "physiologically relevant events" from user input using Gemini.
    Returns a list of English phrases.
    """
    prompt = f"""
You are a biomedical event extractor.

Read the user's description and identify the DISTINCT lifestyle or physiological
activities that have meaningful effects on the body
(e.g., "drinking coffee", "running", "pulling an all-nighter", "eating spicy food").

Rules:
- Return ONLY a JSON array of short English phrases.
- Each phrase should be 2–6 words.
- Include ONLY the most important 1–4 activities that significantly change physiology.
- Do NOT break one coherent activity into many tiny fragments.
- No explanations, no extra text.

User input:
\"\"\"{user_input}\"\"\"
"""
    try:
        response = gemini_client.models.generate_content(
            model=TEXT_MODEL,
            contents=prompt,
        )
        text = (response.text or "").strip()
    except Exception:
        return []

    try:
        # Attempt to parse JSON
        if "```json" in text:
            text = text.replace("```json", "").replace("```", "")
        events = json.loads(text)
        events = [e.strip() for e in events if isinstance(e, str) and e.strip()]
    except Exception:
        # Fallback: treat the whole text as one event
        events = [text] if text else []

    if len(events) > 4:
        events = events[:4]

    return events


def build_multi_organ_journey(user_input: str) -> tuple[list[str], str]:
    """
    1. Breaks user_input into events.
    2. Matches each event against the ontology CSV.
    3. Constructs a multi-organ journey sequence.
    """
    llm_events = extract_events_gemini(user_input)
    direct_matches = find_activity_matches(user_input)
    events = unique_preserve_order(llm_events + direct_matches)
    if not events:
        return [], "No events detected."

    journey_sections = []
    activity_index = 1
    matched_activities = set()

    for ev in events:
        filtered = find_best_activity_steps(ev)
        if filtered.empty:
            continue

        main_activity = filtered.iloc[0].get("activity", "unknown")
        activity_key = str(main_activity).strip().lower()
        if activity_key in matched_activities:
            continue
        matched_activities.add(activity_key)

        lines = []
        lines.append(f"Activity {activity_index}: {main_activity}")
        lines.append(f"User-described event phrase: {ev}")
        lines.append("Multi-organ journey:")
        for _, row in filtered.iterrows():
            step = row.get("step", "?")
            organ = row.get("organ", "organ")
            mech = row.get("mechanism", "")
            lines.append(f"- Step {step}: [{organ}] {mech}")

        journey_sections.append("\n".join(lines))
        activity_index += 1

    if not journey_sections:
        return events, "No matched mechanisms in ontology."

    return events, "\n\n".join(journey_sections)


def generate_physiology_story(user_input: str, journey: str) -> str:
    """
    Generates the final narrative from the ontology-backed journey.
    """
    prompt = f"""
You are writing an educational comic-style story (like an episode of "Cells at Work!")
that shows what happens inside the human body during the user's activities.

Use the multi-organ journey below as the scientific backbone. Internally plan a
4-scene storyboard first, but output only the final story.

Perspective:
- You may use third-person narration, or choose a main character such as:
  - a specific cell type (e.g., neutrophil, hepatocyte, neuron),
  - a molecule (e.g., caffeine, glucose),
  - or a small team (e.g., "Energy Squad").
- The perspective does NOT have to be first-person "I".
- Choose the perspective that best fits the main mechanisms and keep it consistent.

Language:
- Use the SAME primary language as the user's input.
- It is okay to keep technical terms (like enzyme names) in English if needed.

STRUCTURE (MANDATORY):
Keep EXACTLY these four headings, in this exact form:

**SCENE 1: ENTRY**
**SCENE 2: TRANSPORT**
**SCENE 3: THE MECHANISM (CLIMAX)**
**SCENE 4: RESOLUTION**

Under each heading, write 2–5 paragraphs of flowing narrative.

STYLE RULES:
- Rich, concrete biological details (organs, cells, receptors, enzymes, pathways).
- Anime/comic style: dramatic, playful, but scientifically plausible.
- Use dialogues between characters (cells, molecules, organs, pathogens, etc.)
  when it helps make mechanisms vivid.
- Keep it educational and avoid diagnosis, treatment instructions, or medical advice.
- If a mechanism is uncertain, explain it cautiously instead of overstating it.

User input (for language and vibe):
\"\"\"{user_input}\"\"\"

Multi-organ journey from the CSV ontology:
{journey}

Now write the full story:
"""
    response = gemini_client.models.generate_content(
        model=TEXT_MODEL,
        contents=prompt,
    )
    return response.text.strip()


# ========== 4. Comic Panel Generation ==========

def build_comic_scenes_from_story(story: str, n_panels: int):
    """
    Splits the story into n_panels for illustration.
    """
    prompt = f"""
You are turning a science story into a {n_panels}-panel comic.

For each panel, write:
- "scene": 1–3 sentences describing what is happening in this panel (visually).
- "narration": 1 short narration sentence that could appear in a narration box.
- "labels": 1–3 short scientific labels for the key characters or mechanisms.

Rules:
- EXACTLY {n_panels} panels.
- Panels must follow the original story order.
- Use the SAME language as the STORY (Chinese if Chinese, otherwise English).
- Each "scene" should be short and focused on one key moment.
- Keep "narration" under 26 Chinese characters or 16 English words.
- Keep each label under 12 Chinese characters or 4 English words.

Return ONLY valid JSON in the following format:
[
  {{
    "scene": "...",
    "narration": "...",
    "labels": ["...", "..."]
  }},
  ...
]

STORY:
{story}
"""
    response = gemini_client.models.generate_content(
        model=TEXT_MODEL,
        contents=prompt,
        config=dict(
            temperature=0.4,
            response_mime_type="application/json"
        )
    )

    try:
        panels = json.loads(response.text)
    except Exception:
        # Fallback: split by paragraphs
        chunks = re.split(r'\n\s*\n+', story.strip())
        if len(chunks) < n_panels:
            chunks = chunks + [""] * (n_panels - len(chunks))
        size = max(1, len(chunks) // n_panels)
        panels = []
        for i in range(n_panels):
            seg = "\n\n".join(chunks[i*size:(i+1)*size]).strip()
            panels.append({"scene": seg, "narration": "", "labels": []})

    if len(panels) > n_panels:
        panels = panels[:n_panels]
    elif len(panels) < n_panels:
        while len(panels) < n_panels:
            panels.append({"scene": "", "narration": "", "labels": []})

    return panels


# ========== 5. Illustration Generation ==========

def generate_images_from_story(story: str, n_images: int = 1):
    """
    Generates text-free educational illustrations for the story.
    """
    try:
        from PIL import Image as PilImage
    except ImportError as exc:
        raise RuntimeError("Image generation requires pillow.") from exc

    panels = build_comic_scenes_from_story(story, n_images)
    images = []
    panel_summaries = []

    for panel_idx, panel in enumerate(panels):
        panel_scene = panel.get("scene", "").strip()
        if not panel_scene:
            panel_scene = story
        narration = panel.get("narration", "").strip()
        labels = panel.get("labels", [])
        if not isinstance(labels, list):
            labels = []

        scientific_focus = ", ".join(str(label).strip() for label in labels if str(label).strip())
        prompt = f"""
Create one polished 2D educational comic illustration for BodyBuddies Adventures.

Style:
- Friendly science-adventure animation, clean line art, rich but balanced colors.
- Age-appropriate for a general audience; no gore, surgery, or frightening imagery.
- Use expressive anthropomorphic cells or molecules only where biologically helpful.
- Show a clear sense of location inside the human body with simplified anatomy.

Scientific direction:
- Focus on one biologically plausible action and make the mechanism visually clear.
- Distinguish organs, cells, and molecules through shape, scale, and composition.
- Do not invent medical devices, pathogens, or anatomical structures absent from the scene.

Strict text rule:
- No text, labels, letters, captions, speech bubbles, logos, UI, or pseudo-text.
- No decorative border or multi-panel layout.

Scene:
{panel_scene}

Key scientific focus:
{scientific_focus or "Use the most important mechanism in the scene."}
"""
        resp = gemini_client.models.generate_content(
            model=IMAGE_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_modalities=["IMAGE"],
                image_config=types.ImageConfig(
                    aspect_ratio=IMAGE_ASPECT_RATIO,
                    image_size=IMAGE_SIZE,
                ),
            ),
        )

        raw_img = None
        if resp and resp.candidates:
            for candidate in resp.candidates:
                if not candidate.content or not candidate.content.parts:
                    continue
                for part in candidate.content.parts:
                    inline_data = getattr(part, "inline_data", None)
                    if inline_data is not None and inline_data.data:
                        raw_img = PilImage.open(BytesIO(inline_data.data)).convert("RGB")
                        break
                if raw_img is not None:
                    break

        if raw_img is None:
            raise RuntimeError(f"The image model returned no image for panel {panel_idx + 1}.")

        images.append(raw_img)
        panel_summaries.append(narration or panel_scene)

    return images, panel_summaries


def shorten_summary(text: str, max_sentences: int = 3) -> str:
    """Helper to clean up panel summary text for UI display."""
    if not text:
        return ""
    text = text.strip()
    text = re.sub(r'^SCENE\s*\d+\s*:[^\n]*\n', '', text, flags=re.IGNORECASE)
    parts = re.split(r'(?<=[。！？.!?])\s+', text)
    parts = [p.strip() for p in parts if p.strip()]
    if not parts:
        return ""
    short = " ".join(parts[:max_sentences])
    if not short:
        short = text[:120] + ("..." if len(text) > 120 else "")
    return short


# ========== 8. Streamlit UI ==========

st.title("🧬 BodyBuddies Adventures 🧬")

st.markdown("""
**Turn everyday activities into science-grounded body adventures.**

Describe something you did today. The demo matches it against a lightweight
**CSV multi-organ physiology ontology**, then uses Gemini to write a 4-scene story
that explains what is happening across the body.
""")
st.caption("Educational demo only. It is not medical advice, diagnosis, or treatment guidance.")

with st.sidebar:
    st.header("Demo Settings")
    st.caption("The Gemini API key is configured server-side. Visitors never enter a key.")
    st.write(f"Text model: `{TEXT_MODEL}`")
    st.write(f"Ontology rows: `{len(df_onto)}`")
    st.write(f"Image generation: `{'on' if ENABLE_IMAGE_GENERATION else 'off'}`")
    if ENABLE_IMAGE_GENERATION:
        st.write(f"Image model: `{IMAGE_MODEL}`")
    st.caption("Try activities like: " + ", ".join(SUGGESTED_ACTIVITIES[:6]))
    if not ENABLE_IMAGE_GENERATION:
        st.info("Illustrations are disabled by server configuration.")

if "generation_count" not in st.session_state:
    st.session_state.generation_count = 0

remaining_generations = max(0, SESSION_GENERATION_LIMIT - st.session_state.generation_count)

user_activity = st.text_area(
    "Describe your daily activity:",
    value="This morning I drank coffee on an empty stomach, had fried chicken for lunch, and went swimming in the afternoon.",
    max_chars=MAX_INPUT_CHARS,
    help="Tell us anything you did today — the AI will turn it into a scientific adventure happening inside your body!"
)

control_col, image_col = st.columns([2, 1])
with control_col:
    output_options = ["Story only"]
    if ENABLE_IMAGE_GENERATION:
        output_options.append("Story + illustrations")
    output_mode = st.radio(
        "Output mode",
        output_options,
        index=0,
        horizontal=True,
        help="Illustrations take longer and use additional Gemini API quota.",
    )

generate_images = output_mode == "Story + illustrations"
with image_col:
    image_limit = min(4, max(1, MAX_PUBLIC_IMAGES))
    if generate_images and image_limit > 1:
        n_images = st.slider(
            "Number of illustrations",
            min_value=1,
            max_value=image_limit,
            value=min(2, image_limit),
        )
    elif generate_images:
        n_images = 1
        st.caption("One illustration is available.")
    else:
        n_images = 0
        st.caption("No illustrations will be generated.")

st.caption(f"Demo limit: {remaining_generations} generation(s) left in this browser session.")

button_label = "✨ Generate Story & Illustrations" if generate_images else "✨ Generate Story"

if st.button(button_label, type="primary", disabled=remaining_generations <= 0):
    for state_key in [
        "story_result",
        "detected_events",
        "journey_result",
        "generated_images",
        "panel_summaries",
    ]:
        st.session_state.pop(state_key, None)

    if not user_activity.strip():
        st.warning("Please enter a description first!")
    elif len(user_activity) > MAX_INPUT_CHARS:
        st.warning(f"Please keep the description under {MAX_INPUT_CHARS} characters.")
    else:
        try:
            with st.spinner("🧠 Matching your activity to the body ontology..."):
                detected_events, journey_result = build_multi_organ_journey(user_activity)
        except Exception as e:
            st.error("Ontology matching failed. Please try again with a shorter or simpler activity description.")
            if SHOW_DEBUG_ERRORS:
                st.caption(str(e))
            st.stop()

        if not has_ontology_match(journey_result):
            st.warning("No matching physiology pathway was found in the CSV ontology.")
            if detected_events:
                st.caption("Detected activity phrase(s): " + ", ".join(detected_events))
            st.info("Try a description involving: " + ", ".join(SUGGESTED_ACTIVITIES[:8]))
            with st.expander("🔬 Retrieval result", expanded=True):
                st.code(journey_result, language="text")
            st.stop()

        try:
            with st.spinner("✍️ Crafting your 4-scene internal science adventure..."):
                story_result = generate_physiology_story(user_activity, journey_result)
            st.session_state.generation_count += 1
            st.session_state.story_result = story_result
            st.session_state.detected_events = detected_events
            st.session_state.journey_result = journey_result
        except Exception as e:
            st.error("Story generation failed. Please try again with a shorter or simpler activity description.")
            if SHOW_DEBUG_ERRORS:
                st.caption(str(e))
            st.stop()

        if generate_images:
            try:
                with st.spinner("🎨 Painting illustrations from the story scenes..."):
                    imgs, panel_summaries = generate_images_from_story(
                        story_result,
                        n_images=n_images,
                    )
                st.session_state.generated_images = imgs
                st.session_state.panel_summaries = panel_summaries
            except Exception as e:
                st.warning("The story is ready, but illustration generation failed.")
                if SHOW_DEBUG_ERRORS:
                    st.caption(str(e))

if "story_result" in st.session_state:
    story_result = st.session_state.story_result
    detected_events = st.session_state.get("detected_events", [])
    journey_result = st.session_state.get("journey_result", "")

    st.subheader("📖 Final Story")
    st.markdown(story_result)

    with st.expander("🔬 Science basis from CSV ontology", expanded=True):
        if detected_events:
            st.caption("Detected activity phrase(s): " + ", ".join(detected_events))
        st.code(journey_result, language="text")

    imgs = st.session_state.get("generated_images", [])
    panel_summaries = st.session_state.get("panel_summaries", [])
    if imgs:
        st.subheader("🖼️ Story Illustrations")
        for i, (img, summary) in enumerate(zip(imgs, panel_summaries), 1):
            st.image(img, caption=f"Illustration {i}", width="stretch")
            short = shorten_summary(summary, max_sentences=2)
            st.caption(f"**Scene description:** {short}")

st.markdown("---")
st.markdown("""
<div style='text-align: center; color: gray; font-size: 0.8em;'>
    Powered by Google Gemini API | BodyBuddies Adventures
</div>
""", unsafe_allow_html=True)
