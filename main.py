from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
import yt_dlp
import subprocess
import tempfile
import os
import shutil

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
    tmp_dir = tempfile.mkdtemp()
    try:
        # تأكد أن quality رقم صحيح
        try:
            quality_int = int(quality)
        except ValueError:
            quality_int = 1080

        output_template = os.path.join(tmp_dir, "video.%(ext)s")

        ydl_opts = {
            "quiet": True,
            "noplaylist": True,
            "outtmpl": output_template,
            "merge_output_format": "mp4",
            # جلب أفضل جودة مع تفضيل h264
            "format": (
                f"bestvideo[height<={quality_int}][vcodec^=avc1]+bestaudio/"
                f"bestvideo[height<={quality_int}]+bestaudio/"
                f"best[height<={quality_int}]/"
                f"best"
            ),
            "postprocessors": [
                {
                    # تحويل إجباري لـ H264 عبر ffmpeg
                    "key": "FFmpegVideoRemuxer",
                    "preferedformat": "mp4",
                },
                            ],
            "postprocessor_args": {
                "ffmpeg": [
                    "-vcodec", "libx264",
                    "-acodec", "aac",
                    "-preset", "fast",
                    "-crf", "23",
                ],
            },
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            title = info.get("title", "video")[:50]

        # ابحث عن الملف
        all_files = os.listdir(tmp_dir)
        mp4_files = [f for f in all_files if f.endswith(".mp4")]

        if not mp4_files:
            # جرب أي ملف فيديو
            video_files = [f for f in all_files if not f.endswith(".part")]
            if not video_files:
                raise HTTPException(status_code=500, detail="No file created")
            mp4_files = video_files

        final_path = os.path.join(tmp_dir, mp4_files[0])

        with open(final_path, "rb") as f:
            content = f.read()

        safe_title = "".join(
            c for c in title if c.isalnum() or c in " -_"
        ).strip() or "video"

        return Response(
            content=content,
            media_type="video/mp4",
            headers={
                "Content-Disposition": f'attachment; filename="{safe_title}.mp4"',
                "Cache-Control": "no-cache",
            }
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

@app.post("/api/word-to-pdf")
async def word_to_pdf(file: UploadFile = File(...)):
    if not file.filename.endswith((".doc", ".docx")):
        raise HTTPException(status_code=400, detail="Only .doc/.docx allowed")
    tmp_dir = tempfile.mkdtemp()
    try:
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
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

@app.post("/api/pdf-to-word")
async def pdf_to_word(file: UploadFile = File(...)):
    if not file.filename.endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only .pdf allowed")
    tmp_dir = tempfile.mkdtemp()
    try:
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
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)