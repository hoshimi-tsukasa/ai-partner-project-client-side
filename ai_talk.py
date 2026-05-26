import os
import time
import json
import requests
import winsound
import glob
import threading
import queue
from datetime import datetime
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

# --- 設定 ---
XAI_API_KEY = os.getenv("XAI_API_KEY")
XAI_BASE_URL = "https://api.x.ai/v1"
XAI_MODEL = "grok-4-1-fast-non-reasoning"
TSUMIKI_URL = "http://192.168.1.220:8080/v1/chat/completions"
VOICEVOX_URL = "http://localhost:50021"
# --- voice box の設定 ---
VOICEVOX_SPEED_SCALE = 1.25
VOICEVOX_VOLUME_SCALE = 1.1
VOICEVOX_PITCH_SCALE = 0

# --- セッションの固定 ---
session = requests.Session()

# --- 音声処理用キュー ---
audio_queue = queue.Queue()

def audio_worker():
    while True:
        item = audio_queue.get()
        if item is None: break
        text, style_id = item
        try:
            query_res = session.post(f"{VOICEVOX_URL}/audio_query", params={'text': text, 'speaker': style_id}, timeout=10)
            query_data = query_res.json()
            query_data.update({
                'speedScale': VOICEVOX_SPEED_SCALE,
                'volumeScale': VOICEVOX_VOLUME_SCALE,
                'pitchScale': VOICEVOX_PITCH_SCALE
            })
            synthesis_res = session.post(f"{VOICEVOX_URL}/synthesis", params={'speaker': style_id}, data=json.dumps(query_data), timeout=30)
            filename = f"temp_voice_{int(time.time()*1000)}.wav"
            with open(filename, "wb") as f:
                f.write(synthesis_res.content)
            winsound.PlaySound(filename, winsound.SND_FILENAME)
            try: os.remove(filename)
            except: pass
        except Exception as e:
            print(f"\n⚠️ 音声ワーカーエラー: {e}")
        audio_queue.task_done()

threading.Thread(target=audio_worker, daemon=True).start()

def load_file(filepath, default_text=""):
    if not os.path.exists(filepath): return default_text
    with open(filepath, "r", encoding="utf-8") as f: return f.read().strip()

client = OpenAI(api_key=XAI_API_KEY, base_url=XAI_BASE_URL)

def main():
    llama_sys = load_file("system_prompt.txt", "あなたは生意気な少女AIです。")
    grok_sys = load_file("grok_prompt.txt", "あなたはつみきの司令塔です。")

    print("\n" + "="*40)
    # 入力プロンプト変更
    user_input = input("入力タイプを指定: [ターミナル] または [チャット] を付けて入力: ")
    if not user_input.strip(): return
    
    # タグバリデーション
    if not user_input.startswith(("[ターミナル]", "[チャット]")):
        print("エラー: 入力は [ターミナル] または [チャット] タグで始めてください")
        return

    # YouTubeコメント読み上げ (チャット入力時)
    if user_input.startswith("[チャット]"):
        comment_text = user_input[len("[チャット]"):].strip()
        if comment_text:
            print(f"\n📢 YouTubeコメント読み上げ: 「{comment_text}」")
            audio_queue.put((comment_text, 61))
            time.sleep(1.5)  # 読み上げ間隔

    # Grok分析 (全入力共通)
    print(f"📡 思考スキャン中...", end=" ", flush=True)
    t_start = time.time()
    
    grok_res = client.chat.completions.create(
        model=XAI_MODEL,
        messages=[{"role": "system", "content": grok_sys}, {"role": "user", "content": user_input}],
        response_format={ "type": "json_object" }
    )
    res_json = json.loads(grok_res.choices[0].message.content)
    感情ID = res_json.get("style_id", 61)
    ネタ = res_json.get("ネタ", "")
    print(f"Done! (Style:{感情ID})")
    print(f"🧠 Grokの思考(ネタ): {ネタ}")

    # 2. Llama変換
    print(f"💖 つみき: ", end="", flush=True)
    llama_output_full = ""
    
    payload = {
        "messages": [
            {"role": "system", "content": llama_sys},
            {"role": "user", "content": ネタ}
        ],
        "stream": True
    }
    
    try:
        with session.post(TSUMIKI_URL, json=payload, stream=True, timeout=(5, 60)) as r:
            buffer = ""
            for line in r.iter_lines():
                if not line: continue
                decoded = line.decode('utf-8').replace('data: ', '').strip()
                if decoded == '[DONE]': break
                try:
                    chunk = json.loads(decoded)
                    content = chunk['choices'][0]['delta'].get('content', '')
                    if content:
                        print(content, end="", flush=True)
                        buffer += content
                        llama_output_full += content
                        if any(p in content for p in ["。", "！", "？", "!", "?", "、", "\n"]):
                            audio_queue.put((buffer, 感情ID))
                            buffer = ""
                except: continue
        if buffer.strip(): audio_queue.put((buffer, 感情ID))
    except Exception as e:
        print(f"\n⚠️ Llama通信エラー: {e}")

    # 応答時間の確定
    response_time = time.time() - t_start

    # --- 詳細ログを記録 (chat_log.txt) ---
    with open("chat_log.txt", "a", encoding="utf-8") as f:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        f.write(f"[{timestamp}] ID:{感情ID}\n")
        f.write(f" Master: {user_input}\n")
        f.write(f" Grok(ネタ): {ネタ}\n")
        f.write(f" Tsumiki: {llama_output_full}\n")
        f.write(f" ResponseTime: {response_time:.2f}s\n")
        f.write("-" * 40 + "\n")

    print(f"\n⚡ 応答完了: {response_time:.2f}秒")

if __name__ == "__main__":
    print(f"--- つみき v3.3 (タイムログ実装版) 起動 ---")
    while True:
        try: main()
        except KeyboardInterrupt: break
