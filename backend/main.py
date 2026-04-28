from fastapi import FastAPI, WebSocket, WebSocketDisconnect, UploadFile, File, BackgroundTasks
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
import asyncio
import os
import shutil
import uuid
from typing import Dict
import yt_dlp
from downloader import get_ydl_opts

app = FastAPI(title="YouTube Web Downloader")

# 允许跨域（方便本地测试）
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"])

DOWNLOAD_DIR = "downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)
COOKIE_DIR = "cookies"
os.makedirs(COOKIE_DIR, exist_ok=True)

active_connections: Dict[str, WebSocket] = {}
tasks: Dict[str, asyncio.Task] = {}

app.mount("/static", StaticFiles(directory="../frontend"), name="static")

@app.get("/")
async def serve_frontend():
    return FileResponse("../frontend/index.html")

# ================== Cookie 上传 ==================
@app.post("/upload_cookie")
async def upload_cookie(file: UploadFile = File(...)):
    file_path = os.path.join(COOKIE_DIR, file.filename)
    with open(file_path, "wb") as f:
        shutil.copyfileobj(file.file, f)
    return {"success": True, "path": file_path}

# ================== WebSocket 实时日志 ==================
@app.websocket("/ws/{task_id}")
async def websocket_endpoint(websocket: WebSocket, task_id: str):
    await websocket.accept()
    active_connections[task_id] = websocket
    try:
        while True:
            await asyncio.sleep(10)  # 保持连接
    except WebSocketDisconnect:
        active_connections.pop(task_id, None)

async def send_to_ws(task_id: str, msg_type: str, **data):
    if task_id in active_connections:
        try:
            await active_connections[task_id].send_json({"type": msg_type, **data})
        except:
            active_connections.pop(task_id, None)

# ================== 下载核心（支持实时进度） ==================
async def run_download(task_id: str, urls: list, quality: str, playlist: bool, cookie_path: str = None):
    try:
        await send_to_ws(task_id, "status", message="正在提取视频信息...")

        def progress_hook(d):
            if d['status'] == 'downloading':
                try:
                    percent = d.get('_percent_str', '0%').strip('%') or '0'
                    asyncio.create_task(send_to_ws(task_id, "progress", percent=float(percent)))
                    asyncio.create_task(send_to_ws(task_id, "log", message=d.get('_percent_str', '') + " " + d.get('_speed_str', '')))
                    if 'filename' in d:
                        asyncio.create_task(send_to_ws(task_id, "current_file", filename=os.path.basename(d['filename'])))
                except:
                    pass

        for url in urls:
            opts = get_ydl_opts(url, quality, DOWNLOAD_DIR, playlist, cookie_path)
            opts['progress_hooks'] = [progress_hook]

            await send_to_ws(task_id, "log", message=f"开始下载: {url}")

            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
                filename = ydl.prepare_filename(info)
                file_path = os.path.join(DOWNLOAD_DIR, os.path.basename(filename))

                # 下载完成后立即提供下载链接并删除文件（节省硬盘）
                if os.path.exists(file_path):
                    download_url = f"/download/{os.path.basename(file_path)}"
                    await send_to_ws(task_id, "finished", download_url=download_url, message="下载完成")
                    # 延迟删除（给用户一点时间点击下载）
                    asyncio.create_task(delayed_delete(file_path, 30))

    except Exception as e:
        await send_to_ws(task_id, "log", message=f"错误: {str(e)}")
        await send_to_ws(task_id, "finished", message="下载失败")
    finally:
        active_connections.pop(task_id, None)

async def delayed_delete(file_path: str, seconds: int):
    await asyncio.sleep(seconds)
    try:
        if os.path.exists(file_path):
            os.remove(file_path)
    except:
        pass

@app.post("/download")
async def start_download(data: dict, background_tasks: BackgroundTasks):
    task_id = str(uuid.uuid4())
    task = asyncio.create_task(run_download(
        task_id,
        data.get("urls", []),
        data.get("quality", "最高质量（推荐）"),
        data.get("playlist", False),
        data.get("cookie_path")
    ))
    tasks[task_id] = task
    return {"success": True, "task_id": task_id}

@app.get("/download/{filename}")
async def get_file(filename: str):
    file_path = os.path.join(DOWNLOAD_DIR, filename)
    if os.path.exists(file_path):
        return FileResponse(file_path, filename=filename, media_type="application/octet-stream")
    return JSONResponse(status_code=404, content={"detail": "文件不存在或已被删除"})

@app.post("/cancel/{task_id}")
async def cancel_task(task_id: str):
    if task_id in tasks:
        tasks[task_id].cancel()
        await send_to_ws(task_id, "log", message="任务已被用户取消")
    return {"success": True}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000)   # 本地测试用