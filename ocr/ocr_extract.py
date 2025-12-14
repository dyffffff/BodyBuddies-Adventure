from utils.config import image_client, TEXT_MODEL

def extract_text_from_image(img):
    resp = image_client.models.generate_content(
        model=TEXT_MODEL,
        contents=["Extract exact text only.", img]
    )
    return resp.text
