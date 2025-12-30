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
    initial_sidebar_state="expanded" 
)

# --- HISTORY SETUP ---
if "history" not in st.session_state:
    st.session_state.history = []

# --- CALLBACKS ---
def clear_url_input():
    st.session_state.url_input = ""

def clear_img_input():
    st.session_state.img_input = None

# --- SIDEBAR: HISTORY ---
with st.sidebar:
    st.header("📜 Recent Scans")
    if not st.session_state.history:
        st.caption("No searches yet.")
    else:
        for item in reversed(st.session_state.history):
            st.markdown(f"**{item['source']}**")
            st.text(f"{item['verdict']}")
            
            score = item['score']
            color = "red" if score <= 45 else "orange" if score < 80 else "green"
            st.markdown(f"<span style='color:{color}'>Score: {score}/100</span>", unsafe_allow_html=True)
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
                
                app = Firecrawl(api_key=firecrawl_key)
                scraped_data = app.scrape(target_url, formats=['markdown'])
                
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
                
                status_box.write("🧠 Analyzing product quality & fraud...")
                genai.configure(api_key=gemini_key)
                model = genai.GenerativeModel('gemini-2.5-flash')
                
                # --- UPDATED PROMPT: UNIVERSAL CONSISTENCY ---
                prompt = f"""
                You are Veritas. Analyze this website content.
                
                CRITICAL INSTRUCTION - PRODUCT CONSISTENCY:
                - Judge the INTRINSIC QUALITY of the item itself, not just the seller.
                - If this item is a known generic dropshipping product (e.g., "Galaxy Rose", "Levitating Pot", "Cheap Mini Projector"), give it the SAME LOW SCORE you would give it on any other site.
                - A bad product on a nice website is still a bad product.
                
                SCORING RULES:
                - If reviews mention failure ("broke", "weak"): Score < 45 (Red).
                - If it's a generic gadget with no brand history: Score < 60 (Amber).
                - Only give > 80 (Green) for reputable brands with verified durability.

                Return JSON:
                - "product_name": Short name of the product.
                - "score": 0-100.
                - "verdict": Short title.
                - "red_flags": [List of short strings].
                - "key_complaints": [List of specific defects found].
                - "reviews_summary": "Short summary text."

                Content:
                {website_content}
                """
                response = model.generate_content(prompt)
                result = json.loads(clean_json(response.text))
                
                source_label = result.get("product_name", target_url[:30])

            # --- PATH B: ANALYZE IMAGE ---
            elif analysis_trigger == "image" and uploaded_image:
                status_box.write("👁️ Scanning visual elements...")
                genai.configure(api_key=gemini_key)
                model = genai.GenerativeModel('gemini-2.5-flash')
                
                prompt = """
                Analyze this image. Identify the product.
                
                CONSISTENCY CHECK:
                - If this looks like a generic dropshipped item (e.g. cheap plastic gadget), score it LOW regardless of the ad quality.
                - Judge the object, not the photography.
                
                Return JSON with keys: 
                "product_name", "score", "verdict", 
                "red_flags", "reviews_summary", "key_complaints".
                """
                response = model.generate_content([prompt, uploaded_image])
                result = json.loads(clean_json(response.text))
                
                source_label = result.get("product_name", "Screenshot Upload")

            # --- DISPLAY RESULTS ---
            status_box.update(label="✅ Analysis Complete!", state="complete", expanded=False)
            
            st.divider()
            col1, col2, col3 = st.columns([1, 2, 1])
            with col2:
                if analysis_trigger == "link" and product_image_url:
                    st.image(product_image_url, caption="Verifying Product...", width=200)
                elif analysis_trigger == "image" and uploaded_image:
                    st.image(uploaded_image, caption="Verifying Upload...", width=200)
            
            tab1, tab2, tab3 = st.tabs(["🛡️ The Verdict", "🚩 Reality Check", "💬 Reviews"])
            
            with tab1:
                score = result.get("score", 0)
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
                st.subheader("Why?")
                for flag in result.get("red_flags", []):
                    st.markdown(f"**•** {flag}")

            with tab3:
                st.subheader("Consensus")
                complaints = result.get("key_complaints", [])
                if complaints:
                    st.error("🚨 Frequent Complaints:")
                    for complaint in complaints:
                        st.markdown(f"**•** {complaint}")
                
                st.info(result.get("reviews_summary", "No reviews found."))

            st.session_state.history.append({
                "source": source_label,
                "score": score,
                "verdict": result.get("verdict")
            })

        except Exception as e:
            status_box.update(label="❌ Error", state="error")
            st.error(f"Something went wrong: {e}")

        st.divider()
        if st.button("🔄 Start New Search", type="secondary", use_container_width=True, on_click=clear_url_input):
             pass
