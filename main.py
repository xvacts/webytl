from fastapi import FastAPI, WebSocket, WebSocketDisconnect, UploadFile, File, BackgroundTasks
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import asyncio
import os
import shutil
import uuid
from typing import Dict
import yt_dlp
from concurrent.futures import ThreadPoolExecutor

from downloader import get_ydl_opts

app = FastAPI(title="YouTube 下载工具")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DOWNLOAD_DIR = os.path.join(BASE_DIR, "downloads")
COOKIE_DIR = os.path.join(BASE_DIR, "cookies")

os.makedirs(DOWNLOAD_DIR, exist_ok=True)
os.makedirs(COOKIE_DIR, exist_ok=True)

active_connections: Dict[str, WebSocket] = {}
executor = ThreadPoolExecutor(max_workers=3)

# ====================== 首页 ======================
@app.get("/")
async def serve_frontend():
    return FileResponse(os.path.join(BASE_DIR, "index.html"))

# ====================== Cookie 上传 ======================
@app.post("/upload_cookie")
async def upload_cookie(file: UploadFile = File(...)):
    file_path = os.path.join(COOKIE_DIR, file.filename)
    with open(file_path, "wb") as f:
        shutil.copyfileobj(file.file, f)
    return {"success": True, "path": file_path}

# ====================== WebSocket ======================
@app.websocket("/ws/{task_id}")
async def websocket_endpoint(websocket: WebSocket, task_id: str):
    await websocket.accept()
    active_connections[task_id] = websocket
    try:
        while True:
            await asyncio.sleep(30)
    except WebSocketDisconnect:
        active_connections.pop(task_id, None)

async def send_to_ws(task_id: str, msg_type: str, **data):
    if task_id in active_connections:
        try:
            await active_connections[task_id].send_json({"type": msg_type, **data})
        except Exception:
            active_connections.pop(task_id, None)

# ====================== 同步下载函数（关键修复） ======================
def run_yt_dlp_download(task_id: str, urls: list, quality: str, playlist: bool, cookie_path=None):
    """在线程池中执行 yt-dlp 下载"""
    async def inner():
        try:
            await send_to_ws(task_id, "status", message="正在提取视频信息...")
            await send_to_ws(task_id, "log", message="yt-dlp 开始工作...")

            def progress_hook(d):
                if d.get('status') == 'downloading':
                    try:
                        percent_str = d.get('_percent_str', '0%').strip('%') or '0'
                        percent = float(percent_str)
                        asyncio.run_coroutine_threadsafe(
                            send_to_ws(task_id, "progress", percent=percent), asyncio.get_event_loop()
                        )
                        asyncio.run_coroutine_threadsafe(
                            send_to_ws(task_id, "log", message=f"{d.get('_percent_str', '')} {d.get('_speed_str', '')}"), 
                            asyncio.get_event_loop()
                        )
                    except:
                        pass

            for url in urls:
                await send_to_ws(task_id, "log", message=f"开始下载 → {url}")
                
                opts = get_ydl_opts(url, quality, DOWNLOAD_DIR, playlist, cookie_path)
                opts['progress_hooks'] = [progress_hook]

                with yt_dlp.YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(url, download=True)
                    downloaded_file = ydl.prepare_filename(info)
                    filename = os.path.basename(downloaded_file)

                    download_url = f"/download/{filename}"
                    await send_to_ws(task_id, "finished", 
                                   download_url=download_url, 
                                   message="下载完成！")
                    
                    # 延迟删除文件
                    asyncio.create_task(delayed_delete(os.path.join(DOWNLOAD_DIR, filename), 90))

        except Exception as e:
            await send_to_ws(task_id, "log", message=f"发生错误: {str(e)}")
            await send_to_ws(task_id, "finished", message="下载失败")
        finally:
            active_connections.pop(task_id, None)

    # 在新的事件循环中运行
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(inner())
    loop.close()


async def delayed_delete(file_path: str, seconds: int = 90):
    await asyncio.sleep(seconds)
    try:
        if os.path.exists(file_path):
            os.remove(file_path)
    except:
        pass


# ====================== 开始下载接口 ======================
@app.post("/download")
async def start_download(data: dict, background_tasks: BackgroundTasks):
    task_id = str(uuid.uuid4())
    
    background_tasks.add_task(
        run_yt_dlp_download,
        task_id,
        data.get("urls", []),
        data.get("quality", "最高质量（推荐）"),
        data.get("playlist", False),
        data.get("cookie_path")
    )

    return {"success": True, "task_id": task_id}


@app.get("/download/{filename}")
async def get_file(filename: str):
    file_path = os.path.join(DOWNLOAD_DIR, filename)
    if os.path.exists(file_path):
        return FileResponse(file_path, filename=filename)
    return JSONResponse(status_code=404, content={"detail": "文件不存在"})


@app.post("/cancel/{task_id}")
async def cancel_task(task_id: str):
    await send_to_ws(task_id, "log", message="已请求取消")
    return {"success": True}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000)
