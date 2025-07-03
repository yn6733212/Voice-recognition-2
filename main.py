import requests
import asyncio
import edge_tts
import os
import subprocess
import speech_recognition as sr
import pandas as pd
import yfinance as yf
from difflib import get_close_matches
from requests_toolbelt.multipart.encoder import MultipartEncoder
import re
import shutil
import datetime
import tarfile # לייבוא tarfile עבור קבצי .tar.xz

# --- הגדרות מערכת ימות המשיח ---
USERNAME = "0733181201"
PASSWORD = "6714453"
TOKEN = f"{USERNAME}:{PASSWORD}"
DOWNLOAD_PATH = "20"  # השלוחה ממנה מורידים הקלטות
UPLOAD_FOLDER_FOR_OUTPUT = "22" # השלוחה אליה מעלים את התשובות וה-INI

# --- הגדרות קבצים ---
CSV_FILE_PATH = "stock_data.csv"
TEMP_MP3_FILE = "temp_output.mp3" # קובץ זמני ל-MP3 לפני המרה ל-WAV
TEMP_INPUT_WAV = "temp_input.wav" # קובץ זמני לקלט WAV מימות המשיח
OUTPUT_AUDIO_FILE_BASE = "000" # **שינוי כאן: שם בסיס לקובץ WAV שיועלה לימות המשיח יהיה 000**
OUTPUT_INI_FILE_NAME = "ext.ini" # שם קובץ ה-INI שיועלה לימות המשיח

# --- נתיב להרצת ffmpeg ---
FFMPEG_EXECUTABLE = "ffmpeg"  

def ensure_ffmpeg():
    """מוודא ש-FFmpeg מותקן ונגיש."""
    global FFMPEG_EXECUTABLE
    if not shutil.which("ffmpeg"): # בודק אם ffmpeg כבר ב-PATH של המערכת
        print("⬇️ מתקין ffmpeg...")
        ffmpeg_bin_dir = "ffmpeg_bin"
        os.makedirs(ffmpeg_bin_dir, exist_ok=True)
        
        # הורדת גרסת לינוקס סטטית (tar.xz)
        ffmpeg_url = "https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz"
        archive_path = os.path.join(ffmpeg_bin_dir, "ffmpeg.tar.xz")

        try:
            r = requests.get(ffmpeg_url, stream=True)
            r.raise_for_status()
            with open(archive_path, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)
            print("✅ הורדת ffmpeg הושלמה.")
            
            # חילוץ קובץ tar.xz
            with tarfile.open(archive_path, 'r:xz') as tar_ref:
                tar_ref.extractall(ffmpeg_bin_dir)
            os.remove(archive_path) # מוחק את קובץ הארכיון המקורי

            # מוצא את קובץ ההפעלה ffmpeg בתוך התיקייה שחולצה
            found_ffmpeg_path = None
            for root, _, files in os.walk(ffmpeg_bin_dir):
                # קובצי ffmpeg בלינוקס לרוב לא יכילו סיומת .exe
                if "ffmpeg" in files:  
                    found_ffmpeg_path = os.path.join(root, "ffmpeg")
                    break
            
            if found_ffmpeg_path:
                FFMPEG_EXECUTABLE = found_ffmpeg_path
                # הוסף את התיקייה המכילה את קובץ ה-ffmpeg ל-PATH
                os.environ["PATH"] += os.pathsep + os.path.dirname(FFMPEG_EXECUTABLE)
                # ב-Linux, ודא שקובץ ה-ffmpeg הוא בר הרצה
                if os.name == 'posix':  
                    os.chmod(FFMPEG_EXECUTABLE, 0o755) # הגדרת הרשאות הרצה
                print(f"✅ ffmpeg הותקן והוסף ל-PATH מנתיב: {FFMPEG_EXECUTABLE}")
            else:
                print("❌ שגיאה: לא נמצא קובץ הפעלה של ffmpeg לאחר החילוץ.")
                FFMPEG_EXECUTABLE = "ffmpeg" # חזרה לברירת המחדל, תעלה שגיאה בהמשך
        except Exception as e:
            print(f"❌ שגיאה בהתקנת ffmpeg: {e}")
            FFMPEG_EXECUTABLE = "ffmpeg" # חזרה לברירת המחדל
    else:
        print("⏩ ffmpeg כבר קיים ב-PATH של המערכת.")
        FFMPEG_EXECUTABLE = "ffmpeg" # נשתמש בגרסה שנמצאה ב-PATH

def download_yemot_file():
    """מוריד את קובץ ה-WAV החדש ביותר משלוחת ההורדה בימות המשיח."""
    url = "https://www.call2all.co.il/ym/api/GetIVR2Dir"
    params = {"token": TOKEN, "path": DOWNLOAD_PATH}
    try:
        response = requests.get(url, params=params)
        response.raise_for_status()
        files = response.json().get("files", [])
        
        valid_files = [
            (int(f["name"].replace(".wav", "")), f["name"])
            for f in files if f.get("exists") and f["name"].endswith(".wav") and not f["name"].startswith("M")
        ]
        
        if not valid_files:
            return None, None
        
        _, name = max(valid_files)
        
        dl_url = "https://www.call2all.co.il/ym/api/DownloadFile"
        dl_params = {"token": TOKEN, "path": f"ivr2:/{DOWNLOAD_PATH}/{name}"}
        r = requests.get(dl_url, params=dl_params)
        r.raise_for_status()
        
        with open(TEMP_INPUT_WAV, "wb") as f:
            f.write(r.content)
        print(f"📥 הקלטה חדשה הורדה: {name}")
        return TEMP_INPUT_WAV, name
    except requests.exceptions.RequestException as e:
        print(f"❌ שגיאה בהורדת קובץ מימות המשיח: {e}")
        return None, None
    except Exception as e:
        print(f"❌ שגיאה בלתי צפויה בתהליך הורדת הקובץ: {e}")
        return None, None

def delete_yemot_file(file_name_to_delete):
    """מוחק קובץ משלוחת ההורדות בימות המשיח."""
    delete_url = "https://www.call2all.co.il/ym/api/DeleteFile"
    delete_params = {"token": TOKEN, "path": f"ivr2:/{DOWNLOAD_PATH}/{file_name_to_delete}"}
    try:
        r = requests.get(delete_url, params=delete_params)
        r.raise_for_status()
        print(f"🗑️ הקובץ {file_name_to_delete} נמחק בהצלחה מימות המשיח.")
        return True
    except requests.exceptions.RequestException as e:
        print(f"❌ שגיאה במחיקת קובץ מימות המשיח: {e}")
        return False

def transcribe_audio(filename):
    """מתמלל קובץ אודיו באמצעות Google Speech Recognition."""
    r = sr.Recognizer()
    try:
        with sr.AudioFile(filename) as source:
            audio = r.record(source)
        recognized_text = r.recognize_google(audio, language="he-IL")
        print(f"👂 זוהה דיבור: '{recognized_text}'")
        return recognized_text
    except sr.UnknownValueError:
        print("❌ זיהוי דיבור נכשל: לא זוהה דיבור ברור (ייתכן שההקלטה ריקה או שקטה מדי).")
        return ""
    except sr.RequestError as e:
        print(f"❌ שגיאה בחיבור לשירות זיהוי הדיבור של גוגל: {e} (בדוק חיבור אינטרנט או מכסה API).")
        return ""
    except Exception as e:
        print(f"❌ שגיאה בלתי צפויה בתמלול: {e}")
        return ""

def normalize_text(text):
    """מנרמל טקסט להשוואה (מוריד תווים מיוחדים וממיר לאותיות קטנות)."""
    if not isinstance(text, str):
        if pd.isna(text):
            text = ""
        else:
            text = str(text)
    return re.sub(r'[^א-תa-zA-Z0-9 ]', '', text).lower().strip()

def load_stock_data(path):
    """טוען נתוני מניות מקובץ CSV."""
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
        print(f"✅ נתוני מניות נטענו בהצלחה מ- {path}")
        return stock_data
    except FileNotFoundError:
        print(f"❌ שגיאה: הקובץ {path} לא נמצא. ודא שהוא באותה תיקייה.")
        return {}
    except Exception as e:
        print(f"❌ שגיאה בטעינת נתוני מניות: {e}")
        return {}

def get_best_match(query, stock_dict):
    """מוצא את ההתאמה הטובה ביותר לשאילתה מתוך רשימת המניות."""
    matches = get_close_matches(normalize_text(query), stock_dict.keys(), n=1, cutoff=0.7)
    if not matches:
        matches = get_close_matches(normalize_text(query), stock_dict.keys(), n=1, cutoff=0.5)
    return matches[0] if matches else None

def get_stock_price_data(ticker):
    """מביא נתוני מחיר ושינוי יומי עבור מניה."""
    try:
        stock = yf.Ticker(ticker)
        hist = stock.history(period="7d")  
        
        if hist.empty or len(hist) < 2:
            print(f"⚠️ אין מספיק נתוני היסטוריה עבור {ticker}.")
            return None
        
        current_price = hist["Close"].iloc[-1]
        day_before_price = hist["Close"].iloc[-2]
        
        day_change_percent = (current_price - day_before_price) / day_before_price * 100 if day_before_price else 0
        
        return {"current": round(current_price, 2), "day_change_percent": round(day_change_percent, 2)}
    except Exception as e:
        print(f"❌ שגיאה באחזור נתונים עבור {ticker}: {e}")
        return None

def create_ext_ini_file(action_type, value):
    """יוצר קובץ ext.ini להפנייה בימות המשיח."""
    try:
        with open(OUTPUT_INI_FILE_NAME, 'w', encoding='windows-1255') as f:
            if action_type == "go_to_folder":
                f.write(f"type=go_to_folder\n")
                # הסרת "ivr2:" והסרת תו הלוכסן האחרון אם קיים
                relative_path = value.replace("ivr2:", "").rstrip('/')
                f.write(f"go_to_folder={relative_path}\n")
            elif action_type == "play_file":
                f.write(f"type=playfile\n")
                # **שינוי כאן: לא כותבים את שדה file_name**
                # המערכת תניח שהקובץ הוא 000.wav אם רק type=playfile קיים
        return True
    except Exception as e:
        print(f"❌ שגיאה ביצירת קובץ INI: {e}")
        return False

def upload_file_to_yemot(file_path, yemot_file_name_or_path_on_yemot):
    """מעלה קובץ (אודיו או INI) לימות המשיח."""
    full_upload_path = f"ivr2:/{UPLOAD_FOLDER_FOR_OUTPUT}/{yemot_file_name_or_path_on_yemot}"
    
    m = MultipartEncoder(fields={
        "token": TOKEN,
        "path": full_upload_path,
        "upload": (os.path.basename(file_path), open(file_path, 'rb'), 'audio/wav' if file_path.endswith('.wav') else 'text/plain')
    })
    try:
        r = requests.post("https://www.call2all.co.il/ym/api/UploadFile", data=m, headers={'Content-Type': m.content_type})
        r.raise_for_status()
        print(f"⬆️ הקובץ '{os.path.basename(file_path)}' הועלה בהצלחה לנתיב: {full_upload_path}")
        return True
    except requests.exceptions.RequestException as e:
        print(f"❌ שגיאה בהעלאת קובץ לימות המשיח ({os.path.basename(file_path)}): {e}")
        return False
    except Exception as e:
        print(f"❌ שגיאה בלתי צפויה בהעלאת קובץ לימות המשיח ({os.path.basename(file_path)}): {e}")
        return False

def convert_mp3_to_wav(mp3_file, wav_file):
    """ממיר קובץ MP3 ל-WAV באמצעות FFmpeg."""
    try:
        result = subprocess.run(
            [FFMPEG_EXECUTABLE, "-loglevel", "error", "-y", "-i", mp3_file, "-ar", "8000", "-ac", "1", "-acodec", "pcm_s16le", wav_file],
            check=True
        )
        print(f"✅ קובץ שמע נוצר בהצלחה: {wav_file}")
        return True
    except subprocess.CalledProcessError as e:
        print(f"❌ שגיאה בהמרה (FFmpeg): {e}. ודא ש-FFmpeg מותקן ונגיש.")
    except FileNotFoundError:
        print(f"❌ שגיאה בהמרה (FFmpeg): ffmpeg לא נמצא. ודא שהוא מותקן ב-PATH.")
    except Exception as e:
        print(f"❌ שגיאה כללית בהמרה: {e}")
    return False

async def create_audio_file_from_text(text, filename):
    """יוצר קובץ אודיו (MP3 זמני) מטקסט באמצעות Edge TTS."""
    try:
        comm = edge_tts.Communicate(text, voice="he-IL-AvriNeural")
        await comm.save(filename)
        print(f"✅ קובץ טקסט-לקול זמני נוצר: {filename}")
        return True
    except Exception as e:
        print(f"❌ שגיאה ביצירת קובץ אודיו מטקסט: {e}")
        return False

async def main_loop():
    """הלולאה הראשית של הסקריפט, מנהלת את כל התהליך."""
    stock_data = load_stock_data(CSV_FILE_PATH)
    if not stock_data:
        print("❌ לא ניתן להמשיך ללא נתוני מניות. אנא תקן את stock_data.csv.")
        return

    ensure_ffmpeg()

    last_processed_file = None
    
    print("🔁 התחילה לולאה שמזהה קבצים כל שנייה...")
    while True:
        try:
            filename, yemot_filename = download_yemot_file()
            
            if not yemot_filename or yemot_filename == last_processed_file:
                await asyncio.sleep(1)
                continue

            last_processed_file = yemot_filename
            
            # --- שלב 1: זיהוי דיבור ---
            recognized_text = transcribe_audio(TEMP_INPUT_WAV)
            
            response_text = ""
            action_type = "play_file"
            action_value = "" # ישמש לשם הקובץ 000.wav או לנתיב השלוחה

            if recognized_text:
                best_match_key = get_best_match(recognized_text, stock_data)
                
                if best_match_key:
                    stock_info = stock_data[best_match_key]
                    
                    if stock_info["has_dedicated_folder"] and stock_info["target_path"]:
                        response_text = f"מפנה לשלוחת {stock_info['display_name']}."
                        action_type = "go_to_folder"
                        action_value = stock_info["target_path"]
                        print(f"💡 זוהתה הפניה לשלוחה ייעודית: {stock_info['display_name']} -> {stock_info['target_path']}")
                    else:
                        data = get_stock_price_data(stock_info["symbol"])
                        if data:
                            direction = "עלייה" if data["day_change_percent"] > 0 else "ירידה"
                            response_text = (
                                f"מחיר מניית {stock_info['display_name']} עומד כעת על {data['current']} דולר. "
                                f"מתחילת היום נרשמה {direction} של {abs(data['day_change_percent'])} אחוז."
                            )
                            print(f"📊 נמצאו נתונים עבור {stock_info['display_name']}: {response_text}")
                        else:
                            response_text = f"מצטערים, לא הצלחנו למצוא נתונים עבור מניית {stock_info['display_name']}."
                            print(f"❌ לא נמצאו נתונים עבור מניית {stock_info['display_name']}.")
                        
                        # **שינוי כאן: קובץ הפלט יהיה תמיד 000.wav**
                        action_value = f"{OUTPUT_AUDIO_FILE_BASE}.wav" 

                else:
                    response_text = "לא הצלחנו לזהות את נייר הערך שביקשת. אנא נסה שנית."
                    print(f"❌ לא זוהה נייר ערך תואם ברשימה עבור: '{recognized_text}'")
                    # אם לא זוהה דיבור, עדיין נכין קובץ תשובה ב-000.wav
                    action_value = f"{OUTPUT_AUDIO_FILE_BASE}.wav" 
            else:
                response_text = "לא זוהה דיבור ברור בהקלטה. אנא נסה לדבר באופן ברור יותר."
                print("❌ לא זוהה דיבור ברור בהקלטה.")
                # אם לא זוהה דיבור, עדיין נכין קובץ תשובה ב-000.wav
                action_value = f"{OUTPUT_AUDIO_FILE_BASE}.wav" 


            # --- שלב 2: יצירת תגובה קולית והעלאה ---
            generated_audio_success = False
            uploaded_ext_ini = False
            output_yemot_wav_name = None  # יישמר 000.wav אם צריך

            if response_text and action_type == "play_file":
                output_yemot_wav_name = f"{OUTPUT_AUDIO_FILE_BASE}.wav" # **שינוי כאן: שם קובץ הפלט הוא תמיד 000.wav**
                
                if await create_audio_file_from_text(response_text, TEMP_MP3_FILE):
                    if convert_mp3_to_wav(TEMP_MP3_FILE, output_yemot_wav_name):
                        if upload_file_to_yemot(output_yemot_wav_name, output_yemot_wav_name):
                            generated_audio_success = True
                            print(f"✅ תשובה קולית הועלתה בהצלחה: {output_yemot_wav_name}")
                        else:
                            print("❌ נכשלה העלאת קובץ השמע לימות המשיח.")
                    else:
                        print("❌ נכשלה המרת MP3 ל-WAV.")
                else:
                    print("❌ נכשלה יצירת קובץ אודיו מטקסט.")
            elif action_type == "go_to_folder":
                generated_audio_success = True # עבור הפניה לשלוחה, אין קובץ שמע חדש שצריך ליצור

            if generated_audio_success or action_type == "go_to_folder":
                # action_value כבר מכיל את הנתיב לשלוחה או את "000.wav" (אבל לא נשתמש בו עבור play_file ב-INI)
                if create_ext_ini_file(action_type, action_value):
                    if upload_file_to_yemot(OUTPUT_INI_FILE_NAME, OUTPUT_INI_FILE_NAME):
                        uploaded_ext_ini = True
                        print(f"✅ קובץ {OUTPUT_INI_FILE_NAME} הועלה בהצלחה.")
                    else:
                        print(f"❌ נכשלה העלאת קובץ {OUTPUT_INI_FILE_NAME}.")
                else:
                    print(f"❌ נכשלה יצירת קובץ {OUTPUT_INI_FILE_NAME}.")
            else:
                print("⚠️ לא נוצרה תגובה קולית או הפניה לשלוחה.")

            # --- שלב 3: ניקוי קבצים ומחיקת קובץ המקור בימות המשיח ---
            if uploaded_ext_ini:  
                delete_yemot_file(yemot_filename)
            else:
                print(f"⚠️ לא נמחק הקובץ {yemot_filename} מימות המשיח מכיוון שלא נוצרה תגובה/הפניה בהצלחה.")

            local_files_to_clean = [TEMP_INPUT_WAV, TEMP_MP3_FILE, OUTPUT_INI_FILE_NAME]
            # מוחקים את 000.wav המקומי רק אם נוצר
            if output_yemot_wav_name and os.path.exists(output_yemot_wav_name) and action_type == "play_file":
                local_files_to_clean.append(output_yemot_wav_name)

            for f in local_files_to_clean:
                if os.path.exists(f):
                    os.remove(f)
                    print(f"🧹 נמחק קובץ זמני: {f}")
            
            print("✅ סבב עיבוד הסתיים. ממתין לקובץ חדש...\n")

        except Exception as e:
            print(f"❌ שגיאה קריטית בלולאה הראשית: {e}")
            print("⚠️ ממשיך לולאה לאחר שגיאה...")
        
        await asyncio.sleep(1)

if __name__ == "__main__":
    asyncio.run(main_loop())
