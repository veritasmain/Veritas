import streamlit as st
import os
import google.generativeai as genai
from firecrawl import FirecrawlApp
import warnings
import json
from datetime import datetime

# --- ⚙️ APP CONFIGURATION (Must be first) ---
st.set_page_config(
    page_title="VERITAS",
    page_icon="🛡️",
    layout="centered", 
    initial_sidebar_state="collapsed"
)

# --- 🎨 VISUAL THEME (Navy Blue & White) ---
st.markdown("""
    <style>
    /* Main Background - White */
    .stApp {
        background-color: #FFFFFF;
    }
    /* Primary Buttons - Navy Blue */
    .stButton>button {
        background-color: #000080 !important;
        color: white !important;
        border-radius: 8px;
        border: none;
        height: 50px;
        width: 100%;
        font-weight: bold;
        font-size: 18px;
    }
    /* Secondary Buttons - Outline */
    div[data-testid="stButton"] > button[kind="secondary"] {
        background-color: white !important;
        color: #000080 !important;
        border: 2px solid #000080 !important;
    }
    /* Text Inputs */
    .stTextInput>div>div>input {
        border-radius: 8px;
        border: 1px solid #D1D1D1;
    }
    /* Headings */
    h1, h2, h3 {
        color: #000080 !important;
    }
    </style>
    """, unsafe_allow_html=True)

# --- 🔑 KEYS SETUP ---
try:
    GEMINI_KEY = st.secrets["GEMINI_KEY"]
    FIRECRAWL_KEY = st.secrets["FIRECRAWL_KEY"]
except:
    GEMINI_KEY = "PASTE_YOUR_GEMINI_KEY_HERE"
    FIRECRAWL_KEY = "PASTE_YOUR_FIRECRAWL_KEY_HERE"

if GEMINI_KEY != "PASTE_YOUR_GEMINI_KEY_HERE":
    genai.configure(api_key=GEMINI_KEY)

# --- 📂 HISTORY SYSTEM ---
HISTORY_FILE = "scan_history.json"

def load_history():
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, "r") as f:
            try: return json.load(f)
            except: return []
    return []

def save_to_history(data, url):
    history = load_history()
    new_entry = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "url": url,
        "product_name": data.get("product_name", "Unknown Product"),
        "score": data.get("score", 0),
        "verdict": data.get("verdict_badge", "Unknown"),
        "full_data": data
    }
    history.insert(0, new_entry)
    with open(HISTORY_FILE, "w") as f:
        json.dump(history, f, indent=4)

# --- 🧠 THE BRAIN (Logic) ---
def analyze_product(url):
    try:
        # 1. Firecrawl (The Eyes)
        with st.status("👀 Veritas is reading the page...", expanded=False) as status:
            app = FirecrawlApp(api_key=FIRECRAWL_KEY)
            response = app.scrape(url, formats=['markdown'])
            
            # Safe data extraction
            if hasattr(response, 'markdown'):
                raw_data = response.markdown
            elif isinstance(response, dict):
                raw_data = response.get('markdown', str(response))
            else:
                raw_data = str(response)
                
            raw_data = raw_data[:9000] # Limit size
            status.update(label="✅ Page Read Successfully!", state="complete")

        # 2. Gemini (The Brain)
        with st.spinner("🧠 Researching with Gemini..."):
            model = genai.GenerativeModel("models/gemini-2.5-flash")
            
            prompt = f"""
            Analyze this product link: {url}
            Page Text: {raw_data}
            
            This is likely from Amazon, Temu, or TikTok Shop.
            Look for:
            - Dropshipping scams (Generic items sold at markup)
            - Fake reviews or "too good to be true" claims
            - TikTok Shop specific red flags (Viral junk, shipping delays)
            
            Return a purely JSON response with this exact structure:
            {{
                "product_name": "Short Name of Product",
                "score": 45,
                "verdict_badge": "HARD PASS" or "BUY IT",
                "verdict_summary": "One sentence summary of why.",
                "marketing_claim": "The biggest marketing lie found.",
                "reality_check": "The brutal truth about the quality.",
                "reddit_consensus": "What Reddit/users really say."
            }}
            """
            response = model.generate_content(prompt, generation_config={"response_mime_type": "application/json"})
            data = json.loads(response.text)
            
            # Save automatically
            save_to_history(data, url)
            return data

    except Exception as e:
        st.error(f"Error: {e}")
        return None

# --- 📱 APP INTERFACE ---

# 1. Check for incoming "Share" links (e.g. ?q=https://amazon...)
query_params = st.query_params
incoming_url = query_params.get("q", "")
if incoming_url:
    st.query_params.clear() # Clear URL to prevent loops

# --- VIEW: RESULTS PAGE (If a link exists) ---
if incoming_url:
    st.markdown("<h2 style='text-align: center;'>VERITAS REPORT</h2>", unsafe_allow_html=True)
    
    data = analyze_product(incoming_url)
    
    if data:
        # CARD 1: THE VERDICT
        score = data['score']
        color = "#28a745" if score > 70 else "#dc3545"
        
        st.markdown("### 1. THE VERDICT")
        with st.container(border=True):
            c1, c2 = st.columns([1, 2])
            with c1:
                st.markdown(f"<h1 style='text-align: center; color: {color}; margin: 0;'>{score}</h1>", unsafe_allow_html=True)
                st.markdown("<p style='text-align: center; margin: 0; font-size: 0.8rem;'>TRUTH SCORE</p>", unsafe_allow_html=True)
            with c2:
                st.markdown(f"#### {data['verdict_badge']}")
                st.write(data['verdict_summary'])

        # CARD 2: REALITY CHECK
        st.markdown("### 2. REALITY CHECK")
        with st.container(border=True):
            st.warning(f"**THEY SAY:** \"{data['marketing_claim']}\"")
            st.info(f"**REALITY:** \"{data['reality_check']}\"")

        # CARD 3: STREET SMARTS
        st.markdown("### 3. STREET SMARTS")
        with st.container(border=True):
            st.write(f"🗣️ **Reddit Consensus:** {data['reddit_consensus']}")

        # Home Button
        if st.button("🏠 Back to Search", type="secondary"):
            st.rerun()

# --- VIEW: HOME PAGE (Search) ---
else:
    st.markdown("<h1 style='text-align: center; color: #000080;'>VERITAS</h1>", unsafe_allow_html=True)
    st.markdown("<p style='text-align: center; color: grey;'>The Truth Hunter</p>", unsafe_allow_html=True)
    st.divider()

    # Manual Search Box
    manual_url = st.text_input("Paste Product Link:", placeholder="Paste Amazon, Temu, or TikTok link here...")
    
    # --- HERE IS THE BUTTON YOU WANTED ---
    if st.button("Analyze for Fraud"):
        if manual_url:
            st.query_params["q"] = manual_url
            st.rerun()
        else:
            st.warning("Please paste a link first.")

    st.divider()
    
    # History Section
    with st.expander("📜 Recent Scans"):
        history = load_history()
        if not history:
            st.caption("No history yet.")
        else:
            for item in history:
                st.write(f"**{item['score']}/100** - {item['product_name']}")