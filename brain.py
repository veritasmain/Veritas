import streamlit as st
from google import genai  # <--- UPDATED IMPORT
from firecrawl import Firecrawl
from PIL import Image
import json
import re
import os

# --- PAGE CONFIGURATION ---
st.set_page_config(
    page_title="Veritas",
    page_icon="🛡️",
    layout="centered",
    initial_sidebar_state="expanded" 
)

# --- STATE SETUP ---
if "history" not in st.session_state:
    st.session_state.history = []
if "playback_data" not in st.session_state:
    st.session_state.playback_data = None

# --- CALLBACKS ---
def clear_url_input():
    st.session_state.url_input = ""
    st.session_state.playback_data = None 

def clear_img_input():
    st.session_state.img_input = None
    st.session_state.playback_data = None

def load_history_item(item):
    st.session_state.playback_data = item

def close_playback():
    st.session_state.playback_data = None

# --- SIDEBAR ---
with st.sidebar:
    st.header("📜 Recent Scans")
    if not st.session_state.history:
        st.caption("No searches yet.")
    else:
        for index, item in enumerate(reversed(st.session_state.history)):
            col1, col2 = st.columns([3, 1])
            with col1:
                st.button(f"{item['source']}", key=f"h_{index}", on_click=load_history_item, args=(item,), use_container_width=True)
            with col2:
                s = item['score']
                c = "🔴" if s <= 45 else "🟠" if s < 80 else "🟢"
                st.write(f"{c} {s}")
            st.divider()
    
    if st.button("Clear History"):
        st.session_state.history = []
        st.session_state.playback_data = None
        st.rerun()

# --- HEADER ---
st.title("Veritas 🛡️")
st.caption("The Truth Filter for the Internet")

# --- CORE FUNCTIONS ---
def get_api_keys():
    """
    Tries to get keys from Streamlit secrets first, then Environment Variables.
    """
    gemini = None
    firecrawl = None
    
    # Try Secrets
    try:
        gemini = st.secrets.get("GEMINI_KEY")
        firecrawl = st.secrets.get("FIRECRAWL_KEY")
    except:
        pass

    # Fallback to Environment Variables (from setx)
    if not gemini:
        gemini = os.environ.get("GEMINI_API_KEY")
    if not firecrawl:
        firecrawl = os.environ.get("FIRECRAWL_API_KEY")

    if not gemini:
        st.error("🔑 Gemini API Key missing! Check .streamlit/secrets.toml or System Environment.")
    
    return gemini, firecrawl

def clean_and_parse_json(text):
    """Sanitizes text to prevent JSON errors from Temu/AliExpress data."""
    text = re.sub(r'```json', '', text)
    text = re.sub(r'```', '', text)
    text = text.strip()
    try:
        return json.loads(text, strict=False)
    except json.JSONDecodeError:
        text = re.sub(r'[\x00-\x1f\x7f]', ' ', text)
        try:
            return json.loads(text, strict=False)
        except:
            return {}

def generate_with_fallback(prompt, image=None, api_key=None):
    """
    Tries multiple Gemini models using the NEW google-genai library.
    """
    # Initialize the new Client
    client = genai.Client(api_key=api_key)
    
    # THE KITCHEN SINK LIST
    models_to_try = [
        "gemini-1.5-flash",
        "gemini-1.5-pro",
        "gemini-1.0-pro"
    ]

    for model_name in models_to_try:
        try:
            # Prepare contents
            contents_payload = [prompt]
            if image:
                contents_payload.append(image)

            # Generate content using new syntax
            response = client.models.generate_content(
                model=model_name,
                contents=contents_payload
            )
            return response.text

        except Exception as e:
            # Log error and try next model
            print(f"⚠️ Model {model_name} failed: {e}. Switching to next...")
            continue 
    
    raise Exception("All Gemini models are busy right now. Please wait 2 minutes and try again.")

# --- SCRAPING ---
@st.cache_data(ttl=3600, show_spinner=False)
def scrape_website(url, api_key):
    app = Firecrawl(api_key=api_key)
    try:
        # Handle new Firecrawl method signatures
        return app.scrape(url, formats=['markdown'])
    except Exception:
        # Fallback for older versions
        return app.scrape_url(url, params={'formats': ['markdown']})

# --- MAIN LOGIC ---
if not st.session_state.playback_data:
    t1, t2 = st.tabs(["🔗 Paste Link", "📸 Upload Screenshot"])
    target_url = None
    uploaded_image = None
    trigger = False

    with t1:
        target_url = st.text_input("Product URL:", key="url_input")
        if st.button("Analyze Link", type="primary"): trigger = "link"
        st.button("Reset", on_click=clear_url_input)
    with t2:
        f = st.file_uploader("Upload Image", type=["png","jpg"], key="img_input")
        if f and st.button("Analyze Screenshot", type="primary"):
            uploaded_image = Image.open(f)
            trigger = "image"
        st.button("Reset Image", on_click=clear_img_input)

else:
    trigger = "playback"
    st.button("⬅️ Back", on_click=close_playback)

if trigger:
    gemini_key, firecrawl_key = get_api_keys()
    result, score, img_url = {}, 0, None

    if trigger == "playback":
        d = st.session_state.playback_data
        result, score, img_url = d['result'], d['score'], d['image_url']
        st.success(f"📂 History: {d['source']}")

    elif gemini_key:
        status = st.status("🕵️‍♂️ Veritas is thinking...", expanded=True)
        try:
            raw_text_response = ""
            
            if trigger == "link" and target_url and firecrawl_key:
                status.write("🌐 Reading website...")
                scraped_data = scrape_website(target_url, firecrawl_key)
                if not scraped_data: raise Exception("Scrape failed. Check URL.")
                
                # --- DOCUMENT OBJECT FIX ---
                if isinstance(scraped_data, list):
                    scraped_data = scraped_data[0]
                
                if isinstance(scraped_data, dict):
                    content = scraped_data.get('markdown', '')
                    meta = scraped_data.get('metadata', {})
                else:
                    content = getattr(scraped_data, 'markdown', '')
                    meta = getattr(scraped_data, 'metadata', {})
                # ---------------------------
                
                content = str(content)
                img_url = meta.get('og:image') if isinstance(meta, dict) else getattr(meta, 'og_image', None)

                status.write("🧠 Analyzing specs...")
                prompt = f"""
                You are a product auditor. Analyze this text.
                Identify "red_flags" (short strings), "score" (0-100), "verdict" (short title), 
                "detailed_technical_analysis" (bulleted string), "key_complaints" (list), "reviews_summary".
                Return strictly valid JSON.
                
                Text: {content[:15000]} 
                """
                
                raw_text_response = generate_with_fallback(prompt, api_key=gemini_key)
                source_lbl = result.get("product_name", target_url[:30])

            elif trigger == "image" and uploaded_image:
                status.write("👁️ Analyzing image...")
                prompt = """
                Identify product. Return JSON: "product_name", "score" (0-100), "verdict", 
                "red_flags" (list), "detailed_technical_analysis", "key_complaints", "reviews_summary".
                """
                raw_text_response = generate_with_fallback(prompt, image=uploaded_image, api_key=gemini_key)
                source_lbl = "Screenshot"

            result = clean_and_parse_json(raw_text_response)
            score = result.get("score", 0)
            
            st.session_state.history.append({
                "source": result.get("product_name", "Scan"),
                "score": score,
                "verdict": result.get("verdict", "Analyzed"),
                "result": result,
                "image_url": img_url
            })
            
            status.update(label="✅ Done!", state="complete", expanded=False)

        except Exception as e:
            status.update(label="❌ Error", state="error")
            st.error(f"Error: {e}")
            st.stop()

    # --- DISPLAY ---
    st.divider()
    if img_url: st.image(img_url, width=200)
    
    t_res, t_rev, t_det = st.tabs(["Verdict", "Reviews", "Analysis"])
    with t_res:
        c = "red" if score < 45 else "orange" if score < 80 else "green"
        st.markdown(f"<h1 style='text-align:center;color:{c}'>{score}</h1>", unsafe_allow_html=True)
        st.markdown(f"<h3 style='text-align:center'>{result.get('verdict','')}</h3>", unsafe_allow_html=True)
        for f in result.get("red_flags", []): st.write(f"🚩 {f}")

    with t_rev:
        st.info(result.get("reviews_summary", "No reviews found"))
        for c in result.get("key_complaints", []): st.warning(f"• {c}")

    with t_det:
        st.markdown(result.get("detailed_technical_analysis", ""))
