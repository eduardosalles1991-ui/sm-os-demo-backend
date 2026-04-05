"""
ocr_client.py — Google Cloud Vision OCR
Extrai texto de PDFs escaneados e imagens
Free tier: 1.000 páginas/mês
"""
import os, base64, json, requests, logging, io
log = logging.getLogger("smos")

VISION_API_URL = "https://vision.googleapis.com/v1/images:annotate"
_token_cache = {"token": None, "expires": 0}

def _get_access_token() -> str:
    """Obtém token OAuth2 para a service account."""
    import time, jwt as _jwt
    now = int(time.time())
    if _token_cache["token"] and _token_cache["expires"] > now + 60:
        return _token_cache["token"]
    
    creds_json = os.getenv("GOOGLE_VISION_CREDENTIALS")
    if not creds_json:
        raise RuntimeError("GOOGLE_VISION_CREDENTIALS não configurado")
    
    creds = json.loads(creds_json)
    
    # JWT para obter access token
    payload = {
        "iss": creds["client_email"],
        "scope": "https://www.googleapis.com/auth/cloud-vision",
        "aud": "https://oauth2.googleapis.com/token",
        "iat": now,
        "exp": now + 3600,
    }
    
    signed = _jwt.encode(payload, creds["private_key"], algorithm="RS256")
    
    r = requests.post("https://oauth2.googleapis.com/token", data={
        "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
        "assertion": signed,
    }, timeout=10)
    r.raise_for_status()
    data = r.json()
    
    _token_cache["token"] = data["access_token"]
    _token_cache["expires"] = now + data.get("expires_in", 3600)
    return _token_cache["token"]

def ocr_image(image_bytes: bytes, mime_type: str = "image/jpeg") -> str:
    """Extrai texto de uma imagem via Google Vision."""
    try:
        token = _get_access_token()
        b64 = base64.b64encode(image_bytes).decode()
        
        payload = {
            "requests": [{
                "image": {"content": b64},
                "features": [{"type": "DOCUMENT_TEXT_DETECTION", "maxResults": 1}],
                "imageContext": {"languageHints": ["pt", "pt-BR"]}
            }]
        }
        
        r = requests.post(
            VISION_API_URL,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json=payload,
            timeout=30
        )
        r.raise_for_status()
        result = r.json()
        
        resp = result.get("responses", [{}])[0]
        full_text = resp.get("fullTextAnnotation", {}).get("text", "")
        return full_text.strip()
    except Exception as e:
        log.warning(f"[OCR] erro imagem: {e}")
        return ""

def ocr_pdf(pdf_bytes: bytes) -> str:
    """
    Extrai texto de PDF — tenta nativo primeiro, OCR se necessário.
    """
    # 1. Tenta extração nativa (PDFs com texto)
    try:
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(pdf_bytes))
        text_parts = []
        for page in reader.pages[:40]:
            t = page.extract_text() or ""
            text_parts.append(t)
        native_text = "\n".join(text_parts).strip()
        
        # Se extraiu texto suficiente, usa direto
        if len(native_text) > 100:
            log.info(f"[OCR] PDF nativo: {len(native_text)} chars")
            return native_text
    except Exception as e:
        log.warning(f"[OCR] pypdf falhou: {e}")

    # 2. PDF escaneado — converte páginas para imagem e aplica OCR
    log.info("[OCR] PDF escaneado — usando Google Vision")
    try:
        import fitz  # PyMuPDF
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        all_text = []
        
        for page_num in range(min(len(doc), 20)):  # máx 20 páginas
            page = doc[page_num]
            mat = fitz.Matrix(2, 2)  # 2x zoom para melhor qualidade
            pix = page.get_pixmap(matrix=mat)
            img_bytes = pix.tobytes("jpeg")
            
            text = ocr_image(img_bytes, "image/jpeg")
            if text:
                all_text.append(f"[Página {page_num+1}]\n{text}")
        
        return "\n\n".join(all_text)
    except ImportError:
        log.warning("[OCR] PyMuPDF não instalado — fallback para página única")
        # Fallback: trata PDF inteiro como imagem (só funciona para 1 página)
        return ocr_image(pdf_bytes, "application/pdf")
    except Exception as e:
        log.warning(f"[OCR] PDF Vision falhou: {e}")
        return ""

def extract_text_smart(file_bytes: bytes, filename: str) -> str:
    """
    Extração inteligente baseada no tipo de arquivo.
    Suporta: PDF, PNG, JPG, JPEG, TIFF, BMP, WEBP, DOCX, TXT
    """
    fn = (filename or "").lower()
    
    # Texto puro
    if fn.endswith((".txt", ".md")):
        return file_bytes.decode("utf-8", errors="ignore")
    
    # Word
    if fn.endswith(".docx"):
        try:
            from docx import Document
            doc = Document(io.BytesIO(file_bytes))
            return "\n".join(p.text for p in doc.paragraphs if p.text).strip()
        except Exception as e:
            log.warning(f"[OCR] docx falhou: {e}")
            return ""
    
    # PDF
    if fn.endswith(".pdf"):
        return ocr_pdf(file_bytes)
    
    # Imagens
    if fn.endswith((".png", ".jpg", ".jpeg", ".tiff", ".tif", ".bmp", ".webp", ".gif")):
        return ocr_image(file_bytes)
    
    # Fallback — tenta como imagem
    return ocr_image(file_bytes)

def is_configured() -> bool:
    return bool(os.getenv("GOOGLE_VISION_CREDENTIALS"))
