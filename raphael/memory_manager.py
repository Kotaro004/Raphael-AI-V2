import json
import sqlite3
import unicodedata
from contextlib import contextmanager
from datetime import datetime, timezone

VALID_CATEGORIES = {"personal", "project", "contextual", "decision", "general"}
VALID_STATUSES = {"pending", "active"}

def utc_now():
    return datetime.now(timezone.utc).isoformat()

def normalize_key(value):
    value = unicodedata.normalize("NFKC", " ".join(value.split())).casefold()
    return value

@contextmanager
def connect(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def ensure_schema(conn):
    columns = {row[1] for row in conn.execute("PRAGMA table_info(memories)").fetchall()}
    if not columns:
        conn.execute("""CREATE TABLE memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fact TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            category TEXT NOT NULL DEFAULT 'general',
            source TEXT NOT NULL DEFAULT 'owner',
            confidence REAL NOT NULL DEFAULT 1.0,
            status TEXT NOT NULL DEFAULT 'pending',
            memory_key TEXT NOT NULL,
            expires_at TEXT,
            supersedes_id INTEGER REFERENCES memories(id) ON DELETE SET NULL
        )""")
        columns = {row[1] for row in conn.execute("PRAGMA table_info(memories)").fetchall()}
    additions = {
        "updated_at": "TEXT",
        "category": "TEXT NOT NULL DEFAULT 'general'",
        "source": "TEXT NOT NULL DEFAULT 'legacy'",
        "confidence": "REAL NOT NULL DEFAULT 0.5",
        "status": "TEXT NOT NULL DEFAULT 'pending'",
        "memory_key": "TEXT",
        "expires_at": "TEXT",
        "supersedes_id": "INTEGER REFERENCES memories(id) ON DELETE SET NULL",
        "valid_from": "TEXT",
        "valid_until": "TEXT",
    }
    for name, definition in additions.items():
        if name not in columns:
            conn.execute(f"ALTER TABLE memories ADD COLUMN {name} {definition}")
    rows = conn.execute("SELECT id, fact, created_at, updated_at, memory_key FROM memories").fetchall()
    for row in rows:
        conn.execute(
            "UPDATE memories SET updated_at = COALESCE(updated_at, ?), memory_key = COALESCE(memory_key, ?) WHERE id = ?",
            (row["created_at"], normalize_key(row["fact"]), row["id"]),
        )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_memories_status_category ON memories(status, category)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_memories_key ON memories(memory_key)")
    conn.execute("""CREATE TABLE IF NOT EXISTS memory_proposals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        existing_memory_id INTEGER NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
        proposed_fact TEXT NOT NULL,
        proposed_key TEXT NOT NULL,
        reason TEXT NOT NULL,
        similarity REAL NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',
        created_at TEXT NOT NULL,
        decided_at TEXT
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_memory_proposals_status ON memory_proposals(status, created_at)")
    conn.execute("""CREATE TABLE IF NOT EXISTS memory_relationships (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source_memory_id INTEGER NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
        target_memory_id INTEGER NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
        relation TEXT NOT NULL,
        created_at TEXT NOT NULL,
        UNIQUE(source_memory_id, target_memory_id, relation),
        CHECK(source_memory_id != target_memory_id)
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS memory_audit (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        memory_id INTEGER,
        action TEXT NOT NULL,
        before_json TEXT,
        after_json TEXT,
        actor TEXT NOT NULL,
        created_at TEXT NOT NULL
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_memory_relationship_source ON memory_relationships(source_memory_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_memory_audit_memory ON memory_audit(memory_id, created_at)")


def initialize(db_path):
    with connect(db_path) as conn:
        ensure_schema(conn)

def serialize(row):
    return {key: row[key] for key in row.keys()}

def list_memories(db_path, status=None, category=None):
    clauses, values = [], []
    if status is not None:
        if status not in VALID_STATUSES:
            raise ValueError("Estado de memoria no válido.")
        clauses.append("status = ?")
        values.append(status)
    if category is not None:
        if category not in VALID_CATEGORIES:
            raise ValueError("Categoría de memoria no válida.")
        clauses.append("category = ?")
        values.append(category)
    sql = "SELECT * FROM memories"
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY updated_at DESC, id DESC"
    with connect(db_path) as conn:
        return [serialize(row) for row in conn.execute(sql, values).fetchall()]

def create_memory(db_path, fact, category="general", source="owner", confidence=1.0, status="pending", expires_at=None, valid_from=None, valid_until=None):
    fact = " ".join((fact or "").split()).strip()
    if not fact or len(fact) > 2000:
        raise ValueError("El recuerdo debe contener entre 1 y 2000 caracteres.")
    if category not in VALID_CATEGORIES or status not in VALID_STATUSES:
        raise ValueError("Categoría o estado no válido.")
    confidence = float(confidence)
    if not 0.0 <= confidence <= 1.0:
        raise ValueError("La confianza debe estar entre 0 y 1.")
    _validate_timestamp(expires_at, "expires_at")
    _validate_timestamp(valid_from, "valid_from")
    _validate_timestamp(valid_until, "valid_until")
    if valid_from and valid_until and datetime.fromisoformat(valid_until.replace("Z", "+00:00")) <= datetime.fromisoformat(valid_from.replace("Z", "+00:00")):
        raise ValueError("valid_until debe ser posterior a valid_from.")
    now = utc_now()
    key = normalize_key(fact)
    with connect(db_path) as conn:
        existing = conn.execute("SELECT id FROM memories WHERE memory_key = ? AND status = ? LIMIT 1", (key, status)).fetchone()
        if existing:
            raise ValueError("Ya existe un recuerdo igual con ese estado.")
        cur = conn.execute(
            "INSERT INTO memories (fact, created_at, updated_at, category, source, confidence, status, memory_key, expires_at, valid_from, valid_until) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (fact, now, now, category, source[:80], confidence, status, key, expires_at, valid_from or now, valid_until),
        )
        memory_id = cur.lastrowid
        conn.execute(
            "INSERT INTO memory_audit (memory_id, action, before_json, after_json, actor, created_at) VALUES (?, 'memory_created', NULL, ?, ?, ?)",
            (memory_id, json.dumps({"fact": fact, "category": category, "status": status}, ensure_ascii=False), source[:80], now),
        )
        return memory_id

_UNSET = object()

def _validate_timestamp(value, field):
    if value is None:
        return
    if not isinstance(value, str):
        raise ValueError(f"{field} debe ser una fecha ISO 8601.")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{field} debe ser una fecha ISO 8601.") from error
    if parsed.tzinfo is None:
        raise ValueError(f"{field} debe incluir zona horaria.")


def update_memory(db_path, memory_id, fact=None, category=None, confidence=None, expires_at=_UNSET, status=None):
    with connect(db_path) as conn:
        row = conn.execute("SELECT * FROM memories WHERE id = ?", (memory_id,)).fetchone()
        if row is None:
            raise LookupError("Recuerdo no encontrado.")
        new_fact = row["fact"] if fact is None else " ".join(fact.split()).strip()
        new_category = row["category"] if category is None else category
        new_confidence = row["confidence"] if confidence is None else float(confidence)
        new_status = row["status"] if status is None else status
        new_expires_at = row["expires_at"] if expires_at is _UNSET else expires_at
        _validate_timestamp(new_expires_at, "expires_at")
        if not new_fact or len(new_fact) > 2000 or new_category not in VALID_CATEGORIES or new_status not in VALID_STATUSES or not 0 <= new_confidence <= 1:
            raise ValueError("Contenido, categoría, estado o confianza no válidos.")
        key = normalize_key(new_fact)
        duplicate = conn.execute("SELECT id FROM memories WHERE memory_key = ? AND status = ? AND id != ? LIMIT 1", (key, new_status, memory_id)).fetchone()
        if duplicate:
            raise ValueError("Ya existe otro recuerdo igual con ese estado.")
        before = serialize(row)
        now = utc_now()
        conn.execute(
            "UPDATE memories SET fact = ?, memory_key = ?, category = ?, confidence = ?, expires_at = ?, status = ?, updated_at = ? WHERE id = ?",
            (new_fact, key, new_category, new_confidence, new_expires_at, new_status, now, memory_id),
        )
        after = dict(before, fact=new_fact, memory_key=key, category=new_category, confidence=new_confidence, expires_at=new_expires_at, status=new_status, updated_at=now)
        if before != after:
            conn.execute(
                "INSERT INTO memory_audit (memory_id, action, before_json, after_json, actor, created_at) VALUES (?, 'memory_updated', ?, ?, 'owner', ?)",
                (memory_id, json.dumps(before, ensure_ascii=False), json.dumps(after, ensure_ascii=False), now),
            )


def set_memory_status(db_path, memory_id, status):
    if status not in VALID_STATUSES:
        raise ValueError("Estado de memoria no válido.")
    update_memory(db_path, memory_id, status=status)


def delete_memory(db_path, memory_id):
    with connect(db_path) as conn:
        row = conn.execute("SELECT * FROM memories WHERE id = ?", (memory_id,)).fetchone()
        if row is None:
            raise LookupError("Recuerdo no encontrado.")
        now = utc_now()
        conn.execute(
            "INSERT INTO memory_audit (memory_id, action, before_json, after_json, actor, created_at) VALUES (?, 'memory_deleted', ?, NULL, 'owner', ?)",
            (memory_id, json.dumps(serialize(row), ensure_ascii=False), now),
        )
        conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))

def active_memory_facts(db_path):
    now = utc_now()
    with connect(db_path) as conn:
        rows = conn.execute(
            """SELECT fact FROM memories
            WHERE status = 'active'
              AND (expires_at IS NULL OR expires_at > ?)
              AND (valid_from IS NULL OR valid_from <= ?)
              AND (valid_until IS NULL OR valid_until > ?)
            ORDER BY updated_at DESC, id DESC""",
            (now, now, now),
        ).fetchall()
        return [row["fact"] for row in rows]


def _tokens(value):
    return {token for token in normalize_key(value).split() if len(token) > 2}


def _similarity(left, right):
    a, b = _tokens(left), _tokens(right)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def find_memory_conflicts(db_path, fact, category=None, threshold=0.28):
    clean = " ".join((fact or "").split()).strip()
    if not clean:
        return []
    if not 0.0 <= threshold <= 1.0:
        raise ValueError("Umbral de similitud no válido.")
    now = utc_now()
    clauses = [
        "status = 'active'",
        "(expires_at IS NULL OR expires_at > ?)",
        "(valid_from IS NULL OR valid_from <= ?)",
        "(valid_until IS NULL OR valid_until > ?)",
    ]
    values = [now, now, now]
    if category is not None:
        if category not in VALID_CATEGORIES:
            raise ValueError("Categoría de memoria no válida.")
        clauses.append("category = ?")
        values.append(category)
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM memories WHERE " + " AND ".join(clauses),
            values,
        ).fetchall()
    matches = []
    for row in rows:
        score = _similarity(clean, row["fact"])
        if score >= threshold and normalize_key(clean) != row["memory_key"]:
            matches.append({
                "memory": serialize(row),
                "similarity": round(score, 4),
                "possible_conflict": True,
            })
    return sorted(matches, key=lambda item: item["similarity"], reverse=True)


def create_memory_proposal(db_path, existing_memory_id, proposed_fact, reason="Posible actualización detectada", similarity=0.0):
    clean = " ".join((proposed_fact or "").split()).strip()
    if not clean or len(clean) > 2000:
        raise ValueError("La propuesta debe contener entre 1 y 2000 caracteres.")
    if not 0.0 <= float(similarity) <= 1.0:
        raise ValueError("La similitud debe estar entre 0 y 1.")
    now = utc_now()
    with connect(db_path) as conn:
        existing = conn.execute(
            "SELECT id, status FROM memories WHERE id = ?", (existing_memory_id,)
        ).fetchone()
        if existing is None or existing["status"] != "active":
            raise LookupError("El recuerdo original no existe o ya no está activo.")
        duplicate = conn.execute(
            "SELECT id FROM memory_proposals WHERE existing_memory_id = ? AND proposed_key = ? AND status = 'pending'",
            (existing_memory_id, normalize_key(clean)),
        ).fetchone()
        if duplicate:
            return duplicate["id"]
        cur = conn.execute(
            """INSERT INTO memory_proposals
            (existing_memory_id, proposed_fact, proposed_key, reason, similarity, status, created_at)
            VALUES (?, ?, ?, ?, ?, 'pending', ?)""",
            (existing_memory_id, clean, normalize_key(clean), str(reason)[:500], float(similarity), now),
        )
        return cur.lastrowid


def list_memory_proposals(db_path, status="pending"):
    if status not in {"pending", "approved", "rejected"}:
        raise ValueError("Estado de propuesta no válido.")
    with connect(db_path) as conn:
        rows = conn.execute(
            """SELECT p.*, m.fact AS existing_fact, m.category AS existing_category
            FROM memory_proposals p JOIN memories m ON m.id = p.existing_memory_id
            WHERE p.status = ? ORDER BY p.created_at DESC, p.id DESC""",
            (status,),
        ).fetchall()
        return [serialize(row) for row in rows]


def decide_memory_proposal(db_path, proposal_id, decision):
    if decision not in {"approved", "rejected"}:
        raise ValueError("Decisión no válida.")
    now = utc_now()
    with connect(db_path) as conn:
        proposal = conn.execute(
            "SELECT * FROM memory_proposals WHERE id = ?", (proposal_id,)
        ).fetchone()
        if proposal is None:
            raise LookupError("Propuesta no encontrada.")
        if proposal["status"] != "pending":
            raise ValueError("La propuesta ya fue resuelta.")
        if decision == "rejected":
            conn.execute(
                "UPDATE memory_proposals SET status = 'rejected', decided_at = ? WHERE id = ?",
                (now, proposal_id),
            )
            conn.execute(
                """INSERT INTO memory_audit (memory_id, action, before_json, after_json, actor, created_at)
                VALUES (?, 'memory_proposal_rejected', ?, ?, 'owner', ?)""",
                (proposal["existing_memory_id"],
                 json.dumps({"proposal_id": proposal_id, "proposed_fact": proposal["proposed_fact"]}, ensure_ascii=False),
                 json.dumps({"decision": "rejected"}, ensure_ascii=False), now),
            )
            return None
        old = conn.execute(
            "SELECT * FROM memories WHERE id = ?", (proposal["existing_memory_id"],)
        ).fetchone()
        if old is None or old["status"] != "active":
            raise ValueError("El recuerdo original ya no está activo; revisa la propuesta.")
        new_key = proposal["proposed_key"]
        duplicate = conn.execute(
            "SELECT id FROM memories WHERE memory_key = ? AND status = 'active' AND id != ? LIMIT 1",
            (new_key, old["id"]),
        ).fetchone()
        if duplicate:
            raise ValueError("Ya existe otro recuerdo activo con ese contenido.")
        conn.execute(
            "UPDATE memories SET status = 'pending', valid_until = ?, updated_at = ? WHERE id = ?",
            (now, now, old["id"]),
        )
        cur = conn.execute(
            """INSERT INTO memories
            (fact, created_at, updated_at, category, source, confidence, status, memory_key, expires_at, supersedes_id, valid_from, valid_until)
            VALUES (?, ?, ?, ?, 'owner_approved_replacement', 1.0, 'active', ?, NULL, ?, ?, NULL)""",
            (proposal["proposed_fact"], now, now, old["category"], new_key, old["id"], now),
        )
        new_id = cur.lastrowid
        conn.execute(
            """INSERT INTO memory_audit (memory_id, action, before_json, after_json, actor, created_at)
            VALUES (?, 'memory_superseded', ?, ?, 'owner', ?)""",
            (old["id"], json.dumps(serialize(old), ensure_ascii=False),
             json.dumps({"replacement_id": new_id, "fact": proposal["proposed_fact"], "valid_from": now}, ensure_ascii=False), now),
        )
        conn.execute(
            """INSERT INTO memory_audit (memory_id, action, before_json, after_json, actor, created_at)
            VALUES (?, 'memory_created_as_replacement', NULL, ?, 'owner', ?)""",
            (new_id, json.dumps({"fact": proposal["proposed_fact"], "supersedes_id": old["id"], "valid_from": now}, ensure_ascii=False), now),
        )
        conn.execute(
            "UPDATE memory_proposals SET status = 'approved', decided_at = ? WHERE id = ?",
            (now, proposal_id),
        )
        return new_id


def retrieve_relevant_memories(db_path, query, limit=8):
    query_tokens = _tokens(query or "")
    if not query_tokens:
        return active_memory_facts(db_path)[:limit]
    now = utc_now()
    with connect(db_path) as conn:
        rows = conn.execute(
            """SELECT fact, category, confidence, updated_at FROM memories
            WHERE status = 'active'
              AND (expires_at IS NULL OR expires_at > ?)
              AND (valid_from IS NULL OR valid_from <= ?)
              AND (valid_until IS NULL OR valid_until > ?)""",
            (now, now, now),
        ).fetchall()
    scored = []
    category_boost = {"decision": 0.12, "project": 0.10, "personal": 0.06, "contextual": 0.04, "general": 0.0}
    for row in rows:
        tokens = _tokens(row["fact"])
        overlap = len(tokens & query_tokens) / max(1, len(query_tokens))
        score = overlap * float(row["confidence"]) + category_boost.get(row["category"], 0.0)
        if overlap > 0:
            scored.append((score, row["fact"]))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [fact for score, fact in scored[:max(0, limit)]]


def add_relationship(db_path, source_id, target_id, relation):
    allowed = {"related_to", "supports", "contradicts", "derived_from", "same_topic"}
    if relation not in allowed:
        raise ValueError("Tipo de relación no válido.")
    if source_id == target_id:
        raise ValueError("Un recuerdo no puede relacionarse consigo mismo.")
    with connect(db_path) as conn:
        ids = {r[0] for r in conn.execute("SELECT id FROM memories WHERE id IN (?, ?)", (source_id, target_id))}
        if ids != {source_id, target_id}:
            raise LookupError("Uno de los recuerdos no existe.")
        conn.execute("INSERT OR IGNORE INTO memory_relationships (source_memory_id, target_memory_id, relation, created_at) VALUES (?, ?, ?, ?)", (source_id, target_id, relation, utc_now()))
        conn.execute("INSERT INTO memory_audit (memory_id, action, after_json, actor, created_at) VALUES (?, 'relationship_added', ?, 'owner', ?)", (source_id, json.dumps({"target_id": target_id, "relation": relation}, ensure_ascii=False), utc_now()))


def list_relationships(db_path, memory_id):
    with connect(db_path) as conn:
        rows = conn.execute("""SELECT * FROM (
            SELECT r.id, r.relation, r.created_at, m.id AS related_id, m.fact AS related_fact, m.status AS related_status
            FROM memory_relationships r JOIN memories m ON m.id = r.target_memory_id
            WHERE r.source_memory_id = ?
            UNION ALL
            SELECT r.id, r.relation, r.created_at, m.id AS related_id, m.fact AS related_fact, m.status AS related_status
            FROM memory_relationships r JOIN memories m ON m.id = r.source_memory_id
            WHERE r.target_memory_id = ?
        ) ORDER BY created_at DESC, id DESC""", (memory_id, memory_id)).fetchall()
        return [serialize(row) for row in rows]


def record_audit(db_path, memory_id, action, before=None, after=None, actor="owner"):
    import json
    with connect(db_path) as conn:
        conn.execute("INSERT INTO memory_audit (memory_id, action, before_json, after_json, actor, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                     (memory_id, action, json.dumps(before, ensure_ascii=False) if before is not None else None,
                      json.dumps(after, ensure_ascii=False) if after is not None else None, actor, utc_now()))


def memory_history(db_path, memory_id):
    with connect(db_path) as conn:
        rows = conn.execute("SELECT * FROM memory_audit WHERE memory_id = ? ORDER BY created_at DESC, id DESC", (memory_id,)).fetchall()
        return [serialize(row) for row in rows]


def temporal_memories(db_path, include_expired=False):
    now = utc_now()
    clause = "" if include_expired else """WHERE (valid_from IS NULL OR valid_from <= ?)
        AND (valid_until IS NULL OR valid_until > ?)
        AND (expires_at IS NULL OR expires_at > ?)"""
    params = () if include_expired else (now, now, now)
    with connect(db_path) as conn:
        rows = conn.execute("SELECT * FROM memories " + clause + " ORDER BY COALESCE(valid_from, created_at) DESC, id DESC", params).fetchall()
        return [serialize(row) for row in rows]
