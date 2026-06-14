import requests

URL = "http://localhost:50032/v1/speakers"

print("📡 COEIROINKからキャラクター情報を取得中...")
try:
    res = requests.get(URL, timeout=5)
    res.raise_for_status()
    speakers = res.json()

    print("\n========================================")
    print("      COEIROINK キャラクターID一覧      ")
    print("========================================\n")

    for speaker in speakers:
        name = speaker.get("speakerName", "不明")
        uuid = speaker.get("speakerUuid", "不明")
        print(f"👤 キャラクター名: 【 {name} 】")
        print(f"   🔑 SPEAKER_UUID = \"{uuid}\"")
        print("   🎨 利用可能なスタイル:")

        styles = speaker.get("styles", [])
        for style in styles:
            s_id = style.get("styleId", "?")
            s_name = style.get("styleName", "不明")
            print(f"      -> ID: {s_id} （{s_name}）")
        print("-" * 40)

except Exception as e:
    print(f"\n❌ 取得に失敗しました。")
    print(f"COEIROINK本体が本当に起動しているか確認してください。")
    print(f"エラー詳細: {e}")
