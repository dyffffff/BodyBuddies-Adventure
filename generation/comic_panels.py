import json
import google.generativeai as genai
from utils.config import TEXT_MODEL

def build_comic_scenes_from_story(story, n):
    prompt = f"""
Split story into {n} comic panels.
Return JSON list of objects with keys: scene, narration.

Story:
{story}
"""
    model = genai.GenerativeModel(TEXT_MODEL)
    try:
        return json.loads(model.generate_content(prompt).text)
    except:
        return [{"scene": story, "narration": ""}] * n
