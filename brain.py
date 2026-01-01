import streamlit as st
from google import genai
from firecrawl import Firecrawl
from PIL import Image
import json
import re
import traceback
import os
import time
from urllib.parse import urlparse
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
if "uploader_id" not in st.session_state:
    st.session_state.uploader_id = 0

# --- CALLBACKS ---
def clear_url_input():
    st.session_state.url_input = ""
    st.session_state.playback_data = None 

def clear_img_input():
    st.session_state.uploader_id += 1
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
            col1, col2 = st.columns([3, 1])
            with col1:
                # Truncate long names for the button label
                display_name = item['source']
                if len(display_name) > 25:
                    display_name = display_name[:25] + "..."
                
                st.button(
                    display_name, 
                    key=f"hist_btn_{index}", 
                    use_container_width=True,
                    on_click=load_history_item,
                    args=(item,)
                )
            with col2:
                # Scores are now standardized. 40 is the baseline for Generic/Unknown.
                raw = item.get('score', 40)
                score = int(raw)
                color = "🔴" if score <= 45 else "🟠" if score < 80 else "🟢"
                st.write(f"{color} {score}")
            st.caption(f"{item.get('standardized_verdict', item['verdict'])}")
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
    match = re.search(r'```json\s*(.*?)\s*```', response_text, re.DOTALL)
    text = match.group(1) if match else response_text.strip()
    try:
        return json.loads(text, strict=False)
    except json.JSONDecodeError:
        sanitized = re.sub(r'[\x00-\x1f\x7f]', ' ', text)
        try:
            return json.loads(sanitized, strict=False)
        except:
            return {}

def extract_score_safely(result_dict):
    raw = result_dict.get("score")
    # DEFAULT TO 40 (High Risk) if scoring fails. 
    # This aligns "Unknown" items with "Generic" items.
    score = 40 
    
    if isinstance(raw, (int, float)):
        score = int(raw)
    elif isinstance(raw, str):
        nums = re.findall(r'\d+', raw)
        if nums:
            score = int(nums[0])
            
    return max(0, min(100, 5 * round(score / 5)))

def get_standardized_verdict(score):
    if score <= 25:
        return "⛔ CRITICAL WARNING: SCAM / FAKE"
    elif score <= 45:
        return "⚠️ HIGH RISK: LIKELY MISLEADING"
    elif score <= 65:
        return "⚠️ CAUTION: MEDIOCRE QUALITY"
    elif score <= 85:
        return "✅ GOOD: SAFE TO BUY"
    else:
        return "🌟 EXCELLENT: TOP TIER AUTHENTIC"

def extract_name_from_url(url):
    """
    Improved extraction: Tries to find readable words in the URL.
    """
    try:
        path = urlparse(url).path
        # Remove file extensions like .html
        if path.endswith('.html'): path = path[:-5]
        
        segments = [s for s in path.split('/') if s and not s.isdigit()]
        if segments:
            # Find the longest segment, it's usually the title
            slug = max(segments, key=len)
            # Replace dashes with spaces and Title Case it
            clean_name = slug.replace('-', ' ').replace('_', ' ').title()
            # If it's too short, it's probably junk
            if len(clean_name) > 3:
                return clean_name
    except:
        pass
    
    # Fallback to ID extraction if words fail
    ali_match = re.search(r'/item/(\d+)\.html', url)
    if ali_match: return f"AliExpress Item {ali_match.group(1)}"
    
    amz_match = re.search(r'/(dp|gp/product)/([A-Z0-9]{10})', url)
    if amz_match: return f"Amazon Item {amz_match.group(2)}"
    
    return "Unidentified Item"

def sanitize_product_name(name):
    """
    Cleans up garbage names returned by the AI.
    """
    if not name or len(name) > 60: return "Unidentified Item"
    
    bad_phrases = ["unable to", "cannot determine", "based on url", "placeholder", "unknown", "generic"]
    if any(p in name.lower() for p in bad_phrases):
        return "Unidentified Item"
        
    return name

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
        c1, c2 = st.columns([1, 1])
        with c1:
            if st.button("Analyze Link", type="primary", use_container_width=True):
                analysis_trigger = "link"
        with c2:
            st.button("New Link", type="secondary", use_container_width=True, on_click=clear_url_input)
            
    with tab2:
        current_key = f"uploader_{st.session_state.uploader_id}"
        uploaded_file = st.file_uploader("Upload Screenshot", type=["png", "jpg", "jpeg"], key=current_key)
        
        c3, c4 = st.columns([1, 1])
        with c3:
            if uploaded_file and st.button("Analyze Screenshot", type="primary", use_container_width=True):
                uploaded_image = Image.open(uploaded_file)
                analysis_trigger = "image"
        with c4:
            st.button("New Upload", type="secondary", use_container_width=True, on_click=clear_img_input)

else:
    analysis_trigger = "playback"
    st.button("⬅️ New Search", on_click=close_playback)


# --- MAIN LOGIC ---
if analysis_trigger:
    gemini_key, firecrawl_key = get_api_keys()
    result = {}
    score = 40 # Default baseline
    product_image_url = None
    
    if analysis_trigger == "playback":
        data = st.session_state.playback_data
        result = data['result']
        score = extract_score_safely(result)
        st.success(f"📂 Loaded from History: {data['source']}")

    elif gemini_key and firecrawl_key:
        status_box = st.status("Verifying...", expanded=False)
        
        try:
            client = genai.Client(api_key=gemini_key)

            # === PATH A: LINK ANALYSIS ===
            if analysis_trigger == "link" and target_url:
                
                # Extract a readable fallback name immediately
                fallback_name = extract_name_from_url(target_url)
                
                is_hostile = "aliexpress" in target_url.lower() or "temu" in target_url.lower()
                
                scraped_data = None
                scrape_error = True 
                content = ""
                
                # Only try scraping if not hostile
                if not is_hostile:
                    MAX_RETRIES = 3
                    for attempt in range(MAX_RETRIES):
                        try:
                            scraped_data = scrape_website(target_url, firecrawl_key)
                            if scraped_data:
                                content = getattr(scraped_data, 'markdown', '')
                                content_str = str(content).lower()
                                is_trap = len(str(content)) < 600 or \
                                          any(x in content_str for x in ["captcha", "robot check", "login", "access denied"])
                                
                                if not is_trap:
                                    scrape_error = False
                                    break 
                                else:
                                    time.sleep(1)
                        except:
                            pass
                
                # SHARED PROMPT LOGIC FOR CONSISTENCY
                consistency_rules = """
                CONSISTENCY RULES:
                1. **Generic/Unbranded/Clone:** MAX SCORE = 40.
                2. **Impossible Specs (Fake 4K, 16TB for $20):** MAX SCORE = 25.
                3. **Known Verified Brand (Sony, Dell):** BASE SCORE = 80.
                4. **Unknown/Not Found:** SCORE = 40 (Risk).
                """

                # 1. Primary Analysis (If Scrape Success)
                if not scrape_error and scraped_data:
                    meta = getattr(scraped_data, 'metadata', {})
                    product_image_url = meta.get('og:image') if isinstance(meta, dict) else getattr(meta, 'og_image', None)

                    prompt = f"""
                    You are Veritas. Analyze this product.
                    {consistency_rules}
                    
                    TASK:
                    1. Extract Exact "product_name". If unknown, use "Generic Device".
                    2. Analyze Reviews.
                    3. JSON Output (Title Case Keys).

                    Return JSON: product_name, score, detailed_technical_analysis, key_complaints, reviews_summary.
                    Content: {str(content)[:25000]}
                    """
                    response = client.models.generate_content(
                        model='gemini-2.0-flash', 
                        contents=prompt,
                        config={'temperature': 0.0}
                    )
                    temp_result = clean_and_parse_json(response.text)
                    
                    if temp_result.get("product_name") in ["Unknown", "Generic"] or extract_score_safely(temp_result) == 50:
                         scrape_error = True
                    else:
                        result = temp_result

                # 2. Backup Deep Search (If Scrape Failed OR Hostile)
                if scrape_error or not result:
                    search_query_1 = f"{fallback_name} reviews reddit problems"
                    search_query_2 = f"{fallback_name} specs vs reality"
                    
                    prompt = f"""
                    I cannot access page directly. 
                    Target: {fallback_name}
                    
                    MANDATORY: SEARCH for "{search_query_1}" AND "{search_query_2}".
                    
                    {consistency_rules}
                    
                    OUTPUT:
                    - "product_name": EXTRACT REAL NAME. Do not use ID.
                    - "detailed_technical_analysis": JSON OBJECT.

                    Return JSON: product_name, score, detailed_technical_analysis, key_complaints, reviews_summary.
                    """
                    response = client.models.generate_content(
                        model='gemini-2.0-flash', 
                        contents=prompt,
                        config={'tools': [{'google_search': {}}], 'temperature': 0.0}
                    )
                    result = clean_and_parse_json(response.text)

            # === PATH B: IMAGE ANALYSIS ===
            elif analysis_trigger == "image" and uploaded_image:
                prompt = f"""
                YOU ARE A FORENSIC ANALYST.
                
                STEP 1: READ TEXT & IDENTIFY PRODUCT from image.
                STEP 2: SEARCH GOOGLE for the identified product.
                
                CONSISTENCY RULES:
                1. **Generic/Unbranded/Clone:** MAX SCORE = 40.
                2. **Impossible Specs:** MAX SCORE = 25.
                3. **Known Brand:** BASE SCORE = 80.

                RETURN JSON:
                {{
                    "product_name": "Brand Model",
                    "score": 0-100,
                    "reviews_summary": ["Point 1", "Point 2"],
                    "key_complaints": ["Complaint 1"],
                    "detailed_technical_analysis": {{"Price Check": ["..."], "Spec Verify": ["..."]}}
                }}
                """
                response = client.models.generate_content(
                    model='gemini-2.0-flash', 
                    contents=[prompt, uploaded_image],
                    config={'tools': [{'google_search': {}}], 'temperature': 0.1} 
                )
                result = clean_and_parse_json(response.text)

            # PARSE & SAVE
            score = extract_score_safely(result)
            standardized_verdict = get_standardized_verdict(score)
            
            # --- NAME SANITIZATION ---
            ai_name = result.get("product_name", "Unknown")
            clean_name = sanitize_product_name(ai_name)
            
            # If the AI failed to give a good name, fall back to our URL extraction
            if clean_name == "Unidentified Item" and 'fallback_name' in locals() and fallback_name != "Unidentified Item":
                 final_name = fallback_name
            else:
                 final_name = clean_name

            st.session_state.history.append({
                "source": final_name,
                "score": score,
                "verdict": standardized_verdict,
                "standardized_verdict": standardized_verdict,
                "result": result,
                "image_url": product_image_url
            })
            status_box.update(label="✅ Complete", state="complete", expanded=False)

        except Exception as e:
            status_box.update(label="❌ Error", state="error")
            st.error(f"Details: {str(e)}")
            st.code(traceback.format_exc())
            st.stop()

    # --- DISPLAY ---
    st.divider()
    
    display_image = product_image_url
    if analysis_trigger == "playback": display_image = st.session_state.playback_data.get("image_url")
    if analysis_trigger == "image" and uploaded_image: display_image = uploaded_image

    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        if display_image: st.image(display_image, caption="Evidence", width=200)

    t1, t2 = st.tabs(["🛡️ Verdict", "💬 Reviews"])
    
    with t1:
        color = "red" if score <= 45 else "orange" if score < 80 else "green"
        st.markdown(f"<h1 style='text-align: center; color: {color}; font-size: 80px;'>{score}<span style='font-size: 40px; color: grey;'>/100</span></h1>", unsafe_allow_html=True)
        st.markdown(f"<h3 style='text-align: center;'>{standardized_verdict}</h3>", unsafe_allow_html=True)
        
        with st.expander("ℹ️ Why is this score different on other sites?"):
            st.info("""
            **Veritas scores the Transaction Safety, not just the Item.**
            * **Amazon/Walmart:** Safer returns, warranty, faster shipping.
            * **Temu/AliExpress:** Deducts max 10 points for shipping/return risks.
            """)

        if score <= 45: st.error("⛔ DO NOT BUY. Poor quality/scam.")
        elif score < 80: st.warning("⚠️ Mixed reviews. Expect issues.")
        else: st.success("✅ Safe and well-reviewed.")
        
        st.divider()
        
        with st.expander("🔍 Click for Deep Dive Analysis"):
            analysis_data = result.get("detailed_technical_analysis", {})
            if isinstance(analysis_data, dict):
                for header, bullets in analysis_data.items():
                    clean_header = header.replace("_", " ").title()
                    st.markdown(f"### {clean_header}") 
                    if isinstance(bullets, list):
                        for bullet in bullets:
                            st.markdown(f"- {bullet}")
                    else:
                        st.markdown(str(bullets))
                    st.markdown("") 
            else:
                st.markdown(str(analysis_data))

    with t2:
        st.subheader("Consensus")
        complaints_data = result.get("key_complaints")
        if complaints_data:
            if isinstance(complaints_data, list):
                for c in complaints_data:
                    st.markdown(f"**🚨** {c}")
            elif isinstance(complaints_data, str):
                st.markdown(f"**🚨** {complaints_data}")
        
        st.divider()
        st.subheader("Source Summaries")
        reviews_data = result.get("reviews_summary", [])
        if isinstance(reviews_data, list):
            for review in reviews_data:
                st.markdown(f"**•** {review}")
        elif isinstance(reviews_data, str):
            st.markdown(reviews_data)
        else:
            st.caption("No review data found.")
