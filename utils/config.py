import os
import google.generativeai as genai

# ------------------------------------------------
# Load API key
# ------------------------------------------------
api_key = os.environ.get("GEMINI_API_KEY")

if not api_key:
    raise RuntimeError("❌ GEMINI_API_KEY is not set.")

# ------------------------------------------------
# Configure
# ------------------------------------------------
genai.configure(api_key=api_key)

# ------------------------------------------------
# Models
# ------------------------------------------------
TEXT_MODEL = "models/gemini-2.5-flash"
IMAGE_MODEL = "models/gemini-2.5-flash-image"

# ------------------------------------------------
# Image client: use genai directly
# ------------------------------------------------
# (This works for ALL versions of google-generativeai)
image_client = genai
