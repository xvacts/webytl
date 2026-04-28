from fastapi import FastAPI, WebSocket, WebSocketDisconnect, UploadFile, File
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import asyncio
import os
import shutil
import uuid
from typing import Dict
import yt_dlp

# 从 downloader.py 导入
from downloader import get_ydl_opts

app = FastAPI(title="YouTube 下载工具 - Web版")

# ====================== 中间件 ======================
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ====================== 路径配置 ======================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DOWNLOAD_DIR = os.path.join(BASE_DIR, "downloads")
COOKIE_DIR = os.path.join(BASE_DIR, "cookies")

os.makedirs(DOWNLOAD_DIR, exist_ok=True)
os.makedirs(COOKIE_DIR, exist_ok=True)

# ====================== 全局变量 ======================
active_connections: Dict[str, WebSocket] = {}
tasks: Dict[str, asyncio.Task] = {}

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
    return {"success": True, "path": file_path, "filename": file.filename}

# ====================== WebSocket 实时日志 ======================
@app.websocket("/ws/{task_id}")
async def websocket_endpoint(websocket: WebSocket, task_id: str):
    await websocket.accept()
    active_connections[task_id] = websocket
    try:
        while True:
            await asyncio.sleep(30)  # 保持连接
    except WebSocketDisconnect:
        active_connections.pop(task_id, None)

async def send_to_ws(task_id: str, msg_type: str, **data):
    if task_id in active_connections:
        try:
            await active_connections[task_id].send_json({"type": msg_type, **data})
        except:
            active_connections.pop(task_id, None)

# ====================== 下载核心函数 ======================
async def run_download(task_id: str, urls: list, quality: str, playlist: bool, cookie_path: str = None):
    try:
        await send_to_ws(task_id, "status", message="正在准备下载...")

        def progress_hook(d):
            if d.get('status') == 'downloading':
                try:
                    percent_str = d.get('_percent_str', '0%').strip('%') or '0'
                    percent = float(percent_str)
                    asyncio.create_task(send_to_ws(task_id, "progress", percent=percent))
                    asyncio.create_task(send_to_ws(task_id, "log", 
                        message=f"{d.get('_percent_str', '')} {d.get('_speed_str', '')} {d.get('_eta_str', '')}"))
                    
                    if 'filename' in d:
                        asyncio.create_task(send_to_ws(task_id, "current_file", 
                            filename=os.path.basename(d['filename'])))
                except:
                    pass

        for url in urls:
            await send_to_ws(task_id, "log", message=f"开始处理: {url}")
            
            opts = get_ydl_opts(url, quality, DOWNLOAD_DIR, playlist, cookie_path)
            opts['progress_hooks'] = [progress_hook]

            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
                filename = ydl.prepare_filename(info)
                file_path = os.path.join(DOWNLOAD_DIR, os.path.basename(filename))

                if os.path.exists(file_path):
                    download_url = f"/download/{os.path.basename(file_path)}"
                    await send_to_ws(task_id, "finished", 
                                   download_url=download_url, 
                                   message="下载完成")
                    # 60秒后自动删除文件，节省 Render 硬盘空间
                    asyncio.create_task(delayed_delete(file_path, 60))

    except Exception as e:
        error_msg = str(e)
        await send_to_ws(task_id, "log", message=f"错误: {error_msg}")
        await send_to_ws(task_id, "finished", message="下载失败")
    finally:
        active_connections.pop(task_id, None)


async def delayed_delete(file_path: str, seconds: int = 60):
    await asyncio.sleep(seconds)
    try:
        if os.path.exists(file_path):
            os.remove(file_path)
            print(f"[清理] 已删除文件: {os.path.basename(file_path)}")
    except Exception as e:
        print(f"[清理失败] {e}")


# ====================== API 接口 ======================
@app.post("/download")
async def start_download(data: dict):
    task_id = str(uuid.uuid4())
    
    task = asyncio.create_task(run_download(
        task_id=task_id,
        urls=data.get("urls", []),
        quality=data.get("quality", "最高质量（推荐）"),
        playlist=data.get("playlist", False),
        cookie_path=data.get("cookie_path")
    ))
    
    tasks[task_id] = task
    return {"success": True, "task_id": task_id}


@app.get("/download/{filename}")
async def get_file(filename: str):
    file_path = os.path.join(DOWNLOAD_DIR, filename)
    if os.path.exists(file_path):
        return FileResponse(
            path=file_path, 
            filename=filename, 
            media_type="application/octet-stream"
        )
    return JSONResponse(status_code=404, content={"detail": "文件不存在或已被删除"})


@app.post("/cancel/{task_id}")
async def cancel_task(task_id: str):
    if task_id in tasks:
        tasks[task_id].cancel()
        await send_to_ws(task_id, "log", message="任务已被用户取消")
    return {"success": True}


# ====================== 本地启动 ======================
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000)
