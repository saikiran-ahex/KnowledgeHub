import sqlite3
from pathlib import Path

import psycopg

from app import database


SQLITE_DB_PATH = Path('data/app.db')


def _sqlite_table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def _sqlite_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    rows = conn.execute(f'PRAGMA table_info({table_name})').fetchall()
    return {str(row[1]) for row in rows}


def _set_sequence(conn: psycopg.Connection, table_name: str, column_name: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT setval(pg_get_serial_sequence('{table_name}', %s), COALESCE((SELECT MAX({column_name}) FROM {table_name}), 1), true)",
            (column_name,),
        )


def migrate(sqlite_db_path: Path = SQLITE_DB_PATH) -> None:
    if not sqlite_db_path.exists():
        raise FileNotFoundError(f'SQLite database not found: {sqlite_db_path}')

    database.init_db()

    sqlite_conn = sqlite3.connect(sqlite_db_path)
    sqlite_conn.row_factory = sqlite3.Row

    with database.get_db() as pg_conn:
        with pg_conn.cursor() as pg:
            if _sqlite_table_exists(sqlite_conn, 'users'):
                users = sqlite_conn.execute('SELECT id, username, password_hash, created_at FROM users ORDER BY id').fetchall()
                for row in users:
                    pg.execute(
                        '''INSERT INTO users (id, username, password_hash, created_at)
                           VALUES (%s, %s, %s, %s)
                           ON CONFLICT (id) DO UPDATE SET
                               username = EXCLUDED.username,
                               password_hash = EXCLUDED.password_hash,
                               created_at = EXCLUDED.created_at''',
                        (row['id'], row['username'], row['password_hash'], row['created_at']),
                    )

            if _sqlite_table_exists(sqlite_conn, 'files'):
                file_columns = _sqlite_columns(sqlite_conn, 'files')
                files = sqlite_conn.execute('SELECT * FROM files ORDER BY id').fetchall()
                for row in files:
                    content_hash = row['content_hash'] if 'content_hash' in file_columns else None
                    pg.execute(
                        '''INSERT INTO files (id, user_id, doc_id, filename, file_path, file_type, content_hash, chunks_indexed, uploaded_at)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                           ON CONFLICT (id) DO UPDATE SET
                               user_id = EXCLUDED.user_id,
                               doc_id = EXCLUDED.doc_id,
                               filename = EXCLUDED.filename,
                               file_path = EXCLUDED.file_path,
                               file_type = EXCLUDED.file_type,
                               content_hash = EXCLUDED.content_hash,
                               chunks_indexed = EXCLUDED.chunks_indexed,
                               uploaded_at = EXCLUDED.uploaded_at''',
                        (
                            row['id'],
                            row['user_id'],
                            row['doc_id'],
                            row['filename'],
                            row['file_path'],
                            row['file_type'],
                            content_hash,
                            row['chunks_indexed'],
                            row['uploaded_at'],
                        ),
                    )

            if _sqlite_table_exists(sqlite_conn, 'conversations'):
                conversations = sqlite_conn.execute(
                    'SELECT id, user_id, title, created_at, updated_at FROM conversations ORDER BY created_at, id'
                ).fetchall()
                for row in conversations:
                    pg.execute(
                        '''INSERT INTO conversations (id, user_id, title, created_at, updated_at)
                           VALUES (%s, %s, %s, %s, %s)
                           ON CONFLICT (id) DO UPDATE SET
                               user_id = EXCLUDED.user_id,
                               title = EXCLUDED.title,
                               created_at = EXCLUDED.created_at,
                               updated_at = EXCLUDED.updated_at''',
                        (row['id'], row['user_id'], row['title'], row['created_at'], row['updated_at']),
                    )

            if _sqlite_table_exists(sqlite_conn, 'conversation_messages'):
                messages = sqlite_conn.execute(
                    'SELECT id, conversation_id, role, content, sources_json, created_at FROM conversation_messages ORDER BY id'
                ).fetchall()
                for row in messages:
                    pg.execute(
                        '''INSERT INTO conversation_messages (id, conversation_id, role, content, sources_json, created_at)
                           VALUES (%s, %s, %s, %s, %s, %s)
                           ON CONFLICT (id) DO UPDATE SET
                               conversation_id = EXCLUDED.conversation_id,
                               role = EXCLUDED.role,
                               content = EXCLUDED.content,
                               sources_json = EXCLUDED.sources_json,
                               created_at = EXCLUDED.created_at''',
                        (
                            row['id'],
                            row['conversation_id'],
                            row['role'],
                            row['content'],
                            row['sources_json'],
                            row['created_at'],
                        ),
                    )

        _set_sequence(pg_conn, 'users', 'id')
        _set_sequence(pg_conn, 'files', 'id')
        _set_sequence(pg_conn, 'conversation_messages', 'id')
        pg_conn.commit()

    sqlite_conn.close()


if __name__ == '__main__':
    migrate()
    print('SQLite to PostgreSQL migration completed.')
