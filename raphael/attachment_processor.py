"""Secure, in-memory attachment parsing for Raphael chat requests."""
import base64
import csv
import io
import os
import re
import zipfile
import xml.etree.ElementTree as ET
from pathlib import PurePosixPath

MAX_FILES = 5
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_TOTAL_BYTES = 15 * 1024 * 1024
MAX_EXTRACTED_CHARS = 120_000
ALLOWED_EXTENSIONS = {".pdf", ".docx", ".txt", ".jpg", ".jpeg", ".png", ".webp", ".zip", ".pptx", ".csv", ".xlsx"}
IMAGE_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}
IMAGE_TYPES.update({".jpg": "image/jpeg"})
XML_NS = {"a": "http://schemas.openxmlformats.org/drawingml/2006/main", "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main", "p": "http://schemas.openxmlformats.org/presentationml/2006/main", "x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


class AttachmentError(ValueError):
    pass


def _xml_text(data, tag):
    root = ET.fromstring(data)
    return "\n".join("".join(node.itertext()).strip() for node in root.findall(tag, XML_NS) if "".join(node.itertext()).strip())


def _docx(data):
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        names = [n for n in archive.namelist() if re.fullmatch(r"word/document.xml", n)]
        if not names:
            raise AttachmentError("El DOCX no contiene un documento principal válido.")
        return _xml_text(archive.read(names[0]), ".//w:p")


def _pptx(data):
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        slides = sorted((n for n in archive.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)), key=lambda n: int(re.search(r"slide(\d+)", n).group(1)))
        return "\n\n".join(_xml_text(archive.read(name), ".//a:p") for name in slides)


def _xlsx(data):
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        shared = []
        if "xl/sharedStrings.xml" in archive.namelist():
            root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
            shared = ["".join(item.itertext()) for item in root.findall("x:si", XML_NS)]
        sheets = sorted((n for n in archive.namelist() if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", n)), key=lambda n: int(re.search(r"sheet(\d+)", n).group(1)))
        output = []
        for sheet_name in sheets:
            root = ET.fromstring(archive.read(sheet_name))
            rows = []
            for row in root.findall(".//x:row", XML_NS):
                cells = []
                for cell in row.findall("x:c", XML_NS):
                    value = cell.find("x:v", XML_NS)
                    inline = cell.find("x:is", XML_NS)
                    raw = "".join(inline.itertext()) if inline is not None else (value.text if value is not None else "")
                    if cell.get("t") == "s" and raw.isdigit() and int(raw) < len(shared):
                        raw = shared[int(raw)]
                    if raw:
                        cells.append(f"{cell.get('r', '')}={raw}")
                if cells:
                    rows.append(" | ".join(cells))
            output.append("\n".join(rows))
        return "\n\n".join(output)


def _csv(data):
    text = data.decode("utf-8-sig")
    return "\n".join(" | ".join(row) for row in csv.reader(io.StringIO(text)))


def _pdf_block(data, filename):
    return {"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": base64.b64encode(data).decode("ascii")}, "title": filename}


def _image_block(data, extension):
    return {"type": "image", "source": {"type": "base64", "media_type": IMAGE_TYPES[extension], "data": base64.b64encode(data).decode("ascii")}}


def _extract_file(filename, data, blocks, depth=0):
    ext = PurePosixPath(filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise AttachmentError(f"Formato no admitido: {filename}")
    if ext in IMAGE_TYPES:
        blocks.append(_image_block(data, ext))
        return f"[Imagen adjunta: {filename}]"
    if ext == ".pdf":
        blocks.append(_pdf_block(data, filename))
        return f"[PDF adjunto: {filename}]"
    if ext == ".txt":
        return data.decode("utf-8-sig")
    if ext == ".csv":
        return _csv(data)
    if ext == ".docx":
        return _docx(data)
    if ext == ".pptx":
        return _pptx(data)
    if ext == ".xlsx":
        return _xlsx(data)
    if ext == ".zip":
        if depth >= 1:
            raise AttachmentError("No se admiten archivos ZIP anidados.")
        extracted = []
        total = 0
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                entries = [item for item in archive.infolist() if not item.is_dir()]
                if len(entries) > 20:
                    raise AttachmentError("El ZIP contiene demasiados archivos (máximo 20).")
                for item in entries:
                    path = PurePosixPath(item.filename)
                    if path.is_absolute() or ".." in path.parts or item.flag_bits & 1:
                        raise AttachmentError("El ZIP contiene una ruta insegura o un archivo cifrado.")
                    if item.file_size > MAX_FILE_BYTES:
                        raise AttachmentError("Un archivo del ZIP supera el límite de 8 MB.")
                    total += item.file_size
                    if total > MAX_TOTAL_BYTES:
                        raise AttachmentError("El contenido descomprimido del ZIP supera 15 MB.")
                    inner_ext = path.suffix.lower()
                    if inner_ext not in ALLOWED_EXTENSIONS - {".zip"}:
                        continue
                    inner_data = archive.read(item)
                    extracted.append(f"\n--- {item.filename} ---\n" + _extract_file(item.filename, inner_data, blocks, depth + 1))
        except zipfile.BadZipFile as error:
            raise AttachmentError("El ZIP no es válido.") from error
        if not extracted:
            return "El ZIP no contiene documentos compatibles."
        return "\n".join(extracted)
    raise AttachmentError(f"No se pudo procesar {filename}.")


def prepare_attachments(files):
    if len(files) > MAX_FILES:
        raise AttachmentError(f"Puedes adjuntar como máximo {MAX_FILES} archivos.")
    total = 0
    blocks = []
    descriptions = []
    for uploaded in files:
        if not uploaded or not uploaded.filename:
            continue
        filename = os.path.basename(uploaded.filename.replace("\\", "/"))[:180]
        ext = PurePosixPath(filename).suffix.lower()
        if ext not in ALLOWED_EXTENSIONS:
            raise AttachmentError(f"Formato no admitido: {filename}")
        data = uploaded.read(MAX_FILE_BYTES + 1)
        if not data:
            raise AttachmentError(f"El archivo {filename} está vacío.")
        if len(data) > MAX_FILE_BYTES:
            raise AttachmentError(f"{filename} supera el límite de 8 MB.")
        total += len(data)
        if total > MAX_TOTAL_BYTES:
            raise AttachmentError("El total de archivos supera 15 MB.")
        try:
            extracted = _extract_file(filename, data, blocks)
        except (UnicodeDecodeError, zipfile.BadZipFile, ET.ParseError, csv.Error) as error:
            raise AttachmentError(f"No se pudo leer {filename}: formato dañado o codificación no compatible.") from error
        descriptions.append(f"\n\n[Contenido del archivo {filename}]\n{extracted}")
    combined = "".join(descriptions)
    if len(combined) > MAX_EXTRACTED_CHARS:
        combined = combined[:MAX_EXTRACTED_CHARS] + "\n[Contenido truncado por límite de seguridad.]"
    if not descriptions:
        raise AttachmentError("No se recibió ningún archivo válido.")
    return combined, blocks, [os.path.basename(f.filename.replace("\\", "/")) for f in files if f and f.filename]
