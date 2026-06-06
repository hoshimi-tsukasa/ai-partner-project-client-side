import json
import os
import queue
import threading
import time
import winsound
from datetime import datetime

import requests
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

# --- 設定 ---
XAI_API_KEY = os.getenv("XAI_API_KEY")
XAI_BASE_URL = "https://api.x.ai/v1"
XAI_MODEL = "grok-4-1-fast-non-reasoning"
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
        if item is None:
            break
        text, style_id = item
        try:
            query_res = session.post(
                f"{VOICEVOX_URL}/audio_query",
                params={"text": text, "speaker": style_id},
                timeout=10,
            )
            query_data = query_res.json()
            query_data.update(
                {
                    "speedScale": VOICEVOX_SPEED_SCALE,
                    "volumeScale": VOICEVOX_VOLUME_SCALE,
                    "pitchScale": VOICEVOX_PITCH_SCALE,
                }
            )
            synthesis_res = session.post(
                f"{VOICEVOX_URL}/synthesis",
                params={"speaker": style_id},
                data=json.dumps(query_data),
                timeout=30,
            )
            filename = f"temp_voice_{int(time.time() * 1000)}.wav"
            with open(filename, "wb") as f:
                f.write(synthesis_res.content)
            winsound.PlaySound(filename, winsound.SND_FILENAME)
            try:
                os.remove(filename)
            except:
                pass
        except Exception as e:
            print(f"\n⚠️ 音声ワーカーエラー: {e}")
        audio_queue.task_done()


threading.Thread(target=audio_worker, daemon=True).start()


def load_file(filepath, default_text=""):
    if not os.path.exists(filepath):
        return default_text
    with open(filepath, "r", encoding="utf-8") as f:
        return f.read().strip()


client = OpenAI(api_key=XAI_API_KEY, base_url=XAI_BASE_URL)


def main():
    grok_sys = load_file("grok_prompt.txt", "あなたは生意気な少女AIです。")

    print("\n" + "=" * 40)
    # 入力プロンプト変更
    user_input = input(
        "入力タイプを指定: [ターミナル] または [チャット] を付けて入力: "
    )
    if not user_input.strip():
        return

    # タグバリデーション
    if not user_input.startswith(("[ターミナル]", "[チャット]")):
        print("エラー: 入力は [ターミナル] または [チャット] タグで始めてください")
        return

    # YouTubeコメント読み上げ (チャット入力時)
    if user_input.startswith("[チャット]"):
        comment_text = user_input[len("[チャット]") :].strip()
        if comment_text:
            print(f"\n📢 YouTubeコメント読み上げ: 「{comment_text}」")
            audio_queue.put((comment_text, 61))
            time.sleep(1.5)  # 読み上げ間隔

    # Grok直接応答 (全入力共通)
    print(f"📡 Grok応答生成中...", end=" ", flush=True)
    t_start = time.time()

    grok_res = client.chat.completions.create(
        model=XAI_MODEL,
        messages=[
            {"role": "system", "content": grok_sys},
            {"role": "user", "content": user_input},
        ],
        response_format={"type": "json_object"},
    )
    res_json = json.loads(grok_res.choices[0].message.content)
    感情ID = res_json.get("style_id", 61)
    response_text = res_json.get("response", "")
    print(f"Done! (Style:{感情ID})")

    # 直接音声合成
    if response_text.strip():
        print(f"💖 つみき: {response_text}")
        audio_queue.put((response_text, 感情ID))

    # 応答時間の確定
    response_time = time.time() - t_start

    # --- 詳細ログを記録 (chat_log.txt) ---
    with open("chat_log.txt", "a", encoding="utf-8") as f:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        f.write(f"[{timestamp}] ID:{感情ID}\n")
        f.write(f" Master: {user_input}\n")
        f.write(f" Tsumiki(Grok直接): {response_text}\n")
        f.write(f" ResponseTime: {response_time:.2f}s\n")
        f.write("-" * 40 + "\n")

    print(f"\n⚡ 応答完了: {response_time:.2f}秒")


if __name__ == "__main__":
    print(f"--- つみき v3.4 (Grok直接応答版) 起動 ---")
    while True:
        try:
            main()
        except KeyboardInterrupt:
            break
