"""Command line backup and restore. Restore requires a stopped application service."""
import argparse, fcntl, os, shutil, sqlite3, tempfile
from pathlib import Path
from .main import create_backup, validate_backup, DATA_DIR, UPLOAD_DIR, BACKUP_DIR, smtp_cipher, serverchan_cipher
from .database import engine

def restore(archive: Path, check_service=True, actor_id=None):
    archive = archive.resolve()
    manifest = validate_backup(archive)
    service_lock = open(DATA_DIR / ".service.lock", "a+")
    if check_service:
        try: fcntl.flock(service_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            service_lock.close()
            raise RuntimeError("请先停止 private-blog.service，再执行 CLI 恢复") from exc
    # Validate the database before changing current data.
    with tempfile.TemporaryDirectory(dir=DATA_DIR) as tmp:
        stage = Path(tmp) / "stage"
        stage.mkdir()
        import tarfile
        with tarfile.open(archive, "r:gz") as tar:
            root = "private-blog/"
            for name in (root + "database.sqlite3",):
                target = stage / Path(name).name
                with tar.extractfile(name) as src, target.open("wb") as dst: shutil.copyfileobj(src, dst)
            secret_member = root + "app_secret.txt"
            source_secret = tar.extractfile(secret_member).read().decode("utf-8") if secret_member in tar.getnames() else None
            dbfile = stage / "database.sqlite3"
            check = sqlite3.connect(dbfile)
            try:
                if check.execute("PRAGMA integrity_check").fetchone()[0] != "ok": raise ValueError("SQLite 完整性检查失败")
                encrypted_sendkeys = []
                user_columns = {row[1] for row in check.execute("PRAGMA table_info(users)")}
                if "serverchan_sendkey" in user_columns:
                    encrypted_sendkeys = [row[0] for row in check.execute("SELECT serverchan_sendkey FROM users WHERE serverchan_sendkey != ''")]
                smtp_row = check.execute("SELECT value FROM settings WHERE key = 'smtp_password'").fetchone()
                encrypted_smtp_password = smtp_row[0] if smtp_row and smtp_row[0] else ""
                if (encrypted_sendkeys or encrypted_smtp_password) and not source_secret:
                    raise ValueError("此旧备份不含密钥迁移材料，无法无缝恢复 SMTP 密码和 SendKey；请在源服务器升级后重新创建备份")
                if encrypted_sendkeys or encrypted_smtp_password:
                    target_secret = os.getenv("APP_SECRET", "")
                    if not target_secret or target_secret == "replace-with-a-long-random-secret":
                        raise ValueError("目标服务器必须先配置稳定的 APP_SECRET")
                    if encrypted_smtp_password:
                        try: plaintext = smtp_cipher(source_secret).decrypt(encrypted_smtp_password.encode())
                        except Exception as exc: raise ValueError("无法解密备份中的 SMTP 密码；备份密钥材料无效") from exc
                        check.execute("UPDATE settings SET value = ? WHERE key = 'smtp_password'", (smtp_cipher(target_secret).encrypt(plaintext).decode(),))
                    if encrypted_sendkeys:
                        try:
                            rewrapped = [(serverchan_cipher(target_secret).encrypt(serverchan_cipher(source_secret).decrypt(token.encode())).decode(), token) for token in encrypted_sendkeys]
                        except Exception as exc: raise ValueError("无法解密备份中的 Server酱 SendKey；备份密钥材料无效") from exc
                        for new_value, old_value in rewrapped:
                            check.execute("UPDATE users SET serverchan_sendkey = ? WHERE serverchan_sendkey = ?", (new_value, old_value))
                check.execute("UPDATE users SET session_version = session_version + 1")
                check.commit()
            finally: check.close()
            upload_root = stage / "uploads"
            upload_root.mkdir()
            for member in tar.getmembers():
                prefix = root + "uploads/"
                if member.name.startswith(prefix) and member.isfile():
                    rel = Path(member.name[len(prefix):])
                    if rel.is_absolute() or ".." in rel.parts: raise ValueError("附件路径不安全")
                    target = upload_root / rel
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with tar.extractfile(member) as src, target.open("wb") as dst: shutil.copyfileobj(src, dst)
        current_backup = create_backup()
        live_db = Path(os.getenv("DATABASE_URL", "sqlite:///./private_blog.db").removeprefix("sqlite:///"))
        if not live_db.is_absolute(): live_db = Path.cwd() / live_db
        live_db.parent.mkdir(parents=True, exist_ok=True)
        rollback_db = live_db.with_suffix(live_db.suffix + ".pre-restore")
        if live_db.exists(): shutil.copy2(live_db, rollback_db)
        rollback_upload = DATA_DIR / "uploads.pre-restore"
        if rollback_upload.exists(): shutil.rmtree(rollback_upload)
        if UPLOAD_DIR.exists(): shutil.copytree(UPLOAD_DIR, rollback_upload)
        try:
            engine.dispose()
            staged_db = live_db.with_suffix(live_db.suffix + ".restore-new")
            shutil.copy2(dbfile, staged_db)
            for suffix in ("-wal", "-shm"): Path(str(live_db) + suffix).unlink(missing_ok=True)
            os.replace(staged_db, live_db)
            replacement = DATA_DIR / "uploads.restore"
            if replacement.exists(): shutil.rmtree(replacement)
            shutil.copytree(upload_root, replacement)
            old = DATA_DIR / "uploads.old"
            if old.exists(): shutil.rmtree(old)
            if UPLOAD_DIR.exists(): UPLOAD_DIR.rename(old)
            replacement.rename(UPLOAD_DIR)
            if old.exists(): shutil.rmtree(old)
        except Exception:
            if rollback_db.exists(): os.replace(rollback_db, live_db)
            if rollback_upload.exists():
                if UPLOAD_DIR.exists(): shutil.rmtree(UPLOAD_DIR)
                shutil.copytree(rollback_upload, UPLOAD_DIR)
            raise
        finally:
            rollback_db.unlink(missing_ok=True)
            if rollback_upload.exists(): shutil.rmtree(rollback_upload)
        from .main import publish_due
        publish_due()
        from .database import SessionLocal
        from .main import audit
        from .models import User
        with SessionLocal() as db:
            actor = db.get(User, actor_id) if actor_id is not None else None
            audit(db, actor.id if actor else None, "backup.restore", archive.name)
            db.commit()
        print(f"恢复成功，原数据备份：{current_backup}")
    service_lock.close()

def main():
    parser = argparse.ArgumentParser(description="私有博客备份与恢复")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("backup")
    p = sub.add_parser("validate"); p.add_argument("archive", type=Path)
    p = sub.add_parser("restore"); p.add_argument("archive", type=Path); p.add_argument("--confirm", required=True)
    args = parser.parse_args()
    if args.command == "backup":
        path = create_backup(); validate_backup(path); print(path)
    elif args.command == "validate": print(validate_backup(args.archive))
    elif args.command == "restore":
        if args.confirm != "RESTORE": parser.error("恢复必须使用 --confirm RESTORE")
        restore(args.archive)
if __name__ == "__main__": main()
