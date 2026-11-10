import json

def json_to_sql_dump(backup_data, db_name="clear_rms_db"):
    lines = [
        f"-- CLEAR Node PostgreSQL Dump",
        f"-- Database: {db_name}",
        f"-- Export Date: {backup_data.get('export_date')}",
        "BEGIN;",
        ""
    ]
    
    # 1. ServerSettings
    lines.append("-- 1. ServerSettings")
    lines.append("CREATE TABLE IF NOT EXISTS ServerSettings (Key TEXT PRIMARY KEY, Value TEXT);")
    for s in backup_data.get("settings", []):
        k = str(s.get("key", "")).replace("'", "''")
        v = str(s.get("value", "")).replace("'", "''")
        lines.append(f"INSERT INTO ServerSettings (Key, Value) VALUES ('{k}', '{v}') ON CONFLICT (Key) DO UPDATE SET Value = EXCLUDED.Value;")
    lines.append("")
    
    # 2. Groups
    lines.append("-- 2. Groups")
    lines.append("""CREATE TABLE IF NOT EXISTS Groups (
        Id TEXT PRIMARY KEY, Name TEXT, IsSuperAdmin INTEGER DEFAULT 0,
        CanAdd INTEGER DEFAULT 0, CanEdit INTEGER DEFAULT 0, CanDelete INTEGER DEFAULT 0, CanBulkEdit INTEGER DEFAULT 0,
        CanManageUsers INTEGER DEFAULT 0, CanManageRoles INTEGER DEFAULT 0, CanManageSettings INTEGER DEFAULT 0,
        CanReadLog INTEGER DEFAULT 0, CanSaveLocal INTEGER DEFAULT 0, IsHidden INTEGER DEFAULT 0, IsDeleted INTEGER DEFAULT 0
    );""")
    for g in backup_data.get("groups", []):
        gid = str(g.get("id", "")).replace("'", "''")
        name = str(g.get("name", "")).replace("'", "''")
        vals = [
            f"'{gid}'", f"'{name}'", str(int(bool(g.get("is_superadmin")))),
            str(int(bool(g.get("can_add", 1)))), str(int(bool(g.get("can_edit", 1)))),
            str(int(bool(g.get("can_delete", 1)))), str(int(bool(g.get("can_bulk_edit")))),
            str(int(bool(g.get("can_manage_users")))), str(int(bool(g.get("can_manage_roles")))),
            str(int(bool(g.get("can_manage_settings")))), str(int(bool(g.get("can_read_log")))),
            str(int(bool(g.get("can_save_local")))), str(int(bool(g.get("is_hidden")))),
            str(int(bool(g.get("is_deleted"))))
        ]
        joined_vals = ", ".join(vals)
        lines.append(f"INSERT INTO Groups VALUES ({joined_vals}) ON CONFLICT (Id) DO UPDATE SET Name = EXCLUDED.Name;")
    lines.append("")
    
    # 3. Users
    lines.append("-- 3. Users")
    lines.append("""CREATE TABLE IF NOT EXISTS Users (
        Id TEXT PRIMARY KEY, Username TEXT UNIQUE NOT NULL, Email TEXT,
        PlainPassword TEXT DEFAULT '',
        FirstConnect TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
        LastConnect TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
        IsApproved INTEGER DEFAULT 0, IsActive INTEGER DEFAULT 1
    );""")
    for u in backup_data.get("users", []):
        uid = str(u.get("id", "")).replace("'", "''")
        uname = str(u.get("username", "")).replace("'", "''")
        email = str(u.get("email") or "").replace("'", "''")
        pwh = str(u.get("password_hash") or "").replace("'", "''")
        app = str(int(bool(u.get("is_approved"))))
        act = str(int(bool(u.get("is_active", 1))))
        fc = f"'{u['first_connect']}'" if u.get("first_connect") else "NOW()"
        lc = f"'{u['last_connect']}'" if u.get("last_connect") else "NOW()"
        lines.append(f"INSERT INTO Users (Id, Username, Email, PlainPassword, FirstConnect, LastConnect, IsApproved, IsActive) VALUES ('{uid}', '{uname}', '{email}', '{pwh}', {fc}, {lc}, {app}, {act}) ON CONFLICT (Id) DO UPDATE SET Username = EXCLUDED.Username, PlainPassword = EXCLUDED.PlainPassword;")
    lines.append("")
    
    # 4. UserGroups
    lines.append("-- 4. UserGroups")
    lines.append("CREATE TABLE IF NOT EXISTS UserGroups (UserId TEXT, GroupId TEXT, PRIMARY KEY(UserId, GroupId));")
    for ug in backup_data.get("user_groups", []):
        uid = str(ug.get("user_id", "")).replace("'", "''")
        gid = str(ug.get("group_id", "")).replace("'", "''")
        lines.append(f"INSERT INTO UserGroups VALUES ('{uid}', '{gid}') ON CONFLICT DO NOTHING;")
    lines.append("")
    
    # 5. Entities (Подключения и папки)
    lines.append("-- 5. Entities (Connections & Folders)")
    lines.append("""CREATE TABLE IF NOT EXISTS Entities (
        Id TEXT PRIMARY KEY, FolderId TEXT DEFAULT '', EncryptedData TEXT,
        Deleted INTEGER DEFAULT 0, Revision INTEGER DEFAULT 0, DeletedAt TIMESTAMP WITH TIME ZONE
    );""")
    for e in backup_data.get("entities", []):
        eid = str(e.get("id", "")).replace("'", "''")
        fid = str(e.get("folder_id") or "").replace("'", "''")
        enc = str(e.get("encrypted_data") or "").replace("'", "''")
        del_val = str(int(bool(e.get("deleted"))))
        rev = str(int(e.get("revision") or 1))
        del_at = f"'{e['deleted_at']}'" if e.get("deleted_at") else "NULL"
        lines.append(f"INSERT INTO Entities VALUES ('{eid}', '{fid}', '{enc}', {del_val}, {rev}, {del_at}) ON CONFLICT (Id) DO UPDATE SET FolderId=EXCLUDED.FolderId, EncryptedData=EXCLUDED.EncryptedData, Deleted=EXCLUDED.Deleted, Revision=EXCLUDED.Revision;")
    lines.append("")
    
    # 6. EntityPermissions
    lines.append("-- 6. EntityPermissions")
    lines.append("CREATE TABLE IF NOT EXISTS EntityPermissions (EntityId TEXT, GroupId TEXT, AccessLevel TEXT, PRIMARY KEY(EntityId, GroupId));")
    for ep in backup_data.get("entity_permissions", []):
        eid = str(ep.get("entity_id", "")).replace("'", "''")
        gid = str(ep.get("group_id", "")).replace("'", "''")
        al = str(ep.get("access_level", "read")).replace("'", "''")
        lines.append(f"INSERT INTO EntityPermissions VALUES ('{eid}', '{gid}', '{al}') ON CONFLICT DO NOTHING;")
    lines.append("")
    
    # 7. DbVersion
    lines.append("-- 7. DbVersion")
    lines.append("CREATE TABLE IF NOT EXISTS DbVersion (Revision INTEGER DEFAULT 0, AdminRevision INTEGER DEFAULT 0);")
    v = backup_data.get("db_version", {})
    rev = v.get("revision", 1)
    admin_rev = v.get("admin_revision", 1)
    lines.append(f"DELETE FROM DbVersion; INSERT INTO DbVersion VALUES ({rev}, {admin_rev});")
    lines.append("")
    
    lines.append("COMMIT;")
    return "\n".join(lines)
