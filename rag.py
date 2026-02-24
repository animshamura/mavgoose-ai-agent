import os
import requests
from dotenv import load_dotenv
from langchain_community.vectorstores import FAISS
from langchain_core.documents import Document
from langchain_openai import OpenAIEmbeddings
from auth import get_auth_token
import pickle

load_dotenv()

# ==========================================
# ENV VARIABLES
# ==========================================
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
API_BASE_URL = os.getenv("API_BASE_URL")
STORE_ID = os.getenv("STORE_ID")

if not OPENAI_API_KEY:
    raise ValueError("❌ OPENAI_API_KEY missing in .env")
if not API_BASE_URL:
    raise ValueError("❌ API_BASE_URL missing in .env")
if not STORE_ID:
    raise ValueError("❌ STORE_ID missing in .env")

PRICING_API_URL = f"{API_BASE_URL}/api/v1/services/price-list/?store={STORE_ID}"

VECTORSTORE_PATH = "./cache/vectors"  # ✅ folder

# Make sure folder exists

def save_vectorstore(vectorstore, path=VECTORSTORE_PATH):
    with open(path, "wb") as f:
        pickle.dump(vectorstore, f)

def load_vectorstore(path=VECTORSTORE_PATH):
    with open(path, "rb") as f:
        return pickle.load(f)

# ==========================================
# 1️⃣ FETCH PRICING DATA
# ==========================================
def fetch_pricing_documents():
    """
    Fetch pricing data from API and return as LangChain Document list
    """
    auth_token = get_auth_token()
    if not auth_token:
        raise ValueError("❌ PRICING_API_AUTH_TOKEN not found")

    headers = {"Authorization": f"Bearer {auth_token}", "Content-Type": "application/json"}
    response = requests.get(PRICING_API_URL, headers=headers, timeout=20)
    response.raise_for_status()
    data = response.json()

    # Support wrapped responses
    if isinstance(data, dict):
        data = data.get("results", data.get("data", []))
    if not isinstance(data, list):
        raise ValueError("API did not return a list.")

    documents = [
        Document(
            page_content=f"""
Repair pricing:
Store: {item.get("store_name")}
Device: {item.get("brand_name")} {item.get("device_model_name")}
Repair: {item.get("repair_type_name")}
Category: {item.get("category_name")}
Price: ${item.get("price")}
""".strip()
        )
        for item in data
    ]

    return documents

# ==========================================
# 2️⃣ BUILD VECTORSTORE
# ==========================================
def build_vectorstore():
    documents = fetch_pricing_documents()
    if not documents:
        print("⚠️ No documents found. Skipping vectorstore build.")
        return None

    embeddings = OpenAIEmbeddings()
    vectorstore = FAISS.from_documents(documents, embeddings)

    # Save locally for future fast loads
    vectorstore.save_local(VECTORSTORE_PATH) 

    retriever = vectorstore.as_retriever(search_type="similarity", search_kwargs={"k": 3})
    print("✅ Vectorstore built and cached successfully")
    return retriever

# Build retriever globally
retriever = build_vectorstore()

# ==========================================
# 5️⃣ REBUILD VECTORSTORE (Optional)
# ==========================================
def rebuild_vectorstore():
    """
    Rebuild the FAISS vectorstore if pricing data updates.
    This will regenerate embeddings and update the saved .pkl file.
    """
    global retriever
    print("🔄 Rebuilding vectorstore & updating cache...")

    # Rebuild vectorstore from fresh API data
    documents = fetch_pricing_documents()
    if not documents:
        print("⚠️ No documents found. Skipping rebuild.")
        return retriever

    embeddings = OpenAIEmbeddings()
    vectorstore = FAISS.from_documents(documents, embeddings)

    # Save updated vectorstore to disk
    save_vectorstore(vectorstore, "./cache/vectorstore.pkl")

    # Update retriever globally
    retriever = vectorstore.as_retriever(
        search_type="similarity",
        search_kwargs={"k": 3}
    )

    print("✅ Vectorstore rebuilt and saved successfully")

    return retriever

