import hashlib
import json
from contextlib import contextmanager
from datetime import datetime, timezone
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row

from app.config import get_settings


def _connect():
    return psycopg.connect(get_settings().database_url, row_factory=dict_row)


def init_db():
    with _connect() as conn:
        with conn.cursor() as c:
            c.execute(
                '''CREATE TABLE IF NOT EXISTS users (
                    id BIGSERIAL PRIMARY KEY,
                    username TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    is_admin BOOLEAN DEFAULT FALSE,
                    created_at TEXT NOT NULL
                )'''
            )

            c.execute(
                '''CREATE TABLE IF NOT EXISTS files (
                    id BIGSERIAL PRIMARY KEY,
                    user_id BIGINT NOT NULL,
                    doc_id TEXT UNIQUE NOT NULL,
                    filename TEXT NOT NULL,
                    file_path TEXT NOT NULL,
                    file_type TEXT NOT NULL,
                    content_hash TEXT,
                    chunks_indexed INTEGER NOT NULL,
                    uploaded_at TEXT NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                )'''
            )
            c.execute(
                'CREATE UNIQUE INDEX IF NOT EXISTS idx_files_user_content_hash '
                'ON files(user_id, content_hash) WHERE content_hash IS NOT NULL'
            )

            c.execute(
                '''CREATE TABLE IF NOT EXISTS conversations (
                    id TEXT PRIMARY KEY,
                    user_id BIGINT NOT NULL,
                    title TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                )'''
            )

            c.execute(
                '''CREATE TABLE IF NOT EXISTS conversation_messages (
                    id BIGSERIAL PRIMARY KEY,
                    conversation_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    sources_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    image_base64 TEXT,
                    FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
                )'''
            )
            c.execute(
                '''CREATE TABLE IF NOT EXISTS evaluation_runs (
                    id BIGSERIAL PRIMARY KEY,
                    admin_user_id BIGINT NOT NULL,
                    dataset_path TEXT NOT NULL,
                    output_path TEXT,
                    samples INTEGER NOT NULL,
                    total_rows INTEGER NOT NULL,
                    max_rows INTEGER NOT NULL,
                    truncated BOOLEAN NOT NULL DEFAULT FALSE,
                    use_rerank BOOLEAN NOT NULL DEFAULT TRUE,
                    summary_json TEXT NOT NULL,
                    report_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (admin_user_id) REFERENCES users(id) ON DELETE CASCADE
                )'''
            )
            c.execute('ALTER TABLE users ADD COLUMN IF NOT EXISTS is_admin BOOLEAN DEFAULT FALSE')
            c.execute('ALTER TABLE files ADD COLUMN IF NOT EXISTS content_hash TEXT')
            c.execute('ALTER TABLE conversation_messages ADD COLUMN IF NOT EXISTS image_base64 TEXT')
            c.execute('ALTER TABLE evaluation_runs ADD COLUMN IF NOT EXISTS use_rerank BOOLEAN NOT NULL DEFAULT TRUE')
        conn.commit()


@contextmanager
def get_db():
    conn = _connect()
    try:
        yield conn
    finally:
        conn.close()


def create_user(username: str, password_hash: str, is_admin: bool = False) -> int:
    with get_db() as conn:
        with conn.cursor() as c:
            c.execute(
                'INSERT INTO users (username, password_hash, is_admin, created_at) VALUES (%s, %s, %s, %s) RETURNING id',
                (username, password_hash, is_admin, datetime.now(timezone.utc).isoformat()),
            )
            user_id = c.fetchone()['id']
        conn.commit()
        return int(user_id)


def get_user_by_username(username: str):
    with get_db() as conn:
        with conn.cursor() as c:
            c.execute('SELECT * FROM users WHERE username = %s', (username,))
            return c.fetchone()


def upsert_admin_user(username: str, password_hash: str) -> int:
    with get_db() as conn:
        with conn.cursor() as c:
            c.execute(
                '''INSERT INTO users (username, password_hash, is_admin, created_at)
                   VALUES (%s, %s, TRUE, %s)
                   ON CONFLICT (username) DO UPDATE SET
                       password_hash = EXCLUDED.password_hash,
                       is_admin = TRUE
                   RETURNING id''',
                (username, password_hash, datetime.now(timezone.utc).isoformat()),
            )
            user_id = c.fetchone()['id']
        conn.commit()
        return int(user_id)


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def create_file_record(
    user_id: int,
    doc_id: str,
    filename: str,
    file_path: str,
    file_type: str,
    chunks: int,
    content_hash: str | None = None,
):
    with get_db() as conn:
        with conn.cursor() as c:
            c.execute(
                '''INSERT INTO files (user_id, doc_id, filename, file_path, file_type, content_hash, chunks_indexed, uploaded_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id''',
                (user_id, doc_id, filename, file_path, file_type, content_hash, chunks, datetime.now(timezone.utc).isoformat()),
            )
            file_id = c.fetchone()['id']
        conn.commit()
        return int(file_id)


def get_user_file_by_content_hash(user_id: int, content_hash: str):
    with get_db() as conn:
        with conn.cursor() as c:
            c.execute('SELECT * FROM files WHERE user_id = %s AND content_hash = %s', (user_id, content_hash))
            row = c.fetchone()
            return dict(row) if row else None


def get_user_files(user_id: int):
    with get_db() as conn:
        with conn.cursor() as c:
            c.execute('SELECT * FROM files WHERE user_id = %s ORDER BY uploaded_at DESC', (user_id,))
            return [dict(row) for row in c.fetchall()]


def get_all_admin_files():
    with get_db() as conn:
        with conn.cursor() as c:
            c.execute(
                '''SELECT f.* FROM files f
                   JOIN users u ON f.user_id = u.id
                   WHERE u.is_admin = TRUE
                   ORDER BY f.uploaded_at DESC'''
            )
            return [dict(row) for row in c.fetchall()]


def get_admin_user_ids() -> list[int]:
    with get_db() as conn:
        with conn.cursor() as c:
            c.execute('SELECT id FROM users WHERE is_admin = TRUE ORDER BY id ASC')
            return [int(row['id']) for row in c.fetchall()]


def get_admin_file_by_id(file_id: int):
    with get_db() as conn:
        with conn.cursor() as c:
            c.execute(
                '''SELECT f.* FROM files f
                   JOIN users u ON f.user_id = u.id
                   WHERE f.id = %s AND u.is_admin = TRUE''',
                (file_id,),
            )
            row = c.fetchone()
            return dict(row) if row else None


def delete_file_record(file_id: int, user_id: int):
    with get_db() as conn:
        with conn.cursor() as c:
            c.execute('SELECT * FROM files WHERE id = %s AND user_id = %s', (file_id, user_id))
            file = c.fetchone()
            if file:
                c.execute('DELETE FROM files WHERE id = %s', (file_id,))
                conn.commit()
                return dict(file)
    return None


def delete_admin_file_record(file_id: int):
    with get_db() as conn:
        with conn.cursor() as c:
            c.execute(
                '''SELECT f.* FROM files f
                   JOIN users u ON f.user_id = u.id
                   WHERE f.id = %s AND u.is_admin = TRUE''',
                (file_id,),
            )
            file = c.fetchone()
            if file:
                c.execute('DELETE FROM files WHERE id = %s', (file_id,))
                conn.commit()
                return dict(file)
    return None


def create_conversation(user_id: int, title: str = 'New Chat'):
    now = datetime.now(timezone.utc).isoformat()
    conversation_id = uuid4().hex
    with get_db() as conn:
        with conn.cursor() as c:
            c.execute(
                '''INSERT INTO conversations (id, user_id, title, created_at, updated_at)
                   VALUES (%s, %s, %s, %s, %s)''',
                (conversation_id, user_id, title, now, now),
            )
        conn.commit()
    return get_conversation(conversation_id, user_id)


def get_conversation(conversation_id: str, user_id: int):
    with get_db() as conn:
        with conn.cursor() as c:
            c.execute('SELECT * FROM conversations WHERE id = %s AND user_id = %s', (conversation_id, user_id))
            conversation = c.fetchone()
            if not conversation:
                return None

            c.execute(
                '''SELECT * FROM conversation_messages
                   WHERE conversation_id = %s
                   ORDER BY created_at ASC, id ASC''',
                (conversation_id,),
            )
            messages = []
            for row in c.fetchall():
                item = dict(row)
                item['sources'] = json.loads(item.pop('sources_json') or '[]')
                messages.append(item)

            data = dict(conversation)
            data['messages'] = messages
            return data


def get_user_conversations(user_id: int):
    with get_db() as conn:
        with conn.cursor() as c:
            c.execute(
                '''SELECT * FROM conversations
                   WHERE user_id = %s
                   ORDER BY updated_at DESC, created_at DESC''',
                (user_id,),
            )
            conversations = [dict(row) for row in c.fetchall()]
            for conversation in conversations:
                c.execute(
                    '''SELECT * FROM conversation_messages
                       WHERE conversation_id = %s
                       ORDER BY created_at ASC, id ASC''',
                    (conversation['id'],),
                )
                messages = []
                for row in c.fetchall():
                    item = dict(row)
                    item['sources'] = json.loads(item.pop('sources_json') or '[]')
                    messages.append(item)
                conversation['messages'] = messages
            return conversations


def delete_conversation(conversation_id: str, user_id: int):
    with get_db() as conn:
        with conn.cursor() as c:
            c.execute('SELECT * FROM conversations WHERE id = %s AND user_id = %s', (conversation_id, user_id))
            conversation = c.fetchone()
            if not conversation:
                return None
            c.execute('DELETE FROM conversations WHERE id = %s', (conversation_id,))
        conn.commit()
        return dict(conversation)


def append_conversation_message(
    conversation_id: str,
    role: str,
    content: str,
    sources: list[dict] | None = None,
    image_base64: str | None = None,
):
    now = datetime.now(timezone.utc).isoformat()
    with get_db() as conn:
        with conn.cursor() as c:
            c.execute(
                '''INSERT INTO conversation_messages (conversation_id, role, content, sources_json, created_at, image_base64)
                   VALUES (%s, %s, %s, %s, %s, %s) RETURNING id''',
                (conversation_id, role, content, json.dumps(sources or []), now, image_base64),
            )
            message_id = c.fetchone()['id']
            c.execute('UPDATE conversations SET updated_at = %s WHERE id = %s', (now, conversation_id))
        conn.commit()
        return int(message_id)


def count_conversation_messages(conversation_id: str) -> int:
    with get_db() as conn:
        with conn.cursor() as c:
            c.execute('SELECT COUNT(*) AS count FROM conversation_messages WHERE conversation_id = %s', (conversation_id,))
            row = c.fetchone()
            return int(row['count']) if row else 0


def update_conversation_title(conversation_id: str, user_id: int, title: str):
    now = datetime.now(timezone.utc).isoformat()
    with get_db() as conn:
        with conn.cursor() as c:
            c.execute(
                'UPDATE conversations SET title = %s, updated_at = %s WHERE id = %s AND user_id = %s',
                (title, now, conversation_id, user_id),
            )
        conn.commit()


def get_recent_chat_pairs(limit: int = 100):
    with get_db() as conn:
        with conn.cursor() as c:
            c.execute(
                '''
                SELECT
                    um.id AS user_message_id,
                    am.id AS assistant_message_id,
                    c.id AS conversation_id,
                    u.username,
                    um.content AS question,
                    am.content AS answer,
                    am.created_at AS created_at
                FROM conversation_messages um
                JOIN LATERAL (
                    SELECT id, content, created_at
                    FROM conversation_messages
                    WHERE conversation_id = um.conversation_id
                      AND role = 'assistant'
                      AND id > um.id
                    ORDER BY id ASC
                    LIMIT 1
                ) am ON TRUE
                JOIN conversations c ON c.id = um.conversation_id
                JOIN users u ON u.id = c.user_id
                WHERE um.role = 'user'
                ORDER BY am.created_at DESC, am.id DESC
                LIMIT %s
                ''',
                (limit,),
            )
            return [dict(row) for row in c.fetchall()]


def create_evaluation_run(admin_user_id: int, report: dict):
    now = datetime.now(timezone.utc).isoformat()
    with get_db() as conn:
        with conn.cursor() as c:
            c.execute(
                '''INSERT INTO evaluation_runs (
                       admin_user_id, dataset_path, output_path, samples, total_rows, max_rows,
                       truncated, use_rerank, summary_json, report_json, created_at
                   )
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                   RETURNING id''',
                (
                    admin_user_id,
                    str(report.get('dataset_path') or ''),
                    report.get('output_path'),
                    int(report.get('samples') or 0),
                    int(report.get('total_rows') or 0),
                    int(report.get('max_rows') or 0),
                    bool(report.get('truncated', False)),
                    bool(report.get('use_rerank', True)),
                    json.dumps(report.get('summary') or {}),
                    json.dumps(report),
                    now,
                ),
            )
            row = c.fetchone()
        conn.commit()
        return int(row['id']) if row else None


def get_latest_evaluation_run(admin_user_id: int):
    with get_db() as conn:
        with conn.cursor() as c:
            c.execute(
                '''SELECT * FROM evaluation_runs
                   WHERE admin_user_id = %s
                   ORDER BY created_at DESC, id DESC
                   LIMIT 1''',
                (admin_user_id,),
            )
            row = c.fetchone()
            if not row:
                return None
            data = dict(row)
            data['summary'] = json.loads(data.pop('summary_json') or '{}')
            data['report'] = json.loads(data.pop('report_json') or '{}')
            return data


def get_admin_settings() -> dict:
    with get_db() as conn:
        with conn.cursor() as c:
            c.execute('SELECT chat_model, image_model FROM admin_settings WHERE id = 1')
            row = c.fetchone()
            if row:
                return {'chatModel': row['chat_model'], 'imageModel': row['image_model']}
            return {'chatModel': 'gpt-4o-mini', 'imageModel': 'gpt-4o-mini'}


def save_admin_settings(settings: dict):
    now = datetime.now(timezone.utc).isoformat()
    with get_db() as conn:
        with conn.cursor() as c:
            c.execute(
                '''INSERT INTO admin_settings (id, chat_model, image_model, updated_at)
                   VALUES (1, %s, %s, %s)
                   ON CONFLICT (id) DO UPDATE SET
                       chat_model = EXCLUDED.chat_model,
                       image_model = EXCLUDED.image_model,
                       updated_at = EXCLUDED.updated_at''',
                (settings.get('chatModel', 'gpt-4o-mini'), settings.get('imageModel', 'gpt-4o-mini'), now),
            )
        conn.commit()
