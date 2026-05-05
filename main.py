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
        try:
            quality_int = int(quality)
        except ValueError:
            quality_int = 1080

        input_path = os.path.join(tmp_dir, "input.%(ext)s")
        output_path = os.path.join(tmp_dir, "output.mp4")

        # الخطوة 1: تحميل الفيديو بأفضل جودة
        ydl_opts = {
            "quiet": True,
            "noplaylist": True,
            "outtmpl": input_path,
            "format": (
                f"bestvideo[height<={quality_int}]+bestaudio/"
                f"best[height<={quality_int}]/"
                f"best"
            ),
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            title = info.get("title", "video")[:50]

        # الخطوة 2: ابحث عن الملف المحمّل
        downloaded = [
            f for f in os.listdir(tmp_dir)
            if not f.endswith(".part") and not f.endswith(".ytdl")
            and os.path.isfile(os.path.join(tmp_dir, f))
        ]

        if not downloaded:
            raise HTTPException(status_code=500, detail="Download failed")

        source_path = os.path.join(tmp_dir, downloaded[0])

        # الخطوة 3: تحويل إجباري لـ H264+AAC عبر ffmpeg
        ffmpeg_result = subprocess.run([
            "ffmpeg", "-y",
            "-i", source_path,
            "-vcodec", "libx264",
            "-acodec", "aac",
            "-preset", "fast",
            "-crf", "23",
            "-movflags", "+faststart",
            output_path
        ], capture_output=True, timeout=300)

        if ffmpeg_result.returncode != 0 or not os.path.exists(output_path):
            # إذا فشل التحويل، أرسل الملف الأصلي
            with open(source_path, "rb") as f:
                content = f.read()
        else:
            with open(output_path, "rb") as f:
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
        # حفظ الملف باسم آمن بدون مسافات
        safe_name = "input.docx"
        input_path = os.path.join(tmp_dir, safe_name)
        with open(input_path, "wb") as f:
            f.write(await file.read())

        result = subprocess.run([
            "libreoffice",
            "--headless",
            "--norestore",
            "--nofirststartwizard",
            "--convert-to", "pdf",
            "--outdir", tmp_dir,
            input_path
        ], capture_output=True, timeout=120, env={
            **os.environ,
            "HOME": tmp_dir,
        })

        pdf_path = os.path.join(tmp_dir, "input.pdf")

        if result.returncode != 0 or not os.path.exists(pdf_path):
            error_msg = result.stderr.decode()[:200]
            raise HTTPException(status_code=500, detail=f"Conversion failed: {error_msg}")

        with open(pdf_path, "rb") as f:
            content = f.read()

        original_name = file.filename.rsplit(".", 1)[0] + ".pdf"
        return Response(
            content=content,
            media_type="application/pdf",
            headers={"Content-Disposition": f"attachment; filename={original_name}"}
        )
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

@app.post("/api/pdf-to-word")
async def pdf_to_word(file: UploadFile = File(...)):
    if not file.filename.endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only .pdf allowed")
    tmp_dir = tempfile.mkdtemp()
    try:
        # حفظ الملف باسم آمن
        safe_name = "input.pdf"
        input_path = os.path.join(tmp_dir, safe_name)
        with open(input_path, "wb") as f:
            f.write(await file.read())

        result = subprocess.run([
            "libreoffice",
            "--headless",
            "--norestore",
            "--nofirststartwizard",
            "--convert-to", "docx",
            "--outdir", tmp_dir,
            input_path
        ], capture_output=True, timeout=120, env={
            **os.environ,
            "HOME": tmp_dir,
        })

        docx_path = os.path.join(tmp_dir, "input.docx")

        if result.returncode != 0 or not os.path.exists(docx_path):
            error_msg = result.stderr.decode()[:200]
            raise HTTPException(status_code=500, detail=f"Conversion failed: {error_msg}")

        with open(docx_path, "rb") as f:
            content = f.read()

        original_name = file.filename.replace(".pdf", ".docx")
        return Response(
            content=content,
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            headers={"Content-Disposition": f"attachment; filename={original_name}"}
        )
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)