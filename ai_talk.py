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

SILENT_TIME = 300

# CastCraftからの送信を受け取るポート
TCP_HOST = "0.0.0.0"
TCP_PORT = 50082

# --- voice box の設定 ---
VOICEVOX_SPEED_SCALE = 1.25
VOICEVOX_VOLUME_SCALE = 1.1
VOICEVOX_PITCH_SCALE = 0

# --- セッションの固定 ---
session = requests.Session()

# --- 各種キューと記憶の設定 ---
audio_queue = queue.Queue()
comment_queue = queue.Queue()

# 🧠 つみきの記憶用リスト（直近の文脈を保持）
conversation_history = []
MAX_HISTORY_LENGTH = 100  # 保持する最大発言数（14件＝約7往復分）

# ⏳ 沈黙監視用のタイマー変数（新規追加）
last_active_time = time.time()


def load_file(filepath, default_text=""):
    if not os.path.exists(filepath):
        return default_text
    with open(filepath, "r", encoding="utf-8") as f:
        return f.read().strip()


# --- スレッド1: 音声再生ワーカー（クリーン版） ---
def audio_worker():
    while True:
        item = audio_queue.get()
        if item is None:
            break
        text, style_id = item

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
    global conversation_history, last_active_time  # 💡 last_active_time を追加
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

        # 💡 入力があったためタイマーを即座にリセット
        last_active_time = time.time()

        # AI用のタグを剥ぎ取った、表示・読み上げ用のクリーンなテキストを作る
        clean_text = comment_text
        is_hoshimi = False
        is_system = False  # 💡 システムからの自動催促判定用のフラグを追加

        if comment_text.startswith("【ほしみ】: "):
            clean_text = comment_text.replace("【ほしみ】: ", "", 1)
            is_hoshimi = True
        elif comment_text.startswith("【チャット】: "):
            clean_text = comment_text.replace("【チャット】: ", "", 1)
        elif comment_text.startswith("【システム】: "):  # 💡 システム命令の検知を追加
            clean_text = comment_text.replace("【システム】: ", "", 1)
            is_system = True

        print("\n" + "=" * 40)
        if is_hoshimi:
            print(f"\n💻 ほしみからの入力: 「{clean_text}」")
        elif is_system:
            print(f"\n🤖 システムからの自動催促: 「{clean_text}」")
        else:
            print(f"\n📢 YouTubeチャット受信: 「{clean_text}」")

        # 終了コマンドの判定
        if clean_text in ["/exit", "つみき終了"]:
            print("\n🛑 終了コマンドを検知しました。システムを停止します。")
            audio_queue.put(("システムを終了します", 61))
            time.sleep(3.0)
            os._exit(0)

        # 💡 【変更箇所】YouTubeチャットからの入力（ほしみ・システム以外）の時だけ、コメントを読み上げる
        if not is_hoshimi and not is_system:
            audio_queue.put((clean_text, 61))
            time.sleep(0.2)

        print(f"📡 AI応答生成中(記憶数: {len(conversation_history)})...", end=" ", flush=True)
        t_start = time.time()

        try:
            # 🧠 1. システムプロンプトから始まるメッセージの土台を作る
            api_messages = [{"role": "system", "content": system_prompt}]

            # 🧠 2. これまでの会話履歴（記憶）を挟み込む
            api_messages.extend(conversation_history)

            # 🧠 3. 最後に今回の新しいコメントを追加する
            api_messages.append({"role": "user", "content": comment_text})

            deepinfra_res = client.chat.completions.create(
                model=DEEPINFRA_MODEL,
                messages=api_messages,
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
                audio_queue.put((response_text, 感情ID))

                # 💡 AIが話し終わったタイミングで再度タイマーをリセット（自身の発言から30秒を数えるため）
                last_active_time = time.time()

                # 🧠 4. 今回のやり取りを新しい記憶としてストックに追加
                conversation_history.append({"role": "user", "content": comment_text})
                # ※JSONフォーマットを維持させるため、AIの生出力（JSON文字列）のまま記憶させます
                conversation_history.append({"role": "assistant", "content": raw_content})

                # 🧠 5. 記憶が溢れたら、古いもの（先頭）から削除する
                if len(conversation_history) > MAX_HISTORY_LENGTH:
                    conversation_history = conversation_history[-MAX_HISTORY_LENGTH:]
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


# --- スレッド4: 沈黙監視ワーカー（修正版） ---
def silence_monitor_worker():
    global last_active_time
    while True:
        time.sleep(1)  # 1秒ごとにタイマーをチェック

        # 最後に何かしらの発言・入力があってから30秒以上経過したか判定
        if time.time() - last_active_time >= SILENT_TIME:
            # AIが現在応答処理中でなく、かつ音声再生中でもない場合のみ実行
            if comment_queue.empty() and audio_queue.empty():
                print("\n⏳ 30秒間の沈黙を検知しました。新しい話題の切り出しを促します。")

                # 連続して命令が重複投入されないよう、先にタイマーをリセットしておく
                last_active_time = time.time()

                # 💡 沈黙への言及を禁止し、新しい話題を振らせる指示に変更
                instruction = (
                    "【システム】: チャットの区切りが良いので、あなたから完全に新しい話題を切り出してください。\n"
                    "⚠️注意：『静かだね』『誰も喋らない』『沈黙』など、チャットが止まっている状況への言及は【絶対に禁止】します。\n"
                    "何事もなかったかのように、最近あなたが気になっていること、リスナーへの唐突な質問や無茶振り、"
                    "またはあなたらしい生意気な雑談のネタを自発的に始めてください。"
                )
                comment_queue.put(instruction)

# --- CastCraftからの接続を処理する関数（強化版） ---
def handle_client(conn, addr):
    try:
        while True:
            # 💡 対策①: 15バイトのヘッダーが確実に溜まるまでループして受信する
            header = b""
            while len(header) < 15:
                packet = conn.recv(15 - len(header))
                if not packet:
                    break
                header += packet

            # それでも15バイトに満たない場合は通信終了
            if len(header) < 15:
                break

            command, speed, pitch, volume, voice, encoding, length = struct.unpack('<hhhhhBi', header)

            # 本文の受信（ここは元々綺麗にループ処理されています）
            text_bytes = b""
            while len(text_bytes) < length:
                packet = conn.recv(length - len(text_bytes))
                if not packet:
                    break
                text_bytes += packet

            # デコード処理
            if encoding == 0:
                raw_text = text_bytes.decode('utf-8', errors='ignore').strip()
            elif encoding == 1:
                raw_text = text_bytes.decode('utf-16', errors='ignore').strip()
            else:
                raw_text = text_bytes.decode('shift_jis', errors='ignore').strip()

            if raw_text:
                comment_text = raw_text

                # 💡 対策②: CastCraftの「ユーザー名:本文」形式の時だけ安全に切り分ける
                # 本文にURL(https://)が含まれている場合は、コロン分割をスキップする安全策
                if "http" not in raw_text:
                    if ":" in raw_text:
                        parts = raw_text.split(":", 1)
                        # コロンの後ろに文字がある場合のみ採用
                        if len(parts) > 1 and parts[1].strip():
                            comment_text = parts[1].strip()
                    elif "：" in raw_text:
                        parts = raw_text.split("：", 1)
                        if len(parts) > 1 and parts[1].strip():
                            comment_text = parts[1].strip()

                if comment_text:
                    comment_queue.put(f"【チャット】: {comment_text}")
    except Exception as e:
        print(f"⚠️ クライアント処理エラー: {e}")
    finally:
        conn.close()


# 各種スレッドの起動
threading.Thread(target=audio_worker, daemon=True).start()
threading.Thread(target=comment_worker, daemon=True).start()
threading.Thread(target=console_input_worker, daemon=True).start()
threading.Thread(target=silence_monitor_worker, daemon=True).start()  # 💡 監視スレッドを追加

if __name__ == "__main__":
    print(f"--- つみき v3.6 (文脈記憶・沈黙監視対応版) 起動 ---")
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
