import streamlit as st
import google.generativeai as genai
from firecrawl import Firecrawl
from PIL import Image
import json
import re
import time

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
    """Callback: Loads a specific history item into view immediately."""
    st.session_state.playback_data = item

def close_playback():
    """Callback: Exits playback mode."""
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
                score = item['score']
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
    try:
        return st.secrets["GEMINI_KEY"], st.secrets["FIRECRAWL_KEY"]
    except:
        st.error("🔑 API Keys missing! Add GEMINI_KEY and FIRECRAWL_KEY in Secrets.")
        return None, None

def clean_and_parse_json(response_text):
    """
    Strips Markdown, sanitizes control characters, and parses JSON safely.
    Fixes the 'Invalid control character' error common with Gemini/Temu data.
    """
    # 1. Strip Markdown code blocks
    text = re.sub(r'```json', '', response_text)
    text = re.sub(r'```', '', text)
    text = text.strip()

    # 2. Try parsing normally first (allowing loose control chars if possible)
    try:
        return json.loads(text, strict=False)
    except json.JSONDecodeError:
        # 3. If that fails, scrub invisible control characters (newlines/tabs inside strings)
        sanitized_text = re.sub(r'[\x00-\x1f\x7f]', ' ', text)
        try:
            return json.loads(sanitized_text, strict=False)
        except json.JSONDecodeError:
            return {}

# --- CACHED SCRAPING ---
@st.cache_data(ttl=3600, show_spinner=False)
def scrape_website(url, api_key):
    app = Firecrawl(api_key=api_key)
    return app.scrape(url, formats=['markdown'])

# --- INPUT LOGIC ---
if not st.session_state.playback_data:
    input_tab1, input_tab2 = st.tabs(["🔗 Paste Link", "📸 Upload Screenshot"])

    target_url = None
    uploaded_image = None
    analysis_trigger = False

    with input_tab1:
        target_url = st.text_input("Website URL (Amazon, Shopify, etc):", placeholder="https://...", key="url_input")
        col1, col2 = st.columns([1, 1])
        with col1:
            if st.button("Analyze Link", type="primary", use_container_width=True):
                analysis_trigger = "link"
        with col2:
            st.button("New Link", type="primary", use_container_width=True, on_click=clear_url_input)

    with input_tab2:
        uploaded_file = st.file_uploader("Upload an ad or text screenshot", type=["png", "jpg", "jpeg"], key="img_input")
        col3, col4 = st.columns([1, 1])
        with col3:
            if uploaded_file and st.button("Analyze Screenshot", type="primary", use_container_width=True):
                uploaded_image = Image.open(uploaded_file)
                analysis_trigger = "image"
        with col4:
            st.button("New Upload", type="primary", use_container_width=True, on_click=clear_img_input)

else:
    analysis_trigger = "playback"
    st.button("⬅️ Back to Search", on_click=close_playback)


# --- MAIN LOGIC CONTROLLER ---
if analysis_trigger:
    gemini_key, firecrawl_key = get_api_keys()
    
    result = {}
    score = 0
    product_image_url = None
    
    # CASE 1: PLAYBACK
    if analysis_trigger == "playback":
        data = st.session_state.playback_data
        result = data['result']
        score = data['score']
        st.success(f"📂 Loaded from History: {data['source']}")

    # CASE 2: NEW ANALYSIS
    elif gemini_key and firecrawl_key:
        status_box = st.status("🕵️‍♂️ Veritas is investigating...", expanded=True)
        
        try:
            # --- PATH A: LINK ---
            if analysis_trigger == "link" and target_url:
                status_box.write("🌐 Scouting the website...")
                scraped_data = scrape_website(target_url, firecrawl_key)
                
                if not scraped_data:
                    raise Exception("Could not connect to website.")

                try:
                    website_content = scraped_data.markdown
                    metadata = scraped_data.metadata
                except AttributeError:
                    website_content = scraped_data.get('markdown', '')
                    metadata = scraped_data.get('metadata', {})
                
                website_content = str(website_content)
                
                if metadata:
                    if isinstance(metadata, dict):
                         product_image_url = metadata.get('og:image')
                    else:
                         product_image_url = getattr(metadata, 'og_image', None)
                
                status_box.write("🧠 Analyzing technical specs & fraud...")
                genai.configure(api_key=gemini_key)
                
                # --- UPDATED TO GEMINI 2.0 FLASH ---
                # This model has the high free tier (1500 req/day) replacing 1.5
                model = genai.GenerativeModel('gemini-2.0-flash')
                
                prompt = f"""
                You are Veritas, a technical product auditor. Analyze this webpage content.

                PHASE 1: TECHNICAL DEEP DIVE
                - Read specs CAREFULLY. Note context (e.g. "2400W max" vs "1200W rated").
                - Identify convertible features before flagging them as misleading.
                
                PHASE 2: QUALITY & CONSISTENCY
                - Judge the intrinsic quality. Is this a cheap dropshipped item?
                - If reviews mention failure ("broke", "weak"), Score MUST be < 45.

                OUTPUT FORMATTING:
                1. "red_flags": Snappy bullet points (max 8 words).
                2. "detailed_technical_analysis": Must be a SINGLE STRING formatted with bullets (e.g. "- Observation 1\\n- Observation 2").

                Return JSON:
                - "product_name": Short name.
                - "score": 0-100.
                - "verdict": Short title.
                - "red_flags": [List of snappy strings].
                - "detailed_technical_analysis": "Bulleted string."
                - "key_complaints": [List of specific user complaints].
                - "reviews_summary": "Short summary text."

                Content:
                {website_content}
                """
                
                try:
                    response = model.generate_content(prompt)
                except Exception as e:
                    if "429" in str(e):
                        time.sleep(2)
                        response = model.generate_content(prompt)
                    else:
                        raise e

                result = clean_and_parse_json(response.text)
                
                source_label = result.get("product_name", target_url[:30])

                st.session_state.history.append({
                    "source": source_label,
                    "score": result.get("score", 0),
                    "verdict": result.get("verdict"),
                    "result": result,
                    "image_url": product_image_url
                })


            # --- PATH B: IMAGE ---
            elif analysis_trigger == "image" and uploaded_image:
                status_box.write("👁️ Scanning visual elements...")
                genai.configure(api_key=gemini_key)
                
                # --- UPDATED TO GEMINI 2.0 FLASH ---
                model = genai.GenerativeModel('gemini-2.0-flash')
                
                prompt = """
                Analyze this image. Identify the product.
                Return JSON with keys: 
                "product_name", "score", "verdict", 
                "red_flags", "reviews_summary", "key_complaints", "detailed_technical_analysis".
                """
                response = model.generate_content([prompt, uploaded_image])
                result = clean_and_parse_json(response.text)
                
                source_label = result.get("product_name", "Screenshot Upload")
                
                st.session_state.history.append({
                    "source": source_label,
                    "score": result.get("score", 0),
                    "verdict": result.get("verdict"),
                    "result": result,
                    "image_url": None
                })

            status_box.update(label="✅ Analysis Complete!", state="complete", expanded=False)
            score = result.get("score", 0)

        except Exception as e:
            status_box.update(label="❌ Error", state="error")
            if "429" in str(e):
                st.error("⏳ You are scanning too fast! Please wait a few seconds and try again.")
            else:
                st.error(f"Something went wrong: {e}")
            st.stop()


    # --- DISPLAY RESULTS ---
    st.divider()
    
    display_image = product_image_url if 'product_image_url' in locals() and product_image_url else None
    if analysis_trigger == "playback":
         display_image = st.session_state.playback_data.get("image_url")

    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        if display_image:
             st.image(display_image, caption="Product Verification", width=200)
    
    # --- UPDATED TAB ORDER ---
    tab1, tab2, tab3 = st.tabs(["🛡️ The Verdict", "💬 Reviews", "🚩 Reality Check"])
    
    with tab1:
        color = "red" if score <= 45 else "orange" if score < 80 else "green"
        st.markdown(f"<h1 style='text-align: center; color: {color}; font-size: 80px;'>{score}</h1>", unsafe_allow_html=True)
        st.markdown(f"<h3 style='text-align: center;'>{result.get('verdict', 'Unknown')}</h3>", unsafe_allow_html=True)
        
        if score <= 45:
            st.error("⛔ DO NOT BUY. Poor quality or scam detected.")
        elif score < 80:
            st.warning("⚠️ Mixed reviews. Expect quality issues.")
        else:
            st.success("✅ Looks safe and well-reviewed.")

    with tab2:
        st.subheader("Consensus")
        complaints = result.get("key_complaints", [])
        if complaints:
            st.error("🚨 Frequent Complaints:")
            for complaint in complaints:
                st.markdown(f"**•** {complaint}")
        st.info(result.get("reviews_summary", "No reviews found."))

    with tab3:
        st.subheader("At a Glance")
        for flag in result.get("red_flags", []):
            st.markdown(f"**•** {flag}")
        
        st.write("") 
        with st.expander("🔍 View Detailed Technical Analysis"):
            st.markdown(result.get("detailed_technical_analysis", "No detailed analysis available."))
