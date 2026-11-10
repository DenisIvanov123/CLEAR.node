#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SM Remote — Local Web Server
Локальный сервер для веб-версии SM Remote.
Служит веб-интерфейс и предоставляет данные из локальных баз Node1.db (RMS) и Node2.db (РДМ).
"""

import os
import sys
import json
import base64
import sqlite3
import webbrowser
from http.server import HTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

PORT = 5050
WEB_DIR = os.path.join(os.path.dirname(__file__), "web")
APPDATA_DB_DIR = os.path.expandvars(r"%APPDATA%\resto_sm\DataBases\pc")

def get_node_databases():
    nodes = []
    if os.path.isdir(APPDATA_DB_DIR):
        p1 = os.path.join(APPDATA_DB_DIR, "Node1.db")
        if os.path.exists(p1):
            nodes.append({
                "id": "node1",
                "name": "RMS Сервер (iikoOffice / Chain)",
                "path": p1,
                "type": "rms"
            })
        p2 = os.path.join(APPDATA_DB_DIR, "Node2.db")
        if os.path.exists(p2):
            nodes.append({
                "id": "node2",
                "name": "РДМ Терминалы (AnyDesk / Aspia / RDP / Ammyy)",
                "path": p2,
                "type": "rdm"
            })
    return nodes

def decode_password(b64_str):
    if not b64_str:
        return ""
    try:
        # Check if valid base64
        decoded_bytes = base64.b64decode(b64_str)
        return decoded_bytes.decode('utf-8')
    except Exception:
        return b64_str

def load_connections_from_db(db_path):
    if not os.path.exists(db_path):
        return []

    conn = sqlite3.connect(db_path)
    conn.text_factory = lambda b: b.decode('utf-8', errors='replace')
    c = conn.cursor()

    try:
        c.execute("""
            SELECT id, name, type, folder, ip, port, login, password, description, deleted 
            FROM NodeEntities 
            WHERE deleted = 'false' OR deleted = '0' OR deleted = 0
            ORDER BY type = 'folder' DESC, name ASC
        """)
        rows = c.fetchall()
        items = []
        for r in rows:
            raw_pw = r[7] or ""
            plain_pw = decode_password(raw_pw)
            items.append({
                "id": r[0],
                "name": r[1] or "Без имени",
                "type": (r[2] or "iiko").lower(),
                "folder": r[3] or "",
                "ip": r[4] or "",
                "port": r[5] or "",
                "login": r[6] or "",
                "password": plain_pw,
                "description": r[8] or "",
                "deleted": False
            })
        return items
    except Exception as e:
        print(f"Error querying DB {db_path}: {e}")
        return []
    finally:
        conn.close()

class LocalPortalHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=WEB_DIR, **kwargs)

    def end_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(200)
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/api/nodes":
            nodes = get_node_databases()
            resp_nodes = []
            for n in nodes:
                try:
                    c = sqlite3.connect(n["path"])
                    cnt = c.execute("SELECT COUNT(*) FROM NodeEntities WHERE deleted = 'false'").fetchone()[0]
                    c.close()
                except:
                    cnt = 0
                resp_nodes.append({
                    "id": n["id"],
                    "name": f"{n['name']} ({cnt} подкл.)",
                    "count": cnt,
                    "type": n["type"]
                })

            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps(resp_nodes, ensure_ascii=False).encode("utf-8"))
            return

        elif path == "/api/connections":
            qs = parse_qs(parsed.query)
            node_id = qs.get("node", ["node1"])[0]
            nodes = get_node_databases()
            target = next((n for n in nodes if n["id"] == node_id), nodes[0] if nodes else None)

            if not target:
                items = []
                node_name = "Нет баз данных"
            else:
                items = load_connections_from_db(target["path"])
                node_name = target["name"]

            data = {
                "node": node_id,
                "name": node_name,
                "count": len(items),
                "items": items
            }

            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps(data, ensure_ascii=False).encode("utf-8"))
            return

        elif path in ["", "/"]:
            self.path = "/index.html"

        return super().do_GET()

def run_server():
    server_address = ("127.0.0.1", PORT)
    try:
        httpd = HTTPServer(server_address, LocalPortalHandler)
    except OSError:
        # Fallback to alternate port
        server_address = ("127.0.0.1", PORT + 1)
        httpd = HTTPServer(server_address, LocalPortalHandler)

    active_port = server_address[1]
    url = f"http://localhost:{active_port}"
    print(f"==================================================")
    print(f" SM Remote Web Portal запущен:")
    print(f" Адрес: {url}")
    print(f"==================================================")

    # Open browser
    try:
        webbrowser.open(url)
    except Exception as e:
        print(f"Could not open browser: {e}")

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nСервер остановлен.")
        httpd.server_close()

if __name__ == "__main__":
    run_server()
