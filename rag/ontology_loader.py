import pandas as pd
from langchain_community.vectorstores import Chroma
from langchain_community.embeddings import HuggingFaceEmbeddings

def load_ontology(csv_path="ontology_multi_organ_steps.csv"):
    df = pd.read_csv(csv_path)

    texts = (
        df["activity"].astype(str)
        + " | organ: " + df["organ"].astype(str)
        + " | mechanism: " + df["mechanism"].astype(str)
    ).tolist()

    metadata = df.to_dict(orient="records")

    embedding_fn = HuggingFaceEmbeddings(
        model_name="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    )

    db = Chroma.from_texts(
        texts=texts,
        embedding=embedding_fn,
        metadatas=metadata,
        collection_name="bio_multi_organ"
    )

    return df, db
