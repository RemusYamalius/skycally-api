from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse
import yt_dlp
import subprocess
import tempfile
import os
import httpx

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
def root():
    return {"status": "ok", "service": "skycally-api"}

@app.get("/api/video-info")
async def video_info(url: str):
    ydl_opts = {"quiet": True, "no_warnings": True}
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            formats = []
            seen = set()
            for f in info.get("formats", []):
                quality = f.get("format_note") or f.get("height")
                if not quality or not f.get("url"):
                    continue
                key = str(quality)
                if key in seen:
                    continue
                seen.add(key)
                formats.append({
                    "quality": str(quality),
                    "url": f.get("url"),
                    "ext": f.get("ext", "mp4"),
                    "size": f.get("filesize"),
                })
            return {
                "title": info.get("title", "Video"),
                "thumbnail": info.get("thumbnail", ""),
                "formats": formats[-8:],
            }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/api/download")
async def download_video(url: str, quality: str = "1080"):
    ydl_opts = {
        "quiet": True,
        "noplaylist": True,
        "format": f"bestvideo[height<={quality}][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<={quality}]+bestaudio/best[height<={quality}]/best",
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            title = info.get("title", "video")[:50]

            if "url" in info:
                video_url = info["url"]
                http_headers = info.get("http_headers", {})
            elif "formats" in info:
                fmt = info["formats"][-1]
                video_url = fmt["url"]
                http_headers = fmt.get("http_headers", {})
            else:
                raise HTTPException(status_code=400, detail="No URL found")

        safe_title = "".join(
            c for c in title if c.isalnum() or c in " -_"
        ).strip() or "video"

        async def stream():
            async with httpx.AsyncClient(follow_redirects=True) as client:
                async with client.stream(
                    "GET",
                    video_url,
                    headers={
                        **http_headers,
                        "User-Agent": "Mozilla/5.0",
                        "Referer": "https://www.tiktok.com/",
                    },
                    timeout=120,
                ) as r:
                    async for chunk in r.aiter_bytes(chunk_size=65536):
                        yield chunk

        return StreamingResponse(
            stream(),
            media_type="video/mp4",
            headers={
                "Content-Disposition": f'attachment; filename="{safe_title}.mp4"',
                "Cache-Control": "no-cache",
            },
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/word-to-pdf")
async def word_to_pdf(file: UploadFile = File(...)):
    if not file.filename.endswith((".doc", ".docx")):
        raise HTTPException(status_code=400, detail="Only .doc/.docx allowed")
    tmp_dir = tempfile.mkdtemp()
    input_path = os.path.join(tmp_dir, file.filename)
    with open(input_path, "wb") as f:
        f.write(await file.read())
    result = subprocess.run([
        "libreoffice", "--headless", "--convert-to", "pdf",
        "--outdir", tmp_dir, input_path
    ], capture_output=True, timeout=60)
    if result.returncode != 0:
        raise HTTPException(status_code=500, detail="Conversion failed")
    pdf_filename = file.filename.rsplit(".", 1)[0] + ".pdf"
    pdf_path = os.path.join(tmp_dir, pdf_filename)
    with open(pdf_path, "rb") as f:
        content = f.read()
    return Response(
        content=content,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={pdf_filename}"}
    )

@app.post("/api/pdf-to-word")
async def pdf_to_word(file: UploadFile = File(...)):
    if not file.filename.endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only .pdf allowed")
    tmp_dir = tempfile.mkdtemp()
    input_path = os.path.join(tmp_dir, file.filename)
    with open(input_path, "wb") as f:
        f.write(await file.read())
    result = subprocess.run([
        "libreoffice", "--headless", "--convert-to", "docx",
        "--outdir", tmp_dir, input_path
    ], capture_output=True, timeout=60)
    if result.returncode != 0:
        raise HTTPException(status_code=500, detail="Conversion failed")
    docx_filename = file.filename.replace(".pdf", ".docx")
    docx_path = os.path.join(tmp_dir, docx_filename)
    with open(docx_path, "rb") as f:
        content = f.read()
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f"attachment; filename={docx_filename}"}
    )