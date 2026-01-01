import streamlit as st
from google import genai
from firecrawl import Firecrawl
from PIL import Image
import json
import re
import traceback
import os
import time
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
                st.button(
                    f"{item['source']}", 
                    key=f"hist_btn_{index}", 
                    use_container_width=True,
                    on_click=load_history_item,
                    args=(item,)
                )
            with col2:
                raw = item.get('score', 0)
                score = int(raw) if isinstance(raw, (int, float)) else 0
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
    """
    Extracts score. Returns 0 if no score found (signals failure).
    """
    raw = result_dict.get("score")
    score = 0 
    
    if isinstance(raw, (int, float)):
        score = int(raw)
    elif isinstance(raw, str):
        nums = re.findall(r'\d+', raw)
        if nums:
            score = int(nums[0])
            
    # Round to nearest 5
    score = 5 * round(score / 5)
    return max(0, min(100, score))

def extract_id_from_url(url):
    """
    Robust extraction of ID from URL to ensure we never say "Unknown Product".
    """
    # AliExpress Pattern
    ali_match = re.search(r'/item/(\d+)\.html', url)
    if ali_match: 
        return f"AliExpress Item {ali_match.group(1)}"
    
    # Amazon Pattern (ASIN)
    amz_match = re.search(r'/(dp|gp/product)/([A-Z0-9]{10})', url)
    if amz_match: 
        return f"Amazon Item {amz_match.group(2)}"
    
    # Temu Pattern (goods_id)
    temu_match = re.search(r'goods_id=(\d+)', url)
    if temu_match:
        return f"Temu Item {temu_match.group(1)}"

    return ""

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
    score = 0
    product_image_url = None
    
    # CASE 1: PLAYBACK (History)
    if analysis_trigger == "playback":
        data = st.session_state.playback_data
        result = data['result']
        score = extract_score_safely(result)
        st.success(f"📂 Loaded from History: {data['source']}")

    # CASE 2: NEW ANALYSIS
    elif gemini_key and firecrawl_key:
        status_box = st.status("🕵️‍♂️ Veritas is investigating...", expanded=True)
        try:
            client = genai.Client(api_key=gemini_key)

            # === PATH A: LINK ANALYSIS ===
            if analysis_trigger == "link" and target_url:
                
                # 1. Pre-calculate fallback name (Crucial Step)
                fallback_name = extract_id_from_url(target_url)
                
                scraped_data = None
                scrape_error = True # Assume error until proven success
                content = ""
                
                # --- PITBULL RETRY LOOP (5 Attempts) ---
                MAX_RETRIES = 5
                for attempt in range(MAX_RETRIES):
                    status_box.write(f"🌐 Scouting website (Attempt {attempt+1}/{MAX_RETRIES})...")
                    
                    try:
                        scraped_data = scrape_website(target_url, firecrawl_key)
                        
                        if scraped_data:
                            content = getattr(scraped_data, 'markdown', '')
                            # Detailed Trap Detection
                            content_str = str(content).lower()
                            is_trap = len(str(content)) < 600 or \
                                      "captcha" in content_str or \
                                      "robot check" in content_str or \
                                      "login" in content_str or \
                                      "access denied" in content_str or \
                                      "verify you are human" in content_str
                            
                            if not is_trap:
                                scrape_error = False
                                status_box.write("🔓 Access Granted! Analyzing data...")
                                break # Success! Stop retrying.
                            else:
                                status_box.write(f"⚠️ Anti-bot hit. Retrying...")
                                time.sleep(1.5) # Slight pause
                    except:
                        pass
                
                # 3. Attempt Primary Analysis (Only if Scrape Succeeded)
                if not scrape_error and scraped_data:
                    status_box.write("🧠 Reading content...")
                    meta = getattr(scraped_data, 'metadata', {})
                    product_image_url = meta.get('og:image') if isinstance(meta, dict) else getattr(meta, 'og_image', None)

                    prompt = f"""
                    You are Veritas. Analyze this product.
                    
                    STRICT SCORING (MULTIPLES OF 5):
                    - 0-25: SCAM/FAKE/DANGEROUS.
                    - 30-45: TRASH/BROKEN/LOW QUALITY.
                    - 50-75: AVERAGE/DECENT.
                    - 80-100: EXCELLENT (Verified Authentic).
                    **Deduct max 10 points for platform risk (Temu/AliExpress).**

                    TASK 1: EXACT NAMING
                    - "product_name": Use exact Brand & Model. If blocked/unknown, return "Generic".

                    TASK 2: REVIEWS
                    - "reviews_summary": LIST of strings. Cite sources.
                    - "key_complaints": LIST of strings. Attribute to "User reviews".

                    Return JSON: product_name, score, verdict, red_flags, detailed_technical_analysis, key_complaints, reviews_summary.
                    Content: {str(content)[:25000]}
                    """
                    # Temperature 0 forces consistency
                    response = client.models.generate_content(
                        model='gemini-2.0-flash', 
                        contents=prompt,
                        config={'temperature': 0.0}
                    )
                    temp_result = clean_and_parse_json(response.text)
                    temp_name = temp_result.get("product_name", "Unknown")
                    temp_score = extract_score_safely(temp_result)

                    # 4. ZOMBIE CHECK: Reject lazy/blocked results
                    # If score is exactly 50 (neutral) AND name is Generic, scrape failed silently.
                    if temp_name in ["Unknown", "Generic", "Product Page"] or temp_score == 50:
                         scrape_error = True
                         status_box.write("⚠️ Data insufficient. Forcing Backup Search...")
                    else:
                        result = temp_result

                # 5. Backup Search (If ALL retries failed OR Zombie Check failed)
                if scrape_error or not result:
                    status_box.write("🛡️ Direct access failed. Switching to ID Investigation...")
                    
                    prompt = f"""
                    I cannot access page directly (Scraper Blocked). URL: {target_url}
                    1. EXTRACT ID/ASIN from URL.
                    2. SEARCH Google for ID + "Review" + "Reddit".
                    
                    STRICT SCORING (MULTIPLES OF 5):
                    - 0-25: SCAM.
                    - 30-45: TRASH/BROKEN.
                    - 50-75: AVERAGE.
                    - 80-85: EXCELLENT (CAPPED AT 85).
                    
                    OUTPUT REQUIREMENTS:
                    - "product_name": EXACT BRAND & MODEL. Do NOT use "Unknown". Use the Google snippet title.
                    - "verdict": SHORT & PUNCHY (Max 15 words).
                    - "reviews_summary": LIST of strings.
                    - "detailed_technical_analysis": JSON OBJECT.

                    Return JSON: product_name, score, verdict, red_flags, detailed_technical_analysis, key_complaints, reviews_summary.
                    """
                    response = client.models.generate_content(
                        model='gemini-2.0-flash', 
                        contents=prompt,
                        config={'tools': [{'google_search': {}}], 'temperature': 0.0}
                    )
                    result = clean_and_parse_json(response.text)

            # === PATH B: IMAGE ANALYSIS ===
            elif analysis_trigger == "image" and uploaded_image:
                status_box.write("👁️ Analyzing visual evidence...")
                prompt = """
                YOU ARE A FORENSIC ANALYST.
                STEP 1: IDENTIFY & SEARCH Google for item in image.
                STEP 2: SCORE (0-100). Max 10pt deduction for platform risk.
                STEP 3: Return JSON with "product_name", "score", "verdict", "reviews_summary", "detailed_technical_analysis".
                """
                response = client.models.generate_content(
                    model='gemini-2.0-flash', 
                    contents=[prompt, uploaded_image],
                    config={'tools': [{'google_search': {}}], 'temperature': 0.0} 
                )
                result = clean_and_parse_json(response.text)

            # PARSE & SAVE
            score = extract_score_safely(result)
            
            # Final Name Cleanup (Crucial for History consistency)
            final_name = result.get("product_name", "Unidentified Item")
            # If AI still failed to name it, use the ID we extracted earlier
            if final_name in ["Unknown", "N/A", "Unidentified Item", "Generic"] and 'fallback_name' in locals() and fallback_name:
                 final_name = fallback_name

            st.session_state.history.append({
                "source": final_name,
                "score": score,
                "verdict": result.get("verdict", "Analysis Complete"),
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

    t1, t2, t3 = st.tabs(["🛡️ Verdict", "💬 Reviews", "🚩 Analysis"])
    
    with t1:
        color = "red" if score <= 45 else "orange" if score < 80 else "green"
        st.markdown(f"<h1 style='text-align: center; color: {color}; font-size: 80px;'>{score}<span style='font-size: 40px; color: grey;'>/100</span></h1>", unsafe_allow_html=True)
        st.markdown(f"<h3 style='text-align: center;'>{result.get('verdict', 'Done')}</h3>", unsafe_allow_html=True)
        
        with st.expander("ℹ️ Why is this score different on other sites?"):
            st.info("""
            **Veritas scores the Transaction Safety, not just the Item.**
            * **Amazon/Walmart:** Safer returns, warranty, faster shipping.
            * **Temu/AliExpress:** Deducts max 10 points for shipping/return risks.
            """)

        if score <= 45: st.error("⛔ DO NOT BUY. Poor quality/scam.")
        elif score < 80: st.warning("⚠️ Mixed reviews. Expect issues.")
        else: st.success("✅ Safe and well-reviewed.")

    with t2:
        st.subheader("Consensus")
        if result.get("key_complaints"):
            for c in result.get("key_complaints", []): 
                st.markdown(f"**🚨** {c}")
        
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

    with t3:
        st.subheader("Red Flags")
        for flag in result.get("red_flags", []): st.markdown(f"**•** {flag}")
        
        st.divider()
        # Smart Formatter for Analysis Dictionary
        analysis_data = result.get("detailed_technical_analysis", {})
        if isinstance(analysis_data, dict):
            for header, bullets in analysis_data.items():
                st.markdown(f"### {header}") 
                if isinstance(bullets, list):
                    for bullet in bullets:
                        st.markdown(f"- {bullet}")
                else:
                    st.markdown(str(bullets))
                st.markdown("") 
        else:
            st.markdown(str(analysis_data))
