import re
import google.generativeai as genai
from utils.config import TEXT_MODEL

def load_science_terms():
    return ["glucose","insulin","atp","protein","cell","intestine","liver","enzyme"]

def correct_text(ocr_text):
    prompt = f"""
Correct spelling. Use ONLY these scientific terms when relevant:
{", ".join(load_science_terms())}

Text:
{ocr_text}
"""
    model = genai.GenerativeModel(TEXT_MODEL)
    return model.generate_content(prompt).text.strip()
