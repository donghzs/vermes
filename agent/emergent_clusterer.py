"""
agent/emergent_clusterer.py — 涌现聚类引擎

基于用户自身行为模式的自动聚类，从 raw_events 数据中涌现簇结构。
不预设簇数，不预设分类标签。簇命名由内部公共特征自动拼接。

算法: 纯 Python DBSCAN（无 sklearn 依赖，适配 Agent 轻量环境）
特征: 工具名 + 文件扩展名 + 命令前缀 + 时间分布
簇命名: 特征自拼接，如 "terminal:git+commit" 而非 "版本控制"
"""

from __future__ import annotations

import json
import logging
import math
import sqlite3
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


# ── Constants ────────────────────────────────────────────────────────────────

# Default DBSCAN parameters
DEFAULT_EPSILON = 0.35   # cosine distance threshold
DEFAULT_MIN_SAMPLES = 3  # minimum events per cluster (dynamic: max(3, total/50))

# Common file extensions we want to distinguish
COMMON_EXTENSIONS = {
    ".py", ".js", ".ts", ".tsx", ".jsx",   # code
    ".md", ".txt", ".rst", ".org",          # docs
    ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".env",  # config
    ".sh", ".bash", ".zsh", ".fish",        # shell
    ".html", ".css", ".scss", ".vue",       # web
    ".go", ".rs", ".c", ".cpp", ".h", ".java", ".rb",  # systems
    ".sql", ".db", ".sqlite",               # databases
    ".png", ".jpg", ".jpeg", ".gif", ".svg",  # images
    ".pdf", ".docx", ".xlsx", ".pptx",      # documents
}

# Common command prefixes (extracted from terminal args)
COMMON_COMMANDS = {
    "git", "npm", "yarn", "pnpm", "bun",
    "python", "python3", "pip", "pip3", "uv", "poetry", "conda",
    "docker", "podman", "kubectl", "kubectx",
    "curl", "wget", "httpie",
    "ssh", "scp", "rsync", "sftp",
    "make", "cmake", "cargo", "go", "dotnet",
    "node", "deno", "tsc", "eslint",
    "pytest", "unittest", "vitest", "jest",
    "systemctl", "service", "launchctl",
    "psql", "mysql", "redis-cli", "mongo",
    "vim", "nvim", "code", "emacs",
    "ls", "cat", "grep", "find", "sed", "awk",
    "tar", "zip", "unzip", "gzip",
    "brew", "apt", "apt-get", "yum", "dnf",
}


# ── Data Structures ──────────────────────────────────────────────────────────

@dataclass
class EventVector:
    """Feature vector for a single RawEvent.

    All features are computed from the raw event data without preset
    categories. The vector is a list of floats suitable for cosine distance.
    """
    event_id: int
    tool_name: str
    duration: float
    time_of_day: float       # 0.0 (midnight) to 1.0 (almost midnight)
    extensions: Dict[str, float] = field(default_factory=dict)
    commands: Dict[str, float] = field(default_factory=dict)
    is_error: bool = False

    def to_dense_vector(self, feature_names: List[str]) -> List[float]:
        """Convert to a dense float vector aligning to feature_names order."""
        vec = []
        for name in feature_names:
            if name.startswith("tool:"):
                vec.append(1.0 if self.tool_name == name[5:] else 0.0)
            elif name.startswith("ext:"):
                vec.append(self.extensions.get(name[4:], 0.0))
            elif name.startswith("cmd:"):
                vec.append(self.commands.get(name[4:], 0.0))
            elif name == "time_of_day":
                vec.append(self.time_of_day)
            elif name == "duration_log":
                vec.append(math.log1p(self.duration))
            elif name == "is_error":
                vec.append(1.0 if self.is_error else 0.0)
            else:
                vec.append(0.0)
        return vec


@dataclass
class Cluster:
    """A cluster of related events discovered by DBSCAN."""
    id: int
    name: str = ""
    event_ids: List[int] = field(default_factory=list)
    event_count: int = 0
    success_count: int = 0
    error_count: int = 0
    total_duration: float = 0.0
    first_seen: str = ""
    last_seen: str = ""
    last_active_at: str = ""
    lifecycle_stage: str = "emerging"  # emerging|stable|declining|dormant|dead
    feature_signature: str = ""
    parent_cluster_id: Optional[int] = None
    evolved_from: str = ""
    is_active: bool = True

    @property
    def success_rate(self) -> float:
        if self.event_count == 0:
            return 0.0
        return self.success_count / self.event_count

    @property
    def avg_duration(self) -> float:
        if self.event_count == 0:
            return 0.0
        return self.total_duration / self.event_count


# ── Feature Extraction ──────────────────────────────────────────────────────

def _extract_extensions(args_preview: str) -> Dict[str, float]:
    """Extract file extensions from args_preview."""
    exts = Counter()
    lower = args_preview.lower()
    for ext in COMMON_EXTENSIONS:
        if ext in lower:
            exts[ext] += 1
    # Normalize
    total = sum(exts.values()) or 1
    return {k: v / total for k, v in exts.items()}


def _extract_commands(args_preview: str) -> Dict[str, float]:
    """Extract command prefixes from terminal-style args_preview."""
    cmds = Counter()
    lower = args_preview.lower()

    # Try to parse as JSON command arg
    try:
        data = json.loads(args_preview)
        cmd = data.get("command", args_preview)
        if isinstance(cmd, str):
            lower = cmd.lower().strip()
    except (json.JSONDecodeError, TypeError, AttributeError):
        pass

    # Look for known commands
    words = lower.split()
    for cmd_word in words:
        cmd_clean = cmd_word.strip("'\"`|;&&$(){}[]<>!").split("/")[-1]
        if cmd_clean in COMMON_COMMANDS:
            cmds[cmd_clean] += 1

    total = sum(cmds.values()) or 1
    return {k: v / total for k, v in cmds.items()}


def extract_event_vector(row: sqlite3.Row) -> EventVector:
    """Extract feature vector from a raw_events DB row."""
    args = row["args_preview"] or ""
    timestamp = row["timestamp"] or ""

    # Time of day: 0.0 (00:00) ~ 1.0 (23:59:59)
    time_of_day = 0.5  # default
    if timestamp:
        try:
            dt = datetime.fromisoformat(timestamp)
            total_seconds = dt.hour * 3600 + dt.minute * 60 + dt.second
            time_of_day = total_seconds / 86400.0
        except (ValueError, TypeError):
            pass

    return EventVector(
        event_id=row["id"],
        tool_name=row["tool_name"] or "",
        duration=row["duration"] or 0.0,
        time_of_day=time_of_day,
        extensions=_extract_extensions(args),
        commands=_extract_commands(args),
        is_error=bool(row["success"] is not None and row["success"] == 0),
    )


def _collect_feature_space(vectors: List[EventVector]) -> List[str]:
    """Collect all feature dimensions from event vectors.

    Returns an ordered list of feature names like:
      ["tool:terminal", "tool:web_search", "ext:.py", "cmd:git", ...]
    """
    all_tools = set()
    all_exts = set()
    all_cmds = set()
    has_ext = False
    has_cmd = False

    for v in vectors:
        all_tools.add(v.tool_name)
        if v.extensions:
            has_ext = True
            all_exts.update(v.extensions.keys())
        if v.commands:
            has_cmd = True
            all_cmds.update(v.commands.keys())

    features = []
    for t in sorted(all_tools):
        features.append(f"tool:{t}")
    if has_ext:
        for e in sorted(all_exts):
            features.append(f"ext:{e}")
    if has_cmd:
        for c in sorted(all_cmds):
            features.append(f"cmd:{c}")
    features.extend(["time_of_day", "duration_log", "is_error"])
    return features


# ── DBSCAN Implementation ────────────────────────────────────────────────────

def _cosine_similarity(a: List[float], b: List[float]) -> float:
    """Cosine similarity between two float vectors."""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def _cosine_distance(a: List[float], b: List[float]) -> float:
    """Cosine distance (0 = identical, 1 = orthogonal, 2 = opposite)."""
    return 1.0 - _cosine_similarity(a, b)


def dbscan_cluster(
    vectors: List[EventVector],
    feature_names: List[str],
    eps: float = DEFAULT_EPSILON,
    min_samples: int = DEFAULT_MIN_SAMPLES,
) -> Tuple[List[int], List[List[EventVector]], List[EventVector]]:
    """Pure Python DBSCAN clustering.

    Args:
        vectors:      Event vectors to cluster
        feature_names: Ordered feature dimension names
        eps:          Epsilon threshold for cosine distance
        min_samples:   Minimum points to form a cluster

    Returns:
        (labels, clusters, noise):
          - labels: cluster assignment per vector (-1 = noise)
          - clusters: list of vector lists per cluster
          - noise: unclustered vectors
    """
    n = len(vectors)
    if n == 0:
        return [], [], []

    # Convert to dense vectors
    dense = [v.to_dense_vector(feature_names) for v in vectors]

    # Find neighbors for each point
    neighborhoods: List[Set[int]] = []
    for i in range(n):
        hood = set()
        for j in range(n):
            if i != j and _cosine_distance(dense[i], dense[j]) <= eps:
                hood.add(j)
        neighborhoods.append(hood)

    # DBSCAN core points
    labels = [-1] * n
    cluster_id = 0

    for i in range(n):
        if labels[i] != -1:
            continue

        if len(neighborhoods[i]) < min_samples:
            continue  # noise for now (may become border point later)

        # Start a new cluster from core point
        labels[i] = cluster_id
        seeds = set(neighborhoods[i])
        visited = {i}  # track expanded points to prevent infinite loops

        while seeds:
            j = seeds.pop()
            if j in visited:
                continue
            visited.add(j)

            if labels[j] == -1:
                labels[j] = cluster_id  # border point
            elif labels[j] != cluster_id:
                continue  # already assigned to another cluster
            else:
                labels[j] = cluster_id

            if len(neighborhoods[j]) >= min_samples:
                # Core point — expand: add unvisited neighbors
                for neighbor in neighborhoods[j]:
                    if neighbor not in visited:
                        seeds.add(neighbor)

        cluster_id += 1

    # Collect clusters and noise
    cluster_vectors: List[List[EventVector]] = [[] for _ in range(cluster_id)]
    noise_vectors: List[EventVector] = []

    for i, label in enumerate(labels):
        if label >= 0:
            cluster_vectors[label].append(vectors[i])
        else:
            noise_vectors.append(vectors[i])

    return labels, cluster_vectors, noise_vectors


# ── Cluster Naming ───────────────────────────────────────────────────────────

def _generate_cluster_name(event_vectors: List[EventVector]) -> str:
    """Auto-generate a human-readable cluster name from feature prevalence."""
    if not event_vectors:
        return "empty"

    # Count tools
    tools = Counter(v.tool_name for v in event_vectors)
    top_tools = [t for t, _ in tools.most_common(2) if t]

    # Count commands
    all_cmds = Counter()
    for v in event_vectors:
        all_cmds.update(v.commands)
    top_cmds = [c for c, _ in all_cmds.most_common(3)]

    # Count extensions
    all_exts = Counter()
    for v in event_vectors:
        all_exts.update(v.extensions)
    top_exts = [e for e, _ in all_exts.most_common(2)]

    # Build name: tool1+tool2:cmd1+cmd2:ext1+ext2
    parts = []
    if top_tools:
        parts.append("+".join(top_tools))
    if top_cmds:
        parts.append("+".join(top_cmds))
    if top_exts:
        parts.append("+".join(top_exts))

    return ":".join(parts) if parts else "misc"


def _generate_feature_signature(event_vectors: List[EventVector]) -> str:
    """Generate a compact feature signature for cluster matching."""
    parts = []
    for v in event_vectors:
        parts.append(v.tool_name)
    return "|".join(sorted(set(parts)))


# ── Cluster Matching ────────────────────────────────────────────────────────

@dataclass
class ClusterDelta:
    """Describes how clusters changed between runs."""
    new_clusters: List[Cluster] = field(default_factory=list)
    merged_clusters: List[Tuple[Cluster, List[Cluster]]] = field(default_factory=list)
    split_clusters: List[Tuple[Cluster, List[Cluster]]] = field(default_factory=list)
    dead_clusters: List[Cluster] = field(default_factory=list)
    stable_clusters: List[Tuple[Cluster, Cluster]] = field(default_factory=list)
    revived_clusters: List[Cluster] = field(default_factory=list)


def match_clusters(
    new_clusters: List[Cluster],
    old_clusters: List[Cluster],
) -> ClusterDelta:
    """Match new clusters against **all** previous clusters (active + dead).

    Key change: dead clusters are included in matching. If a new batch's
    feature_signature matches a dead cluster, the dead cluster is revived
    (added to revived_clusters) instead of creating a duplicate. This
    prevents the "same behavior → N duplicate dead clusters" problem.
    """
    delta = ClusterDelta()
    old_by_sig = {c.feature_signature: c for c in old_clusters}
    matched_old_ids: Set[int] = set()

    for nc in new_clusters:
        # Try exact match first (across ALL clusters, including dead)
        if nc.feature_signature in old_by_sig:
            oc = old_by_sig[nc.feature_signature]
            nc.parent_cluster_id = oc.id
            delta.stable_clusters.append((nc, oc))
            matched_old_ids.add(oc.id)
            # If the old cluster was dead, mark for revival
            if oc.lifecycle_stage == "dead" or not oc.is_active:
                delta.revived_clusters.append(oc)
            continue

        # Try partial match (Jaccard similarity of signatures)
        nc_sigs = set(nc.feature_signature.split("|"))
        best_match = None
        best_sim = 0.0

        for oc in old_clusters:
            if oc.id in matched_old_ids:
                continue
            oc_sigs = set(oc.feature_signature.split("|"))
            if not nc_sigs or not oc_sigs:
                continue
            intersection = nc_sigs & oc_sigs
            union = nc_sigs | oc_sigs
            sim = len(intersection) / len(union) if union else 0.0
            if sim > 0.3 and sim > best_sim:
                best_sim = sim
                best_match = oc

        if best_match:
            nc.parent_cluster_id = best_match.id
            nc.evolved_from = best_match.name
            delta.stable_clusters.append((nc, best_match))
            matched_old_ids.add(best_match.id)
            if best_match.lifecycle_stage == "dead":
                delta.revived_clusters.append(best_match)
        else:
            delta.new_clusters.append(nc)

    # Dead clusters: old clusters that were active but not matched this batch
    for oc in old_clusters:
        if oc.id not in matched_old_ids:
            # Only mark as dead if it was previously active
            if getattr(oc, "is_active", True) and oc.lifecycle_stage != "dead":
                delta.dead_clusters.append(oc)

    return delta


# ── SQLite Table Management ──────────────────────────────────────────────────

CLUSTERS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS clusters (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    name                TEXT NOT NULL DEFAULT '',
    feature_signature   TEXT DEFAULT '',
    event_count         INTEGER DEFAULT 0,
    success_count       INTEGER DEFAULT 0,
    error_count         INTEGER DEFAULT 0,
    total_duration      REAL DEFAULT 0.0,
    first_seen          TEXT DEFAULT '',
    last_seen           TEXT DEFAULT '',
    last_active_at      TEXT DEFAULT '',
    success_rate        REAL DEFAULT 0.0,
    avg_duration        REAL DEFAULT 0.0,
    is_active           INTEGER DEFAULT 1,
    lifecycle_stage     TEXT DEFAULT 'emerging',
    parent_cluster_id   INTEGER DEFAULT NULL,
    evolved_from        TEXT DEFAULT '',
    created_at          TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_clusters_active   ON clusters(is_active);
CREATE INDEX IF NOT EXISTS idx_clusters_stage    ON clusters(lifecycle_stage);
CREATE INDEX IF NOT EXISTS idx_clusters_parent   ON clusters(parent_cluster_id);

CREATE TABLE IF NOT EXISTS cluster_lifecycle_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp       TEXT NOT NULL DEFAULT (datetime('now')),
    cluster_id      INTEGER NOT NULL,
    from_stage      TEXT,
    to_stage        TEXT,
    reason          TEXT DEFAULT '',
    FOREIGN KEY (cluster_id) REFERENCES clusters(id)
);
CREATE INDEX IF NOT EXISTS idx_lifecycle_cluster ON cluster_lifecycle_events(cluster_id);
"""


def ensure_cluster_tables(conn: sqlite3.Connection) -> None:
    """Create cluster tables and indexes if they don't exist.

    Also runs lazy migrations for columns added after the initial schema.
    G5 fix: is_active column must exist for recurrence fix (commit 1580faef6)
    to work on legacy databases created before the column was in CREATE TABLE.
    """
    # G5: Check for legacy database missing is_active BEFORE executing
    # CLUSTERS_TABLE_SQL (which creates indexes referencing is_active).
    table_exists = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='clusters'"
    ).fetchone()
    if table_exists:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(clusters)").fetchall()]
        if "is_active" not in cols:
            conn.execute("ALTER TABLE clusters ADD COLUMN is_active INTEGER DEFAULT 1")
            conn.commit()
    conn.executescript(CLUSTERS_TABLE_SQL)
    conn.commit()


# ── Main Clustering Engine ───────────────────────────────────────────────────

class EmergentClusterer:
    """Clustering engine that discovers event clusters from raw data.

    No preset categories. No hardcoded labels. Everything emerges from
    the user's actual tool usage patterns.

    Usage:
        clusterer = EmergentClusterer(db_path)
        result = clusterer.run()

        # result contains:
        #   - clusters: List[Cluster] with auto-generated names
        #   - noise_events: unclustered events
        #   - delta: how clusters changed from last run
    """

    def __init__(
        self,
        db_path: str,
        eps: float = DEFAULT_EPSILON,
        min_samples: int = DEFAULT_MIN_SAMPLES,
    ):
        self.db_path = db_path
        self.eps = eps
        self.min_samples = min_samples

    def run(self) -> Dict[str, Any]:
        """Run the full clustering pipeline.

        Returns:
            {"clusters": [...], "noise_count": N, "delta": ClusterDelta, "update_stats": {...}}
        """
        start_time = time.time()

        # 1. Load unclustered events
        vectors = self._load_event_vectors()
        if not vectors:
            return {"clusters": [], "noise_count": 0, "delta": ClusterDelta(), "update_stats": {"events": 0, "time_ms": 0}}

        # 2. Build feature space
        feature_names = _collect_feature_space(vectors)

        # 3. Adaptive min_samples
        adaptive_min = max(self.min_samples, len(vectors) // 50)

        # 4. Run DBSCAN
        labels, cluster_vectors, noise_vectors = dbscan_cluster(
            vectors, feature_names, eps=self.eps, min_samples=adaptive_min,
        )

        # 5. Build Cluster objects
        new_clusters = self._build_clusters(cluster_vectors, labels, vectors)

        # 6. Match against old clusters
        old_clusters = self._load_old_clusters()
        delta = match_clusters(new_clusters, old_clusters)

        # 7. Save to DB (returns label→DB ID mapping)
        label_to_db_id = self._save_clusters(new_clusters, delta)

        # 8. Backfill raw_events.cluster_id using real DB IDs
        self._backfill_cluster_ids(vectors, labels, label_to_db_id)

        elapsed = (time.time() - start_time) * 1000

        return {
            "clusters": new_clusters,
            "noise_count": len(noise_vectors),
            "delta": delta,
            "update_stats": {
                "events": len(vectors),
                "clusters_found": len(new_clusters),
                "time_ms": round(elapsed, 1),
                "eps": self.eps,
                "min_samples": adaptive_min,
                "feature_dims": len(feature_names),
            },
        }

    def _load_event_vectors(self) -> List[EventVector]:
        """Load unclustered events from raw_events table."""
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row

            # Ensure table exists
            from agent.raw_event import ensure_raw_events_table
            ensure_raw_events_table(conn)

            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM raw_events WHERE cluster_id IS NULL ORDER BY id"
            )
            rows = cursor.fetchall()
            conn.close()
            return [extract_event_vector(r) for r in rows]
        except Exception:
            logger.debug("Failed to load event vectors", exc_info=True)
            return []

    def _load_old_clusters(self) -> List[Cluster]:
        """Load **all** clusters (active + dead) from previous runs.

        Previously only loaded is_active=1, which meant dead clusters
        could never be matched against — so a recurring behavior whose
        cluster happened to be marked dead in one batch would be
        permanently invisible and a duplicate new cluster would be created.
        Loading all clusters (including dead) allows match_clusters to
        revive them when the same feature_signature reappears.
        """
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            ensure_cluster_tables(conn)

            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM clusters ORDER BY id"
            )
            rows = cursor.fetchall()
            conn.close()

            return [_row_to_cluster(r) for r in rows]
        except Exception:
            return []

    def _build_clusters(
        self,
        cluster_vectors: List[List[EventVector]],
        labels: List[int],
        all_vectors: List[EventVector],
    ) -> List[Cluster]:
        """Build Cluster objects with stats from event vectors."""
        clusters = []

        for cid, cv_list in enumerate(cluster_vectors):
            if not cv_list:
                continue

            event_ids = [v.event_id for v in cv_list]
            timestamps = [
                v.time_of_day for v in cv_list
            ]

            # Compute stats
            now = datetime.now().isoformat()
            cluster = Cluster(
                id=cid,
                name=_generate_cluster_name(cv_list),
                feature_signature=_generate_feature_signature(cv_list),
                event_ids=event_ids,
                event_count=len(cv_list),
                success_count=sum(1 for v in cv_list if not v.is_error),
                error_count=sum(1 for v in cv_list if v.is_error),
                total_duration=sum(v.duration for v in cv_list),
                first_seen=now,
                last_seen=now,
                last_active_at=now,
                lifecycle_stage="emerging",
            )
            clusters.append(cluster)

        return clusters

    def _save_clusters(
        self,
        new_clusters: List[Cluster],
        delta: ClusterDelta,
    ) -> Dict[int, int]:
        """Save clusters to DB, updating existing and inserting new.

        Returns:
            label_to_db_id: mapping from in-memory cluster label (0-based)
                            to actual DB cluster ID.
        """
        label_to_db_id: Dict[int, int] = {}
        try:
            conn = sqlite3.connect(self.db_path)
            ensure_cluster_tables(conn)
            cursor = conn.cursor()
            now = datetime.now().isoformat()

            # Build a lookup from new_clusters list index (label) to Cluster object
            # new_clusters[i] corresponds to DBSCAN label i
            for label, nc in enumerate(new_clusters):
                # Check if this cluster was matched to an existing one
                matched = False
                for sc, _ in delta.stable_clusters:
                    if sc is nc:
                        if nc.parent_cluster_id:
                            self._upsert_cluster(cursor, nc, nc.parent_cluster_id, now)
                            label_to_db_id[label] = nc.id  # upsert preserves old ID
                        else:
                            self._insert_cluster(cursor, nc, now)
                            label_to_db_id[label] = nc.id
                        matched = True
                        break
                if not matched:
                    # Check if it's a new cluster
                    for nwc in delta.new_clusters:
                        if nwc is nc:
                            self._insert_cluster(cursor, nc, now)
                            label_to_db_id[label] = nc.id
                            break

            # Mark dead clusters as inactive (only those that were active
            # and didn't match any new batch)
            for dc in delta.dead_clusters:
                cursor.execute(
                    "UPDATE clusters SET is_active = 0, lifecycle_stage = 'dead' WHERE id = ?",
                    (dc.id,),
                )

            # Revive previously-dead clusters that matched a new batch
            for rc in delta.revived_clusters:
                cursor.execute(
                    "UPDATE clusters SET is_active = 1, lifecycle_stage = 'emerging' WHERE id = ?",
                    (rc.id,),
                )
                logger.info(
                    "Revived dead cluster id=%d name=%r (matched new batch)",
                    rc.id, rc.name,
                )

            conn.commit()
            conn.close()
        except Exception:
            logger.debug("Failed to save clusters", exc_info=True)

        return label_to_db_id

    def _insert_cluster(self, cursor, cluster: Cluster, now: str) -> None:
        cursor.execute(
            """INSERT INTO clusters
               (name, feature_signature, event_count, success_count, error_count,
                total_duration, first_seen, last_seen, last_active_at,
                success_rate, avg_duration, is_active, lifecycle_stage,
                parent_cluster_id, evolved_from)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                cluster.name,
                cluster.feature_signature,
                cluster.event_count,
                cluster.success_count,
                cluster.error_count,
                cluster.total_duration,
                cluster.first_seen or now,
                cluster.last_seen or now,
                cluster.last_active_at or now,
                cluster.success_rate,
                cluster.avg_duration,
                1,
                cluster.lifecycle_stage,
                cluster.parent_cluster_id,
                cluster.evolved_from,
            ),
        )
        cluster.id = cursor.lastrowid

    def _upsert_cluster(self, cursor, cluster: Cluster, old_id: int, now: str) -> None:
        """Update an existing cluster, **accumulating** counts across batches.

        Previously this overwrote event_count/success_count/etc with the
        current batch's values, destroying recurrence signal. Now we read
        the old row and add the new batch's counts to the stored totals.
        """
        row = cursor.execute(
            "SELECT event_count, success_count, error_count, total_duration FROM clusters WHERE id=?",
            (old_id,),
        ).fetchone()
        old_ec = row[0] if row else 0
        old_sc = row[1] if row else 0
        old_er = row[2] if row else 0
        old_td = row[3] if row else 0.0

        new_ec = old_ec + cluster.event_count
        new_sc = old_sc + cluster.success_count
        new_er = old_er + cluster.error_count
        new_td = old_td + cluster.total_duration

        new_rate = (new_sc / new_ec) if new_ec > 0 else 0.0
        new_avg = (new_td / new_ec) if new_ec > 0 else 0.0

        # G6 fix: don't downgrade stable → emerging on upsert.
        # _build_clusters sets all new clusters to 'emerging', but if the
        # existing cluster is already 'stable', keep it (max semantics).
        old_stage_row = cursor.execute(
            "SELECT lifecycle_stage FROM clusters WHERE id=?", (old_id,)
        ).fetchone()
        old_stage = old_stage_row[0] if old_stage_row else "emerging"
        stage_priority = {"stable": 2, "emerging": 1, "dead": 0}
        final_stage = (
            cluster.lifecycle_stage
            if stage_priority.get(cluster.lifecycle_stage, 0)
            >= stage_priority.get(old_stage, 0)
            else old_stage
        )

        cursor.execute(
            """UPDATE clusters SET
               name=?, feature_signature=?, event_count=?, success_count=?,
               error_count=?, total_duration=?, last_seen=?, last_active_at=?,
               success_rate=?, avg_duration=?, lifecycle_stage=?, evolved_from=?,
               is_active=1
               WHERE id=?""",
            (
                cluster.name,
                cluster.feature_signature,
                new_ec,
                new_sc,
                new_er,
                new_td,
                cluster.last_seen or now,
                cluster.last_active_at or now,
                new_rate,
                new_avg,
                final_stage,
                cluster.evolved_from,
                old_id,
            ),
        )
        cluster.id = old_id

    def _backfill_cluster_ids(
        self,
        vectors: List[EventVector],
        labels: List[int],
        label_to_db_id: Dict[int, int],
    ) -> None:
        """Backfill cluster_id in raw_events table using real DB cluster IDs."""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            for vec, label in zip(vectors, labels):
                if label >= 0 and label in label_to_db_id:
                    db_cluster_id = label_to_db_id[label]
                    cursor.execute(
                        "UPDATE raw_events SET cluster_id = ? WHERE id = ?",
                        (db_cluster_id, vec.event_id),
                    )

            conn.commit()
            conn.close()
        except Exception:
            logger.debug("Failed to backfill cluster_ids", exc_info=True)


def _row_to_cluster(row: sqlite3.Row) -> Cluster:
    """Convert a DB row to Cluster object."""
    return Cluster(
        id=row["id"],
        name=row["name"] or "",
        event_count=row["event_count"] or 0,
        success_count=row["success_count"] or 0,
        error_count=row["error_count"] or 0,
        total_duration=row["total_duration"] or 0.0,
        first_seen=row["first_seen"] or "",
        last_seen=row["last_seen"] or "",
        last_active_at=row["last_active_at"] or "",
        lifecycle_stage=row["lifecycle_stage"] or "emerging",
        feature_signature=row["feature_signature"] or "",
        parent_cluster_id=row["parent_cluster_id"],
        evolved_from=row["evolved_from"] or "",
        is_active=bool(row["is_active"]) if "is_active" in row.keys() else True,
    )


# ── Trigger Logic ────────────────────────────────────────────────────────────

def should_trigger_clustering(db_path: str, min_unclustered: int = 50) -> bool:
    """Check if enough unclustered events have accumulated."""
    from agent.raw_event import get_unclustered_count
    return get_unclustered_count(db_path) >= min_unclustered


def run_clustering_if_needed(db_path: str) -> Optional[Dict[str, Any]]:
    """Run clustering if enough events have accumulated. Returns result or None."""
    if not should_trigger_clustering(db_path):
        return None

    clusterer = EmergentClusterer(db_path)
    try:
        return clusterer.run()
    except Exception as e:
        logger.error("Clustering failed: %s", e, exc_info=True)
        return None
