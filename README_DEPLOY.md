# Развертывание CLEAR.node через Docker Compose

Для запуска ноды на чистом сервере Linux (Ubuntu/Debian/CentOS) требуется только Docker и Docker Compose.

---

### Быстрый запуск за 2 минуты

1. **Клонируйте или скопируйте папку с проектом на сервер**:
   ```bash
   cd /opt/clear-node
   ```

2. **Создайте `.env` файл (на основе `.env.example`)**:
   ```bash
   cp .env.example .env
   ```
   Укажите надежный пароль к базе:
   ```env
   PG_USER=clear_admin
   PG_PASSWORD=StrongDatabasePassword_2026
   PG_DATABASE=clear_node_db
   NODE_SECRET_KEY=YourSuperSecretKey_ForJwtSignatures
   ```

3. **Запустите стек**:
   ```bash
   docker compose up -d
   ```

Сервер автоматически:
- Поднимет PostgreSQL 16 с персистентным хранилищем.
- Дождется готовности базы данных через healthcheck.
- Запустит API CLEAR.node на FastAPI + WebSocket.
- Запустит Nginx reverse proxy с поддержкой WebSocket upgrade на порту 80/443.

---

### Подключение HTTPS (Let's Encrypt)
Если у вас есть домен (например `node.mycompany.com`):
```bash
chmod +x init-letsencrypt.sh
./init-letsencrypt.sh node.mycompany.com admin@mycompany.com
```
Скрипт автоматически получит бесплатный SSL-сертификат и настроит автопродление каждые 12 часов.
