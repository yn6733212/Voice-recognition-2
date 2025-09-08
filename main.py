import requests
import asyncio
import edge_tts
import os
import subprocess
import speech_recognition as sr
import pandas as pd
import yfinance as yf
from difflib import get_close_matches
import re
import shutil
import tarfile
import logging
import warnings
from requests_toolbelt import MultipartEncoder
from flask import Flask, request, jsonify

# ===== לוגים: 4 נקודות עיקריות בלבד =====
logging.basicConfig(
    level=logging.ERROR,  # משאיר שגיאות בלבד מהמערכת
    format="%(message)s"
)
warnings.filterwarnings("ignore")
logging.getLogger("werkzeug").setLevel(logging.ERROR)
logging.getLogger("urllib3").setLevel(logging.ERROR)
logging.getLogger("edge_tts").setLevel(logging.ERROR)
logging.getLogger("yfinance").setLevel(logging.ERROR)
log = logging.getLogger(__name__)

def L_start():
    print("🚀 השרת עלה. ממתין לקובץ…")

def L_new_file(p):
    print(f"📥 התקבל קובץ חדש בשרת: {p}")

def L_recognized(txt):
    print(f"🎯 זוהה: {txt}")

def L_not_recognized():
    print("🗣️ לא זוהה דיבור ברור")

def L_done():
    print("✅ הושלם בהצלחה")

def L_error(msg):
    print(f"❌ שגיאה: {msg}")

# --- הגדרות מערכת ימות המשיח ---
USERNAME = "0733181201"
PASSWORD = "6714453"
TOKEN = f"{USERNAME}:{PASSWORD}"
UPLOAD_FOLDER_FOR_OUTPUT = "22"

# --- הגדרות קבצים ---
CSV_FILE_PATH = "stock_data.csv"
TEMP_MP3_FILE = "temp_output.mp3"
TEMP_INPUT_WAV = "temp_input.wav"
OUTPUT_AUDIO_FILE_BASE = "000"
OUTPUT_INI_FILE_NAME = "ext.ini"

# --- נתיב להרצת ffmpeg ---
FFMPEG_EXECUTABLE = "ffmpeg"

# --- הגדרת Flask App ---
app = Flask(__name__)

def ensure_ffmpeg():
    global FFMPEG_EXECUTABLE
    if not shutil.which("ffmpeg"):
        try:
            ffmpeg_bin_dir = "ffmpeg_bin"
            os.makedirs(ffmpeg_bin_dir, exist_ok=True)
            ffmpeg_url = "https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz"
            archive_path = os.path.join(ffmpeg_bin_dir, "ffmpeg.tar.xz")
            r = requests.get(ffmpeg_url, stream=True, timeout=60)
            r.raise_for_status()
            with open(archive_path, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)
            with tarfile.open(archive_path, 'r:xz') as tar_ref:
                tar_ref.extractall(ffmpeg_bin_dir)
            os.remove(archive_path)
            for root, _, files in os.walk(ffmpeg_bin_dir):
                if "ffmpeg" in files:
                    FFMPEG_EXECUTABLE = os.path.join(root, "ffmpeg")
                    if os.name == 'posix':
                        os.chmod(FFMPEG_EXECUTABLE, 0o755)
                    break
        except Exception as e:
            L_error(f"התקנת FFmpeg נכשלה: {e}")
            FFMPEG_EXECUTABLE = "ffmpeg"

def transcribe_audio(filename):
    r = sr.Recognizer()
    try:
        with sr.AudioFile(filename) as source:
            audio = r.record(source)
        return r.recognize_google(audio, language="he-IL")
    except sr.UnknownValueError:
        return ""
    except Exception as e:
        L_error(f"תמלול נכשל: {e}")
        return ""

def normalize_text(text):
    if not isinstance(text, str):
        if pd.isna(text):
            text = ""
        else:
            text = str(text)
    return re.sub(r'[^א-תa-zA-Z0-9 ]', '', text).lower().strip()

def load_stock_data(path):
    try:
        df = pd.read_csv(path)
        stock_data = {}
        for _, row in df.iterrows():
            name = row.get("name")
            symbol = row.get("symbol")
            display_name = row.get("display_name", name)
            type_ = row.get("type")
            has_dedicated_folder = str(row.get("has_dedicated_folder", "false")).lower() == 'true'
            target_path = row.get("target_path", "")
            if name and symbol and type_:
                stock_data[normalize_text(name)] = {
                    "symbol": symbol,
                    "display_name": display_name,
                    "type": type_,
                    "has_dedicated_folder": has_dedicated_folder,
                    "target_path": target_path if has_dedicated_folder and pd.notna(target_path) else ""
                }
        return stock_data
    except Exception as e:
        L_error(f"טעינת רשימת ניירות ערך נכשלה: {e}")
        return {}

def get_best_match(query, stock_dict):
    matches = get_close_matches(normalize_text(query), stock_dict.keys(), n=1, cutoff=0.7)
    if not matches:
        matches = get_close_matches(normalize_text(query), stock_dict.keys(), n=1, cutoff=0.5)
    return matches[0] if matches else None

def get_stock_price_data(ticker):
    try:
        stock = yf.Ticker(ticker)
        hist = stock.history(period="7d")
        if hist.empty or len(hist) < 2:
            return None
        current_price = hist["Close"].iloc[-1]
        day_before_price = hist["Close"].iloc[-2]
        day_change_percent = (current_price - day_before_price) / day_before_price * 100 if day_before_price else 0
        return {"current": round(current_price, 2), "day_change_percent": round(day_change_percent, 2)}
    except Exception:
        return None

def create_ext_ini_file(action_type, value):
    try:
        with open(OUTPUT_INI_FILE_NAME, 'w', encoding='windows-1255') as f:
            if action_type == "go_to_folder":
                f.write("type=go_to_folder\n")
                relative_path = value.replace("ivr2:", "").rstrip('/')
                f.write(f"go_to_folder={relative_path}\n")
            elif action_type == "play_file":
                f.write("type=playfile\n")
                f.write("playfile_end_goto=/1/2\n")
        return True
    except Exception as e:
        L_error(f"שגיאה ביצירת INI: {e}")
        return False

def upload_file_to_yemot(file_path, yemot_file_name_or_path_on_yemot):
    try:
        full_upload_path = f"ivr2:/{UPLOAD_FOLDER_FOR_OUTPUT}/{yemot_file_name_or_path_on_yemot}"
        m = MultipartEncoder(fields={
            "token": TOKEN,
            "path": full_upload_path,
            "upload": (os.path.basename(file_path), open(file_path, 'rb'),
                       'audio/wav' if file_path.endswith('.wav') else 'text/plain')
        })
        r = requests.post("https://www.call2all.co.il/ym/api/UploadFile",
                          data=m, headers={'Content-Type': m.content_type}, timeout=30)
        r.raise_for_status()
        return True
    except Exception as e:
        L_error(f"העלאה נכשלה ({os.path.basename(file_path)}): {e}")
        return False

def convert_mp3_to_wav(mp3_file, wav_file):
    try:
        subprocess.run(
            [FFMPEG_EXECUTABLE, "-loglevel", "error", "-y", "-i", mp3_file,
             "-ar", "8000", "-ac", "1", "-acodec", "pcm_s16le", wav_file],
            check=True
        )
        return True
    except Exception as e:
        L_error(f"המרה נכשלה (FFmpeg): {e}")
        return False

async def create_audio_file_from_text(text, filename):
    try:
        comm = edge_tts.Communicate(text, voice="he-IL-AvriNeural")
        await comm.save(filename)
        return True
    except Exception as e:
        L_error(f"TTS נכשל: {e}")
        return False

# --- פונקציית העיבוד המרכזית (שקטה, רק 1–2 לוגים) ---
async def process_yemot_recording(audio_file_path):
    stock_data = load_stock_data(CSV_FILE_PATH)
    action_type = "play_file"
    action_value = f"{OUTPUT_AUDIO_FILE_BASE}.wav"

    if not stock_data:
        response_text = "לא ניתן להמשיך ללא נתוני מניות."
    else:
        recognized_text = transcribe_audio(audio_file_path)
        if recognized_text:
            # לוג 3: תוצאת זיהוי
            L_recognized(recognized_text)
            best_match_key = get_best_match(recognized_text, stock_data)
            if best_match_key:
                stock_info = stock_data[best_match_key]
                # אם יש שלוחה ייעודית—נפנה לשם, עדיין נשמור לוג זיהוי אחד בלבד
                if stock_info["has_dedicated_folder"] and stock_info["target_path"]:
                    response_text = f"מפנה לשלוחת {stock_info['display_name']}."
                    action_type = "go_to_folder"
                    action_value = stock_info["target_path"]
                else:
                    data = get_stock_price_data(stock_info["symbol"])
                    if data:
                        direction = "עלייה" if data["day_change_percent"] > 0 else "ירידה"
                        response_text = (
                            f"מחיר מניית {stock_info['display_name']} עומד כעת על {data['current']} דולר. "
                            f"מתחילת היום נרשמה {direction} של {abs(data['day_change_percent'])} אחוז."
                        )
                    else:
                        response_text = f"מצטערים, לא הצלחנו למצוא נתונים עבור מניית {stock_info['display_name']}."
            else:
                response_text = "לא הצלחנו לזהות את נייר הערך שביקשת. אנא נסה שנית."
        else:
            # לוג 3 (חלופי): לא זוהה דיבור
            L_not_recognized()
            response_text = "לא זוהה דיבור ברור בהקלטה. אנא נסה לדבר באופן ברור יותר."

    generated_audio_success = False
    output_yemot_wav_name = f"{OUTPUT_AUDIO_FILE_BASE}.wav"

    if response_text and action_type == "play_file":
        if await create_audio_file_from_text(response_text, TEMP_MP3_FILE):
            if convert_mp3_to_wav(TEMP_MP3_FILE, output_yemot_wav_name):
                if upload_file_to_yemot(output_yemot_wav_name, output_yemot_wav_name):
                    generated_audio_success = True
    elif action_type == "go_to_folder":
        generated_audio_success = True

    uploaded_ext_ini = False
    if generated_audio_success or action_type == "go_to_folder":
        if create_ext_ini_file(action_type, action_value):
            uploaded_ext_ini = upload_file_to_yemot(OUTPUT_INI_FILE_NAME, OUTPUT_INI_FILE_NAME)

    # ניקוי קבצים זמניים (שקט)
    try:
        for f in [audio_file_path, TEMP_MP3_FILE, OUTPUT_INI_FILE_NAME, output_yemot_wav_name]:
            if f and os.path.exists(f):
                os.remove(f)
    except Exception:
        pass

    if (generated_audio_success or action_type == "go_to_folder") and uploaded_ext_ini:
        # לוג 4: סיום
        L_done()
        return jsonify({"success": True})
    else:
        L_error("כשל ביצירת תגובה/העלאה")
        return jsonify({"success": False, "message": "Failed to create response"})

# --- נקודת קצה של ה-API ---
@app.route('/process_audio', methods=['GET'])
def process_audio_endpoint():
    stockname = request.args.get('stockname')
    if not stockname:
        L_error("חסר פרמטר 'stockname'")
        return jsonify({"error": "Missing 'stockname' parameter"}), 400

    yemot_download_url = "https://www.call2all.co.il/ym/api/DownloadFile"
    file_path_on_yemot = f"ivr2:/{stockname.lstrip('/')}"
    params = {"token": TOKEN, "path": file_path_on_yemot}

    try:
        # לוג 2: התקבל קובץ
        L_new_file(file_path_on_yemot)
        response = requests.get(yemot_download_url, params=params, timeout=30)
        response.raise_for_status()
        file_path = TEMP_INPUT_WAV
        with open(file_path, 'wb') as f:
            f.write(response.content)
        return asyncio.run(process_yemot_recording(file_path))
    except Exception as e:
        L_error(f"הורדה/עיבוד נכשל: {e}")
        return jsonify({"error": "Failed to process audio"}), 500

if __name__ == "__main__":
    ensure_ffmpeg()
    L_start()  # לוג 1: ממתין לקובץ
    app.run(host='0.0.0.0', port=5000, use_reloader=False)
