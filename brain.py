import streamlit as st
from google import genai
from firecrawl import Firecrawl
from PIL import Image
import json
import re
import os
import requests
from io import BytesIO

# --- PAGE CONFIGURATION (RESTORED) ---
st.set_page_config(
    page_title="Veritas",
    page_icon="🛡️",
    layout="centered",
    initial_sidebar_state="expanded" 
)

# --- STATE SETUP (RESTORED) ---
if "history" not in st.session_state:
    st.session_state.history = []
if "playback_data" not in st.session_state:
    st.session_state.playback_data = None

# --- CALLBACKS (RESTORED) ---
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

# --- SIDEBAR (RESTORED RECENT SCANS) ---
with st.sidebar:
    st.header("📜 Recent Scans")
    if not st.session_state.history:
        st.caption("No searches yet.")
    else:
        for index, item in enumerate(reversed(st.session_state.history)):
            col1, col2 = st.columns([3, 1])
            with col1:
                # History button
                st.button(f"{item['source']}", key=f"h_{index}", on_click=load_history_item, args=(item,), use_container_width=True)
            with col2:
                # Score indicator
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

# --- CORE FUNCTIONS (UPDATED FOR NEW APIS) ---
def get_api_keys():
    gemini = st.secrets.get("GEMINI_KEY") or os.environ.get("GEMINI_API_KEY")
    firecrawl = st.secrets.get("FIRECRAWL_KEY") or os.environ.get("FIRECRAWL_API_KEY")
    if not gemini:
        st.error("🔑 API Keys missing!")
    return gemini, firecrawl

def clean_and_parse_json(text):
    text = re.sub(r'```json|```', '', text).strip()
    try:
        return json.loads(text, strict=False)
    except:
        # Emergency backup for messy AI responses
        match = re.search(r'\{.*\}', text, re.DOTALL)
        if match:
            try: return json.loads(match.group(), strict=False)
            except: pass
        return {}

def generate_with_fallback(prompt, image=None, api_key=None):
    client = genai.Client(api_key=api_key)
    # Tries Flash first (fast/cheap), then Pro
    for model_name in ["gemini-1.5-flash", "gemini-1.5-pro"]:
        try:
            payload = [prompt, image] if image else [prompt]
            response = client.models.generate_content(model=model_name, contents=payload)
            return response.text
        except Exception as e:
            if "429" in str(e): continue
            raise e
    raise Exception("AI is overloaded. Try again in 10s.")

@st.cache_data(ttl=3600, show_spinner=False)
def scrape_website(url, api_key):
    app = Firecrawl(api_key=api_key)
    # Firecrawl v3: scrape() instead of scrape_url()
    params = {'formats': ['markdown', 'screenshot'], 'waitFor': 3000, 'mobile': True}
    try:
        return app.scrape(url, params=params)
    except:
        return None

# --- MAIN LOGIC ---
if not st.session_state.playback_data:
    t1, t2 = st.tabs(["🔗 Paste Link", "📸 Upload Screenshot"])
    target_url, uploaded_image, trigger = None, None, False

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
    gemini_key, fc_key = get_api_keys()
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

            if trigger == "link" and target_url and fc_key:
                status.write("🌐 Accessing website...")
                data = scrape_website(target_url, fc_key)
                content = getattr(data, 'markdown', '')
                scr_url = getattr(data, 'screenshot', None)
                metadata = getattr(data, 'metadata', {})
                img_url = metadata.get('og_image') if hasattr(metadata, 'og_image') else metadata.get('og:image')

                # Temu/Anti-bot logic (Vision Fallback)
                if len(content) < 300 or "verify" in content.lower():
                    if scr_url:
                        status.write("🛡️ Block detected! Reading screenshot...")
                        res = requests.get(scr_url)
                        uploaded_image = Image.open(BytesIO(res.content))
                        trigger = "image"
                        img_url = scr_url
                    else:
                        raise Exception("Scraper blocked and no screenshot available.")
                else:
                    status.write("🧠 Analyzing specs...")
                    prompt = f"Extract red flags, score (0-100), verdict, detailed analysis, and key complaints in JSON format:\n\n{content[:15000]}"
                    raw_text_response = generate_with_fallback(prompt, api_key=gemini_key)
                    source_lbl = target_url[:30]

            if trigger == "image" and uploaded_image:
                status.write("👁️ Analyzing visual evidence...")
                prompt = "Analyze this product image. Provide 'product_name', 'score' (0-100), 'verdict', 'red_flags', 'detailed_technical_analysis', 'key_complaints', 'reviews_summary' in JSON format."
                raw_text_response = generate_with_fallback(prompt, image=uploaded_image, api_key=gemini_key)

            result = clean_and_parse_json(raw_text_response)
            score = result.get("score", 0)
            
            # Save to history list
            st.session_state.history.append({
                "source": result.get("product_name", source_lbl),
                "score": score,
                "verdict": result.get("verdict", "Analyzed"),
                "result": result,
                "image_url": img_url
            })
            status.update(label="✅ Analysis Complete", state="complete")
        except Exception as e:
            status.update(label="❌ Error", state="error")
            st.error(e)
            st.stop()

    # --- DISPLAY (RESTORED ORIGINAL TABS) ---
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
