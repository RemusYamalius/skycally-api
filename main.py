from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
import yt_dlp
import subprocess
import tempfile
import os
import shutil
import unicodedata
import io

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition"],
)

def safe_filename(title: str, ext: str = "mp4") -> str:
    normalized = unicodedata.normalize("NFKD", title)
    ascii_title = normalized.encode("ascii", "ignore").decode("ascii")
    safe = "".join(c for c in ascii_title if c.isalnum() or c in " -_").strip()
    return (safe[:50] or "video") + f".{ext}"

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

        input_template = os.path.join(tmp_dir, "input.%(ext)s")
        output_path = os.path.join(tmp_dir, "output.mp4")

        ydl_opts = {
            "quiet": True,
            "noplaylist": True,
            "outtmpl": input_template,
            "format": (
                f"bestvideo[height<={quality_int}]+bestaudio/"
                f"best[height<={quality_int}]/best"
            ),
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            title = info.get("title", "video")

        downloaded = [
            f for f in os.listdir(tmp_dir)
            if os.path.isfile(os.path.join(tmp_dir, f))
            and not f.endswith(".part")
            and not f.endswith(".ytdl")
        ]

        if not downloaded:
            raise HTTPException(status_code=500, detail="Download failed")

        source_path = os.path.join(tmp_dir, downloaded[0])

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

        final_path = output_path if (
            ffmpeg_result.returncode == 0 and os.path.exists(output_path)
        ) else source_path

        with open(final_path, "rb") as f:
            content = f.read()

        filename = safe_filename(title, "mp4")

        return Response(
            content=content,
            media_type="video/mp4",
            headers={
                "Content-Disposition": f"attachment; filename=\"{filename}\"",
                "Cache-Control": "no-cache",
            }
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

@app.post("/api/remove-bg")
async def remove_background(file: UploadFile = File(...)):
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="Only image files allowed")
    try:
        from rembg import remove
        from PIL import Image

        content = await file.read()
        input_image = Image.open(io.BytesIO(content))

        if input_image.mode != "RGBA":
            input_image = input_image.convert("RGBA")

        output_image = remove(input_image)

        output_buffer = io.BytesIO()
        output_image.save(output_buffer, format="PNG")
        output_bytes = output_buffer.getvalue()

        return Response(
            content=output_bytes,
            media_type="image/png",
            headers={
                "Content-Disposition": "attachment; filename=removed_bg.png",
                "Cache-Control": "no-cache",
            }
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/word-to-pdf")
async def word_to_pdf(file: UploadFile = File(...)):
    if not file.filename.lower().endswith((".doc", ".docx")):
        raise HTTPException(status_code=400, detail="Only .doc/.docx allowed")
    tmp_dir = tempfile.mkdtemp()
    try:
        input_path = os.path.join(tmp_dir, "input.docx")
        content_bytes = await file.read()
        with open(input_path, "wb") as f:
            f.write(content_bytes)

        lo_profile = os.path.join(tmp_dir, "lo_profile")
        os.makedirs(lo_profile, exist_ok=True)
        os.chmod(lo_profile, 0o777)

        env = {
            **os.environ,
            "HOME": tmp_dir,
            "TMPDIR": tmp_dir,
            "XDG_RUNTIME_DIR": tmp_dir,
        }

        result = subprocess.run([
            "libreoffice",
            f"-env:UserInstallation=file:///{lo_profile}",
            "--headless",
            "--norestore",
            "--nofirststartwizard",
            "--convert-to", "pdf:writer_pdf_Export",
            "--outdir", tmp_dir,
            input_path
        ], capture_output=True, timeout=120, env=env)

        pdf_path = os.path.join(tmp_dir, "input.pdf")

        if not os.path.exists(pdf_path):
            stderr = result.stderr.decode(errors="ignore")[:300]
            raise HTTPException(
                status_code=500,
                detail=f"LibreOffice failed: {stderr}"
            )

        with open(pdf_path, "rb") as f:
            content = f.read()

        out_name = safe_filename(
            file.filename.rsplit(".", 1)[0], "pdf"
        )
        return Response(
            content=content,
            media_type="application/pdf",
            headers={"Content-Disposition": f"attachment; filename=\"{out_name}\""}
        )
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

@app.post("/api/pdf-to-word")
async def pdf_to_word(file: UploadFile = File(...)):
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only .pdf allowed")
    tmp_dir = tempfile.mkdtemp()
    try:
        input_path = os.path.join(tmp_dir, "input.pdf")
        content_bytes = await file.read()
        with open(input_path, "wb") as f:
            f.write(content_bytes)

        lo_profile = os.path.join(tmp_dir, "lo_profile")
        os.makedirs(lo_profile, exist_ok=True)
        os.chmod(lo_profile, 0o777)

        env = {
            **os.environ,
            "HOME": tmp_dir,
            "TMPDIR": tmp_dir,
            "XDG_RUNTIME_DIR": tmp_dir,
        }

        result = subprocess.run([
            "libreoffice",
            f"-env:UserInstallation=file:///{lo_profile}",
            "--headless",
            "--norestore",
            "--nofirststartwizard",
            "--convert-to", "docx:MS Word 2007 XML",
            "--outdir", tmp_dir,
            input_path
        ], capture_output=True, timeout=120, env=env)

        docx_path = os.path.join(tmp_dir, "input.docx")

        if not os.path.exists(docx_path):
            stderr = result.stderr.decode(errors="ignore")[:300]
            raise HTTPException(
                status_code=500,
                detail=f"LibreOffice failed: {stderr}"
            )

        with open(docx_path, "rb") as f:
            content = f.read()

        out_name = safe_filename(
            file.filename.replace(".pdf", ""), "docx"
        )
        return Response(
            content=content,
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            headers={"Content-Disposition": f"attachment; filename=\"{out_name}\""}
        )
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)