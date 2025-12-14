import json
from collections import Counter
import google.generativeai as genai
from utils.config import TEXT_MODEL

def extract_events_gemini(user_input):
    prompt = f"""
Return 1-4 main user activities as a JSON list.

User:
{user_input}
"""
    model = genai.GenerativeModel(TEXT_MODEL)
    out = model.generate_content(prompt).text.strip()

    try: events = json.loads(out)
    except: events = [out]

    return events[:4]

def build_multi_organ_journey(user_input, db, k_search=20):
    events = extract_events_gemini(user_input)
    sections = []

    for idx, ev in enumerate(events, 1):
        results = db.similarity_search(ev, k=k_search)
        if not results: continue

        acts = [r.metadata["activity"] for r in results]
        main_act = Counter(acts).most_common(1)[0][0]

        filtered = sorted(
            [r for r in results if r.metadata["activity"] == main_act],
            key=lambda r: int(r.metadata["step"])
        )

        block = [f"Activity {idx}: {main_act}", f"Event phrase: {ev}", "Multi-organ journey:"]
        for r in filtered:
            block.append(f"- Step {r.metadata['step']}: [{r.metadata['organ']}] {r.metadata['mechanism']}")
        sections.append("\n".join(block))

    return "\n\n".join(sections)
