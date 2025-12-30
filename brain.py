import streamlit as st
import google.generativeai as genai
from firecrawl import Firecrawl
from PIL import Image
import json
import re

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
    text = re.sub(r'```json', '', text)
    text = re.sub(r'```', '', text)
    return text.strip()

# --- INPUT TABS ---
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
                app = Firecrawl(api_key=firecrawl_key)
                scraped_data = app.scrape(target_url, formats=['markdown'])
                
                if not scraped_data:
                    raise Exception("Could not connect to website.")

                # 2. Extract Data
                try:
                    website_content = scraped_data.markdown
                    metadata = scraped_data.metadata
                except AttributeError:
                    website_content = scraped_data.get('markdown', '')
                    metadata = scraped_data.get('metadata', {})
                
                website_content = str(website_content)[:30000]
                
                # Get Image
                if metadata:
                    if isinstance(metadata, dict):
                         product_image_url = metadata.get('og:image')
                    else:
                         product_image_url = getattr(metadata, 'og_image', None)
                
                # 3. Analyze with Gemini
                status_box.write("🧠 Analyzing fraud patterns...")
                genai.configure(api_key=gemini_key)
                
                # FIXED: Using 'gemini-pro' (Text only model)
                model = genai.GenerativeModel('gemini-pro')
                
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
                
                source_label = target_url[:30] + "..."

            # --- PATH B: ANALYZE IMAGE ---
            elif analysis_trigger == "image" and uploaded_image:
                status_box.write("👁️ Scanning visual elements...")
                
                genai.configure(api_key=gemini_key)
                
                # FIXED: Using 'gemini-pro-vision' (The classic image model)
                model = genai.GenerativeModel('gemini-pro-vision')
                
                prompt = """
                Analyze this image. Look for scam signs like fake prices, typos, or unrealistic claims.
                Return JSON with keys: "score" (0-100), "verdict", "red_flags", "reviews_summary".
                """
                response = model.generate_content(
