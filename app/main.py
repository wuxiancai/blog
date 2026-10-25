import asyncio, fcntl, hashlib, html, json, logging, mimetypes, os, re, secrets, shutil, tarfile, tempfile, time, uuid, threading, zipfile, smtplib
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from email.message import EmailMessage
import base64
import urllib.error, urllib.parse, urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo
import bleach
import markdown
from cryptography.fernet import Fernet
from fastapi import Depends, FastAPI, Form, HTTPException, Request, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse, StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from passlib.context import CryptContext
from sqlalchemy import select, func, text
from sqlalchemy.orm import Session, selectinload
from starlette.middleware.sessions import SessionMiddleware
from .database import Base, engine, get_db, SessionLocal
from .models import User, UserGroup, Article, Attachment, Gallery, GalleryImage, Comment, Setting, AuditLog, article_readers, now_utc

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("DATA_DIR", str(ROOT / "data"))).resolve()
UPLOAD_DIR = DATA_DIR / "uploads"
BACKUP_DIR = Path(os.getenv("BACKUP_DIR", str(DATA_DIR / "backups"))).resolve()
for directory in (UPLOAD_DIR, BACKUP_DIR): directory.mkdir(parents=True, exist_ok=True)
SECRET = os.getenv("APP_SECRET") or secrets.token_urlsafe(48)
TZ = ZoneInfo(os.getenv("APP_TIMEZONE", "Asia/Shanghai"))
MAX_UPLOAD = int(os.getenv("MAX_UPLOAD_BYTES", "524288000"))
MAX_STORAGE = int(os.getenv("MAX_UPLOAD_TOTAL_BYTES", str(20 * 1024 * 1024 * 1024)))
pwd = CryptContext(schemes=["argon2"], deprecated="auto")
templates = Jinja2Templates(directory=str(ROOT / "app" / "templates"))

@asynccontextmanager
async def lifespan(application):
    global SERVICE_LOCK_HANDLE
    SERVICE_LOCK_HANDLE = open(DATA_DIR / ".service.lock", "a+")
    try: fcntl.flock(SERVICE_LOCK_HANDLE, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc: raise RuntimeError("检测到另一份私有博客服务正在使用数据目录") from exc
    if os.getenv("AUTO_MIGRATE", "true").lower() == "true":
        from alembic import command
        from alembic.config import Config
        migration_config = Config(str(ROOT / "alembic.ini"))
        migration_config.set_main_option("script_location", str(ROOT / "migrations"))
        command.upgrade(migration_config, "head")
    else:
        Base.metadata.create_all(engine)
    with SessionLocal() as db:
        admin = db.scalar(select(User).where(User.username == "admin"))
        if not admin:
            db.add(User(username="admin", display_name="管理员", password_hash=pwd.hash(os.getenv("INITIAL_ADMIN_PASSWORD", "1234")), role="admin", must_change_password=True))
        db.commit()
    publish_due()
    async def scanner():
        while True:
            await asyncio.sleep(60)
            try: publish_due()
            except Exception: logging.exception("定时发布扫描失败")
    task = asyncio.create_task(scanner())
    try:
        yield
    finally:
        task.cancel()
        try: await task
        except asyncio.CancelledError: pass
        if SERVICE_LOCK_HANDLE:
            fcntl.flock(SERVICE_LOCK_HANDLE, fcntl.LOCK_UN)
            SERVICE_LOCK_HANDLE.close()
            SERVICE_LOCK_HANDLE = None

app = FastAPI(title="私有博客", docs_url=None, redoc_url=None, lifespan=lifespan)
app.add_middleware(SessionMiddleware, secret_key=SECRET, session_cookie="private_blog_session", same_site="lax", https_only=os.getenv("COOKIE_SECURE", "false").lower() == "true", max_age=60 * 60 * 12)
app.mount("/static", StaticFiles(directory=str(ROOT / "app" / "static")), name="static")
LOGIN_ATTEMPTS: dict[str, list[float]] = {}
FILE_LOCK = threading.RLock()
SERVICE_LOCK_HANDLE = None
ALLOWED_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".mp4", ".webm", ".mov", ".pdf", ".txt", ".zip", ".docx", ".odt"}
ALLOWED_MIME = {"image/png", "image/jpeg", "image/gif", "image/webp", "video/mp4", "video/webm", "video/quicktime", "application/pdf", "text/plain", "application/zip", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "application/vnd.oasis.opendocument.text", "application/octet-stream"}
GALLERY_IMAGE_TYPES_BY_EXT = {
    ".png": {"image/png"}, ".jpg": {"image/jpeg"}, ".jpeg": {"image/jpeg"},
    ".gif": {"image/gif"}, ".webp": {"image/webp"},
    ".heic": {"image/heic", "image/heif", "image/heic-sequence", "image/heif-sequence", "application/octet-stream"},
    ".heif": {"image/heif", "image/heic", "image/heif-sequence", "image/heic-sequence", "application/octet-stream"},
    ".dng": {"image/x-adobe-dng", "image/x-dng", "image/tiff", "application/octet-stream"},
    ".raw": {"image/x-raw", "image/raw", "image/vnd.raw", "image/x-panasonic-raw", "image/x-sony-arw", "image/x-canon-cr2", "image/x-nikon-nef", "image/x-fuji-raf", "application/octet-stream"},
}
GALLERY_PREVIEW_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}

def aware(dt):
    if dt is None: return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)

templates.env.filters["localtime"] = lambda value: aware(value).astimezone(TZ) if value else None

def publish_due():
    with FILE_LOCK:
        with SessionLocal() as db:
            now = now_utc()
            due = db.scalars(select(Article).options(selectinload(Article.readers), selectinload(Article.reader_groups).selectinload(UserGroup.users), selectinload(Article.author)).where(Article.status == "scheduled", Article.publish_at <= now, Article.deleted_at.is_(None))).all()
            notifications = []
            for article in due:
                article.status = "published"
                article.published_at = now
                targets = list(article.readers) if article.visibility == "selected" else db.scalars(select(User).where(User.active.is_(True))).all()
                if article.visibility == "selected": targets.extend(member for group in article.reader_groups for member in group.users)
                email_targets = {u.email.strip() for u in targets if u.active and u.email.strip() and u.id != article.author_id}
                sendkeys = {u.serverchan_sendkey for u in targets if u.active and u.serverchan_sendkey and u.id != article.author_id}
                base_url_row = db.get(Setting, "smtp_base_url")
                base_url = (os.getenv("APP_BASE_URL", "").strip() or (base_url_row.value if base_url_row else "")).rstrip("/")
                notifications.append((article.id, article.title, article.author.display_name or article.author.username, email_targets, sendkeys, base_url))
            db.commit()
        for article_id, title, author, recipients, sendkeys, base_url in notifications:
            for recipient in recipients:
                try: send_publish_notice(recipient, title, author, article_id)
                except Exception: logging.exception("定时文章通知邮件发送失败: %s", recipient)
            for sendkey in sendkeys:
                try: send_serverchan_notice(sendkey, title, author, article_id, base_url)
                except Exception: logging.exception("定时文章 Server酱 微信通知发送失败")

def send_serverchan_notice(encrypted_sendkey: str, title: str, author: str, article_id: int, base_url: str):
    sendkey = serverchan_cipher().decrypt(encrypted_sendkey.encode()).decode()
    title_text = f"文章发布：{title}"[:32].replace("\n", " ").replace("\r", " ")
    body = f"{author} 发布了一篇与你分享的文章：**{title}**"
    if base_url: body += f"\n\n[阅读文章]({base_url}/articles/{article_id})"
    endpoint = f"https://sctapi.ftqq.com/{sendkey}.send"
    request = urllib.request.Request(endpoint, data=urllib.parse.urlencode({"title": title_text, "desp": body}).encode(), headers={"Content-Type": "application/x-www-form-urlencoded"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            result = json.loads(response.read(65536).decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"Server酱 HTTP 错误 {exc.code}") from None
    except Exception:
        raise RuntimeError("Server酱连接或响应解析失败") from None
    if str(result.get("code")) != "0":
        raise RuntimeError(f"Server酱返回错误 code={result.get('code', 'unknown')}")

def serverchan_cipher():
    secret = os.getenv("APP_SECRET", "")
    if not secret or secret == "replace-with-a-long-random-secret":
        raise RuntimeError("请先在 APP_SECRET 中设置稳定的随机密钥，再保存 Server酱 SendKey")
    key = base64.urlsafe_b64encode(hashlib.sha256(("private-blog-serverchan:" + secret).encode()).digest())
    return Fernet(key)

def send_publish_notice(recipient: str, title: str, author: str, article_id: int):
    config = smtp_config()
    host = config["host"].strip() if config["enabled"] else ""
    if not host: return
    base_url = config["base_url"].rstrip("/")
    article_url = f"{base_url}/articles/{article_id}"
    site_address = urllib.parse.urlsplit(base_url).netloc
    message = EmailMessage()
    safe_title = title.replace("\n", " ").replace("\r", " ")
    message["Subject"] = f"{site_address} | {safe_title}"
    message["From"] = config["from"] or config["user"]
    message["To"] = recipient
    message.set_content(f"{author} 发布了一篇与你分享的文章：{title}\n\n文章地址：{article_url}\n\n点击地址后，登录网站即可阅读。")
    message.add_alternative(
        f"<p>{html.escape(author)} 发布了一篇与你分享的文章：<strong>{html.escape(title)}</strong></p>"
        f'<p><a href="{html.escape(article_url, quote=True)}">点击这里阅读文章</a></p>'
        "<p>打开链接后，输入你的用户名和密码即可阅读。</p>",
        subtype="html",
    )
    port = int(config["port"])
    if config["use_ssl"]:
        with smtplib.SMTP_SSL(host, port, timeout=20) as smtp:
            _smtp_login(smtp, config["user"], config["password"]); smtp.send_message(message)
    else:
        with smtplib.SMTP(host, port, timeout=20) as smtp:
            smtp.starttls(); _smtp_login(smtp, config["user"], config["password"]); smtp.send_message(message)

def _smtp_login(smtp, username, password):
    if username: smtp.login(username, password)

def smtp_cipher():
    secret = os.getenv("APP_SECRET", "")
    if not secret or secret == "replace-with-a-long-random-secret":
        raise RuntimeError("请先在 APP_SECRET 中设置稳定的随机密钥，再保存 SMTP 密码")
    key = base64.urlsafe_b64encode(hashlib.sha256(("private-blog-smtp:" + secret).encode()).digest())
    return Fernet(key)

def smtp_config(db=None):
    close_db = db is None
    db = db or SessionLocal()
    try:
        stored = {row.key: row.value for row in db.scalars(select(Setting).where(Setting.key.like("smtp_%")))}
        def value(name, env_name, default=""):
            return stored.get("smtp_" + name, os.getenv(env_name, default))
        password = (smtp_cipher().decrypt(stored["smtp_password"].encode()).decode() if stored["smtp_password"] else "") if "smtp_password" in stored else os.getenv("SMTP_PASSWORD", "")
        enabled_default = bool(os.getenv("SMTP_HOST"))
        enabled = stored.get("smtp_enabled", str(enabled_default).lower()) == "true"
        base_url = os.getenv("APP_BASE_URL", "").strip() or stored.get("smtp_base_url", os.getenv("APP_BASE_URL", ""))
        return {"enabled": enabled, "host": value("host", "SMTP_HOST", "smtp.126.com"), "port": value("port", "SMTP_PORT", "465"), "use_ssl": value("use_ssl", "SMTP_USE_SSL", "true").lower() == "true", "user": value("user", "SMTP_USER"), "password": password, "from": value("from", "SMTP_FROM"), "base_url": base_url, "base_url_auto": bool(os.getenv("APP_BASE_URL", "").strip())}
    finally:
        if close_db: db.close()

def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    uid = request.session.get("uid")
    if not uid: raise HTTPException(401, "请先登录")
    user = db.get(User, int(uid))
    if not user or not user.active or user.session_version != request.session.get("sv"):
        request.session.clear()
        raise HTTPException(401, "会话已失效，请重新登录")
    if user.must_change_password and request.url.path not in ("/change-password", "/logout", "/health"):
        raise HTTPException(403, "首次登录请先修改密码", headers={"Location": "/change-password"})
    return user

def require_admin(user: User = Depends(current_user)):
    if user.role != "admin": raise HTTPException(403, "需要管理员权限")
    return user

def csrf_token(request: Request):
    token = request.session.get("csrf")
    if not token:
        token = secrets.token_urlsafe(32)
        request.session["csrf"] = token
    return token

def verify_csrf(request: Request, token: str):
    if not secrets.compare_digest(str(request.session.get("csrf", "")), str(token or "")):
        raise HTTPException(403, "CSRF 校验失败")

def valid_password(password: str):
    classes = sum(bool(re.search(pattern, password)) for pattern in (r"[A-Z]", r"[a-z]", r"\d", r"[^A-Za-z0-9]"))
    return len(password) >= 9 and classes >= 3

def can_read(article: Article, user: User):
    if article.deleted_at is not None: return False
    if article.status != "published": return article.author_id == user.id or user.role == "admin"
    return article.visibility == "all" or article.author_id == user.id or user.role == "admin" or any(u.id == user.id for u in article.readers) or any(any(member.id == user.id for member in group.users) for group in article.reader_groups)

def parse_reader_selection(selection: list[str], db: Session):
    if "all" in selection: return [], []
    user_ids, group_ids = set(), set()
    for item in selection:
        kind, separator, raw_id = item.partition(":")
        if not separator or not raw_id.isdigit() or kind not in {"user", "group"}: raise HTTPException(400, "用户或用户组选择无效")
        if kind == "user": user_ids.add(int(raw_id))
        else: group_ids.add(int(raw_id))
    users = db.scalars(select(User).where(User.id.in_(user_ids), User.active.is_(True))).all() if user_ids else []
    groups = db.scalars(select(UserGroup).where(UserGroup.id.in_(group_ids))).all() if group_ids else []
    if len(users) != len(user_ids) or len(groups) != len(group_ids): raise HTTPException(400, "所选用户已停用或用户组不存在，请刷新页面后重试")
    return users, groups

def reader_selection_for(article: Article):
    if article.visibility == "all": return {"all"}
    return {*(f"user:{item.id}" for item in article.readers), *(f"group:{item.id}" for item in article.reader_groups)}

def article_or_404(db, article_id, user):
    article = db.get(Article, article_id)
    if not article or not can_read(article, user): raise HTTPException(404, "文章不存在")
    return article

def render_md(text):
    raw = markdown.markdown(text or "", extensions=["fenced_code", "tables", "sane_lists"])
    return bleach.clean(raw, tags={"p","br","strong","em","del","blockquote","ul","ol","li","h1","h2","h3","h4","pre","code","table","thead","tbody","tr","th","td","a","img","video","source","hr"}, attributes={"a":["href","title","rel"],"img":["src","alt","title"],"video":["src","controls","width","height","preload","poster"],"source":["src","type"],"code":["class"]}, protocols={"http","https","mailto"})

EDITOR_TAGS = {"p","div","br","strong","b","em","i","u","s","del","blockquote","ul","ol","li","h1","h2","h3","h4","pre","code","table","thead","tbody","tr","th","td","a","img","video","source","hr"}
EDITOR_ATTRIBUTES = {"a":["href","title","rel"],"img":["src","alt","title","width","height"],"video":["src","controls","width","height","preload","poster"],"source":["src","type"],"code":["class"]}

def sanitize_editor_html(value):
    return bleach.clean(value or "", tags=EDITOR_TAGS, attributes=EDITOR_ATTRIBUTES, protocols={"http","https","mailto"}, strip=True)

def render_body(value, body_format):
    return sanitize_editor_html(value) if body_format == "html" else render_md(value)

def validate_upload_content(ext, data, path):
    valid = {
        ".png": data.startswith(b"\x89PNG\r\n\x1a\n"), ".jpg": data.startswith(b"\xff\xd8\xff"),
        ".jpeg": data.startswith(b"\xff\xd8\xff"), ".gif": data.startswith((b"GIF87a", b"GIF89a")),
        ".webp": data.startswith(b"RIFF") and data[8:12] == b"WEBP",
        ".heic": len(data) >= 12 and data[4:8] == b"ftyp" and data[8:12] in {b"heic", b"heix", b"hevc", b"hevx", b"heis", b"hevm", b"hevs", b"heim", b"mif1", b"msf1"},
        ".heif": len(data) >= 12 and data[4:8] == b"ftyp" and data[8:12] in {b"heic", b"heix", b"hevc", b"hevx", b"heis", b"hevm", b"hevs", b"heim", b"mif1", b"msf1"},
        ".dng": data.startswith((b"II*\x00", b"MM\x00*", b"II+\x00", b"MM\x00+")),
        ".pdf": data.startswith(b"%PDF-"), ".mp4": len(data) >= 12 and data[4:8] == b"ftyp",
        ".mov": len(data) >= 12 and data[4:8] == b"ftyp", ".webm": data.startswith(b"\x1aE\xdf\xa3"),
        ".zip": data.startswith(b"PK\x03\x04"), ".docx": data.startswith(b"PK\x03\x04"),
        ".odt": data.startswith(b"PK\x03\x04"), ".txt": b"\x00" not in data,
    }
    if ext in valid and not valid[ext]: raise HTTPException(400, "文件内容与声明类型不匹配")
    if ext in {".docx", ".odt"}:
        try:
            with zipfile.ZipFile(path) as archive:
                required = "word/document.xml" if ext == ".docx" else "mimetype"
                if required not in archive.namelist(): raise HTTPException(400, "文档文件结构无效")
        except zipfile.BadZipFile as exc: raise HTTPException(400, "文档文件结构无效") from exc

def sha256_stream(stream):
    digest = hashlib.sha256()
    while chunk := stream.read(1024 * 1024): digest.update(chunk)
    return digest.hexdigest()

def page(request, name, **context):
    static_dir = ROOT / "app" / "static"
    asset_files = [static_dir / "editor.js", static_dir / "access-picker.js", static_dir / "gallery-upload.js", static_dir / "gallery-viewer.js", static_dir / "style.css"]
    asset_version = str(max((path.stat().st_mtime_ns for path in asset_files if path.is_file()), default=1))
    uid = request.session.get("uid")
    if uid:
        with SessionLocal() as db:
            account = db.get(User, int(uid))
            request.session["display_name"] = account.display_name if account and account.active else ""
    return templates.TemplateResponse(request, name, {"csrf": csrf_token(request), "display_tz": TZ, "asset_version": asset_version, **context})

def audit(db: Session, actor_id: int | None, action: str, target="", details=None):
    db.add(AuditLog(actor_id=actor_id, action=action, target=str(target), details=json.dumps(details or {}, ensure_ascii=False)))

@app.middleware("http")
async def security_headers(request, call_next):
    if getattr(app.state, "maintenance", False) and request.url.path != "/health":
        from starlette.responses import PlainTextResponse
        return PlainTextResponse("系统维护中，请稍后重试。", status_code=503, headers={"Retry-After": "30"})
    if request.method in {"POST", "PUT", "PATCH", "DELETE"} and request.url.path not in {"/login", "/health"}:
        # Form handlers validate the session token explicitly; reject cross-origin browser writes as a second layer.
        origin = request.headers.get("origin")
        if origin and origin.rstrip("/") != str(request.base_url).rstrip("/"):
            from starlette.responses import PlainTextResponse
            return PlainTextResponse("跨站请求已拒绝", status_code=403)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Content-Security-Policy"] = "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self'; img-src 'self' data:; media-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'"
    return response

@app.get("/health")
def health(): return {"status": "ok"}

@app.get("/", response_class=HTMLResponse)
def home(request: Request): return RedirectResponse("/articles", 303)

@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    if request.session.get("uid"): return RedirectResponse("/articles", 303)
    return page(request, "login.html", error="")

@app.post("/login")
def login(request: Request, username: str = Form(""), password: str = Form(...), csrf: str = Form(...), db: Session = Depends(get_db)):
    verify_csrf(request, csrf)
    ip = request.client.host if request.client else "unknown"
    attempts = [t for t in LOGIN_ATTEMPTS.get(ip, []) if time.time() - t < 900]
    if len(attempts) >= 10: raise HTTPException(429, "登录尝试过多，请 15 分钟后重试")
    normalized = (username.strip() or "admin").casefold()
    user = db.scalar(select(User).where(func.lower(User.username) == normalized))
    if not user or not user.active or not pwd.verify(password, user.password_hash):
        attempts.append(time.time()); LOGIN_ATTEMPTS[ip] = attempts
        return page(request, "login.html", error="用户名或密码错误")
    LOGIN_ATTEMPTS.pop(ip, None)
    request.session.clear()
    request.session.update({"uid": user.id, "sv": user.session_version, "role": user.role, "display_name": user.display_name, "csrf": secrets.token_urlsafe(32)})
    user.last_login_at = now_utc(); db.commit()
    return RedirectResponse("/change-password" if user.must_change_password else "/articles", 303)

@app.post("/logout")
def logout(request: Request, csrf: str = Form(...)):
    verify_csrf(request, csrf); request.session.clear(); return RedirectResponse("/login", 303)

@app.get("/change-password", response_class=HTMLResponse)
def change_password_page(request: Request, user: User = Depends(current_user)):
    return page(request, "password.html", error="")

@app.post("/change-password")
def change_password(request: Request, new_password: str = Form(...), csrf: str = Form(...), user: User = Depends(current_user), db: Session = Depends(get_db)):
    verify_csrf(request, csrf)
    if not valid_password(new_password): return page(request, "password.html", error="密码至少 9 位，并包含大写、小写、数字、特殊符号中的至少三类")
    user.password_hash = pwd.hash(new_password); user.must_change_password = False; user.session_version += 1; audit(db, user.id, "user.change_password"); db.commit()
    request.session.clear(); request.session.update({"uid": user.id, "sv": user.session_version, "role": user.role, "display_name": user.display_name, "csrf": secrets.token_urlsafe(32)})
    return RedirectResponse("/articles", 303)

@app.get("/articles", response_class=HTMLResponse)
def articles(request: Request, q: str = "", page_no: int = 1, user: User = Depends(current_user), db: Session = Depends(get_db)):
    publish_due()
    stmt = select(Article).where(Article.deleted_at.is_(None), Article.status == "published")
    if q: stmt = stmt.where((Article.title.ilike(f"%{q}%")) | Article.summary.ilike(f"%{q}%") | Article.body.ilike(f"%{q}%"))
    if user.role != "admin": stmt = stmt.where((Article.visibility == "all") | (Article.author_id == user.id) | Article.readers.any(User.id == user.id) | Article.reader_groups.any(UserGroup.users.any(User.id == user.id)))
    rows = db.scalars(stmt.options(selectinload(Article.author), selectinload(Article.readers)).order_by(Article.published_at.desc()).limit(21).offset(max(0, page_no - 1) * 20)).all()
    return page(request, "articles.html", articles=rows[:20], has_next=len(rows) > 20, q=q, page_no=page_no, user=user)

@app.get("/gallery", response_class=HTMLResponse)
def gallery(request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    stmt = select(Gallery).options(selectinload(Gallery.owner), selectinload(Gallery.readers), selectinload(Gallery.reader_groups)).order_by(Gallery.updated_at.desc())
    if user.role != "admin":
        stmt = stmt.where((Gallery.visibility == "all") | (Gallery.owner_id == user.id) | Gallery.readers.any(User.id == user.id) | Gallery.reader_groups.any(UserGroup.users.any(User.id == user.id)))
    items = db.scalars(stmt).all()
    counts = {gallery_id: count for gallery_id, count in db.execute(select(GalleryImage.gallery_id, func.count(GalleryImage.id)).group_by(GalleryImage.gallery_id)).all()}
    return page(request, "gallery.html", items=items, image_counts=counts)

@app.get("/gallery/new", response_class=HTMLResponse)
def new_gallery_page(request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    users = db.scalars(select(User).where(User.active.is_(True), User.id != user.id).order_by(User.display_name, User.username)).all()
    groups = db.scalars(select(UserGroup).order_by(UserGroup.name)).all()
    return page(request, "gallery_form.html", users=users, groups=groups, selected_readers={"all"}, error="")

@app.post("/gallery/new")
def create_gallery(request: Request, title: str = Form(...), description: str = Form(""), reader_selection: list[str] = Form([]), csrf: str = Form(...), user: User = Depends(current_user), db: Session = Depends(get_db)):
    verify_csrf(request, csrf)
    title = title.strip()[:240]
    if not title: raise HTTPException(400, "图集名称不能为空")
    readers, groups = parse_reader_selection(reader_selection, db)
    item = Gallery(title=title, description=description[:2000], owner_id=user.id, visibility="selected" if readers or groups else "all", readers=readers, reader_groups=groups)
    db.add(item); db.flush(); audit(db, user.id, "gallery.create", item.id, {"visibility": item.visibility}); db.commit()
    return RedirectResponse(f"/gallery/{item.id}", 303)

def gallery_or_404(db: Session, gallery_id: int, user: User):
    item = db.get(Gallery, gallery_id)
    if not item or not (user.role == "admin" or item.owner_id == user.id or item.visibility == "all" or any(reader.id == user.id for reader in item.readers) or any(any(member.id == user.id for member in group.users) for group in item.reader_groups)):
        raise HTTPException(404, "图集不存在")
    return item

@app.get("/gallery/{gallery_id}", response_class=HTMLResponse)
def gallery_detail(request: Request, gallery_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    item = gallery_or_404(db, gallery_id, user)
    images = db.scalars(select(GalleryImage).where(GalleryImage.gallery_id == item.id).order_by(GalleryImage.id.desc())).all()
    return page(request, "gallery_detail.html", gallery=item, images=images, can_manage=item.owner_id == user.id or user.role == "admin")

@app.post("/gallery/{gallery_id}/upload")
async def upload_gallery_images(gallery_id: int, request: Request, files: list[UploadFile] = File([]), folder_files: list[UploadFile] = File([]), csrf: str = Form(...), user: User = Depends(current_user), db: Session = Depends(get_db)):
    verify_csrf(request, csrf)
    item = db.get(Gallery, gallery_id)
    if not item or (item.owner_id != user.id and user.role != "admin"): raise HTTPException(404, "图集不存在")
    files = [file for file in files if file.filename]
    folder_files = [file for file in folder_files if file.filename and file.content_type in GALLERY_IMAGE_TYPES_BY_EXT.get(Path(file.filename.replace("\\", "/")).suffix.lower(), set())]
    files.extend(folder_files)
    if not files: raise HTTPException(400, "请选择图片")
    used = sum(f.stat().st_size for f in UPLOAD_DIR.rglob("*") if f.is_file())
    created = []; targets = []
    try:
        with FILE_LOCK:
            for file in files:
                name = Path(file.filename.replace("\\", "/")).name
                name = re.sub(r"[^\w. ()-]", "_", name, flags=re.UNICODE).strip(" .") or "image"
                ext = Path(name).suffix.lower()
                if file.content_type not in GALLERY_IMAGE_TYPES_BY_EXT.get(ext, set()):
                    raise HTTPException(400, f"不支持的图片类型：{name}")
                stored = uuid.uuid4().hex + ext; target = UPLOAD_DIR / stored; targets.append(target)
                digest = hashlib.sha256(); size = 0; head = b""
                with target.open("wb") as out:
                    while chunk := await file.read(1024 * 1024):
                        size += len(chunk)
                        if size > MAX_UPLOAD or used + size > MAX_STORAGE: raise HTTPException(413, "图片超过上传或存储配额限制")
                        if len(head) < 8192: head += chunk[:8192-len(head)]
                        digest.update(chunk); out.write(chunk)
                if size == 0: raise HTTPException(400, f"图片文件为空：{name}")
                validate_upload_content(ext, head, target); used += size
                created.append(GalleryImage(gallery_id=item.id, uploader_id=user.id, stored_name=stored, original_name=name[:255], content_type=file.content_type, size=size, sha256=digest.hexdigest()))
        db.add_all(created); item.updated_at = now_utc(); audit(db, user.id, "gallery.upload", item.id, {"count": len(created)}); db.commit()
    except Exception:
        for target in targets: target.unlink(missing_ok=True)
        raise
    return RedirectResponse(f"/gallery/{item.id}", 303)

@app.post("/gallery/{gallery_id}/images/{image_id}/delete")
def delete_gallery_image(request: Request, gallery_id: int, image_id: int, csrf: str = Form(...), user: User = Depends(current_user), db: Session = Depends(get_db)):
    verify_csrf(request, csrf)
    item = db.get(Gallery, gallery_id)
    image = db.get(GalleryImage, image_id)
    if not item or not image or image.gallery_id != item.id or (item.owner_id != user.id and user.role != "admin"): raise HTTPException(404, "图片不存在")
    (UPLOAD_DIR / image.stored_name).unlink(missing_ok=True)
    db.delete(image); item.updated_at = now_utc(); audit(db, user.id, "gallery.image_delete", image_id); db.commit()
    return RedirectResponse(f"/gallery/{item.id}", 303)

@app.get("/articles/drafts", response_class=HTMLResponse)
def drafts_page(request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    stmt = select(Article).where(Article.deleted_at.is_(None), Article.status.in_(("draft", "scheduled")))
    if user.role != "admin": stmt = stmt.where(Article.author_id == user.id)
    rows = db.scalars(stmt.options(selectinload(Article.author)).order_by(Article.updated_at.desc())).all()
    return page(request, "drafts.html", articles=rows, user=user)

@app.get("/articles/new", response_class=HTMLResponse)
def new_article_page(request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    users = db.scalars(select(User).where(User.active.is_(True), User.id != user.id).order_by(User.display_name, User.username)).all()
    groups = db.scalars(select(UserGroup).options(selectinload(UserGroup.users)).order_by(UserGroup.name)).all()
    return page(request, "editor.html", article=None, users=users, groups=groups, selected_readers={"all"})

def parse_local_datetime(value, local_time=""):
    if not value: return None
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            dt = datetime.fromisoformat(f"{value}T{local_time or '00:00'}")
        else:
            dt = datetime.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(400, "发布时间格式无效") from exc
    if dt.tzinfo is None:
        naive = dt
        dt = dt.replace(tzinfo=TZ, fold=0)
        if dt.astimezone(timezone.utc).astimezone(TZ).replace(tzinfo=None) != naive:
            raise HTTPException(400, "所选时间在此时区不存在（夏令时切换导致），请改选其他时间")
    return dt.astimezone(timezone.utc)

@app.post("/articles")
def create_article(request: Request, title: str = Form(...), body: str = Form(""), body_format: str = Form("markdown"), summary: str = Form(""), status: str = Form("draft"), publish_date: str = Form(""), publish_time: str = Form(""), reader_selection: list[str] = Form([]), csrf: str = Form(...), user: User = Depends(current_user), db: Session = Depends(get_db)):
    verify_csrf(request, csrf)
    if status not in {"draft", "published", "scheduled"} or body_format not in {"markdown", "html"}: raise HTTPException(400, "无效的文章选项")
    readers, groups = parse_reader_selection(reader_selection, db)
    visibility = "selected" if readers or groups else "all"
    publish_at = parse_local_datetime(publish_date, publish_time)
    if status == "scheduled" and not publish_at: raise HTTPException(400, "定时发布必须设置时间")
    if status == "published" and publish_at and publish_at > now_utc(): raise HTTPException(400, "未来时间必须选择定时发布")
    body = sanitize_editor_html(body) if body_format == "html" else body
    article = Article(slug=f"{uuid.uuid4().hex[:12]}", title=title.strip()[:240], summary=summary[:2000], body=body, body_format=body_format, author_id=user.id, status=status, visibility=visibility, publish_at=publish_at, published_at=now_utc() if status == "published" else None)
    if not article.title: raise HTTPException(400, "标题不能为空")
    article.readers = readers
    article.reader_groups = groups
    db.add(article); db.flush(); audit(db, user.id, "article.create", article.id, {"status": status, "visibility": visibility}); db.commit()
    return RedirectResponse(f"/articles/{article.id}/edit" if status == "draft" else f"/articles/{article.id}", 303)

@app.get("/articles/{article_id}", response_class=HTMLResponse)
def article_detail(request: Request, article_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    publish_due(); article = article_or_404(db, article_id, user)
    comments = db.scalars(select(Comment).options(selectinload(Comment.author)).where(Comment.article_id == article.id, Comment.deleted.is_(False)).order_by(Comment.created_at)).all()
    visible = []
    visible_ids = set()
    for c in comments:
        if c.parent_id and c.parent_id not in visible_ids: continue
        if c.visibility == "public" or c.author_id == user.id or user.id == article.author_id:
            visible.append(c); visible_ids.add(c.id)
    return page(request, "article.html", article=article, rendered=render_body(article.body, article.body_format), comments=visible, user=user)

@app.get("/articles/{article_id}/edit", response_class=HTMLResponse)
def edit_page(request: Request, article_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    article = db.get(Article, article_id)
    if not article or (article.author_id != user.id and user.role != "admin"): raise HTTPException(404, "文章不存在")
    users = db.scalars(select(User).where(User.active.is_(True), User.id != user.id)).all()
    attachments = db.scalars(select(Attachment).where(Attachment.article_id == article.id).order_by(Attachment.id)).all()
    groups = db.scalars(select(UserGroup).options(selectinload(UserGroup.users)).order_by(UserGroup.name)).all()
    editor_body = sanitize_editor_html(article.body) if article.body_format == "html" else render_md(article.body)
    return page(request, "editor.html", article=article, editor_body=editor_body, users=users, groups=groups, selected_readers=reader_selection_for(article), attachments=attachments)

@app.post("/articles/{article_id}/edit")
def edit_article(request: Request, article_id: int, title: str = Form(...), body: str = Form(""), body_format: str = Form("markdown"), summary: str = Form(""), status: str = Form("draft"), publish_date: str = Form(""), publish_time: str = Form(""), cover_attachment_id: int | None = Form(None), reader_selection: list[str] = Form([]), csrf: str = Form(...), user: User = Depends(current_user), db: Session = Depends(get_db)):
    verify_csrf(request, csrf); article = db.get(Article, article_id)
    if not article or (article.author_id != user.id and user.role != "admin"): raise HTTPException(404, "文章不存在")
    if status not in {"draft", "published", "scheduled"} or body_format not in {"markdown", "html"}: raise HTTPException(400, "无效的文章选项")
    readers, groups = parse_reader_selection(reader_selection, db)
    visibility = "selected" if readers or groups else "all"
    article.title = title.strip()[:240]; article.summary = summary[:2000]; article.body = sanitize_editor_html(body) if body_format == "html" else body; article.body_format = body_format; article.visibility = visibility
    if cover_attachment_id is not None:
        cover = db.get(Attachment, cover_attachment_id)
        if not cover or cover.article_id != article.id or not cover.content_type.startswith("image/"):
            raise HTTPException(400, "封面必须是本文章已上传的图片")
    article.cover_attachment_id = cover_attachment_id
    article.publish_at = parse_local_datetime(publish_date, publish_time)
    article.status = status
    if status == "published" and article.publish_at and article.publish_at > now_utc(): raise HTTPException(400, "未来时间必须选择定时发布")
    if status == "published": article.published_at = article.published_at or now_utc()
    elif status == "scheduled":
        if not article.publish_at: raise HTTPException(400, "定时发布必须设置时间")
        article.published_at = None
    else: article.published_at = None
    article.readers = readers
    article.reader_groups = groups
    audit(db, user.id, "article.edit", article.id, {"status": status, "visibility": visibility}); db.commit(); return RedirectResponse(f"/articles/{article.id}", 303)

@app.post("/articles/{article_id}/delete")
def delete_article(request: Request, article_id: int, csrf: str = Form(...), user: User = Depends(current_user), db: Session = Depends(get_db)):
    verify_csrf(request, csrf); article = db.get(Article, article_id)
    if not article or (article.author_id != user.id and user.role != "admin"): raise HTTPException(404, "文章不存在")
    article.status = "deleted"; article.deleted_at = now_utc(); audit(db, user.id, "article.delete", article.id); db.commit(); return RedirectResponse("/articles", 303)

@app.post("/articles/{article_id}/comments")
def add_comment(request: Request, article_id: int, content: str = Form(...), visibility: str = Form("private"), parent_id: int | None = Form(None), csrf: str = Form(...), user: User = Depends(current_user), db: Session = Depends(get_db)):
    verify_csrf(request, csrf); article = article_or_404(db, article_id, user)
    if visibility not in {"private", "public"} or not content.strip(): raise HTTPException(400, "评论无效")
    if parent_id:
        parent = db.get(Comment, parent_id)
        if not parent or parent.article_id != article.id or parent.deleted: raise HTTPException(404, "父评论不存在")
        allowed_parent = parent.visibility == "public" or parent.author_id == user.id or user.id == article.author_id
        if not allowed_parent: raise HTTPException(404, "父评论不存在")
    comment = Comment(article_id=article.id, parent_id=parent_id, author_id=user.id, content=content[:10000], visibility=visibility)
    db.add(comment); db.flush(); audit(db, user.id, "comment.create", comment.id, {"article_id": article.id, "visibility": visibility}); db.commit()
    return RedirectResponse(f"/articles/{article.id}#comments", 303)

@app.post("/markdown/preview", response_class=HTMLResponse)
def markdown_preview(request: Request, body: str = Form(""), body_format: str = Form("markdown"), csrf: str = Form(...), user: User = Depends(current_user)):
    verify_csrf(request, csrf)
    if body_format not in {"markdown", "html"}: raise HTTPException(400, "正文格式无效")
    return HTMLResponse(render_body(body, body_format))

@app.post("/comments/{comment_id}/delete")
def delete_comment(request: Request, comment_id: int, csrf: str = Form(...), user: User = Depends(current_user), db: Session = Depends(get_db)):
    verify_csrf(request, csrf); c = db.get(Comment, comment_id)
    if not c or c.author_id != user.id: raise HTTPException(404, "评论不存在")
    c.deleted = True; c.content = "此评论已删除"; audit(db, user.id, "comment.delete", c.id); db.commit(); return RedirectResponse(f"/articles/{c.article_id}#comments", 303)

@app.post("/articles/{article_id}/attachments")
async def upload(article_id: int, request: Request, file: UploadFile = File(...), csrf: str = Form(...), user: User = Depends(current_user), db: Session = Depends(get_db)):
    verify_csrf(request, csrf); article = db.get(Article, article_id)
    if not article or (article.author_id != user.id and user.role != "admin"): raise HTTPException(404, "文章不存在")
    name = Path((file.filename or "file").replace("\\", "/")).name
    name = re.sub(r"[^\w. ()-]", "_", name, flags=re.UNICODE).strip(" .") or "file"
    ext = Path(name).suffix.lower()
    if ext not in ALLOWED_EXT or file.content_type not in ALLOWED_MIME: raise HTTPException(400, "不支持的文件类型")
    used = sum(f.stat().st_size for f in UPLOAD_DIR.rglob("*") if f.is_file())
    if used >= MAX_STORAGE: raise HTTPException(413, "附件存储配额已用完")
    stored = uuid.uuid4().hex + ext
    target = UPLOAD_DIR / stored
    digest = hashlib.sha256(); size = 0; head = b""
    try:
        with FILE_LOCK:
            with target.open("wb") as out:
                while chunk := await file.read(1024 * 1024):
                    size += len(chunk)
                    if size > MAX_UPLOAD: raise HTTPException(413, "单个附件超过 500MB 限制")
                    if used + size > MAX_STORAGE: raise HTTPException(413, "附件存储配额已用完")
                    if len(head) < 8192: head += chunk[:8192-len(head)]
                    digest.update(chunk); out.write(chunk)
            if b"<script" in head.lower(): raise HTTPException(400, "文件内容不安全")
            validate_upload_content(ext, head, target)
            record = Attachment(article_id=article.id, uploader_id=user.id, stored_name=stored, original_name=name[:255], content_type=file.content_type or "application/octet-stream", size=size, sha256=digest.hexdigest())
            db.add(record); db.flush(); audit(db, user.id, "attachment.upload", record.id, {"article_id": article.id, "size": size}); db.commit()
    except Exception:
        target.unlink(missing_ok=True); raise
    if "application/json" in request.headers.get("accept", ""):
        return JSONResponse({"id": record.id, "url": f"/attachments/{record.id}", "name": record.original_name, "content_type": record.content_type})
    return RedirectResponse(f"/articles/{article.id}", 303)

@app.get("/attachments/{attachment_id}")
def attachment(attachment_id: int, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    item = db.get(Attachment, attachment_id)
    if not item: raise HTTPException(404, "附件不存在")
    article = db.get(Article, item.article_id)
    if not article or not can_read(article, user): raise HTTPException(404, "附件不存在")
    path = UPLOAD_DIR / item.stored_name
    if not path.is_file(): raise HTTPException(404, "附件文件缺失")
    return FileResponse(path, media_type=item.content_type, filename=item.original_name, content_disposition_type="inline" if item.content_type.startswith(("image/", "video/")) else "attachment")

@app.get("/gallery-images/{image_id}")
def gallery_image(image_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    image = db.get(GalleryImage, image_id)
    if not image: raise HTTPException(404, "图片不存在")
    gallery_or_404(db, image.gallery_id, user)
    path = UPLOAD_DIR / image.stored_name
    if not path.is_file(): raise HTTPException(404, "图片文件缺失")
    disposition = "inline" if Path(image.original_name).suffix.lower() in GALLERY_PREVIEW_EXTENSIONS else "attachment"
    return FileResponse(path, media_type=image.content_type, filename=image.original_name, content_disposition_type=disposition)

@app.get("/admin/users", response_class=HTMLResponse)
def users_page(request: Request, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    return page(request, "users.html", users=db.scalars(select(User).order_by(User.id)).all())

@app.get("/admin/settings", response_class=HTMLResponse)
def admin_settings_page(request: Request, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    config = smtp_config(db)
    return page(request, "admin_settings.html", config=config, password_is_set=bool(config["password"]), error="")

@app.post("/admin/settings")
def update_admin_settings(request: Request, enabled: str = Form(""), host: str = Form(""), port: str = Form("465"), use_ssl: str = Form(""), username: str = Form(""), password: str = Form(""), clear_password: str = Form(""), sender: str = Form(""), base_url: str = Form(""), csrf: str = Form(...), admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    verify_csrf(request, csrf)
    host, username, sender, base_url = host.strip(), username.strip(), sender.strip(), base_url.strip()
    try: port_number = int(port)
    except ValueError: raise HTTPException(400, "SMTP 端口必须是数字")
    if not 1 <= port_number <= 65535: raise HTTPException(400, "SMTP 端口必须在 1 至 65535 之间")
    if enabled and (not host or not re.fullmatch(r"[^\s@]+@126\.com", username, flags=re.IGNORECASE) or not re.fullmatch(r"[^\s@]+@126\.com", sender, flags=re.IGNORECASE)):
        raise HTTPException(400, "126 邮箱通知需要 SMTP 主机、完整的 @126.com SMTP 账号和发件人邮箱")
    deployment_base_url = os.getenv("APP_BASE_URL", "").strip()
    effective_base_url = deployment_base_url or base_url
    if enabled:
        parsed_base_url = urllib.parse.urlsplit(effective_base_url)
        if parsed_base_url.scheme not in {"http", "https"} or not parsed_base_url.netloc or parsed_base_url.username or parsed_base_url.password or parsed_base_url.query or parsed_base_url.fragment:
            raise HTTPException(400, "启用邮件通知时，请填写可访问本站的完整网址（含 http:// 或 https://、IP 和端口）")
    if password and (len(password) > 1024 or not username): raise HTTPException(400, "设置 SMTP 密码前请填写账号")
    existing_config = smtp_config(db)
    if enabled and (clear_password or not (password or existing_config["password"])):
        raise HTTPException(400, "启用 126 邮箱通知必须设置客户端授权密码")
    settings = {"smtp_enabled": "true" if enabled else "false", "smtp_host": host, "smtp_port": str(port_number), "smtp_use_ssl": "true" if use_ssl else "false", "smtp_user": username, "smtp_from": sender}
    if not deployment_base_url: settings["smtp_base_url"] = base_url
    if password:
        settings["smtp_password"] = smtp_cipher().encrypt(password.encode()).decode()
    elif clear_password:
        settings["smtp_password"] = ""
    for key, value in settings.items():
        row = db.get(Setting, key)
        if row: row.value = value
        else: db.add(Setting(key=key, value=value))
    audit(db, admin.id, "settings.smtp.update", "smtp", {"enabled": bool(enabled), "password_changed": bool(password), "password_cleared": bool(clear_password)})
    db.commit()
    return RedirectResponse("/admin/settings", 303)

@app.get("/admin/groups", response_class=HTMLResponse)
def groups_page(request: Request, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    groups = db.scalars(select(UserGroup).order_by(UserGroup.name)).all()
    users = db.scalars(select(User).where(User.active.is_(True)).order_by(User.username)).all()
    return page(request, "groups.html", groups=groups, users=users)

@app.post("/admin/groups")
def create_group(request: Request, name: str = Form(...), description: str = Form(""), member_ids: list[int] = Form([]), csrf: str = Form(...), admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    verify_csrf(request, csrf)
    name = name.strip()
    if not re.fullmatch(r"[\w .-]{2,100}", name, flags=re.UNICODE): raise HTTPException(400, "用户组名称格式无效")
    if db.scalar(select(UserGroup).where(func.lower(UserGroup.name) == name.casefold())): raise HTTPException(409, "用户组已存在")
    group = UserGroup(name=name, description=description[:500], users=db.scalars(select(User).where(User.id.in_(member_ids), User.active.is_(True))).all())
    db.add(group); db.flush(); audit(db, admin.id, "group.create", group.id, {"name": group.name}); db.commit()
    return RedirectResponse("/admin/groups", 303)

@app.post("/admin/groups/{group_id}/edit")
def edit_group(request: Request, group_id: int, name: str = Form(...), description: str = Form(""), member_ids: list[int] = Form([]), csrf: str = Form(...), admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    verify_csrf(request, csrf)
    group = db.get(UserGroup, group_id)
    if not group: raise HTTPException(404, "用户组不存在")
    name = name.strip()
    if not re.fullmatch(r"[\w .-]{2,100}", name, flags=re.UNICODE): raise HTTPException(400, "用户组名称格式无效")
    duplicate = db.scalar(select(UserGroup).where(func.lower(UserGroup.name) == name.casefold(), UserGroup.id != group.id))
    if duplicate: raise HTTPException(409, "用户组已存在")
    group.name = name; group.description = description[:500]
    group.users = db.scalars(select(User).where(User.id.in_(member_ids), User.active.is_(True))).all()
    audit(db, admin.id, "group.edit", group.id, {"members": len(group.users)}); db.commit()
    return RedirectResponse("/admin/groups", 303)

@app.post("/admin/groups/{group_id}/delete")
def delete_group(request: Request, group_id: int, csrf: str = Form(...), admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    verify_csrf(request, csrf)
    group = db.get(UserGroup, group_id)
    if not group: raise HTTPException(404, "用户组不存在")
    audit(db, admin.id, "group.delete", group.id, {"name": group.name}); db.delete(group); db.commit()
    return RedirectResponse("/admin/groups", 303)

@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, user: User = Depends(current_user)):
    return page(request, "settings.html", user=user, serverchan_key_is_set=bool(user.serverchan_sendkey), error="")

@app.post("/settings")
def update_settings(request: Request, display_name: str = Form(""), email: str = Form(""), serverchan_sendkey: str = Form(""), clear_serverchan_sendkey: str = Form(""), csrf: str = Form(...), user: User = Depends(current_user), db: Session = Depends(get_db)):
    verify_csrf(request, csrf)
    email = email.strip()
    if email and (len(email) > 320 or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email)): raise HTTPException(400, "邮箱格式无效")
    serverchan_sendkey = serverchan_sendkey.strip()
    if serverchan_sendkey and (not serverchan_sendkey.startswith("SCT") or not re.fullmatch(r"[A-Za-z0-9_-]{3,255}", serverchan_sendkey)):
        raise HTTPException(400, "请填写 Server酱 Turbo 的有效格式 SendKey（以 SCT 开头）")
    user.display_name = display_name.strip()[:120]
    request.session["display_name"] = user.display_name
    user.email = email
    if serverchan_sendkey: user.serverchan_sendkey = serverchan_cipher().encrypt(serverchan_sendkey.encode()).decode()
    elif clear_serverchan_sendkey: user.serverchan_sendkey = ""
    audit(db, user.id, "user.profile", user.id)
    db.commit()
    return RedirectResponse("/settings", 303)

@app.post("/admin/users")
def create_user(request: Request, username: str = Form(...), display_name: str = Form(""), password: str = Form(...), csrf: str = Form(...), user: User = Depends(require_admin), db: Session = Depends(get_db)):
    verify_csrf(request, csrf)
    if not re.fullmatch(r"[A-Za-z0-9_.-]{2,80}", username): raise HTTPException(400, "用户名格式无效")
    if not valid_password(password): raise HTTPException(400, "密码至少 9 位且包含至少三类字符")
    if db.scalar(select(User).where(func.lower(User.username) == username.casefold())): raise HTTPException(409, "用户名已存在")
    created = User(username=username, display_name=display_name[:120], password_hash=pwd.hash(password), role="user", must_change_password=False)
    db.add(created); db.flush(); audit(db, user.id, "user.create", created.id, {"username": created.username}); db.commit()
    return RedirectResponse("/admin/users", 303)

@app.post("/admin/users/{user_id}/edit")
def edit_user(request: Request, user_id: int, display_name: str = Form(""), csrf: str = Form(...), admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    verify_csrf(request, csrf)
    target = db.get(User, user_id)
    if not target: raise HTTPException(404, "用户不存在")
    target.display_name = display_name.strip()[:120]
    if target.id == admin.id: request.session["display_name"] = target.display_name
    audit(db, admin.id, "user.edit", target.id)
    db.commit()
    return RedirectResponse("/admin/users", 303)

@app.post("/admin/users/{user_id}/disable")
def disable_user(request: Request, user_id: int, csrf: str = Form(...), admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    verify_csrf(request, csrf); target = db.get(User, user_id)
    if not target: raise HTTPException(404, "用户不存在")
    if target.role == "admin" and target.active and db.scalar(select(func.count()).select_from(User).where(User.role == "admin", User.active.is_(True))) <= 1: raise HTTPException(400, "不能禁用最后一个管理员")
    target.active = not target.active; target.session_version += 1; audit(db, admin.id, "user.disable" if not target.active else "user.enable", target.id); db.commit(); return RedirectResponse("/admin/users", 303)

@app.post("/admin/users/{user_id}/reset-password")
def reset_user_password(request: Request, user_id: int, password: str = Form(...), csrf: str = Form(...), admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    verify_csrf(request, csrf)
    if not valid_password(password): raise HTTPException(400, "密码至少 9 位且包含至少三类字符")
    target = db.get(User, user_id)
    if not target: raise HTTPException(404, "用户不存在")
    target.password_hash = pwd.hash(password); target.must_change_password = True; target.session_version += 1; audit(db, admin.id, "user.reset_password", target.id); db.commit(); return RedirectResponse("/admin/users", 303)

def _create_backup_unlocked(actor_id=None) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    archive = BACKUP_DIR / f"private-blog-backup-{stamp}.tar.gz"
    database_bytes = 0
    with engine.connect() as conn:
        row = conn.exec_driver_sql("PRAGMA page_count").fetchone()
        page_size = conn.exec_driver_sql("PRAGMA page_size").fetchone()
        database_bytes = int(row[0]) * int(page_size[0])
    attachments_bytes = sum(f.stat().st_size for f in UPLOAD_DIR.rglob("*") if f.is_file())
    needed = database_bytes + attachments_bytes + 64 * 1024 * 1024
    same_volume = DATA_DIR.stat().st_dev == BACKUP_DIR.stat().st_dev
    if shutil.disk_usage(DATA_DIR).free < needed * (2 if same_volume else 1) or shutil.disk_usage(BACKUP_DIR).free < needed:
        raise OSError("可用空间不足，不能安全创建备份")
    with tempfile.TemporaryDirectory(dir=DATA_DIR) as tmp:
        root = Path(tmp) / "backup"; root.mkdir()
        dbfile = root / "database.sqlite3"
        with SessionLocal() as db:
            audit(db, actor_id, "backup.create", archive.name)
            db.commit()
        with engine.connect() as conn:
            conn.exec_driver_sql("PRAGMA wal_checkpoint(FULL)")
            conn.exec_driver_sql(f"VACUUM INTO '{dbfile.as_posix()}'")
        shutil.copytree(UPLOAD_DIR, root / "uploads", dirs_exist_ok=True)
        files = {}
        for f in sorted(root.rglob("*")):
            if f.is_file():
                with f.open("rb") as stream: files[str(f.relative_to(root))] = sha256_stream(stream)
        manifest = {"format": 1, "created_at": datetime.now(timezone.utc).isoformat(), "app_version": "0.1.0", "files": files}
        (root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
        with tarfile.open(archive, "w:gz") as tar: tar.add(root, arcname="private-blog")
    return archive

def create_backup(actor_id=None) -> Path:
    with FILE_LOCK:
        return _create_backup_unlocked(actor_id)

def validate_backup(path: Path):
    with tarfile.open(path, "r:gz") as tar:
        members = tar.getmembers()
        if len(members) > 100_000: raise ValueError("备份文件数量超过上限")
        member_names = [m.name for m in members]
        if len(set(member_names)) != len(member_names): raise ValueError("备份包含重复路径")
        if sum(m.size for m in members) > 30 * 1024**3: raise ValueError("备份解压后超过 30 GiB")
        for m in members:
            p = Path(m.name)
            if p.is_absolute() or ".." in p.parts or not p.parts or p.parts[0] != "private-blog" or not (m.isfile() or m.isdir()): raise ValueError("备份包含不安全路径或类型")
        names = {m.name for m in members}
        manifest_name = "private-blog/manifest.json"
        db_name = "private-blog/database.sqlite3"
        if manifest_name not in names or db_name not in names: raise ValueError("备份结构不完整")
        manifest = json.load(tar.extractfile(manifest_name))
        expected_files = {"private-blog/" + name for name in manifest.get("files", {})} | {manifest_name}
        actual_files = {m.name for m in members if m.isfile()}
        if expected_files != actual_files: raise ValueError("备份清单与归档文件不一致")
        for name, digest in manifest.get("files", {}).items():
            if name.startswith("/") or ".." in Path(name).parts: raise ValueError("清单路径不安全")
            member = tar.extractfile("private-blog/" + name)
            if member is None: raise ValueError("备份校验失败")
            with member:
                if sha256_stream(member) != digest: raise ValueError("备份校验失败")
        if manifest.get("format") != 1: raise ValueError("不支持的备份格式")
        if manifest.get("app_version") != "0.1.0": raise ValueError("备份版本与当前程序不兼容")
    return manifest

@app.get("/admin/backups", response_class=HTMLResponse)
def backups_page(request: Request, user: User = Depends(require_admin)):
    items = sorted(BACKUP_DIR.glob("private-blog-backup-*.tar.gz"), key=lambda x: x.stat().st_mtime, reverse=True)
    return page(request, "backups.html", backups=[{"name": x.name, "size": x.stat().st_size, "mtime": datetime.fromtimestamp(x.stat().st_mtime, TZ), "valid": _backup_valid(x)} for x in items])

@app.get("/admin/audit", response_class=HTMLResponse)
def audit_page(request: Request, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    entries = db.scalars(select(AuditLog).order_by(AuditLog.created_at.desc()).limit(200)).all()
    return page(request, "audit.html", entries=entries)

def _backup_valid(p):
    try: validate_backup(p); return True
    except Exception: return False

@app.post("/admin/backups")
def backup_now(request: Request, csrf: str = Form(...), user: User = Depends(require_admin)):
    verify_csrf(request, csrf); archive = create_backup(user.id); validate_backup(archive); return RedirectResponse("/admin/backups", 303)

@app.get("/admin/backups/{name}")
def download_backup(name: str, user: User = Depends(require_admin)):
    if not re.fullmatch(r"private-blog-backup-[0-9TZ]+\.tar\.gz", name): raise HTTPException(404)
    path = BACKUP_DIR / name
    if not path.is_file(): raise HTTPException(404)
    validate_backup(path)
    return FileResponse(path, filename=name, media_type="application/gzip")

@app.post("/admin/backups/restore")
async def restore_upload(request: Request, file: UploadFile = File(...), confirm: str = Form(...), csrf: str = Form(...), user: User = Depends(require_admin)):
    verify_csrf(request, csrf)
    if confirm != "RESTORE": raise HTTPException(400, "请输入 RESTORE 确认")
    temp = DATA_DIR / f"restore-{uuid.uuid4().hex}.tar.gz"
    size = 0
    app.state.maintenance = True
    try:
        with temp.open("wb") as f:
            while block := await file.read(1024 * 1024):
                size += len(block)
                if size > 25 * 1024**3: raise HTTPException(413, "备份包最大 25 GiB")
                f.write(block)
        validate_backup(temp)
        await asyncio.sleep(0.5)
        engine.dispose()
        from .backup import restore
        restore(temp, check_service=False, actor_id=user.id)
        request.session.clear()
        return RedirectResponse("/login", 303)
    finally:
        temp.unlink(missing_ok=True)
        app.state.maintenance = False

@app.exception_handler(404)
async def not_found(request: Request, exc):
    if "text/html" in request.headers.get("accept", ""):
        return page(request, "error.html", code=404, message="页面不存在")
    from starlette.responses import JSONResponse
    return JSONResponse({"detail": "Not Found"}, status_code=404)

@app.exception_handler(403)
async def forbidden(request: Request, exc):
    if "text/html" in request.headers.get("accept", ""):
        return page(request, "error.html", code=403, message=getattr(exc, "detail", "访问被拒绝"))
    from starlette.responses import JSONResponse
    return JSONResponse({"detail": getattr(exc, "detail", "Forbidden")}, status_code=403, headers=getattr(exc, "headers", None))

@app.exception_handler(401)
async def unauthorized(request: Request, exc):
    return RedirectResponse("/login", 303)

@app.exception_handler(500)
async def internal_error(request: Request, exc):
    logging.error("未处理的应用异常", exc_info=exc)
    if "text/html" in request.headers.get("accept", ""):
        return page(request, "error.html", code=500, message="服务器遇到问题，请稍后重试。")
    from starlette.responses import JSONResponse
    return JSONResponse({"detail": "Internal Server Error"}, status_code=500)
