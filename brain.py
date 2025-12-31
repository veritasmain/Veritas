import streamlit as st
import os
import json
import re
import requests
from io import BytesIO
from PIL import Image
from google import genai
from firecrawl import Firecrawl

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
    gemini = st.secrets.get("GEMINI_KEY") or os.environ.get("GEMINI_API_KEY")
    firecrawl = st.secrets.get("FIRECRAWL_KEY") or os.environ.get("FIRECRAWL_API_KEY")
    if not gemini:
        st.error("🔑 Gemini API Key missing!")
    return gemini, firecrawl

def clean_and_parse_json(text):
    text = re.sub(r'```json|```', '', text).strip()
    try:
        return json.loads(text, strict=False)
    except:
        # Fallback if AI gets chatty
        match = re.search(r'\{.*\}', text, re.DOTALL)
        if match:
            try: return json.loads(match.group(), strict=False)
            except: pass
        return {}

def generate_with_fallback(prompt, image=None, api_key=None):
    client = genai.Client(api_key=api_key)
    # 1.5-flash is best for speed/cost. 1.5-pro is the fallback.
    models = ["gemini-1.5-flash", "gemini-1.5-pro"]
    
    for model_name in models:
        try:
            payload = [prompt, image] if image else [prompt]
            response = client.models.generate_content(model=model_name, contents=payload)
            return response.text
        except Exception as e:
            if "429" in str(e): # Rate limit
                continue
            raise e
    raise Exception("All AI models are currently busy.")

# --- THE NEW SCRAPER (v3 Syntax) ---
@st.cache_data(ttl=3600, show_spinner=False)
def scrape_website(url, api_key):
    # 'Firecrawl' class replaces 'FirecrawlApp' in latest SDK
    app = Firecrawl(api_key=api_key)
    params = {
        'formats': ['markdown', 'screenshot'],
        'waitFor': 3000,
        'mobile': True
    }
    try:
        # scrape_url is now just scrape()
        return app.scrape(url, params=params)
    except Exception as e:
        st.error(f"Scraper error: {e}")
        return None

# --- MAIN LOGIC ---
if not st.session_state.playback_data:
    t1, t2 = st.tabs(["🔗 Paste Link", "📸 Upload Screenshot"])
    target_url, uploaded_image, trigger = None, None, False

    with t1:
        target_url = st.text_input("Product URL:", key="url_input")
        if st.button("Analyze Link", type="primary"): trigger = "link"
    with t2:
        f = st.file_uploader("Upload Image", type=["png","jpg"], key="img_input")
        if f and st.button("Analyze Screenshot", type="primary"):
            uploaded_image = Image.open(f)
            trigger = "image"

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
            source_lbl = "Analysis"

            if trigger == "link" and target_url and firecrawl_key:
                status.write("🌐 Accessing website...")
                data = scrape_website(target_url, firecrawl_key)
                
                # Accessing Document object attributes (New v3 Style)
                content = getattr(data, 'markdown', '')
                scr_url = getattr(data, 'screenshot', None)
                metadata = getattr(data, 'metadata', {})
                img_url = metadata.get('og:image') if isinstance(metadata, dict) else getattr(metadata, 'og_image', None)

                # Check for Temu/Anti-bot blocks (Empty or 'Verify' text)
                if len(content) < 300 or "verify" in content.lower():
                    if scr_url:
                        status.write("🛡️ Block detected! Switching to Vision Mode...")
                        res = requests.get(scr_url)
                        uploaded_image = Image.open(BytesIO(res.content))
                        trigger = "image" # Force Vision processing
                    else:
                        raise Exception("Site blocked access and no screenshot was provided.")
                else:
                    status.write("🧠 Reading specifications...")
                    prompt = f"Identify red flags, score (0-100), verdict, technical analysis, and review summary for this product text. Return strictly JSON.\n\n{content[:15000]}"
                    raw_text_response = generate_with_fallback(prompt, api_key=gemini_key)
                    source_lbl = target_url[:30]

            if trigger == "image" and uploaded_image:
                status.write("👁️ Analyzing visual data...")
                prompt = "Analyze this product screenshot. Identify 'product_name', 'score' (0-100), 'verdict', 'red_flags', 'detailed_technical_analysis', 'key_complaints', 'reviews_summary'. Return JSON."
                raw_text_response = generate_with_fallback(prompt, image=uploaded_image, api_key=gemini_key)

            result = clean_and_parse_json(raw_text_response)
            score = result.get("score", 0)
            
            # Update History
            st.session_state.history.append({
                "source": result.get("product_name", source_lbl),
                "score": score,
                "verdict": result.get("verdict", "Analyzed"),
                "result": result,
                "image_url": img_url or scr_url
            })
            status.update(label="✅ Analysis Complete", state="complete")

        except Exception as e:
            status.update(label="❌ Analysis Failed", state="error")
            st.error(e)
            st.stop()

    # --- DISPLAY ---
    st.divider()
    if img_url: st.image(img_url, width=250)
    
    tabs = st.tabs(["Verdict", "Reviews", "Tech Details"])
    with tabs[0]:
        color = "red" if score < 45 else "orange" if score < 80 else "green"
        st.markdown(f"<h1 style='text-align:center;color:{color}'>{score}</h1>", unsafe_allow_html=True)
        st.subheader(result.get("verdict", ""))
        for flag in result.get("red_flags", []): st.write(f"🚩 {flag}")

    with tabs[1]:
        st.info(result.get("reviews_summary", "Summary unavailable."))
        for comp in result.get("key_complaints", []): st.warning(f"• {comp}")

    with tabs[2]:
        st.markdown(result.get("detailed_technical_analysis", "No technical breakdown provided."))
