import yt_dlp
import os
import asyncio
from typing import Dict, Any, Optional

QUALITY_MAP = {
    "最高质量（推荐）": "bestvideo*+bestaudio/best",
    "1080p（或更低）": "bestvideo[height<=1080]+bestaudio/best[height<=1080]",
    "720p（或更低）": "bestvideo[height<=720]+bestaudio/best[height<=720]",
    "480p（或更低）": "bestvideo[height<=480]+bestaudio/best[height<=480]",
    "最小体积（适合流量少）": "bestvideo[height<=360][vcodec^=avc1]+bestaudio[ext=m4a]/best[ext=mp4]",
    "仅音频（MP3 192kbps）": "bestaudio/best",
}

def get_ydl_opts(url: str, quality: str, save_dir: str, playlist: bool, cookie_path: Optional[str] = None):
    fmt = QUALITY_MAP.get(quality, "bestvideo*+bestaudio/best")
    opts: Dict[str, Any] = {
        'outtmpl': os.path.join(save_dir, '%(title)s.%(ext)s'),
        'format': fmt,
        'continuedl': True,
        'retries': 10,
        'fragment_retries': 10,
        'noplaylist': not playlist,
        'quiet': False,
    }

    if quality == "仅音频（MP3 192kbps）":
        opts['postprocessors'] = [{'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3', 'preferredquality': '192'}]
    else:
        opts['merge_output_format'] = 'mp4'

    if cookie_path and os.path.exists(cookie_path):
        opts['cookiefile'] = cookie_path

    return opts
