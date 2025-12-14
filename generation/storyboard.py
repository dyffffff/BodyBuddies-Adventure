import google.generativeai as genai
from utils.config import TEXT_MODEL
from rag.journey_builder import build_multi_organ_journey

def generate_scientific_storyboard(user_input, db):
    journey = build_multi_organ_journey(user_input, db)

    prompt = f"""
Convert this into a 4-scene scientific storyboard:

{journey}
"""
    model = genai.GenerativeModel(TEXT_MODEL)
    return model.generate_content(prompt).text.strip()
