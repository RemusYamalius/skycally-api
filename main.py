from fastapi import FastAPI, UploadFile, File, HTTPException, Form
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

@app.post("/api/video-to-gif")
async def video_to_gif(
    file: UploadFile = File(...),
    start: float = Form(0),
    duration: float = Form(3),
    width: int = Form(480),
    fps: int = Form(15),
):
    # حماية من قيم خارج الحدود
    duration = min(duration, 10)
    fps = min(fps, 30)
    width = min(width, 800)

    tmp_dir = tempfile.mkdtemp()
    try:
        input_path = os.path.join(tmp_dir, "input.mp4")
        output_path = os.path.join(tmp_dir, "output.gif")
        palette_path = os.path.join(tmp_dir, "palette.png")

        content = await file.read()
        with open(input_path, "wb") as f:
            f.write(content)

        # الخطوة 1: توليد palette لجودة ألوان أفضل
        palette_result = subprocess.run([
            "ffmpeg", "-y",
            "-ss", str(start),
            "-t", str(duration),
            "-i", input_path,
            "-vf", f"fps={fps},scale={width}:-1:flags=lanczos,palettegen",
            palette_path
        ], capture_output=True, timeout=60)

        if palette_result.returncode != 0:
            raise HTTPException(status_code=500, detail="Palette generation failed")

        # الخطوة 2: توليد GIF باستخدام الـ palette
        gif_result = subprocess.run([
            "ffmpeg", "-y",
            "-ss", str(start),
            "-t", str(duration),
            "-i", input_path,
            "-i", palette_path,
            "-lavfi", f"fps={fps},scale={width}:-1:flags=lanczos[x];[x][1:v]paletteuse",
            output_path
        ], capture_output=True, timeout=120)

        if gif_result.returncode != 0 or not os.path.exists(output_path):
            raise HTTPException(status_code=500, detail="GIF conversion failed")

        with open(output_path, "rb") as f:
            gif_bytes = f.read()

        return Response(
            content=gif_bytes,
            media_type="image/gif",
            headers={
                "Content-Disposition": "attachment; filename=output.gif",
                "Cache-Control": "no-cache",
            }
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
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
@app.post("/api/split-pdf")
async def split_pdf(
    file: UploadFile = File(...),
    pages: str = Form(...),  # مثال: "1,3,5" أو "1-3" أو "all"
):
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only .pdf allowed")

    tmp_dir = tempfile.mkdtemp()
    try:
        from pypdf import PdfReader, PdfWriter

        content_bytes = await file.read()
        input_path = os.path.join(tmp_dir, "input.pdf")
        with open(input_path, "wb") as f:
            f.write(content_bytes)

        reader = PdfReader(input_path)
        total_pages = len(reader.pages)

        # تحليل صفحات المطلوبة
        selected = set()
        for part in pages.split(","):
            part = part.strip()
            if "-" in part:
                start, end = part.split("-")
                for i in range(int(start), int(end) + 1):
                    if 1 <= i <= total_pages:
                        selected.add(i)
            elif part == "all":
                selected = set(range(1, total_pages + 1))
                break
            elif part.isdigit():
                i = int(part)
                if 1 <= i <= total_pages:
                    selected.add(i)

        if not selected:
            raise HTTPException(status_code=400, detail="No valid pages selected")

        writer = PdfWriter()
        for i in sorted(selected):
            writer.add_page(reader.pages[i - 1])

        output_path = os.path.join(tmp_dir, "split.pdf")
        with open(output_path, "wb") as f:
            writer.write(f)

        with open(output_path, "rb") as f:
            content = f.read()

        return Response(
            content=content,
            media_type="application/pdf",
            headers={
                "Content-Disposition": f"attachment; filename=split.pdf",
                "Cache-Control": "no-cache",
            }
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
@app.post("/api/audio-convert")
async def audio_convert(
    file: UploadFile = File(...),
    format: str = Form(...),  # mp3, wav, ogg, aac, flac
):
    allowed_formats = {"mp3", "wav", "ogg", "aac", "flac"}
    if format not in allowed_formats:
        raise HTTPException(status_code=400, detail="Unsupported format")

    tmp_dir = tempfile.mkdtemp()
    try:
        content_bytes = await file.read()
        ext = file.filename.rsplit(".", 1)[-1].lower()
        input_path = os.path.join(tmp_dir, f"input.{ext}")
        output_path = os.path.join(tmp_dir, f"output.{format}")

        with open(input_path, "wb") as f:
            f.write(content_bytes)

        result = subprocess.run([
            "ffmpeg", "-y",
            "-i", input_path,
            output_path
        ], capture_output=True, timeout=120)

        if result.returncode != 0 or not os.path.exists(output_path):
            raise HTTPException(status_code=500, detail="Conversion failed")

        with open(output_path, "rb") as f:
            content = f.read()

        mime = {
            "mp3": "audio/mpeg",
            "wav": "audio/wav",
            "ogg": "audio/ogg",
            "aac": "audio/aac",
            "flac": "audio/flac",
        }.get(format, "audio/mpeg")

        original_name = file.filename.rsplit(".", 1)[0]
        out_name = safe_filename(original_name, format)

        return Response(
            content=content,
            media_type=mime,
            headers={
                "Content-Disposition": f"attachment; filename=\"{out_name}\"",
                "Cache-Control": "no-cache",
            }
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
@app.post("/api/video-compress")
async def video_compress(
    file: UploadFile = File(...),
    quality: str = Form("medium"),  # low, medium, high
):
    quality_map = {
        "low": "28",
        "medium": "23",
        "high": "18",
    }
    crf = quality_map.get(quality, "23")

    tmp_dir = tempfile.mkdtemp()
    try:
        content_bytes = await file.read()
        ext = file.filename.rsplit(".", 1)[-1].lower()
        input_path = os.path.join(tmp_dir, f"input.{ext}")
        output_path = os.path.join(tmp_dir, "compressed.mp4")

        with open(input_path, "wb") as f:
            f.write(content_bytes)

        result = subprocess.run([
            "ffmpeg", "-y",
            "-i", input_path,
            "-vcodec", "libx264",
            "-crf", crf,
            "-preset", "fast",
            "-acodec", "aac",
            "-movflags", "+faststart",
            output_path
        ], capture_output=True, timeout=300)

        if result.returncode != 0 or not os.path.exists(output_path):
            raise HTTPException(status_code=500, detail="Compression failed")

        with open(output_path, "rb") as f:
            content = f.read()

        original_name = file.filename.rsplit(".", 1)[0]
        out_name = safe_filename(original_name + "_compressed", "mp4")

        return Response(
            content=content,
            media_type="video/mp4",
            headers={
                "Content-Disposition": f"attachment; filename=\"{out_name}\"",
                "Cache-Control": "no-cache",
            }
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
@app.post("/api/extract-audio")
async def extract_audio(
    file: UploadFile = File(...),
    format: str = Form("mp3"),  # mp3, aac, wav
):
    allowed_formats = ["mp3", "aac", "wav"]
    if format not in allowed_formats:
        format = "mp3"

    tmp_dir = tempfile.mkdtemp()
    try:
        content_bytes = await file.read()
        ext = file.filename.rsplit(".", 1)[-1].lower()
        input_path = os.path.join(tmp_dir, f"input.{ext}")
        output_path = os.path.join(tmp_dir, f"audio.{format}")

        with open(input_path, "wb") as f:
            f.write(content_bytes)

        codec_map = {
            "mp3": "libmp3lame",
            "aac": "aac",
            "wav": "pcm_s16le",
        }
        codec = codec_map[format]

        result = subprocess.run([
            "ffmpeg", "-y",
            "-i", input_path,
            "-vn",
            "-acodec", codec,
            "-q:a", "2",
            output_path
        ], capture_output=True, timeout=300)

        if result.returncode != 0 or not os.path.exists(output_path):
            raise HTTPException(status_code=500, detail="Audio extraction failed")

        with open(output_path, "rb") as f:
            content = f.read()

        original_name = file.filename.rsplit(".", 1)[0]
        out_name = safe_filename(original_name + "_audio", format)

        media_types = {
            "mp3": "audio/mpeg",
            "aac": "audio/aac",
            "wav": "audio/wav",
        }

        return Response(
            content=content,
            media_type=media_types[format],
            headers={
                "Content-Disposition": f"attachment; filename=\"{out_name}\"",
                "Cache-Control": "no-cache",
            }
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)