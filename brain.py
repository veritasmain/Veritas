import streamlit as st
from google import genai
from firecrawl import Firecrawl
from PIL import Image
import json
import re
import time
import os
import requests
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
                # FIX 1: Ensure score is treated as a number in the sidebar too
                raw_score = item.get('score')
                score = 0
                if isinstance(raw_score, (int, float)):
                    score = int(raw_score)
                
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
    # Try Secrets first, then Environment Variables
    gemini = st.secrets.get("GEMINI_KEY") or os.environ.get("GEMINI_API_KEY")
    firecrawl = st.secrets.get("FIRECRAWL_KEY") or os.environ.get("FIRECRAWL_API_KEY")
    
    if not gemini or not firecrawl:
        st.error("🔑 API Keys missing! Check .streamlit/secrets.toml or System Environment.")
    return gemini, firecrawl

def clean_and_parse_json(response_text):
    """
    Strips Markdown, sanitizes control characters, and parses JSON safely.
    Fixes the 'Invalid control character' error common with Gemini/Temu data.
    """
    # 1. Strip Markdown code blocks
    text = re.sub(r'```json', '', response_text)
    text = re.sub(r'```', '', text)
    text = text.strip()

    # 2. Try parsing normally first
    try:
        return json.loads(text, strict=False)
    except json.JSONDecodeError:
        # 3. If that fails, scrub invisible control characters
        sanitized_text = re.sub(r'[\x00-\x1f\x7f]', ' ', text)
        try:
            return json.loads(sanitized_text, strict=False)
        except json.JSONDecodeError:
            return {}

# --- SCRAPING (CACHED MODE) ---
@st.cache_data(ttl="24h", show_spinner=False)
def scrape_website(url, _api_key):
    # Note: We use _api_key with an underscore to prevent Streamlit from hashing it
    app = Firecrawl(api_key=_api_key)
    
    params = {
        'formats': ['markdown', 'screenshot'],
        'waitFor': 5000,
        'mobile': True
    }
    
    try:
        print(f"DEBUG: Attempting to scrape {url}...")
        
        # Method 1: Try new V1 syntax (most robust)
        if hasattr(app, 'scrape_url'):
            return app.scrape_url(url, params=params)
        
        # Method 2: Fallback for your specific version
        return app.scrape(url)
            
    except Exception as e:
        raise Exception(f"FIRECRAWL ERROR: {e}")

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
        
        # FIX 2: Handle playback score safety
        raw_score = data.get('score')
        score = 0
        if isinstance(raw_score, (int, float)):
            score = int(raw_score)
            
        st.success(f"📂 Loaded from History: {data['source']}")

    # CASE 2: NEW ANALYSIS
    elif gemini_key and firecrawl_key:
        status_box = st.status("🕵️‍♂️ Veritas is investigating...", expanded=True)
        
        try:
            # Initialize Client (New Library Syntax)
            client = genai.Client(api_key=gemini_key)

            # --- PATH A: LINK (With Forensic ID Extraction) ---
            if analysis_trigger == "link" and target_url:
                status_box.write("🌐 Scouting the website...")
                
                # 1. Attempt to Scrape
                scraped_data = None
                scrape_error = False
                
                try:
                    scraped_data = scrape_website(target_url, firecrawl_key)
                    website_content = getattr(scraped_data, 'markdown', '')
                    
                    is_trap = len(str(website_content)) < 500 or "verify" in str(website_content).lower()
                    if is_trap and "amazon" not in target_url.lower():
                        scrape_error = True
                        
                except Exception:
                    scrape_error = True

                # 2. DECISION LOGIC
                if not scrape_error and scraped_data:
                    # --- SUCCESSFUL SCRAPE ---
                    status_box.write("🧠 Reading page content directly...")
                    website_content = getattr(scraped_data, 'markdown', '')
                    metadata = getattr(scraped_data, 'metadata', {})
                    
                    if isinstance(metadata, dict):
                         product_image_url = metadata.get('og:image')
                    else:
                         product_image_url = getattr(metadata, 'og_image', None)

                    prompt = f"""
                    You are Veritas. Analyze this product page text.
                    
                    PHASE 1: SPECS & CLAIMS
                    - Analyze the technical claims (batteries, materials, power).
                    - Flag "too good to be true" specs (e.g. "100TB SSD for $20").
                    
                    PHASE 2: QUALITY CHECK
                    - Is this a generic dropshipped item? 
                    - Look for grammatical errors or high-pressure sales tactics.

                    Return JSON:
                    "product_name", "score", "verdict", "red_flags", "detailed_technical_analysis", "key_complaints", "reviews_summary".

                    Content:
                    {str(website_content)[:20000]}
                    """
                    
                    response = client.models.generate_content(model='gemini-2.0-flash', contents=prompt)
                
                else:
                    # --- BLOCKED? USE "ID DETECTIVE" MODE ---
                    status_box.write("🛡️ Site blocked the scraper. Switching to ID Investigation...")
                    status_box.write("🔎 Extracting unique Product ID/SKU from URL to find matches...")
                    
                    prompt = f"""
                    I cannot access this website content directly because of anti-bot protection.
                    
                    URL: {target_url}
                    
                    YOUR MISSION:
                    1. EXTRACT THE UNIQUE ID from the URL (ASIN, goods_id, or Item ID).
                    
                    2. PERFORM A BROAD WEB SEARCH (Do NOT limit to Amazon):
                       - Search "Reddit [ID] review" to find honest forum discussions.
                       - Search the ID on YouTube to find video reviews/teardowns.
                       - Check Trustpilot, ScamAdviser, and other fraud databases.
                       - Compare the price on this URL vs. AliExpress/Alibaba listings of the same item.
                    
                    3. If no ID is found, search for the product SLUG/Name combined with "scam" or "review".
                    
                    Return JSON:
                    "product_name", "score", "verdict", "red_flags", "detailed_technical_analysis", "key_complaints", "reviews_summary".
                    """
                    
                    response = client.models.generate_content(
                        model='gemini-2.0-flash', 
                        contents=prompt,
                        config={'tools': [{'google_search': {}}]}
                    )

                # 3. PARSE & SAVE RESULTS
                result = clean_and_parse_json(response.text)
                source_label = result.get("product_name", target_url[:30])
                
                if 'product_image_url' not in locals():
                    product_image_url = None

                # FIX 3: Safety Cast before saving
                raw_score = result.get("score")
                score = 0
                if isinstance(raw_score, (int, float)):
                    score = int(raw_score)

                st.session_state.history.append({
                    "source": source_label,
                    "score": score,
                    "verdict": result.get("verdict"),
                    "result": result,
                    "image_url": product_image_url
                })


            # --- PATH B: IMAGE (With "Visual Detective" Mode) ---
            if analysis_trigger == "image" and uploaded_image:
                status_box.write("👁️ Analyzing screenshot & Searching multiple sources...")
                
                prompt = """
                1. ANALYZE the image to find the Product Name, Price, and Visual Features.
                
                2. PERFORM A BROAD WEB SEARCH (Do NOT limit to Amazon):
                   - Search for the visual match on Reddit, YouTube, TikTok, and independent blogs.
                   - Look for "scam" or "warning" posts on fraud-alert websites associated with this image.
                   - Compare prices: Is this exact image on AliExpress for 10x less?
                   - If it's a generic item, find reviews for the "White Label" version (e.g., same factory, different logo).
                
                Return JSON with keys: 
                "product_name", "score", "verdict", 
                "red_flags", "reviews_summary", "key_complaints", "detailed_technical_analysis".
                """
                
                response = client.models.generate_content(
                    model='gemini-2.0-flash', 
                    contents=[prompt, uploaded_image],
                    config={
                        'tools': [{'google_search': {}}] 
                    }
                )
                
                result = clean_and_parse_json(response.text)
                source_label = result.get("product_name", "Screenshot Upload")
                
                # FIX 4: Safety Cast before saving
                raw_score = result.get("score")
                score = 0
                if isinstance(raw_score, (int, float)):
                    score = int(raw_score)
                
                st.session_state.history.append({
                    "source": source_label,
                    "score": score,
                    "verdict": result.get("verdict"),
                    "result": result,
                    "image_url": product_image_url
                })

            status_box.update(label="✅ Analysis Complete!", state="complete", expanded=False)

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
    if analysis_trigger == "image" and uploaded_image:
         display_image = uploaded_image

    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        if display_image:
             st.image(display_image, caption="Product Verification", width=200)
    
    # --- UPDATED TAB ORDER ---
    tab1, tab2, tab3 = st.tabs(["🛡️ The Verdict", "💬 Reviews", "🚩 Reality Check"])
    
    with tab1:
        # FIX 5: Ensure score is definitely an integer here for the logic
        safe_score = int(score) if isinstance(score, (int, float)) else 0
        
        color = "red" if safe_score <= 45 else "orange" if safe_score < 80 else "green"
        st.markdown(f"<h1 style='text-align: center; color: {color}; font-size: 80px;'>{safe_score}</h1>", unsafe_allow_html=True)
        st.markdown(f"<h3 style='text-align: center;'>{result.get('verdict', 'Unknown')}</h3>", unsafe_allow_html=True)
        
        if safe_score <= 45:
            st.error("⛔ DO NOT BUY. Poor quality or scam detected.")
        elif safe_score < 80:
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
