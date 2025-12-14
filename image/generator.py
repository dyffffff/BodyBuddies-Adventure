from io import BytesIO
from PIL import Image as PilImage
from utils.config import IMAGE_MODEL, image_client
from generation.comic_panels import build_comic_scenes_from_story
from ocr.ocr_extract import extract_text_from_image
from ocr.ocr_correct import correct_text

def generate_images_from_story(story, n_images=1, use_ocr=True):
    panels = build_comic_scenes_from_story(story, n_images)
    imgs = []

    for p in panels:
        resp = image_client.models.generate_content(
            model=IMAGE_MODEL,
            contents=[f"Draw comic panel:\n{p['scene']}"]
        )
        raw = None
        for c in resp.candidates:
            for part in c.content.parts:
                if getattr(part, "inline_data", None):
                    raw = PilImage.open(BytesIO(part.inline_data.data))

        if use_ocr:
            ocr_text = extract_text_from_image(raw)
            corrected = correct_text(ocr_text)
            resp2 = image_client.models.generate_content(
                model=IMAGE_MODEL,
                contents=[f"Redraw with corrected text:\n{corrected}"]
            )
            for c in resp2.candidates:
                for part in c.content.parts:
                    if getattr(part, "inline_data", None):
                        raw = PilImage.open(BytesIO(part.inline_data.data))
        imgs.append(raw)

    return imgs
