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

VECTORSTORE_PATH = "./cache/vectors"
EMBEDDINGS_CACHE_PATH = "./cache/embeddings.pkl"

os.makedirs("./cache", exist_ok=True)

# ==========================================
# 1⃣ FETCH PRICING DATA
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
# 2⃣ CACHE / LOAD EMBEDDINGS
# ==========================================
def get_cached_embeddings(documents):
    """
    Generate embeddings for documents, cache them locally.
    """
    if os.path.exists(EMBEDDINGS_CACHE_PATH):
        with open(EMBEDDINGS_CACHE_PATH, "rb") as f:
            print("✅ Loaded cached embeddings")
            return pickle.load(f)

    embeddings_model = OpenAIEmbeddings(openai_api_key=OPENAI_API_KEY)
    embeddings_list = [embeddings_model.embed_query(doc.page_content) for doc in documents]

    with open(EMBEDDINGS_CACHE_PATH, "wb") as f:
        pickle.dump(embeddings_list, f)
        print("✅ Saved embeddings cache")

    return embeddings_list

# ==========================================
# 3⃣ BUILD VECTORSTORE
# ==========================================
def build_vectorstore():
    documents = fetch_pricing_documents()
    if not documents:
        print("⚠ No documents found. Skipping vectorstore build.")
        return None

    embeddings_list = get_cached_embeddings(documents)
    vectorstore = FAISS.from_documents(documents, OpenAIEmbeddings(openai_api_key=OPENAI_API_KEY))
    vectorstore.save_local(VECTORSTORE_PATH)
    print("✅ Vectorstore built and cached successfully")

    retriever = vectorstore.as_retriever(search_type="similarity", search_kwargs={"k": 3})
    return retriever

# ==========================================
# 4⃣ LOAD OR BUILD VECTORSTORE
# ==========================================
def load_or_build_vectorstore():
    if os.path.exists(VECTORSTORE_PATH):
        try:
            vectorstore = FAISS.load_local(VECTORSTORE_PATH, OpenAIEmbeddings(openai_api_key=OPENAI_API_KEY))
            print("✅ Vectorstore loaded from cache")
            return vectorstore.as_retriever(search_type="similarity", search_kwargs={"k": 3})
        except Exception as e:
            print("⚠ Failed to load vectorstore:", e)

    # fallback: build new
    return build_vectorstore()

# Build retriever globally
retriever = load_or_build_vectorstore()
# ==========================================
# 5⃣ REBUILD VECTORSTORE (Optional)
# ==========================================
def rebuild_vectorstore():
    """
    Force rebuild of vectorstore using fresh pricing data and embeddings.
    """
    global retriever
    print("🔄 Rebuilding vectorstore & updating cache...")

    documents = fetch_pricing_documents()
    if not documents:
        print("⚠ No documents found. Skipping rebuild.")
        return retriever

    # Remove old FAISS index folder
    if os.path.exists(VECTORSTORE_PATH):
        import shutil
        shutil.rmtree(VECTORSTORE_PATH)
        print("✅ Old vectorstore cleared")

    # Remove old embeddings cache
    if os.path.exists(EMBEDDINGS_CACHE_PATH):
        os.remove(EMBEDDINGS_CACHE_PATH)
        print("✅ Old embeddings cache cleared")

    # Create fresh embeddings
    embeddings_model = OpenAIEmbeddings(openai_api_key=OPENAI_API_KEY)
    embeddings_list = [embeddings_model.embed_query(doc.page_content) for doc in documents]

    # Build FAISS from fresh documents & embeddings
    vectorstore = FAISS.from_documents(documents, embeddings_model)
    vectorstore.save_local(VECTORSTORE_PATH)

    retriever = vectorstore.as_retriever(search_type="similarity", search_kwargs={"k": 3})
    print("✅ Vectorstore rebuilt and saved successfully")

    return retriever
