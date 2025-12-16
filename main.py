import os
import time
import json
import requests
import google.generativeai as genai
from dotenv import load_dotenv
from prompts import (
    ANALYSIS_SYSTEM_PROMPT, 
    FEEDBACK_SUCCESS_PROMPT, 
    FEEDBACK_FAILURE_PROMPT
)

# --- Configuration ---
load_dotenv()
API_KEY = os.getenv("GEMINI_API_KEY")

if not API_KEY:
    raise ValueError("API Key not found! Please check your .env file.")

genai.configure(api_key=API_KEY)
model = genai.GenerativeModel('gemini-2.5-flash')

# --- File Paths ---
MASTER_SENTENCES_FILE = "master_good_sentences.txt"
FEEDBACK_LOG_FILE = "all_feedback_logs.txt"
DAILY_LOG_FILE = "daily_call_logs.txt"

# --- Helper Functions ---

def download_audio(url, save_path="temp_audio.mp3"):
    print(f"[INFO] Downloading audio from: {url}")
    try:
        response = requests.get(url, stream=True)
        response.raise_for_status()
        with open(save_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
        print("[SUCCESS] Download complete.")
        return save_path
    except Exception as e:
        print(f"[ERROR] Downloading: {e}")
        return None

def upload_to_gemini(file_path):
    print(f"[INFO] Uploading {file_path} to Gemini...")
    audio_file = genai.upload_file(path=file_path)
    
    while audio_file.state.name == "PROCESSING":
        print("[WAIT] Waiting for audio file processing...")
        time.sleep(2)
        audio_file = genai.get_file(audio_file.name)
        
    if audio_file.state.name == "FAILED":
        raise ValueError("Audio processing failed on Gemini side.")
        
    print("[SUCCESS] Upload & Processing complete.")
    return audio_file

def analyze_audio_data(audio_file):
    """Phase 1: Get Data (Type, Outcome, Scores, Sentences)"""
    print("[INFO] Analyzing Audio Structure...")
    try:
        response = model.generate_content(
            [ANALYSIS_SYSTEM_PROMPT, audio_file],
            generation_config={"response_mime_type": "application/json"}
        )
        return json.loads(response.text)
    except Exception as e:
        print(f"[ERROR] Analysis failed: {e}")
        return None

def generate_conditional_feedback(audio_file, call_type, call_outcome):
    """Phase 2: Generate Feedback based on Success vs Failure"""
    print(f"[INFO] Generating Feedback for: {call_type} ({call_outcome})...")
    
    if call_outcome == "SUCCESSFUL":
        prompt_template = FEEDBACK_SUCCESS_PROMPT
    else:
        prompt_template = FEEDBACK_FAILURE_PROMPT
        
    formatted_prompt = prompt_template.format(call_type=call_type)
    
    try:
        response = model.generate_content([formatted_prompt, audio_file])
        return response.text
    except Exception as e:
        print(f"[ERROR] Feedback generation failed: {e}")
        return "Feedback generation failed."

def save_good_sentences(sentences, call_type):
    """Only runs if Call was SUCCESSFUL (Enquiry OR Sale)"""
    if not sentences: return
    
    print(f"[DB] Saving {len(sentences)} Winning Sentences from {call_type} to {MASTER_SENTENCES_FILE}...")
    with open(MASTER_SENTENCES_FILE, "a", encoding="utf-8") as f:
        f.write(f"\n--- WINNING PHRASES ({call_type}) - {int(time.time())} ---\n")
        for sent in sentences:
            f.write(f"» {sent}\n")

def append_to_logs(url, data, feedback_text):
    """Logs the event with full scoring details."""
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    
    # Extract data safely
    call_type = data.get("call_type", "UNKNOWN").upper()
    call_outcome = data.get("call_outcome", "UNKNOWN").upper()
    summary = data.get("transcript_summary", "No summary provided.")
    variables = data.get("variables_analysis", {})
    
    # Format Scores for the text file
    scores_text = ""
    for key, value in variables.items():
        clean_key = key.replace("_", " ").title()
        scores_text += f"   - {clean_key}: {value}\n"

    # Construct the Final Log Entry
    full_log_entry = f"""
============================================================
CALL TIMESTAMP: {timestamp}
URL: {url}
--------------------------------------------------
CALL TYPE: {call_type}
OUTCOME:   {call_outcome}
--------------------------------------------------
SUMMARY:
{summary}
--------------------------------------------------
SCORES & NOTES:
{scores_text}
--------------------------------------------------
{feedback_text}
============================================================
"""

    # 1. Save Detailed Log
    with open(FEEDBACK_LOG_FILE, "a", encoding="utf-8") as f:
        f.write(full_log_entry)

    # 2. Save Simple Running Log
    simple_log = f"[{timestamp}] {call_type} ({call_outcome}) | URL: {url}\n"
    with open(DAILY_LOG_FILE, "a", encoding="utf-8") as f:
        f.write(simple_log)

# --- Main Logic ---

def process_single_url(audio_url):
    timestamp = int(time.time())
    temp_file = f"temp_{timestamp}.mp3"
    
    local_audio = download_audio(audio_url, temp_file)
    
    if local_audio:
        gemini_file = None
        try:
            gemini_file = upload_to_gemini(local_audio)
            
            # Step 1: Analyze Data
            data = analyze_audio_data(gemini_file)
            
            if data:
                call_type = data.get("call_type", "ENQUIRY").upper()
                call_outcome = data.get("call_outcome", "UNSUCCESSFUL").upper()
                golden_sentences = data.get("golden_sentences", [])
                
                print(f"\n[RESULT] Identified: {call_type} -> {call_outcome}")

                # Step 2: Logic Branching (Save Sentences ONLY if Successful)
                if call_outcome == "SUCCESSFUL":
                    print("[ACTION] Success Detected! Capturing Golden Sentences.")
                    save_good_sentences(golden_sentences, call_type)
                else:
                    print("[ACTION] Outcome Unsuccessful. Skipping Golden Sentences.")

                # Step 3: Generate Specific Feedback (Good only OR Good+Bad)
                feedback_text = generate_conditional_feedback(gemini_file, call_type, call_outcome)
                print("\n" + feedback_text)

                # Step 4: Log Everything
                append_to_logs(audio_url, data, feedback_text)
                print("[SUCCESS] Logs updated.")

        except Exception as e:
            print(f"[ERROR] Processing call: {e}")
        
        finally:
            if os.path.exists(temp_file):
                os.remove(temp_file)
            if gemini_file:
                try:
                    genai.delete_file(gemini_file.name)
                except:
                    pass

if __name__ == "__main__":
    print("--- Intelligent Sales Call Analyzer ---")
    print(f"1. Master Sentences -> {MASTER_SENTENCES_FILE}")
    print(f"2. All Feedback     -> {FEEDBACK_LOG_FILE}")
    print(f"3. Running Logs     -> {DAILY_LOG_FILE}")
    
    while True:
        url_input = input("\nPaste Audio URL (or type 'exit'): ").strip()
        
        if url_input.lower() == 'exit':
            break
            
        if url_input:
            process_single_url(url_input)
        else:
            print("Please enter a valid URL.")