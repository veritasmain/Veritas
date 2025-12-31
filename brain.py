import streamlit as st
from google import genai
from firecrawl import Firecrawl
from PIL import Image
import json
import re
import time
import os
import requests
import traceback
from io import BytesIO

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

# --- SIDEBAR: HISTORY ---
with st.sidebar:
    st.header("📜 Recent Scans")
    if not st.session_state.history:
        st.caption("No searches yet.")
    else:
        for index, item in enumerate(reversed(st.session_state.history)):
            col_hist1, col_hist2 = st.columns([3, 1])
            with col_hist1:
                st.button(
                    f"{item['source']}", 
                    key=f"hist_btn_{index}", 
                    use_container_width=True,
                    on_click=load_history_item,
                    args=(item,)
                )
            with col_hist2:
                # Score Safety Check
                raw_score = item.get('score', 0)
                score = int(raw_score) if isinstance(raw_score, (int, float)) else 0
                
                color = "🔴" if score <= 45 else "🟠" if score < 80 else "🟢"
                st.write(f"{color} {score}")
            st.caption(f"{item['verdict']}")
            st.divider()
    
    if st.button("Clear History"):
        st.session_state.history = []
        st.session_state.playback_data = None
        st.rerun()

# --- MAIN HEADER ---
st.title("Veritas 🛡️")
st.caption("The Truth Filter for the Internet")

# --- HELPER FUNCTIONS ---
def get_api_keys():
    gemini = st.secrets.get("GEMINI_KEY") or os.environ.get("GEMINI_API_KEY")
    firecrawl = st.secrets.get("FIRECRAWL_KEY") or os.environ.get("FIRECRAWL_API_KEY")
    
    if not gemini or not firecrawl:
        st.error("🔑 API Keys missing! Check .streamlit/secrets.toml")
        st.stop()
    return gemini, firecrawl

def clean_and_parse_json(response_text):
    text = re.sub(r'```json', '', response_text)
    text = re.sub(r'```', '', text).strip()
    try:
        return json.loads(text, strict=False)
    except json.JSONDecodeError:
        sanitized_text = re.sub(r'[\x00-\x1f\x7f]', ' ', text)
        try:
            return json.loads(sanitized_text, strict=False)
        except:
            return {}

# --- SCRAPING ---
@st.cache_data(ttl="24h", show_spinner=False)
def scrape_website(url, _api_key):
    try:
        app = Firecrawl(api_key=_api_key)
        params = {'formats': ['markdown'], 'mobile': True}
        if hasattr(app, 'scrape_url'):
            return app.scrape_url(url, params=params)
        return app.scrape(url)
    except:
        return None

# --- INPUT UI ---
if not st.session_state.playback_data:
    tab1, tab2 = st.tabs(["🔗 Paste Link", "📸 Upload Screenshot"])
    target_url = None
    uploaded_image = None
    analysis_trigger = False

    with tab1:
        target_url = st.text_input("Website URL:", key="url_input")
        if st.button("Analyze Link", type="primary", use_container_width=True):
            analysis_trigger = "link"
            
    with tab2:
        uploaded_file = st.file_uploader("Upload Screenshot", type=["png", "jpg", "jpeg"], key="img_input")
        if uploaded_file and st.button("Analyze Screenshot", type="primary", use_container_width=True):
            uploaded_image = Image.open(uploaded_file)
            analysis_trigger = "image"
else:
    analysis_trigger = "playback"
    st.button("⬅️ New Search", on_click=close_playback)


# --- MAIN LOGIC ---
if analysis_trigger:
    gemini_key, firecrawl_key = get_api_keys()
    
    result = {}
    score = 0
    product_image_url = None
    
    # CASE 1: PLAYBACK
    if analysis_trigger == "playback":
        data = st.session_state.playback_data
        result = data['result']
        
        # Safe Score Loading
        raw_score = data.get('score', 0)
        score = int(raw_score) if isinstance(raw_score, (int, float)) else 0
            
        st.success(f"📂 Loaded from History: {data['source']}")

    # CASE 2: NEW ANALYSIS
    elif gemini_key and firecrawl_key:
        status_box = st.status("🕵️‍♂️ Veritas is investigating...", expanded=True)
        
        try:
            client = genai.Client(api_key=gemini_key)

            # === PATH A: LINK ===
            if analysis_trigger == "link" and target_url:
                status_box.write("🌐 Scouting the website...")
                scraped_data = None
                scrape_error = False
                
                try:
                    scraped_data = scrape_website(target_url, firecrawl_key)
                    website_content = getattr(scraped_data, 'markdown', '')
                    
                    # Trap Detection
                    is_trap = len(str(website_content)) < 500 or "verify" in str(website_content).lower()
                    if is_trap and "amazon" not in target_url.lower():
                        scrape_error = True
                except:
                    scrape_error = True

                if not scrape_error and scraped_data:
                    status_box.write("🧠 Reading page content...")
                    website_content = getattr(scraped_data, 'markdown', '')
                    meta = getattr(scraped_data, 'metadata', {})
                    product_image_url = meta.get('og:image') if isinstance(meta, dict) else getattr(meta, 'og_image', None)

                    prompt = f"""
                    You are Veritas. Analyze this product text.
                    Return JSON: product_name, score (0-100), verdict, red_flags, detailed_technical_analysis, key_complaints, reviews_summary.
                    Content: {str(website_content)[:20000]}
                    """
                    response = client.models.generate_content(model='gemini-2.0-flash', contents=prompt)
                
                else:
                    status_box.write("🛡️ Anti-bot detected. Switching to ID Investigation...")
                    prompt = f"""
                    I cannot access the page directly. URL: {target_url}
                    1. Extract the UNIQUE ID (ASIN/goods_id) from the URL.
                    2. SEARCH Google for this ID + 'reviews' + 'scam'.
                    3. Return JSON: product_name, score (0-100), verdict, red_flags, detailed_technical_analysis, key_complaints, reviews_summary.
                    """
                    response = client.models.generate_content(
                        model='gemini-2.0-flash', contents=prompt,
                        config={'tools': [{'google_search': {}}]}
                    )

            # === PATH B: IMAGE (UPDATED DETECTIVE MODE) ===
            elif analysis_trigger == "image" and uploaded_image:
                status_box.write("👁️ Reading image text & Searching web...")
                
                # --- THE FIX IS HERE: AGGRESSIVE TEXT READING PROMPT ---
                prompt = """
                1. TEXT EXTRACTION (CRITICAL): Read EVERY SINGLE WORD visible on the product or packaging in the image. (e.g. Look for "GTMEDIA", "N1", "4K", Model Numbers).
                
                2. SEARCH STRATEGY: 
                   - Use the extracted text as your primary Google Search query (e.g. "GTMEDIA N1 review").
                   - If no text is found, describe the object visually (e.g. "Black Night Vision Monocular") and search for that.
                
                3. VERIFY: Use the search results to find the REAL price and common complaints.
                
                4. RETURN JSON keys: 
                "product_name", "score", "verdict", "red_flags", "reviews_summary", "key_complaints", "detailed_technical_analysis".
                
                IMPORTANT RULE: 
                - If you find a brand name in the text, USE IT as the "product_name".
                - DO NOT return "Unknown". If you can't find the name, use the visual description (e.g. "Generic Night Vision Device").
                """
                
                response = client.models.generate_content(
                    model='gemini-2.0-flash', 
                    contents=[prompt, uploaded_image],
                    config={'tools': [{'google_search': {}}]} 
                )

            # PARSE & SAVE
            result = clean_and_parse_json(response.text)
            
            # Safe Score Handling
            raw_score = result.get("score", 0)
            score = int(raw_score) if isinstance(raw_score, (int, float)) else 0
            
            # Fallback name if AI still fails
            final_name = result.get("product_name", "Unidentified Item")
            if final_name == "Unknown": final_name = "Scanned Item (No Name)"

            st.session_state.history.append({
                "source": final_name,
                "score": score,
                "verdict": result.get("verdict", "Analysis Complete"),
                "result": result,
                "image_url": product_image_url
            })
            status_box.update(label="✅ Investigation Complete", state="complete", expanded=False)

        except Exception as e:
            status_box.update(label="❌ Error", state="error")
            st.error(f"Error details: {str(e)}")
            st.code(traceback.format_exc()) # Show full error for debugging
            st.stop()

    # --- DISPLAY ---
    st.divider()
    
    display_image = product_image_url
    if analysis_trigger == "playback": display_image = st.session_state.playback_data.get("image_url")
    if analysis_trigger == "image" and uploaded_image: display_image = uploaded_image

    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        if display_image: st.image(display_image, caption="Evidence", width=200)

    # Tabs
    t1, t2, t3 = st.tabs(["🛡️ Verdict", "💬 Reviews", "🚩 Analysis"])
    
    with t1:
        # Final safety check for score display
        safe_score = int(score) if isinstance(score, (int, float)) else 0
        color = "red" if safe_score <= 45 else "orange" if safe_score < 80 else "green"
        
        st.markdown(f"<h1 style='text-align: center; color: {color}; font-size: 80px;'>{safe_score}</h1>", unsafe_allow_html=True)
        st.markdown(f"<h3 style='text-align: center;'>{result.get('verdict', 'Analysis Done')}</h3>", unsafe_allow_html=True)
        
        if safe_score <= 45: st.error("⛔ DO NOT BUY. Poor quality or scam detected.")
        elif safe_score < 80: st.warning("⚠️ Mixed reviews. Expect quality issues.")
        else: st.success("✅ Looks safe and well-reviewed.")

    with t2:
        st.subheader("Consensus")
        if result.get("key_complaints"):
            st.error("🚨 Frequent Complaints:")
            for c in result.get("key_complaints", []): st.markdown(f"**•** {c}")
        st.info(result.get("reviews_summary", "No reviews found."))

    with t3:
        st.subheader("Details")
        for flag in result.get("red_flags", []): st.markdown(f"**•** {flag}")
        with st.expander("🔍 Technical Deep Dive"):
            st.markdown(result.get("detailed_technical_analysis", "N/A"))
