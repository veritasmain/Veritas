import streamlit as st
from google import genai
from firecrawl import Firecrawl
from PIL import Image
import json
import re
import traceback
import os
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
    Extracts score and rounds it to the nearest multiple of 5.
    """
    raw = result_dict.get("score")
    score = 50 # Default
    
    if isinstance(raw, (int, float)):
        score = int(raw)
    elif isinstance(raw, str):
        nums = re.findall(r'\d+', raw)
        if nums:
            score = int(nums[0])
            
    # Round to nearest 5
    score = 5 * round(score / 5)
    return max(0, min(100, score))

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
    
    # CASE 1: PLAYBACK
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

            # === PATH A: LINK ===
            if analysis_trigger == "link" and target_url:
                status_box.write("🌐 Scouting website...")
                scraped_data = None
                scrape_error = False
                try:
                    scraped_data = scrape_website(target_url, firecrawl_key)
                    content = getattr(scraped_data, 'markdown', '')
                    is_trap = len(str(content)) < 500 or "verify" in str(content).lower()
                    if is_trap and "amazon" not in target_url.lower(): scrape_error = True
                except: scrape_error = True

                if not scrape_error and scraped_data:
                    status_box.write("🧠 Reading content...")
                    content = getattr(scraped_data, 'markdown', '')
                    meta = getattr(scraped_data, 'metadata', {})
                    product_image_url = meta.get('og:image') if isinstance(meta, dict) else getattr(meta, 'og_image', None)

                    prompt = f"""
                    You are Veritas. Analyze this product.
                    
                    STRICT SCORING RULES (MULTIPLES OF 5 ONLY):
                    - 0-25: SCAM / DANGEROUS / FAKE ITEM.
                    - 30-45: SIGNIFICANT ISSUES. (If reviews say "Trash", "Broken", or "Features don't work", MAX SCORE IS 45. DO NOT GIVE 60+).
                    - 50-75: DECENT / AVERAGE.
                    - 80-100: EXCELLENT.

                    TASK 1: SHORT VERDICT
                    - "verdict": SHORT and PUNCHY (Max 15 words). Headline style.

                    TASK 2: MARKET COMPARISON
                    - "detailed_technical_analysis": JSON OBJECT (Headers -> Bullets).
                    - PRICE: Use relative terms ("Cheaper", "Similar"). No exact prices.
                    - SPECS: Do NOT guess. If specs differ, say "Not as advertised on verified sources".

                    TASK 3: REVIEW SOURCING
                    - "reviews_summary": LIST of strings. Full sentences citing sources.
                    - "key_complaints": LIST of strings. "Feature: Specific failure".

                    Return JSON: product_name, score, verdict, red_flags, detailed_technical_analysis, key_complaints, reviews_summary.
                    Content: {str(content)[:20000]}
                    """
                    response = client.models.generate_content(model='gemini-2.0-flash', contents=prompt)
                else:
                    status_box.write("🛡️ Anti-bot detected. Switching to ID Investigation...")
                    prompt = f"""
                    I cannot access page. URL: {target_url}
                    1. EXTRACT ID. 2. SEARCH Google for ID + "Review" + "Reddit" + "AliExpress".
                    
                    STRICT SCORING (MULTIPLES OF 5 ONLY):
                    - 0-25: SCAM.
                    - 30-45: TRASH / BROKEN (Max score 45 if functional issues exist).
                    
                    OUTPUT REQUIREMENTS:
                    - "verdict": SHORT & PUNCHY (Max 15 words).
                    - "reviews_summary": LIST of strings. Detailed summaries per source.
                    - "detailed_technical_analysis": JSON OBJECT.
                    - SPECS: Say "Not as advertised on verified sources" if conflicting.

                    Return JSON: product_name, score, verdict, red_flags, detailed_technical_analysis, key_complaints, reviews_summary.
                    """
                    response = client.models.generate_content(
                        model='gemini-2.0-flash', contents=prompt,
                        config={'tools': [{'google_search': {}}]}
                    )

            # === PATH B: IMAGE ===
            elif analysis_trigger == "image" and uploaded_image:
                status_box.write("👁️ Analyzing visual evidence...")
                
                prompt = """
                YOU ARE A FORENSIC ANALYST.
                
                STEP 1: IDENTIFY & SEARCH
                - Extract text from the image. SEARCH Google for this item on Amazon/Reddit.
                
                STEP 2: STRICT SCORING (MULTIPLES OF 5 ONLY):
                   - 0-25: SCAM (Fake item/Dangerous).
                   - 30-45: FAILED PRODUCT (If reviews mention "trash", "broken", or "doesn't work", MAX SCORE IS 45).
                   - 50-75: AVERAGE (Works as intended).
                   - 80-100: EXCELLENT.

                STEP 3: VERDICT
                   - SHORT and PUNCHY (Max 15 words).

                STEP 4: CROSS-REFERENCE ANALYSIS (STRUCTURED JSON)
                   - "detailed_technical_analysis": JSON OBJECT (Headers -> Bullets).
                   - PRICE: Use relative terms ("Cheaper elsewhere", "Markup detected").
                   - SPECS: Do NOT guess (e.g. "Likely 720p"). Instead say: "Not as advertised on verified sources" or "Discrepancy detected".

                STEP 5: REVIEWS
                   - "reviews_summary": LIST of strings. Detailed 2-3 sentence summaries per source.
                   - "key_complaints": LIST of strings. "Feature: Specific technical failure".

                Return JSON keys: 
                "product_name", "score", "verdict", "red_flags", "reviews_summary", "key_complaints", "detailed_technical_analysis".
                """
                
                response = client.models.generate_content(
                    model='gemini-2.0-flash', 
                    contents=[prompt, uploaded_image],
                    config={'tools': [{'google_search': {}}]} 
                )

            # PARSE & SAVE
            result = clean_and_parse_json(response.text)
            score = extract_score_safely(result)
            
            final_name = result.get("product_name", "Unidentified Item")
            if final_name in ["Unknown", "N/A"]: final_name = "Scanned Item (Generic)"

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
            * **Amazon/Walmart (+5-10 pts):** Safer returns, warranty, faster shipping.
            * **Temu/AliExpress (-5-10 pts):** Higher risk of shipping damage or returns.
            """)

        if score <= 45: st.error("⛔ DO NOT BUY. Poor quality/scam.")
        elif score < 80: st.warning("⚠️ Mixed reviews. Expect issues.")
        else: st.success("✅ Safe and well-reviewed.")

    with t2:
        st.subheader("Consensus")
        
        # 1. Display Complaints (Red)
        if result.get("key_complaints"):
            for c in result.get("key_complaints", []): 
                st.markdown(f"**🚨** {c}")
        
        st.divider()
        
        # 2. Display General Reviews (Bullet Points)
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
        
        # --- SMART FORMATTER FOR ANALYSIS ---
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
