import os
import re
from datetime import timedelta
from zoneinfo import ZoneInfo

os.environ["DATABASE_URL"] = "sqlite:///./.test-private-blog.db"
os.environ["DATA_DIR"] = "./.test-data"
os.environ["APP_SECRET"] = "test-secret-that-is-long-enough-for-signed-session"
os.environ["INITIAL_ADMIN_PASSWORD"] = "1234"
os.environ["AUTO_MIGRATE"] = "true"

from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from app import main
from app.main import app, pwd, create_backup, validate_backup, UPLOAD_DIR
from app.database import SessionLocal
from app.models import User, UserGroup, group_memberships, Article, Comment, Attachment, AuditLog, article_readers, now_utc
from app.backup import restore

def csrf_from(response):
    return re.search(r'name="csrf" value="([^"]+)"', response.text).group(1)

def setup_state():
    with SessionLocal() as db:
        db.execute(delete(AuditLog)); db.execute(delete(Comment)); db.execute(delete(Attachment)); db.execute(delete(group_memberships)); db.execute(delete(UserGroup)); db.execute(delete(article_readers)); db.execute(delete(Article)); db.execute(delete(User)); db.commit()
        db.add(User(username="admin", display_name="Admin", password_hash=pwd.hash("1234"), role="admin", must_change_password=True)); db.commit()

def test_first_login_password_policy_and_authenticated_content_flow():
    with TestClient(app) as client:
        setup_state()
        response = client.get("/login")
        response = client.post("/login", data={"username":"", "password":"1234", "csrf":csrf_from(response)}, follow_redirects=False)
        assert response.status_code == 303 and response.headers["location"] == "/change-password"
        assert client.get("/articles").status_code == 403
        password_page = client.get("/change-password")
        weak = client.post("/change-password", data={"new_password":"weak", "csrf":csrf_from(password_page)})
        assert "至少 9 位" in weak.text
        password_page = client.get("/change-password")
        response = client.post("/change-password", data={"new_password":"N3w!StrongPass", "csrf":csrf_from(password_page)}, follow_redirects=False)
        assert response.status_code == 303
        logout_page = client.get("/articles/new")
        client.post("/logout", data={"csrf":csrf_from(logout_page)}, follow_redirects=False)
        login_page = client.get("/login")
        uppercase_login = client.post("/login", data={"username":"ADMIN", "password":"N3w!StrongPass", "csrf":csrf_from(login_page)}, follow_redirects=False)
        assert uppercase_login.status_code == 303
        page = client.get("/articles/new")
        response = client.post("/articles", data={"title":"公开文章", "body":"# 安全正文", "status":"published", "visibility":"all", "csrf":csrf_from(page)}, follow_redirects=False)
        assert response.status_code == 303
        article_id = int(response.headers["location"].rsplit("/", 1)[-1])
        assert "安全正文" in client.get(f"/articles/{article_id}").text
        editor = client.get(f"/articles/{article_id}/edit")
        preview = client.post("/markdown/preview", data={"body":"# 预览\n<script>alert(1)</script>", "csrf":csrf_from(editor)})
        assert "<h1>预览</h1>" in preview.text and "<script>" not in preview.text
        bad = client.post(f"/articles/{article_id}/attachments", data={"csrf":csrf_from(editor)}, files={"file":("fake.png", b"not a png", "image/png")})
        assert bad.status_code == 400

def test_private_article_and_comment_access_boundaries():
    with TestClient(app) as reader:
        setup_state()
        with SessionLocal() as db:
            admin = db.scalar(select(User).where(User.username == "admin"))
            other = User(username="reader", display_name="Reader", password_hash=pwd.hash("N3w!StrongPass"), role="user")
            db.add(other); db.flush()
            article = Article(slug="private-test", title="私密标题", body="私密正文", author_id=admin.id, status="published", visibility="selected", published_at=now_utc())
            db.add(article); db.flush()
            comment = Comment(article_id=article.id, author_id=admin.id, content="作者私密留言", visibility="private")
            public_comment = Comment(article_id=article.id, author_id=admin.id, content="公开留言", visibility="public")
            private_attachment = Attachment(article_id=article.id, uploader_id=admin.id, stored_name="hidden-file.png", original_name="hidden.png", content_type="image/png", size=8, sha256="0" * 64)
            db.add_all([comment, public_comment, private_attachment]); db.commit(); aid = article.id; attachment_id = private_attachment.id
        page = reader.get("/login"); reader.post("/login", data={"username":"reader", "password":"N3w!StrongPass", "csrf":csrf_from(page)})
        assert reader.get(f"/articles/{aid}").status_code == 404
        assert reader.get(f"/attachments/{attachment_id}").status_code == 404
        with SessionLocal() as db:
            article = db.get(Article, aid)
            allowed_reader = db.scalar(select(User).where(User.username == "reader"))
            article.readers = [allowed_reader]
            db.commit()
        shared = reader.get(f"/articles/{aid}")
        assert shared.status_code == 200 and "公开留言" in shared.text and "作者私密留言" not in shared.text
    with TestClient(app) as admin_client:
        page = admin_client.get("/login"); admin_client.post("/login", data={"username":"admin", "password":"1234", "csrf":csrf_from(page)})
        # setup_state marks the bootstrap password-change requirement.
        pw = admin_client.get("/change-password")
        admin_client.post("/change-password", data={"new_password":"N3w!StrongPass", "csrf":csrf_from(pw)})
        detail = admin_client.get(f"/articles/{aid}")
        assert "私密正文" in detail.text and "作者私密留言" in detail.text

def test_scheduled_publishing_and_backup_integrity():
    with TestClient(app) as client:
        setup_state()
        with SessionLocal() as db:
            admin = db.scalar(select(User).where(User.username == "admin"))
            due = Article(slug="due-test", title="已到期", body="", author_id=admin.id, status="scheduled", visibility="all", publish_at=now_utc()-timedelta(minutes=1))
            future = Article(slug="future-test", title="未来内容保密", body="secret", author_id=admin.id, status="scheduled", visibility="all", publish_at=now_utc()+timedelta(days=1))
            db.add_all([due, future]); db.commit(); due_id = due.id
        page = client.get("/login"); client.post("/login", data={"username":"admin", "password":"1234", "csrf":csrf_from(page)})
        pw = client.get("/change-password"); client.post("/change-password", data={"new_password":"N3w!StrongPass", "csrf":csrf_from(pw)})
        index = client.get("/articles")
        assert "已到期" in index.text and "未来内容保密" not in index.text
        assert client.get(f"/articles/{due_id}").status_code == 200
        editor = client.get(f"/articles/{due_id}/edit")
        upload = client.post(f"/articles/{due_id}/attachments", data={"csrf":csrf_from(editor)}, files={"file":("tiny.png", b"\x89PNG\r\n\x1a\nfixture", "image/png")}, follow_redirects=False)
        assert upload.status_code == 303
        with SessionLocal() as db:
            attachment = db.scalar(select(Attachment).where(Attachment.article_id == due_id))
            attachment_id, stored_name = attachment.id, attachment.stored_name
        assert client.get(f"/attachments/{attachment_id}").content.startswith(b"\x89PNG")
        editor = client.get(f"/articles/{due_id}/edit")
        edited = client.post(f"/articles/{due_id}/edit", data={"title":"已到期", "body":"", "summary":"", "status":"published", "visibility":"all", "cover_attachment_id":str(attachment_id), "csrf":csrf_from(editor)}, follow_redirects=False)
        assert edited.status_code == 303
        assert f'class="post-cover" src="/attachments/{attachment_id}"' in client.get(f"/articles/{due_id}").text
        ranged = client.get(f"/attachments/{attachment_id}", headers={"Range":"bytes=0-3"})
        assert ranged.status_code == 206 and len(ranged.content) == 4
    archive = create_backup()
    manifest = validate_backup(archive)
    assert manifest["format"] == 1
    with SessionLocal() as db:
        db.get(Article, due_id).title = "被后续修改"
        db.commit()
    (UPLOAD_DIR / stored_name).unlink()
    restore(archive, check_service=True)
    with SessionLocal() as db:
        assert db.get(Article, due_id).title == "已到期"
        assert db.scalar(select(AuditLog).where(AuditLog.action == "backup.restore")) is not None
        assert db.get(Article, due_id).status == "published"
        assert db.get(Attachment, attachment_id) is not None
    assert (UPLOAD_DIR / stored_name).is_file()
    with SessionLocal() as db:
        db.get(Article, due_id).title = "网页恢复前的修改"
        db.commit()
    with TestClient(app) as client:
        login_page = client.get("/login")
        client.post("/login", data={"username":"admin", "password":"N3w!StrongPass", "csrf":csrf_from(login_page)})
        backup_page = client.get("/admin/backups")
        response = client.post("/admin/backups/restore", data={"csrf":csrf_from(backup_page), "confirm":"RESTORE"}, files={"file":(archive.name, archive.read_bytes(), "application/gzip")}, follow_redirects=False)
        assert response.status_code == 303 and response.headers["location"] == "/login"
    with SessionLocal() as db:
        assert db.get(Article, due_id).title == "已到期"
    archive.unlink()

def test_user_management_revokes_disabled_sessions_and_timezone_boundaries():
    with TestClient(app) as admin_client:
        setup_state()
        login_page = admin_client.get("/login")
        admin_client.post("/login", data={"username":"admin", "password":"1234", "csrf":csrf_from(login_page)})
        password_page = admin_client.get("/change-password")
        admin_client.post("/change-password", data={"new_password":"N3w!StrongPass", "csrf":csrf_from(password_page)})
        users_page = admin_client.get("/admin/users")
        created = admin_client.post("/admin/users", data={"username":"reader", "display_name":"读者", "password":"N3w!StrongPass", "csrf":csrf_from(users_page)}, follow_redirects=False)
        assert created.status_code == 303
        with SessionLocal() as db:
            reader_id = db.scalar(select(User).where(User.username == "reader")).id
        groups_page = admin_client.get("/admin/groups")
        group_response = admin_client.post("/admin/groups", data={"name":"知识小组", "description":"测试组", "member_ids":str(reader_id), "csrf":csrf_from(groups_page)}, follow_redirects=False)
        assert group_response.status_code == 303
        assert "知识小组" in admin_client.get("/admin/groups").text
    with TestClient(app) as reader:
        login_page = reader.get("/login")
        reader.post("/login", data={"username":"reader", "password":"N3w!StrongPass", "csrf":csrf_from(login_page)})
        assert reader.get("/admin/users").status_code == 403
        assert 'href="/admin/users"' not in reader.get("/articles").text
        reader_cookie = reader.cookies.get("private_blog_session")
    with TestClient(app) as admin_client:
        login_page = admin_client.get("/login")
        admin_client.post("/login", data={"username":"admin", "password":"N3w!StrongPass", "csrf":csrf_from(login_page)})
        users_page = admin_client.get("/admin/users")
        disabled = admin_client.post(f"/admin/users/{reader_id}/disable", data={"csrf":csrf_from(users_page)}, follow_redirects=False)
        assert disabled.status_code == 303
    with TestClient(app) as expired:
        expired.cookies.set("private_blog_session", reader_cookie)
        assert expired.get("/articles", follow_redirects=False).status_code == 303
    previous = main.TZ
    try:
        main.TZ = ZoneInfo("Asia/Shanghai")
        midnight = main.parse_local_datetime("2028-10-02")
        assert midnight.hour == 16 and midnight.day == 1  # 2028-10-02 00:00 China time is prior-day 16:00 UTC.
        main.TZ = ZoneInfo("America/New_York")
        try:
            main.parse_local_datetime("2025-03-09", "02:30")
            assert False, "nonexistent DST local time must fail"
        except Exception as exc:
            assert getattr(exc, "status_code", None) == 400
        repeated = main.parse_local_datetime("2025-11-02", "01:30")
        assert repeated.hour == 5  # Ambiguous time uses the first occurrence (fold=0).
    finally:
        main.TZ = previous
