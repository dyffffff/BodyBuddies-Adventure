import streamlit as st
from rag.ontology_loader import load_ontology
from generation.story import generate_physiology_story
from image.generator import generate_images_from_story

df, db = load_ontology()

st.title("🧬 BodyBuddies Adventures")

user = st.text_input("Describe your activity:")
n = st.slider("Number of images", 1, 6, 1)
use_ocr = st.checkbox("OCR correction", True)

if st.button("Generate"):
    story = generate_physiology_story(user, db)
    st.subheader("Story")
    st.markdown(story)

    imgs = generate_images_from_story(story, n_images=n, use_ocr=use_ocr)
    for i,img in enumerate(imgs,1):
        st.image(img, caption=f"Panel {i}")
