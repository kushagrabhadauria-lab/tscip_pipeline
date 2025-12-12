import os
import time
import json
import requests
import google.generativeai as genai
from dotenv import load_dotenv
from prompts import ANALYSIS_SYSTEM_PROMPT

# --- Configuration ---
load_dotenv()
API_KEY = os.getenv("GEMINI_API_KEY")

if not API_KEY:
    raise ValueError("API Key not found! Please check your .env file.")

genai.configure(api_key=API_KEY)
model = genai.GenerativeModel('gemini-2.5-flash')

# Files
MASTER_SENTENCES_FILE = "master_good_sentences.txt"
CALL_LOGS_FILE = "all_call_logs.jsonl"

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

def analyze_audio(audio_file):
    print("[INFO] Analyzing Audio (Context, Variables)...")
    max_retries = 3
    for attempt in range(max_retries):
        try:
            response = model.generate_content(
                [ANALYSIS_SYSTEM_PROMPT, audio_file],
                generation_config={"response_mime_type": "application/json"}
            )
            return json.loads(response.text)
        except Exception as e:
            if "429" in str(e):
                wait_time = (attempt + 1) * 10 
                print(f"[WARN] Quota hit. Waiting {wait_time}s...")
                time.sleep(wait_time)
            else:
                print(f"[ERROR] Analysis attempt {attempt + 1} failed: {e}")
                break
    return None

def append_to_master_sentences(data):
    """Adds golden sentences from THIS call to the master file."""
    if not data or "golden_sentences" not in data:
        return

    sentences = data["golden_sentences"]
    if sentences:
        count = len(sentences)
        print(f"[DB] Adding {count} golden sentences to {MASTER_SENTENCES_FILE}")
        with open(MASTER_SENTENCES_FILE, "a", encoding="utf-8") as f:
            for sent in sentences:
                f.write(f"{sent}\n")
            f.write("-" * 30 + "\n")

def generate_feedback_report(gemini_file):
    """Generates a feedback report for the call."""
    print("[INFO] Generating Feedback Report...")
    
    FEEDBACK_PROMPT = """
    You are a Sales Coach. Analyze the audio call provided.

    **Goal:** provide actionable feedback to help the agent improve results.

    **OUTPUT FORMAT (Markdown):**
    ### Call Feedback Report

    #### 1. What Went Well (Strengths):
    * (List specific good behaviors, tone, or phrases used)

    #### 2. Areas for Improvement (Opportunities):
    * (List what could be changed to get better results next time)

    #### 3. Actionable Advice:
    * (One specific thing the agent should do differently in the next call)
    """

    try:
        response = model.generate_content([FEEDBACK_PROMPT, gemini_file])
        print("\n" + "*" * 50)
        print(response.text)
        print("*" * 50 + "\n")
        return response.text
    except Exception as e:
        print(f"[ERROR] generating feedback: {e}")
        return None

# --- Main Logic ---

def process_single_url(audio_url):
    timestamp = int(time.time())
    temp_file = f"temp_{timestamp}.mp3"
    local_audio = download_audio(audio_url, temp_file)
    
    if local_audio:
        try:
            gemini_file = upload_to_gemini(local_audio)
            
            # 1. Analyze for Data (Variables & Golden Sentences)
            analysis_result = analyze_audio(gemini_file)

            if analysis_result:
                # 2. ALWAYS Save Good Sentences (as requested)
                append_to_master_sentences(analysis_result)
                
                # 3. ALWAYS Generate Feedback
                feedback_text = generate_feedback_report(gemini_file)

                # 4. Save Log
                log_entry = {
                    "timestamp": timestamp,
                    "url": audio_url,
                    "summary": analysis_result.get('transcript_summary'),
                    "scores": analysis_result.get('variables_analysis'),
                    "feedback": feedback_text
                }
                with open(CALL_LOGS_FILE, "a", encoding="utf-8") as f:
                    f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")

        except Exception as e:
            print(f"[ERROR] Processing call: {e}")
        
        finally:
            if os.path.exists(temp_file):
                os.remove(temp_file)
            if 'gemini_file' in locals():
                try:
                    genai.delete_file(gemini_file.name)
                except:
                    pass

if __name__ == "__main__":
    print("--- Single URL Call Analyzer ---")
    while True:
        url_input = input("\nPaste Audio URL (or type 'exit'): ").strip()
        
        if url_input.lower() == 'exit':
            break
            
        if url_input:
            process_single_url(url_input)
        else:
            print("Please enter a valid URL.")