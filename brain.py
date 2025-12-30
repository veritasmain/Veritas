import streamlit as st
import google.generativeai as genai
from firecrawl import FirecrawlApp
from PIL import Image
import json
import re
import requests

# --- PAGE CONFIGURATION ---
st.set_page_config(
    page_title="Veritas",
    page_icon="🛡️",
    layout="centered",
    initial_sidebar_state="collapsed"
)

# --- HISTORY SETUP ---
if "history" not in st.session_state:
    st.session_state.history = []

# --- SIDEBAR: HISTORY ---
with st.sidebar:
    st.header("📜 Recent Scans")
    if not st.session_state.history:
        st.caption("No searches yet.")
    else:
        for item in reversed(st.session_state.history):
            st.text(f"{item['verdict']}")
            st.caption(f"{item['source']} - {item['score']}/100")
            st.divider()
    
    if st.button("Clear History"):
        st.session_state.history = []
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

def clean_json(text):
    # Cleans API response to ensure valid JSON
    text = re.sub(r'```json', '', text)
    text = re.sub(r'```', '', text)
    return text.strip()

# --- INPUT TABS ---
# We use tabs to switch between Link and Screenshot mode
input_tab1, input_tab2 = st.tabs(["🔗 Paste Link", "📸 Upload Screenshot"])

target_url = None
uploaded_image = None
analysis_trigger = False

with input_tab1:
    target_url = st.text_input("Website URL (Amazon, Shopify, etc):", placeholder="https://...")
    if st.button("Analyze Link", type="primary"):
        analysis_trigger = "link"

with input_tab2:
    uploaded_file = st.file_uploader("Upload an ad or text screenshot", type=["png", "jpg", "jpeg"])
    if uploaded_file and st.button("Analyze Screenshot", type="primary"):
        uploaded_image = Image.open(uploaded_file)
        analysis_trigger = "image"

# --- ANALYSIS LOGIC ---
if analysis_trigger:
    gemini_key, firecrawl_key = get_api_keys()
    
    if gemini_key and firecrawl_key:
        status_box = st.status("🕵️‍♂️ Veritas is investigating...", expanded=True)
        
        try:
            result = {}
            product_image_url = None
            
            # --- PATH A: ANALYZE LINK ---
            if analysis_trigger == "link" and target_url:
                status_box.write("🌐 Scouting the website...")
                
                # 1. Scrape with Firecrawl
                app = FirecrawlApp(api_key=firecrawl_key)
                # We ask for 'markdown' (text) and 'metadata' (to find the image)
                scraped_data = app.scrape_url(target_url, params={'formats': ['markdown', 'json']})
                
                if not scraped_data:
                    raise Exception("Could not connect to website.")

                # 2. Extract Data
                website_content = scraped_data.get('markdown', '')[:20000]
                metadata = scraped_data.get('metadata', {})
                
                # Try to find the 'og:image' (OpenGraph image) which is usually the main product pic
                product_image_url = metadata.get('og:image')
                
                # 3. Analyze with Gemini
                status_box.write("🧠 Analyzing fraud patterns...")
                genai.configure(api_key=gemini_key)
                model = genai.GenerativeModel('gemini-1.5-flash')
                
                prompt = f"""
                You are Veritas. Analyze this website content for fraud.
                
                Return JSON with these keys:
                - "score": 0-100 (0=Scam, 100=Safe).
                - "verdict": Short title (e.g. "High Risk Scam").
                - "red_flags": List of strings explaining why.
                - "reviews_summary": Summary of customer sentiment found in text.

                Content:
                {website_content}
                """
                response = model.generate_content(prompt)
                result = json.loads(clean_json(response.text))
                
                # Save source for history
                source_label = target_url[:30] + "..."

            # --- PATH B: ANALYZE IMAGE ---
            elif analysis_trigger == "image" and uploaded_image:
                status_box.write("👁️ Scanning visual elements...")
                
                genai.configure(api_key=gemini_key)
                model = genai.GenerativeModel('gemini-1.5-flash')
                
                prompt = """
                Analyze this image (screenshot of a product or ad). Look for scam signs like fake prices, typos, or unrealistic claims.
                Return JSON with keys: "score" (0-100), "verdict", "red_flags", "reviews_summary" (if any text in image).
                """
                response = model.generate_content([prompt, uploaded_image])
                result = json.loads(clean_json(response.text))
                
                source_label = "Screenshot Upload"

            # --- DISPLAY RESULTS ---
            status_box.update(label="✅ Analysis Complete!", state="complete", expanded=False)
            
            # 1. PRODUCT IMAGE (The "Verification" Check)
            st.divider()
            col1, col2, col3 = st.columns([1, 2, 1])
            with col2:
                if analysis_trigger == "link" and product_image_url:
                    st.image(product_image_url, caption="Verifying Product...", width=200)
                elif analysis_trigger == "image" and uploaded_image:
                    st.image(uploaded_image, caption="Verifying Upload...", width=200)
            
            # 2. THE 3-CARD SYSTEM (Tabs)
            tab1, tab2, tab3 = st.tabs(["🛡️ The Verdict", "🚩 Reality Check", "💬 Reviews"])
            
            with tab1:
                # SCORE CARD
                score = result.get("score", 0)
                color = "red" if score < 50 else "orange" if score < 80 else "green"
                
                st.markdown(f"<h1 style='text-align: center; color: {color}; font-size: 80px;'>{score}</h1>", unsafe_allow_html=True)
                st.markdown(f"<h3 style='text-align: center;'>{result.get('verdict', 'Unknown')}</h3>", unsafe_allow_html=True)
                
                if score < 50:
                    st.error("⛔ DO NOT BUY. Strong evidence of fraud.")
                elif score < 80:
                    st.warning("⚠️ Proceed with caution.")
                else:
                    st.success("✅ Looks safe to proceed.")

            with tab2:
                # FLAGS CARD
                st.subheader("Why?")
                for flag in result.get("red_flags", []):
                    st.write(f"• {flag}")

            with tab3:
                # REVIEWS CARD
                st.subheader("Public Consensus")
                st.info(result.get("reviews_summary", "No reviews found."))

            # 3. SAVE TO HISTORY
            st.session_state.history.append({
                "source": source_label,
                "score": score,
                "verdict": result.get("verdict")
            })

        except Exception as e:
            status_box.update(label="❌ Error", state="error")
            st.error(f"Something went wrong: {e}")
