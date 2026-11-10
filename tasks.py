import os
import io
import time
import json
import asyncio
import ftplib
import tarfile
import urllib.request
from datetime import datetime, timezone, timedelta
from database import get_pool, PgConnection
from ws_router import manager

SAMARA_TZ = timezone(timedelta(hours=4))
FTP_HOST = "ftp.standartmaster.ru"
FTP_USER = "smaster"
FTP_PASS = "Rets15101%"
FTP_BASE_DIR = "/backups"

async def run_ftp_backup():
    now_samara = datetime.now(SAMARA_TZ)
    date_folder = now_samara.strftime("%Y%m%d")
    timestamp_str = now_samara.strftime("%Y%m%d_%H%M%S")
    
    print(f"[FTP BACKUP] Starting scheduled backup at {now_samara.strftime('%Y-%m-%d %H:%M:%S')} (Samara)")
    
    nodes_info = [
        ("node1", "https://dokploy.standartmaster.ru/rms"),
        ("node2", "https://dokploy.standartmaster.ru/rdm")
    ]
    
    archives = []
    
    for node_id, base_url in nodes_info:
        try:
            login_url = f"{base_url}/auth/login"
            payload = json.dumps({"username": "Deniska", "password": "341049"}).encode("utf-8")
            req = urllib.request.Request(login_url, data=payload, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                token = json.loads(resp.read().decode("utf-8"))["access_token"]
                
            backup_url = f"{base_url}/admin/backup"
            req_b = urllib.request.Request(backup_url, headers={"Authorization": f"Bearer {token}"})
            with urllib.request.urlopen(req_b, timeout=60) as resp:
                backup_json = resp.read()
            tar_buf = io.BytesIO()
            with tarfile.open(fileobj=tar_buf, mode="w:gz") as tar:
                # 1. Full JSON Application Data
                json_bytes = io.BytesIO(backup_json)
                ti = tarfile.TarInfo(name=f"{node_id}_export.json")
                ti.size = len(backup_json)
                ti.mtime = int(time.time())
                tar.addfile(ti, json_bytes)

                # 2. Complete PostgreSQL SQL Dump
                try:
                    env = os.environ.copy()
                    env["PGPASSWORD"] = "ClearNode_Pass_2026!"
                    db_name = "clear_rms_db" if node_id == "node1" else "clear_terminal_db"
                    proc = await asyncio.create_subprocess_exec(
                        "pg_dump", "-h", "postgres", "-U", "clear_admin", "-d", db_name, "--clean", "--if-exists",
                        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=env
                    )
                    stdout, _ = await proc.communicate()
                    if stdout:
                        sql_ti = tarfile.TarInfo(name=f"{node_id}_full_dump.sql")
                        sql_ti.size = len(stdout)
                        sql_ti.mtime = int(time.time())
                        tar.addfile(sql_ti, io.BytesIO(stdout))
                        print(f"[FTP BACKUP] Added full pg_dump SQL ({len(stdout)} bytes) to {node_id}")
                except Exception as e:
                    print(f"[FTP BACKUP] pg_dump skipped: {e}")

            tar_buf.seek(0)
            archive_name = f"clear_db_{node_id}_{timestamp_str}.tar.gz"
            archives.append((archive_name, tar_buf))
            print(f"[FTP BACKUP] In-memory archive {archive_name}: {len(tar_buf.getvalue())} bytes")
        except Exception as e:
            print(f"[FTP BACKUP ERROR] Failed to export {node_id}: {e}")
            
    if not archives:
        print("[FTP BACKUP ERROR] No archives created, aborting upload.")
        return False
        
    try:
        ftp = ftplib.FTP(FTP_HOST, timeout=30)
        ftp.login(FTP_USER, FTP_PASS)
        
        try:
            ftp.cwd(FTP_BASE_DIR)
        except Exception:
            ftp.mkd(FTP_BASE_DIR)
            ftp.cwd(FTP_BASE_DIR)
            
        try:
            ftp.cwd(date_folder)
        except Exception:
            ftp.mkd(date_folder)
            ftp.cwd(date_folder)
            
        for archive_name, buf in archives:
            buf.seek(0)
            ftp.storbinary(f"STOR {archive_name}", buf)
            print(f"[FTP BACKUP] Stored {archive_name} to {FTP_BASE_DIR}/{date_folder}/")
            
        ftp.quit()
        print(f"[FTP BACKUP SUCCESS] All backups uploaded to {FTP_BASE_DIR}/{date_folder}/")
        return True
    except Exception as e:
        print(f"[FTP BACKUP ERROR] FTP upload failed: {e}")
        return False

async def background_ftp_backup_task():
    # Run only in RMS node container (port 8010)
    if os.environ.get("PORT") != "8010":
        return
        
    last_backup_date = None
    print("[FTP BACKUP] Scheduler initialized. Daily target: 06:00 Samara time (UTC+4)")
    
    while True:
        try:
            now_samara = datetime.now(SAMARA_TZ)
            today_str = now_samara.strftime("%Y%m%d")
            
            # Run at 06:00 Samara time (once per day)
            if now_samara.hour == 6 and last_backup_date != today_str:
                success = await run_ftp_backup()
                if success:
                    last_backup_date = today_str
        except Exception as e:
            print(f"[FTP BACKUP ERROR] Scheduler loop error: {e}")
            
        await asyncio.sleep(45)

async def background_cleanup_task():
    while True:
        try:
            pool = await get_pool()
            async with pool.acquire() as raw_conn:
                conn = PgConnection(raw_conn)
                c = await conn.cursor()
                
                await c.execute("SELECT Value FROM ServerSettings WHERE Key = 'DeletedRetentionDays'")
                row = await c.fetchone()
                retention_days = int(row[0]) if row else 30

                cleanup_query = f"""
                    SELECT Id FROM Entities 
                    WHERE Deleted = 1 
                    AND DeletedAt IS NOT NULL 
                    AND DeletedAt < NOW() - INTERVAL '{retention_days} days'
                """
                await c.execute(cleanup_query)
                to_delete = [r[0] for r in await c.fetchall()]

                if to_delete:
                    await conn.ensure_transaction()
                    
                    await c.execute("UPDATE DbVersion SET Revision = Revision + 1")
                    await c.execute("SELECT Revision FROM DbVersion LIMIT 1")
                    new_rev = (await c.fetchone())[0]

                    for entity_id in to_delete:
                        await c.execute("""
                            INSERT INTO DeletedEntities (Id, Revision) VALUES (?, ?)
                            ON CONFLICT (Id) DO UPDATE SET Revision = EXCLUDED.Revision
                        """, (entity_id, new_rev))
                        await c.execute("DELETE FROM Entities WHERE Id = ?", (entity_id,))
                        await c.execute("DELETE FROM EntityPermissions WHERE EntityId = ?", (entity_id,))

                    await conn.commit()
                    await manager.broadcast({"event": "new_revision", "revision": new_rev})
                    print(f"[CLEANUP] Окончательно удалено старых записей: {len(to_delete)}.")

                await c.execute("SELECT Value FROM ServerSettings WHERE Key = 'AuditRetentionDays'")
                audit_row = await c.fetchone()
                audit_retention = int(audit_row[0]) if audit_row else 90

                await c.execute(f"DELETE FROM AuditLog WHERE Timestamp < NOW() - INTERVAL '{audit_retention} days'")
                if c.rowcount > 0:
                    print(f"[CLEANUP] Удалено старых записей из лога: {c.rowcount}")
                
                await conn.commit()

        except Exception as e:
            print(f"[CLEANUP ERROR] Ошибка при очистке БД: {e}")

        await asyncio.sleep(3600)
