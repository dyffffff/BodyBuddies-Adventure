import google.generativeai as genai
from utils.config import TEXT_MODEL
from generation.storyboard import generate_scientific_storyboard

def generate_physiology_story(user_input, db):
    storyboard = generate_scientific_storyboard(user_input, db)

    prompt = f"Write a comic-style biology story based on:\n{storyboard}"
    model = genai.GenerativeModel(TEXT_MODEL)
    return model.generate_content(prompt).text.strip()
