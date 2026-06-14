import json
import os
import queue
import threading
import time
import winsound
import socket
import struct
from datetime import datetime

import requests
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

# --- 設定 ---
DEEPINFRA_API_KEY = os.getenv("DEEPINFRA_API_KEY")
DEEPINFRA_BASE_URL = "https://api.deepinfra.com/v1/openai"
# DEEPINFRA_MODEL = "Qwen/Qwen2.5-72B-Instruct"
# DEEPINFRA_MODEL = "Qwen/Qwen3.6-27B"
DEEPINFRA_MODEL = "deepseek-ai/DeepSeek-V4-Flash"
VOICEVOX_URL = "http://localhost:50021"

# CastCraftからの送信を受け取るポート
TCP_HOST = "0.0.0.0"
TCP_PORT = 50082

# --- voice box の設定 ---
VOICEVOX_SPEED_SCALE = 1.25
VOICEVOX_VOLUME_SCALE = 1.1
VOICEVOX_PITCH_SCALE = 0

# --- セッションの固定 ---
session = requests.Session()

# --- 各種キュー ---
audio_queue = queue.Queue()
comment_queue = queue.Queue()


def load_file(filepath, default_text=""):
    if not os.path.exists(filepath):
        return default_text
    with open(filepath, "r", encoding="utf-8") as f:
        return f.read().strip()


# 💡 リスナーが打った「英字コメント」をVOICEVOXで安全に読ませるための1文字読み変換
def alphabet_to_katakana(text):
    mapping = {
        'a': 'エー', 'b': 'ビー', 'c': 'シー', 'd': 'ディー', 'e': 'イー',
        'f': 'エフ', 'g': 'ジー', 'h': 'エイチ', 'i': 'アイ', 'j': 'ジェー',
        'k': 'ケー', 'l': 'エル', 'm': 'エム', 'n': 'エヌ', 'o': 'オー',
        'p': 'ピー', 'q': 'キュー', 'r': 'アール', 's': 'エス', 't': 'ティー',
        'u': 'ユー', 'v': 'ブイ', 'w': 'ダブリュー', 'x': 'エックス', 'y': 'ワイ', 'z': 'ゼット',
        'A': 'エー', 'B': 'ビー', 'C': 'シー', 'D': 'ディー', 'E': 'イー',
        'F': 'エフ', 'G': 'ジー', 'H': 'エイチ', 'I': 'アイ', 'J': 'ジェー',
        'K': 'ケー', 'L': 'エル', 'M': 'エム', 'N': 'エヌ', 'O': 'オー',
        'P': 'ピー', 'Q': 'キュー', 'R': 'アール', 'S': 'エス', 'T': 'ティー',
        'U': 'ユー', 'V': 'ブイ', 'W': 'ダブリュー', 'X': 'エックス', 'Y': 'ワイ', 'Z': 'ゼット'
    }
    for eng, kata in mapping.items():
        text = text.replace(eng, kata)
    return text


# --- スレッド1: 音声再生ワーカー（クリーン版） ---
def audio_worker():
    while True:
        item = audio_queue.get()
        if item is None:
            break
        text, style_id = item

        # 💡 ここでの一律アルファベット変換を削除しました（AIが作ったカタカナをそのまま活かします）
        print(f"\n🔊 [音声キュー処理開始] テキスト: 「{text}」 (Style:{style_id})")
        try:
            # 1. 音声クエリの作成
            print("   -> 1/3 VOICEVOXに音声クエリを要求中...", end="", flush=True)
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
            print(" OK!")

            # 2. 音色合成
            print("   -> 2/3 VOICEVOXで音声合成（WAV生成）中...", end="", flush=True)
            synthesis_res = session.post(
                f"{VOICEVOX_URL}/synthesis",
                params={"speaker": style_id},
                data=json.dumps(query_data),
                timeout=30,
            )
            print(" OK!")

            # 3. 再生
            filename = f"temp_voice_{int(time.time() * 1000)}.wav"
            with open(filename, "wb") as f:
                f.write(synthesis_res.content)

            print(f"   -> 3/3 winsoundで再生中... (サイズ: {len(synthesis_res.content)} bytes)")
            winsound.PlaySound(filename, winsound.SND_FILENAME)

            # 息継ぎ（ウェイト）
            time.sleep(0.6)

            try:
                os.remove(filename)
            except:
                pass
            print("   -> 再生完了！")
        except Exception as e:
            print(f"\n⚠️ 音声ワーカーエラー: {e}")
        audio_queue.task_done()


# --- スレッド2: AI応答生成ワーカー ---
def comment_worker():
    base_system_prompt = load_file("system_prompt.txt", "あなたは生意気な少女AIです。")

    system_prompt = (
        base_system_prompt +
        "\n必ず次のJSONフォーマットのみで返答を出力してください。余計な解説は一切不要です。\n"
        '{"response": "あなたの生意気な返答メッセージ", "style_id": 61}'
    )

    client = OpenAI(api_key=DEEPINFRA_API_KEY, base_url=DEEPINFRA_BASE_URL)

    while True:
        item = comment_queue.get()
        if item is None:
            break
        comment_text = item

        # AI用のタグを剥ぎ取った、表示・読み上げ用のクリーンなテキストを作る
        clean_text = comment_text
        is_hoshimi = False
        if comment_text.startswith("【ほしみ】: "):
            clean_text = comment_text.replace("【ほしみ】: ", "", 1)
            is_hoshimi = True
        elif comment_text.startswith("【チャット】: "):
            clean_text = comment_text.replace("【チャット】: ", "", 1)

        print("\n" + "=" * 40)
        if is_hoshimi:
            print(f"\n💻 ほしみからの入力: 「{clean_text}」")
        else:
            print(f"\n📢 YouTubeチャット受信: 「{clean_text}」")

        # 終了コマンドの判定
        if clean_text in ["/exit", "つみき終了"]:
            print("\n🛑 終了コマンドを検知しました。システムを停止します。")
            audio_queue.put(("システムを終了します", 61))
            time.sleep(3.0)
            os._exit(0)

        # 💡 ユーザーのコメント読み上げ時のみ、英字を安全な1文字読みに変換して音声キューへ投入
        user_speech_text = alphabet_to_katakana(clean_text)
        audio_queue.put((user_speech_text, 61))
        time.sleep(0.2)

        print(f"📡 DeepInfra応答生成中...", end=" ", flush=True)
        t_start = time.time()

        try:
            deepinfra_res = client.chat.completions.create(
                model=DEEPINFRA_MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": comment_text},
                ],
                response_format={"type": "json_object"},
            )

            raw_content = deepinfra_res.choices[0].message.content
            print(f"Done! (AI生出力: {raw_content})")

            res_json = json.loads(raw_content)

            if isinstance(res_json, list) and len(res_json) > 0:
                res_json = res_json[0]

            response_text = ""
            感情ID = 61

            if isinstance(res_json, dict):
                response_text = res_json.get("response") or res_json.get("reply") or res_json.get("text") or ""
                感情ID = res_json.get("style_id", 61)
            else:
                response_text = str(res_json)

            if response_text.strip():
                print(f"💖 つみき: {response_text}")
                # 💡 AIの返答はそのまま音声キューに流し込みます（AIがプロンプトに従って完璧なカタカナ表現を作ります）
                audio_queue.put((response_text, 感情ID))
            else:
                print("⚠️ 警告: AIの返答テキストが空っぽです")

            response_time = time.time() - t_start
            print(f"⚡ 応答処理完了: {response_time:.2f}秒")

            with open("chat_log.txt", "a", encoding="utf-8") as f:
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                f.write(f"[{timestamp}] ID:{感情ID}\n")
                f.write(f" Master: {clean_text}\n")
                f.write(f" Tsumiki: {response_text}\n")
                f.write(f" ResponseTime: {response_time:.2f}s\n")
                f.write("-" * 40 + "\n")

        except Exception as e:
            print(f"\n⚠️ AI応答生成エラー: {e}")

        comment_queue.task_done()


# --- スレッド3: キーボード入力ワーカー ---
def console_input_worker():
    while True:
        try:
            user_input = input().strip()
            if user_input:
                comment_queue.put(f"【ほしみ】: {user_input}")

        except (KeyboardInterrupt, EOFError):
            break
        except Exception as e:
            print(f"⚠️ コンソール入力エラー: {e}")
            time.sleep(1)


# --- CastCraftからの接続を処理する関数 ---
def handle_client(conn, addr):
    try:
        while True:
            header = conn.recv(15)
            if not header or len(header) < 15:
                break

            command, speed, pitch, volume, voice, encoding, length = struct.unpack('<hhhhhBi', header)

            text_bytes = b""
            while len(text_bytes) < length:
                packet = conn.recv(length - len(text_bytes))
                if not packet:
                    break
                text_bytes += packet

            if encoding == 0:
                raw_text = text_bytes.decode('utf-8', errors='ignore').strip()
            elif encoding == 1:
                raw_text = text_bytes.decode('utf-16', errors='ignore').strip()
            else:
                raw_text = text_bytes.decode('shift_jis', errors='ignore').strip()

            if raw_text:
                comment_text = raw_text
                if ":" in raw_text:
                    comment_text = raw_text.split(":", 1)[1].strip()
                elif "：" in raw_text:
                    comment_text = raw_text.split("：", 1)[1].strip()

                if comment_text:
                    comment_queue.put(f"【チャット】: {comment_text}")
    except Exception:
        pass
    finally:
        conn.close()


threading.Thread(target=audio_worker, daemon=True).start()
threading.Thread(target=comment_worker, daemon=True).start()
threading.Thread(target=console_input_worker, daemon=True).start()


if __name__ == "__main__":
    print(f"--- つみき v3.6 (英字カタカナ化＆自動判別版) 起動 ---")
    print(f"📡 ポート {TCP_PORT} で待ち受けつつ、キーボード入力も受付中...")

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    try:
        server.bind((TCP_HOST, TCP_PORT))
        server.listen(10)
    except Exception as e:
        print(f"❌ サーバー起動エラー: {e}")
        exit(1)

    while True:
        try:
            conn, addr = server.accept()
            threading.Thread(target=handle_client, args=(conn, addr), daemon=True).start()
        except KeyboardInterrupt:
            print("\n終了します。")
            break
        except Exception:
            pass

    server.close()
