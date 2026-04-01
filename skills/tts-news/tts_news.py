#!/usr/bin/env python3
"""
TTS News Script: 读取新闻文本 → MiniMax Speech HD → MP3 → 发送到飞书

用法:
  python3 tts_news.py "新闻文本内容"
  cat news.txt | python3 tts_news.py
"""

import sys
import os
import json
import tempfile
import requests
from pathlib import Path
from datetime import datetime

# ── 配置 ──────────────────────────────────────────
FEISHU_APP_ID = "cli_a9259b2e6738dcd6"
FEISHU_APP_SECRET = "OuFdqJk0K1XLZAjUESV28bM6cPg0JeTQ"
FEISHU_USER_OPEN_ID = "ou_d1b4e7815a063128f882df928effe864"  # 冰河大人

# MiniMax T2A API
T2A_URL = "https://api.minimax.io/v1/t2a_v2"
T2A_MODEL = "speech-02-hd"

# ── Helpers ──────────────────────────────────────

def get_feishu_token():
    """获取 Feishu tenant access token"""
    resp = requests.post(
        "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
        json={"app_id": FEISHU_APP_ID, "app_secret": FEISHU_APP_SECRET},
        timeout=10
    )
    resp.raise_for_status()
    return resp.json()["tenant_access_token"]

def generate_tts(text: str, output_path: Path) -> Path:
    """调用 MiniMax T2A API，生成 MP3 并保存到 output_path"""
    api_key = None
    auth_file = Path.home() / ".openclaw" / "agents" / "main" / "agent" / "auth-profiles.json"
    if auth_file.exists():
        with open(auth_file) as f:
            profiles = json.load(f).get("profiles", {})
        for p in profiles.values():
            access = p.get("access", "")
            if access.startswith("sk-"):
                api_key = access
                break

    if not api_key:
        raise RuntimeError("未找到 MiniMax API Key")

    resp = requests.post(
        T2A_URL,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": T2A_MODEL,
            "text": text,
            "voice_setting": {
                "voice_id": "male-qn-qingse",  # 清亮男声
                "speed": 1.0,
                "pitch": 0,
                "vol": 1.0,
            },
            "audio_setting": {
                "sample_rate": 32000,
                "bitrate": 128000,
                "format": "mp3",
                "channel": 1,
            },
        },
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()

    audio_url = (
        data.get("data", {}).get("audio_file")
        or data.get("audio_file")
    )
    if audio_url:
        audio_resp = requests.get(audio_url, timeout=60)
        audio_resp.raise_for_status()
        output_path.write_bytes(audio_resp.content)
    else:
        output_path.write_bytes(resp.content)

    return output_path

def upload_to_feishu(file_path: Path, token: str) -> str:
    """上传文件到飞书，返回 file_key"""
    with open(file_path, "rb") as f:
        files = {"file": (file_path.name, f, "audio/mpeg")}
        resp = requests.post(
            "https://open.feishu.cn/open-apis/im/v1/files",
            headers={"Authorization": f"Bearer {token}"},
            files=files,
            data={"file_name": file_path.name, "file_type": "mp3"},
            timeout=30,
        )
    resp.raise_for_status()
    return resp.json()["data"]["file_key"]

def send_feishu_file(file_key: str, file_name: str, token: str):
    """发送文件消息给用户"""
    resp = requests.post(
        "https://open.feishu.cn/open-apis/im/v1/messages",
        params={"receive_id_type": "open_id"},
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json={
            "receive_id": FEISHU_USER_OPEN_ID,
            "msg_type": "file",
            "content": json.dumps({"file_key": file_key, "file_name": file_name}),
        },
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()

def send_feishu_text(text: str, token: str):
    """发送文字消息给用户"""
    resp = requests.post(
        "https://open.feishu.cn/open-apis/im/v1/messages",
        params={"receive_id_type": "open_id"},
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json={
            "receive_id": FEISHU_USER_OPEN_ID,
            "msg_type": "text",
            "content": json.dumps({"text": text}),
        },
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()

# ── 主流程 ────────────────────────────────────────
def main():
    if len(sys.argv) > 1:
        news_text = sys.argv[1]
    elif not sys.stdin.isatty():
        news_text = sys.stdin.read().strip()
    else:
        print("用法: python3 tts_news.py <新闻文本>  或  cat news.txt | python3 tts_news.py", file=sys.stderr)
        sys.exit(1)

    if not news_text:
        print("新闻文本为空", file=sys.stderr)
        sys.exit(1)

    print(f"📝 新闻文本长度: {len(news_text)} 字符")

    # 生成 TTS
    print("🎙️  正在生成语音...")
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
        tmp_path = Path(tmp.name)

    try:
        generate_tts(news_text, tmp_path)
        file_size = tmp_path.stat().st_size
        print(f"✅ MP3 生成成功: {file_size} bytes")

        print("🔑 获取飞书 token...")
        token = get_feishu_token()

        duration_sec = max(1, file_size // 1500)
        send_feishu_text(
            f"🎙️ AI 新闻语音已生成（约 {duration_sec} 秒），点击音频收听",
            token
        )

        print("📤 上传音频到飞书...")
        file_key = upload_to_feishu(tmp_path, token)
        print(f"✅ 文件上传成功: {file_key}")

        file_name = f"AI新闻_{datetime.now().strftime('%m-%d_%H:%M')}.mp3"
        result = send_feishu_file(file_key, file_name, token)
        print(f"✅ 发送成功: {result}")

    finally:
        if tmp_path.exists():
            os.unlink(tmp_path)
            print("🧹 临时文件已清理")

if __name__ == "__main__":
    main()
