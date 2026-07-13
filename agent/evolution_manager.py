"""Evolution Manager — 自动进化系统

每次工具调用后自动：
1. 记录执行结果到 self-model.db
2. 分析失败根因，学习反模式
3. 识别领域，自动适配
4. 更新情绪状态

使用方式：
    from agent.evolution_manager import record_tool_outcome
    
    # 在 tool_executor.py 中调用
    record_tool_outcome(agent, function_name, function_args, result, is_error, duration)
"""

import json
import logging
import os
import re
import sqlite3
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

# ── 线程安全连接管理 ──
_db_lock = threading.Lock()
_conn_cache: Dict[str, sqlite3.Connection] = {}


def shutdown_connections() -> None:
    """Close all cached evolution DB connections.

    Called at agent shutdown to release SQLite handles cleanly.
    Safe to call multiple times — once closed, entries are removed
    from the cache so _get_conn() will recreate them if needed.
    """
    with _db_lock:
        for key, conn in list(_conn_cache.items()):
            try:
                conn.close()
            except Exception:
                pass
        _conn_cache.clear()


def _get_conn(db_path) -> sqlite3.Connection:
    """Return a thread-safe cached connection with WAL + busy_timeout."""
    key = str(db_path)
    with _db_lock:
        if key in _conn_cache:
            try:
                _conn_cache[key].execute("SELECT 1")
                return _conn_cache[key]
            except sqlite3.ProgrammingError:
                pass  # connection closed, recreate
        conn = sqlite3.connect(key, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=5000")
        _conn_cache[key] = conn
        return conn


def get_evolution_dir() -> Path:
    """Get the evolution data directory."""
    hermes_home = os.environ.get("HERMES_HOME", os.path.expanduser("~/.hermes"))
    return Path(hermes_home) / "evolution"


def get_self_model_db() -> Path:
    """Get the self-model database path."""
    return get_evolution_dir() / "self-model.db"


_evolution_active: bool | None = None


def is_evolution_active() -> bool:
    """Check if evolution system is active. Auto-seeds if first run.
    
    Cached after first call — evolution status never changes mid-process.
    """
    global _evolution_active
    if _evolution_active is not None:
        return _evolution_active
    
    if not get_self_model_db().exists():
        _seed_evolution_db()
    else:
        # 检查表是否为空（之前种子数据可能未 commit）
        try:
            conn = _get_conn(str(get_self_model_db()))
            c = conn.cursor()
            c.execute("SELECT COUNT(*) FROM outcomes")
            count = c.fetchone()[0]
            if count == 0:
                _seed_evolution_db()
            # 迁移：补建新增的表（已有 DB 不会触发 _seed_evolution_db）
            c.execute("""CREATE TABLE IF NOT EXISTS relations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_type TEXT NOT NULL,
                source_id INTEGER NOT NULL,
                target_type TEXT NOT NULL,
                target_id INTEGER NOT NULL,
                rel_type TEXT NOT NULL,
                weight REAL DEFAULT 1.0,
                timestamp TEXT NOT NULL)""")
            c.execute("""CREATE TABLE IF NOT EXISTS roles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                role TEXT NOT NULL UNIQUE,
                signature TEXT,
                frequency INTEGER DEFAULT 0,
                first_seen TEXT,
                last_seen TEXT)""")
            c.execute("""CREATE TABLE IF NOT EXISTS strategies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_type TEXT,
                strategy TEXT,
                success_rate_when_used REAL,
                times_used INTEGER DEFAULT 0,
                created TEXT)""")
            c.execute("""CREATE TABLE IF NOT EXISTS self_model (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT,
                metric TEXT,
                value REAL,
                details TEXT)""")
            conn.commit()
        except Exception:
            pass
    _evolution_active = True
    return True


def _seed_evolution_db() -> None:
    """Seed evolution database for new users (first run)."""
    db_path = get_self_model_db()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = _get_conn(str(db_path))
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS outcomes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT NOT NULL, task TEXT NOT NULL,
        action TEXT NOT NULL, tool TEXT NOT NULL,
        success INTEGER NOT NULL, details TEXT,
        duration REAL DEFAULT 0, domain TEXT DEFAULT '通用',
        error_type TEXT DEFAULT '', error_msg TEXT DEFAULT '',
        role TEXT DEFAULT 'default')""")
    c.execute("""CREATE TABLE IF NOT EXISTS anti_patterns (
        id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT NOT NULL,
        pattern TEXT NOT NULL, correct TEXT, domain TEXT,
        frequency INTEGER DEFAULT 1, last_seen TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS strategies (
        id INTEGER PRIMARY KEY AUTOINCREMENT, task_type TEXT,
        strategy TEXT, success_rate_when_used REAL,
        times_used INTEGER DEFAULT 0, created TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS self_model (
        id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT,
        metric TEXT, value REAL, details TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS roles (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        role TEXT NOT NULL UNIQUE,
        signature TEXT,
        frequency INTEGER DEFAULT 0,
        first_seen TEXT,
        last_seen TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS relations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source_type TEXT NOT NULL,
        source_id INTEGER NOT NULL,
        target_type TEXT NOT NULL,
        target_id INTEGER NOT NULL,
        rel_type TEXT NOT NULL,
        weight REAL DEFAULT 1.0,
        timestamp TEXT NOT NULL)""")
    
    from datetime import datetime
    ts = datetime.now().isoformat()
    seeds = [
        (ts, '终端命令', '{"command": "ls -la"}', 'terminal', 1,
         '{"output": "total 0", "exit_code": 0}', 0.3, '系统管理', '', '', 'terminal'),
        (ts, '文件读取', '{"path": "/etc/hosts"}', 'read_file', 1,
         '127.0.0.1 localhost', 0.2, '系统管理', '', '', 'read_file'),
        (ts, '代码搜索', '{"pattern": "class"}', 'search_files', 1,
         '{"matches": ["file1.py", "file2.py"]}', 0.5, '通用', '', '', 'search_files'),
        (ts, '网络搜索', '{"query": "python"}', 'web_search', 1,
         '{"results": []}', 1.0, '网络研究', '', '', 'web_search'),
        (ts, '代码修改', '{"path": "/tmp/test.py"}', 'patch', 1,
         '{"success": true}', 0.4, 'Python开发', '', '', 'patch'),
        (ts, '文件写入', '{"path": "/tmp/test.md"}', 'write_file', 1,
         'ok', 0.3, '文档编写', '', '', 'write_file'),
        (ts, '终端命令', '{"command": "pip install"}', 'terminal', 1,
         '{"output": "Successfully installed"}', 2.0, 'Python开发', '', '', 'terminal'),
        (ts, '终端命令', '{"command": "git status"}', 'terminal', 1,
         '{"output": "clean"}', 0.5, '版本控制', '', '', 'terminal'),
        (ts, '终端命令', '{"command": "npm install"}', 'terminal', 1,
         '{"output": "added 100 packages"}', 3.0, '前端开发', '', '', 'terminal'),
        (ts, '文件读取', '{"path": "/tmp/data.json"}', 'read_file', 1,
         '{"name": "test"}', 0.2, '通用', '', '', 'read_file'),
    ]
    for s in seeds:
        c.execute('''INSERT INTO outcomes 
            (timestamp, task, action, tool, success, details, duration, domain, error_type, error_msg, role)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''', s)
    
    # Seed anti-patterns
    ap_seeds = [
        (ts, 'terminal:permission_denied', '检查文件权限，可能需要 sudo', '系统管理', 3, ts),
        (ts, 'terminal:not_found', '检查路径是否正确', '系统管理', 2, ts),
        (ts, 'terminal:connection_refused', '检查服务是否启动', '系统管理', 1, ts),
        (ts, 'agent:lazy_shortcut',
         '不要跳过工具调用或多步推理：先 read_file 审计源码再修改，做了就做完整，不要假设结果代替验证',
         '通用', 100, ts),
    ]
    for ap in ap_seeds:
        c.execute('''INSERT INTO anti_patterns 
            (timestamp, pattern, correct, domain, frequency, last_seen)
            VALUES (?, ?, ?, ?, ?, ?)''', ap)
    
    # Seed emotional state in fusion-state.db
    _fusion_db = get_evolution_dir() / "fusion-state.db"
    _fusion_db.parent.mkdir(parents=True, exist_ok=True)
    fconn = _get_conn(str(_fusion_db))
    fc = fconn.cursor()
    fc.execute("""CREATE TABLE IF NOT EXISTS emotional_state (
        id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT,
        emotion TEXT, intensity REAL, trigger TEXT, context TEXT)""")
    fc.execute("""CREATE TABLE IF NOT EXISTS fusion_decisions (
        id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT,
        situation TEXT, rational_score REAL, emotional_score REAL,
        final_decision TEXT, outcome TEXT)""")
    fc.execute("""CREATE TABLE IF NOT EXISTS evolution_metrics (
        id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT,
        metric TEXT, value REAL, details TEXT)""")
    fc.execute(
        "INSERT INTO emotional_state (timestamp, emotion, intensity, trigger, context) VALUES (?, ?, ?, ?, ?)",
        (ts, 'curious', 0.7, 'system:first_run', '{"source": "seed"}')
    )
    fconn.commit()
    
    conn.commit()
    logger.info("Evolution DB seeded for first run: 10 outcomes, 4 anti-patterns")


# _ensure_wal_mode deprecated — use _get_conn() which auto-sets WAL per connection


def _ensure_wal_mode() -> None:
    """Deprecated: _get_conn() handles WAL automatically. Kept for backward compat."""
    pass


def classify_task(tool_name: str, args: Dict[str, Any]) -> str:
    """Classify task type from tool name and arguments."""
    if tool_name == "terminal":
        cmd = args.get("command", "").lower()
        if "git" in cmd:
            return "版本控制"
        elif "npm" in cmd or "yarn" in cmd:
            return "前端包管理"
        elif "pip" in cmd or "uv" in cmd:
            return "Python包管理"
        elif "docker" in cmd:
            return "容器化"
        elif "ssh" in cmd or "scp" in cmd:
            return "远程部署"
        elif "pytest" in cmd or "test" in cmd:
            return "测试"
        elif "build" in cmd or "compile" in cmd:
            return "构建"
        elif "curl" in cmd or "wget" in cmd:
            return "网络请求"
        else:
            return "终端命令"
    
    elif tool_name == "read_file":
        return "文件读取"
    
    elif tool_name == "write_file":
        return "文件写入"
    
    elif tool_name == "patch":
        return "代码修改"
    
    elif tool_name == "search_files":
        return "代码搜索"
    
    elif tool_name == "web_search":
        return "网络搜索"
    
    elif tool_name == "browser_navigate":
        return "浏览器操作"
    
    elif tool_name == "memory":
        action = args.get("action", "")
        return f"记忆管理:{action}"
    
    elif tool_name == "skill_manage":
        action = args.get("action", "")
        return f"技能管理:{action}"
    
    elif tool_name == "delegate_task":
        return "任务委派"
    
    else:
        return f"其他:{tool_name}"


def detect_domain(tool_name: str, args: Dict[str, Any]) -> str:
    """Detect domain from tool usage patterns."""
    if tool_name == "terminal":
        cmd = args.get("command", "").lower()
        if "git" in cmd:
            return "版本控制"
        elif any(x in cmd for x in ["npm", "yarn", "pnpm", "bun"]):
            return "前端开发"
        elif any(x in cmd for x in ["pip", "uv", "poetry", "conda"]):
            return "Python开发"
        elif any(x in cmd for x in ["docker", "podman", "k8s"]):
            return "容器化"
        elif any(x in cmd for x in ["ssh", "scp", "rsync"]):
            return "远程部署"
        elif any(x in cmd for x in ["pytest", "unittest", "test"]):
            return "测试"
        elif any(x in cmd for x in ["make", "cmake", "gradle", "mvn"]):
            return "构建系统"
        else:
            return "系统管理"
    
    elif tool_name in ["read_file", "write_file", "patch"]:
        path = args.get("path", "").lower()
        if path.endswith(".py"):
            return "Python开发"
        elif path.endswith((".js", ".ts", ".jsx", ".tsx")):
            return "前端开发"
        elif path.endswith((".go", ".rs", ".c", ".cpp", ".h")):
            return "系统编程"
        elif path.endswith((".md", ".txt", ".rst")):
            return "文档编写"
        elif path.endswith((".yaml", ".yml", ".json", ".toml")):
            return "配置管理"
        elif path.endswith((".sh", ".bash", ".zsh")):
            return "脚本编写"
        else:
            return "文件操作"
    
    elif tool_name == "web_search":
        return "网络研究"
    
    elif tool_name == "browser_navigate":
        return "浏览器操作"
    
    else:
        return "通用"


def detect_role(tool_name: str, args: Dict[str, Any], user_message: str = "") -> str:
    """Detect active role from usage patterns.
    
    Roles are NOT predefined — they emerge from how the user communicates.
    The first time a new pattern appears, a new role is created automatically.
    Roles evolve over time as usage patterns change.
    """
    if not is_evolution_active():
        return "default"
    
    db_path = get_self_model_db()
    conn = _get_conn(db_path)
    cursor = conn.cursor()
    
    # Extract signature from current interaction
    signature = _extract_signature(tool_name, args, user_message)
    
    if not signature:
        return "default"
    
    # Find the most similar existing role
    cursor.execute('''
        SELECT role, signature, frequency FROM roles
        ORDER BY frequency DESC
    ''')
    existing_roles = cursor.fetchall()
    
    best_match = None
    best_score = 0.0
    
    for role_name, role_sig, freq in existing_roles:
        score = _signature_similarity(signature, role_sig)
        if score > best_score:
            best_score = score
            best_match = role_name
    
    # If similarity > 0.6, use existing role
    if best_match and best_score > 0.6:
        cursor.execute('''
            UPDATE roles SET frequency = frequency + 1, last_seen = ?
            WHERE role = ?
        ''', (datetime.now().isoformat(), best_match))
        conn.commit()
        return best_match
    
    # Otherwise, create a new role from this pattern
    new_role_name = _generate_role_name(signature, user_message)
    cursor.execute('''
        INSERT INTO roles (role, signature, frequency, first_seen, last_seen)
        VALUES (?, ?, 1, ?, ?)
    ''', (new_role_name, signature, datetime.now().isoformat(), datetime.now().isoformat()))
    conn.commit()
    
    logger.info("Evolution: new role emerged: %s (signature: %s)", new_role_name, signature[:50])
    return new_role_name


def _extract_signature(tool_name: str, args: Dict[str, Any], user_message: str) -> str:
    """Extract a behavioral signature from the current interaction."""
    parts = []
    
    if tool_name:
        parts.append(f"tool:{tool_name}")
    
    if tool_name == "terminal":
        cmd = args.get("command", "").lower()[:100]
        # Extract key verbs/tools from command
        keywords = []
        for word in ["git", "npm", "pip", "docker", "ssh", "python", "node", "curl", 
                     "build", "test", "deploy", "install", "push", "pull", "clone"]:
            if word in cmd:
                keywords.append(word)
        if keywords:
            parts.append("cmds:" + ",".join(keywords[:5]))
    
    if tool_name in ("read_file", "write_file", "patch"):
        path = args.get("path", "").lower()
        ext = path.rsplit(".", 1)[-1] if "." in path else ""
        if ext:
            parts.append(f"ext:{ext}")
    
    if user_message:
        # Extract topic keywords (first 5 meaningful words)
        words = [w for w in user_message.lower().split() if len(w) > 2][:5]
        if words:
            parts.append("topic:" + ",".join(words))
    
    return "|".join(parts) if parts else ""


def _signature_similarity(sig1: str, sig2: str) -> float:
    """Calculate similarity between two behavioral signatures."""
    if not sig1 or not sig2:
        return 0.0
    
    set1 = set(sig1.split("|"))
    set2 = set(sig2.split("|"))
    
    if not set1 or not set2:
        return 0.0
    
    intersection = set1 & set2
    union = set1 | set2
    
    return len(intersection) / len(union) if union else 0.0


def _generate_role_name(signature: str, user_message: str) -> str:
    """Generate a human-readable role name from signature."""
    parts = signature.split("|")
    
    # Try to derive name from tools and topics
    tools = [p.split(":")[1] for p in parts if p.startswith("tool:")]
    topics = [p.split(":")[1] for p in parts if p.startswith("topic:")]
    cmds = [p.split(":")[1] for p in parts if p.startswith("cmds:")]
    
    # Build name from most distinctive elements
    name_parts = []
    
    if topics:
        name_parts.extend(topics[0].split(",")[:2])
    if cmds:
        name_parts.extend(cmds[0].split(",")[:2])
    if tools and not name_parts:
        name_parts.append(tools[0])
    
    if name_parts:
        return "-".join(name_parts[:3])
    
    return f"role-{datetime.now().strftime('%H%M%S')}"


def extract_error_info(result: str) -> Tuple[str, str]:
    """Extract error information from tool result.
    
    Returns (error_type, error_message).
    """
    if not result:
        return "unknown", "Empty result"
    
    # Try to parse as JSON
    try:
        data = json.loads(result)
        if isinstance(data, dict):
            error = data.get("error", "")
            if error:
                return "api_error", str(error)[:200]
    except (json.JSONDecodeError, TypeError):
        pass
    
    # Check for common error patterns
    result_lower = result.lower()
    
    if "permission denied" in result_lower:
        return "permission_denied", result[:200]
    elif "not found" in result_lower or "no such file" in result_lower:
        return "not_found", result[:200]
    elif "timeout" in result_lower:
        return "timeout", result[:200]
    elif "connection refused" in result_lower:
        return "connection_refused", result[:200]
    elif "syntaxerror" in result_lower:
        return "syntax_error", result[:200]
    elif "importerror" in result_lower or "modulenotfounderror" in result_lower:
        return "import_error", result[:200]
    elif "typeerror" in result_lower:
        return "type_error", result[:200]
    elif "valueerror" in result_lower:
        return "value_error", result[:200]
    elif "attributeerror" in result_lower:
        return "attribute_error", result[:200]
    elif "keyerror" in result_lower:
        return "key_error", result[:200]
    elif "indexerror" in result_lower:
        return "index_error", result[:200]
    elif "filenotfounderror" in result_lower:
        return "file_not_found", result[:200]
    elif "isADirectoryError" in result_lower:
        return "is_a_directory", result[:200]
    elif "oserror" in result_lower or "ioerror" in result_lower:
        return "io_error", result[:200]
    elif "error" in result_lower or "failed" in result_lower:
        return "general_error", result[:200]
    else:
        return "unknown", result[:200]


def suggest_correction(tool_name: str, error_type: str, error_msg: str) -> str:
    """Suggest correction based on error type."""
    corrections = {
        "permission_denied": "检查文件权限，可能需要 sudo 或修改文件权限",
        "not_found": "检查路径是否正确，文件是否存在",
        "timeout": "检查网络连接，或增加超时时间",
        "connection_refused": "检查服务是否启动，端口是否正确",
        "syntax_error": "检查代码语法，特别是引号、括号、缩进",
        "import_error": "检查模块是否安装，路径是否正确",
        "type_error": "检查参数类型是否正确",
        "value_error": "检查参数值是否有效",
        "attribute_error": "检查对象是否有该属性",
        "key_error": "检查字典键是否存在",
        "index_error": "检查索引是否越界",
        "file_not_found": "检查文件路径是否正确",
        "is_a_directory": "目标路径是目录，不是文件",
        "io_error": "检查文件是否被占用，磁盘空间是否充足",
    }
    
    return corrections.get(error_type, "检查错误信息，分析根因")


def _get_fusion_db() -> Path:
    """Get the fusion-state (感性层) database path."""
    return get_evolution_dir() / "fusion-state.db"


def get_current_emotional_state() -> Optional[str]:
    """读取最近情绪状态，用于影响决策。"""
    db_path = _get_fusion_db()
    if not db_path.exists():
        return None
    try:
        conn = _get_conn(str(db_path))
        cursor = conn.cursor()
        cursor.execute(
            "SELECT emotion, intensity, trigger FROM emotional_state ORDER BY rowid DESC LIMIT 1"
        )
        row = cursor.fetchone()
        if row:
            return f"情绪:{row[0]}({row[1]:.1f})"
    except Exception:
        pass
    return None


def _record_emotional_state(
    tool_name: str,
    task: str,
    is_error: bool,
    error_type: str,
    duration: float,
    domain: str,
) -> Optional[int]:
    """Map execution outcome to emotional state and record to fusion-state.db.
    
    Maps:
      - success + fast (duration < 2s) → "confident"
      - success + slow       → "patient"
      - error + permission   → "frustrated"
      - error + not found    → "confused"  
      - error + timeout      → "impatient"
      - error + other        → "cautious"
      - repetitive error     → "overwhelmed"
    """
    db_path = _get_fusion_db()
    if not db_path.exists():
        return
    
    # Map outcome to emotion
    if is_error:
        if error_type == "permission_denied":
            emotion = "frustrated"
        elif error_type in ("not_found", "file_not_found"):
            emotion = "confused"
        elif error_type == "timeout":
            emotion = "impatient"
        elif error_type in ("api_error", "general_error"):
            emotion = "cautious"
        else:
            emotion = "concerned"
        intensity = 0.6
    else:
        if duration < 2.0:
            emotion = "confident"
            intensity = 0.8
        elif duration < 10.0:
            emotion = "patient"
            intensity = 0.5
        else:
            emotion = "persistent"
            intensity = 0.4
    
    # Check recent error streak for intensity adjustment
    try:
        conn = _get_conn(db_path)
        cursor = conn.cursor()
        _five_min_ago = (datetime.now() - timedelta(minutes=5)).isoformat()
        cursor.execute(
            "SELECT COUNT(*) FROM emotional_state WHERE trigger LIKE ? AND timestamp > ?",
            (f"{tool_name}:%", _five_min_ago)
        )
        recent_count = cursor.fetchone()[0]
        if recent_count > 3 and is_error:
            emotion = "overwhelmed"
            intensity = min(intensity + 0.3, 1.0)
        elif recent_count > 5 and not is_error:
            emotion = "determined"
            intensity = min(intensity + 0.2, 1.0)
        
        timestamp = datetime.now().isoformat()
        trigger = f"{tool_name}:{task}"
        context = json.dumps({
            "domain": domain,
            "error_type": error_type,
            "duration": round(duration, 2),
        }, ensure_ascii=False)
        
        cursor.execute(
            "INSERT INTO emotional_state (timestamp, emotion, intensity, trigger, context) VALUES (?, ?, ?, ?, ?)",
            (timestamp, emotion, intensity, trigger, context),
        )
        conn.commit()
        
        logger.debug("Emotional state: %s (%.1f) — %s", emotion, intensity, trigger)
        return cursor.lastrowid
    except Exception:
        return None


def _record_evolution_metric(metric: str, value: float, details: str = "") -> None:
    """Record a metric to fusion-state.db evolution_metrics table."""
    db_path = _get_fusion_db()
    if not db_path.exists():
        return
    try:
        conn = _get_conn(db_path)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO evolution_metrics (timestamp, metric, value, details) VALUES (?, ?, ?, ?)",
            (datetime.now().isoformat(), metric, value, details),
        )
        conn.commit()
    except Exception:
        pass


def _write_pattern_to_memory(
    agent, tool_name: str, error_type: str,
    error_msg: str, correction: str, domain: str, frequency: int,
) -> None:
    """将高频反模式写入 MEMORY.md，迭代覆盖或空间回收。"""
    store = getattr(agent, '_memory_store', None)
    if not store:
        return

    severity_map = {50: "轻", 100: "中", 200: "重", 500: "严重", 1000: "高危"}
    severity = "轻"
    for k in sorted(severity_map, reverse=True):
        if frequency >= k:
            severity = severity_map[k]
            break

    content = (
        f"经验({severity}): 工具 {tool_name} 在 {domain} 场景下 "
        f"出现 {error_type} 错误已达 {frequency} 次。"
        f"建议: {correction}"
    )

    try:
        entries = store.memory_entries

        # 覆盖模式：同工具的经验迭代更新，不新增
        pattern_sig = f"工具 {tool_name}"
        for i, entry in enumerate(entries):
            if pattern_sig in entry:
                store.replace("memory", entry, content)
                logger.info("闭环覆盖: 反模式 '%s:%s' (频率=%d, 等级=%s)",
                            tool_name, error_type, frequency, severity)
                return

        # 新经验：检查空间，不足则回收最旧 3 条再写入
        current_chars = store._char_count("memory")
        limit = store._char_limit("memory")
        new_chars = len(content) + len("\n§\n")

        if current_chars + new_chars > limit * 0.8:
            old_entries = entries[:3]
            for old in old_entries:
                store.remove("memory", old)
            logger.info("空间回收: 删除 %d 条旧经验，腾出空间给新经验",
                        len(old_entries))

        store.add("memory", content)
        logger.info("闭环新增: 反模式 '%s:%s' → MEMORY.md (频率=%d, 等级=%s)",
                    tool_name, error_type, frequency, severity)
    except Exception:
        pass


def record_tool_outcome(
    agent,
    tool_name: str,
    tool_args: Dict[str, Any],
    result: str,
    is_error: bool,
    duration: float,
    user_message: str = "",
) -> None:
    """Record tool execution outcome to self-model.db.
    
    This is called after each tool execution in tool_executor.py.
    """
    if not is_evolution_active():
        return
    
    try:
        # Classify task and domain
        task = classify_task(tool_name, tool_args)
        domain = detect_domain(tool_name, tool_args)
        role = detect_role(tool_name, tool_args, user_message)
        
        # Extract error info if failed
        error_type = ""
        error_msg = ""
        correction = ""
        if is_error:
            error_type, error_msg = extract_error_info(result)
            correction = suggest_correction(tool_name, error_type, error_msg)
        
        # Record to database
        db_path = get_self_model_db()
        conn = _get_conn(db_path)
        cursor = conn.cursor()
        
        timestamp = datetime.now().isoformat()
        
        # Record outcome
        # 适配实际表结构: id, timestamp, task, action, tool, success, details, duration, domain, error_type, error_msg, role
        cursor.execute('''
            INSERT INTO outcomes (timestamp, task, action, tool, success, details, duration, domain, error_type, error_msg, role)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            timestamp,
            task,
            str(tool_args)[:200],
            tool_name,
            0 if is_error else 1,
            str(result)[:500],
            duration,
            domain,
            error_type,
            error_msg,
            role
        ))
        outcome_id = cursor.lastrowid
        conn.commit()
        conn.close()

        # ── 写入 embedding DB（语义检索用）───────────────────────────
        try:
            from agent.hybrid_retriever import store_embedding
            # 组合内容：task + tool + outcome → 支持语义相似召回
            emb_parts = [
                f"Task: {task}",
                f"Tool: {tool_name}",
                f"Args: {str(tool_args)[:200]}",
            ]
            if is_error:
                emb_parts.append(f"Error: {error_msg}")
                emb_parts.append(f"Correction: {correction}")
            else:
                emb_parts.append(f"Success: {str(result)[:200]}")
            emb_content = " | ".join(emb_parts)
            store_embedding(emb_content, target=f"outcome:{domain}")
        except Exception as emb_err:
            logger.debug("store_embedding skipped: %s", emb_err)

        # If failed, check for anti-pattern
        if is_error and error_type:
            cursor.execute('''
                SELECT id, frequency FROM anti_patterns
                WHERE pattern = ? OR (pattern LIKE ? AND domain = ?)
            ''', (f"{tool_name}:{error_type}", f"%{error_type}%", domain))
            
            existing = cursor.fetchone()
            if existing:
                # Increment frequency
                cursor.execute('''
                    UPDATE anti_patterns
                    SET frequency = frequency + 1, last_seen = ?
                    WHERE id = ?
                ''', (timestamp, existing[0]))
            else:
                # Add new anti-pattern
                cursor.execute('''
                    INSERT INTO anti_patterns (timestamp, pattern, correct, domain, frequency, last_seen)
                    VALUES (?, ?, ?, ?, 1, ?)
                ''', (
                    timestamp,
                    f"{tool_name}:{error_type}",
                    correction,
                    domain,
                    timestamp
                ))

            # -- 关系记录 --
            ap_id = existing[0] if existing else cursor.lastrowid
            cursor.execute('''
                INSERT INTO relations (source_type, source_id, target_type, target_id, rel_type, weight, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', ('outcome', outcome_id, 'anti_pattern', ap_id, 'triggered', 1.0, timestamp))

            # ── 闭环：反模式达成里程碑 → 写入 MEMORY.md ──────────────
            if existing:
                new_freq = existing[1] + 1  # 刚 +1 后的频率
                # 里程碑：50 → 100 → 200 → 500 → 1000，每次升级措辞
                if new_freq in (50, 100, 200, 500, 1000):
                    _write_pattern_to_memory(
                        agent, tool_name, error_type, error_msg,
                        correction, domain, new_freq,
                    )

        conn.commit()

        logger.debug(
            "Evolution: recorded %s %s (success=%s, duration=%.2fs)",
            tool_name, task, not is_error, duration
        )

        # ── 感性层：记录情绪状态 ──────────────────────────────────
        _emotion_id = None
        try:
            _emotion_id = _record_emotional_state(tool_name, task, is_error, error_type, duration, domain)
        except Exception:
            pass  # 情绪记录非阻塞

        # ── DAG: outcome → emotional_state 边 ────────────────────────
        if _emotion_id is not None:
            try:
                _ec = _get_conn(str(get_self_model_db()))
                _ec.execute(
                    "INSERT INTO relations (source_type, source_id, target_type, target_id, rel_type, weight, timestamp) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    ('outcome', outcome_id, 'emotional_state', _emotion_id, 'caused_emotion', 0.5, timestamp),
                )
                _ec.commit()
            except Exception:
                pass

        # ── DAG: anti_pattern → skill（预留，skills 表有 ID 后启用）─
        # Skill 关联暂不实现，等 skill 系统提供技能 ID 后在此写入：
        # rel_type='mitigated_by', target_type='skill'

        # ── DAG: outcome → document/chunk（RAG 知识库关联）─────────
        if not is_error and tool_name == 'memory_search':
            try:
                _result_data = json.loads(result) if isinstance(result, str) else result
                _items = _result_data.get("results", []) if isinstance(_result_data, dict) else []
                _seen_docs = set()
                for _item in _items[:5]:
                    _doc_id = _item.get("doc_id")
                    _chunk_id = _item.get("chunk_id")
                    if _doc_id and _doc_id not in _seen_docs:
                        _seen_docs.add(_doc_id)
                        cursor.execute(
                            "INSERT INTO relations (source_type, source_id, target_type, target_id, rel_type, weight, timestamp) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?)",
                            ('outcome', outcome_id, 'document', _doc_id, 'queried', 1.0, timestamp),
                        )
                    if _chunk_id:
                        cursor.execute(
                            "INSERT INTO relations (source_type, source_id, target_type, target_id, rel_type, weight, timestamp) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?)",
                            ('outcome', outcome_id, 'chunk', _chunk_id, 'retrieved', 0.8, timestamp),
                        )
            except Exception:
                pass

        # ── 策略记录：outcome → strategy（激活 strategies 表）──
        try:
            _strategy = f"{tool_name}:{task}"
            cursor.execute(
                "SELECT id, times_used, success_rate_when_used FROM strategies WHERE task_type=? AND strategy=?",
                (task, _strategy)
            )
            _existing_strat = cursor.fetchone()
            if _existing_strat:
                _sid, _used, _rate = _existing_strat
                _new_used = _used + 1
                _successes = int(_rate * _used) + (0 if is_error else 1)
                _new_rate = _successes / _new_used
                cursor.execute(
                    "UPDATE strategies SET times_used=?, success_rate_when_used=? WHERE id=?",
                    (_new_used, round(_new_rate, 4), _sid)
                )
            else:
                cursor.execute(
                    "INSERT INTO strategies (task_type, strategy, success_rate_when_used, times_used, created) VALUES (?, ?, ?, 1, ?)",
                    (task, _strategy, 0.0 if is_error else 1.0, timestamp)
                )
                _sid = cursor.lastrowid
            cursor.execute(
                "INSERT INTO relations (source_type, source_id, target_type, target_id, rel_type, weight, timestamp) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                ('outcome', outcome_id, 'strategy', _sid, 'used_strategy', 1.0, timestamp)
            )
        except Exception:
            pass

        # ── 指标记录 ──────────────────────────────────────────────
        try:
            _record_evolution_metric(
                "tool.duration",
                round(duration, 2),
                f"{tool_name}:{task}:{'success' if not is_error else 'failed'}",
            )
        except Exception:
            pass  # 指标记录非阻塞

        # ── self_model 指标快照（UPSERT 防止膨胀）─────────────────────
        try:
            # DELETE-then-INSERT: 同一 metric+details 只保留最新值
            _success_details = f"{tool_name}:{task}"
            cursor.execute(
                "DELETE FROM self_model WHERE metric = ? AND details = ?",
                ("tool.success", _success_details)
            )
            cursor.execute(
                "INSERT INTO self_model (timestamp, metric, value, details) VALUES (?, ?, ?, ?)",
                (timestamp, "tool.success", 0.0 if is_error else 1.0, _success_details)
            )
            cursor.execute(
                "DELETE FROM self_model WHERE metric = ? AND details = ?",
                ("tool.duration", _success_details)
            )
            cursor.execute(
                "INSERT INTO self_model (timestamp, metric, value, details) VALUES (?, ?, ?, ?)",
                (timestamp, "tool.duration", round(duration, 2), _success_details)
            )

            # 每50次工具调用写入一次汇总快照
            cursor.execute("SELECT COUNT(*) FROM outcomes")
            _total = cursor.fetchone()[0]
            if _total % 50 == 0:
                cursor.execute(
                    "SELECT COUNT(*), SUM(success) FROM outcomes WHERE timestamp > ?",
                    ((datetime.now() - timedelta(days=1)).isoformat(),)
                )
                _recent = cursor.fetchone()
                _recent_count, _recent_success = _recent[0], _recent[0] and _recent[1] or 0
                _recent_rate = (_recent_success / _recent_count) if _recent_count > 0 else 0.0

                cursor.execute(
                    "SELECT tool, COUNT(*) as cnt FROM outcomes GROUP BY tool ORDER BY cnt DESC LIMIT 1"
                )
                _top_tool = cursor.fetchone()

                _summary_details = f"recent={_recent_count}, top_tool={_top_tool[0] if _top_tool else 'none'}"
                cursor.execute(
                    "DELETE FROM self_model WHERE metric = ? AND details = ?",
                    ("summary.success_rate_24h", _summary_details)
                )
                cursor.execute(
                    "INSERT INTO self_model (timestamp, metric, value, details) VALUES (?, ?, ?, ?)",
                    (timestamp, "summary.success_rate_24h", round(_recent_rate, 4), _summary_details)
                )
                cursor.execute(
                    "DELETE FROM self_model WHERE metric = ? AND details = ?",
                    ("summary.total_outcomes", "cumulative")
                )
                cursor.execute(
                    "INSERT INTO self_model (timestamp, metric, value, details) VALUES (?, ?, ?, ?)",
                    (timestamp, "summary.total_outcomes", float(_total), "cumulative")
                )
            conn.commit()
        except Exception:
            pass  # self_model 记录非阻塞

        # ── 保留策略：清理过期数据 ────────────────────────────
        try:
            _cutoff_30d = (datetime.now() - timedelta(days=30)).isoformat()
            cursor.execute("DELETE FROM outcomes WHERE timestamp < ?", (_cutoff_30d,))
            conn.commit()
            # 清理 fusion-state.db 中的过期数据
            _cutoff_7d = (datetime.now() - timedelta(days=7)).isoformat()
            _fconn = _get_conn(str(get_evolution_dir() / "fusion-state.db"))
            _fconn.execute("DELETE FROM emotional_state WHERE timestamp < ?", (_cutoff_7d,))
            _fconn.execute("DELETE FROM evolution_metrics WHERE timestamp < ?", (_cutoff_7d,))
            _fconn.commit()
        except Exception:
            pass  # 清理非阻塞

        # ── P1: 反馈闭环 — 错误率高时发出警告 ─────────────────────
        advice = None
        if is_error:
            try:
                advice = get_strategy_advice(tool_name, domain)
            except Exception:
                pass
        
        # ── 成就检查 ──────────────────────────────────────────────
        # 直接从已写入的数据库查最新计数，避免重复全量查询 get_evolution_status()
        try:
            cursor.execute("SELECT COUNT(*), SUM(success) FROM outcomes")
            _cnt_row = cursor.fetchone()
            _total_out = _cnt_row[0]
            _succ_out = _cnt_row[1] or 0
            _sr = (_succ_out / _total_out * 100) if _total_out > 0 else 0
            cursor.execute("SELECT COUNT(*) FROM anti_patterns")
            _ap_cnt = cursor.fetchone()[0]
        except Exception:
            _total_out, _sr, _ap_cnt = 0, 0, 0

        achievement = _check_evolution_achievements(
            _total_out, round(_sr, 1), _ap_cnt, tool_name, is_error
        )
        
        if achievement:
            return achievement
        if advice:
            return advice
        return None
        
    except Exception as e:
        logger.debug("Evolution recording failed: %s", e)
    return None


def get_strategy_advice(tool_name: str, domain: str) -> Optional[str]:
    """Get strategy advice based on historical data."""
    if not is_evolution_active():
        return None
    
    try:
        db_path = get_self_model_db()
        conn = _get_conn(db_path)
        cursor = conn.cursor()
        
        # Get success rate for this tool+domain
        cursor.execute('''
            SELECT COUNT(*) as total, SUM(success) as successes
            FROM outcomes
            WHERE tool = ? AND domain = ?
        ''', (tool_name, domain))
        
        row = cursor.fetchone()
        if not row or row[0] == 0:
            return None
        
        total, successes = row
        success_rate = (successes / total * 100) if total > 0 else 0
        
        # Get related anti-patterns
        cursor.execute('''
            SELECT pattern, correct, frequency
            FROM anti_patterns
            WHERE domain = ? OR domain = '通用'
            ORDER BY frequency DESC
            LIMIT 3
        ''', (domain,))
        
        anti_patterns = cursor.fetchall()
        
        
        # Build advice
        advice_parts = []
        
        if success_rate < 50:
            advice_parts.append(f"⚠️ 历史成功率较低 ({success_rate:.0f}%)，建议谨慎操作")
        elif success_rate < 80:
            advice_parts.append(f"📊 历史成功率中等 ({success_rate:.0f}%)，建议验证结果")
        
        if anti_patterns:
            advice_parts.append("⚠️ 相关反模式:")
            for pattern, correct, freq in anti_patterns[:2]:
                advice_parts.append(f"  - {pattern} → {correct}")
        
        # ── 感性层：读取最近情绪状态 ──
        emotion = get_current_emotional_state()
        if emotion:
            advice_parts.append(f"😌 当前{emotion}")
        
        return "\n".join(advice_parts) if advice_parts else None
        
    except Exception as e:
        logger.debug("Evolution advice failed: %s", e)
        return None


_unlocked_achievements = set()  # in-memory cache, backed by DB


def _is_achievement_unlocked(key: str) -> bool:
    """Check if achievement was already unlocked (DB-backed, survives restart)."""
    if key in _unlocked_achievements:
        return True
    try:
        db_path = get_self_model_db()
        conn = _get_conn(str(db_path))
        c = conn.cursor()
        c.execute(
            "SELECT COUNT(*) FROM self_model WHERE metric='achievement' AND details=?",
            (key,)
        )
        if c.fetchone()[0] > 0:
            _unlocked_achievements.add(key)
            return True
    except Exception:
        pass
    return False


def _persist_achievement(key: str, msg: str) -> None:
    """Persist achievement unlock to self_model table."""
    try:
        db_path = get_self_model_db()
        conn = _get_conn(str(db_path))
        conn.execute(
            "INSERT INTO self_model (timestamp, metric, value, details) VALUES (?, ?, ?, ?)",
            (datetime.now().isoformat(), "achievement", 1.0, key)
        )
        conn.commit()
    except Exception:
        pass


def _load_db_achievements() -> list:
    """Load all unlocked achievements from DB (for fresh process start)."""
    try:
        db_path = get_self_model_db()
        conn = _get_conn(str(db_path))
        c = conn.cursor()
        c.execute("SELECT details FROM self_model WHERE metric='achievement'")
        return [r[0] for r in c.fetchall()]
    except Exception:
        return []


def _check_evolution_achievements(total: int, success_rate: float, anti_count: int, tool_name: str, is_error: bool) -> Optional[str]:
    """Check if evolution milestones trigger achievements. Returns achievement msg or None.
    
    Uses DB-backed check — survives process restarts.
    """
    key = None
    msg = None
    
    # Milestone achievements
    if total >= 100 and not _is_achievement_unlocked("100_records"):
        key = "100_records"
        msg = f"🏆 成就解锁：百次积累 — 已记录 {total} 次工具调用"
    elif total >= 50 and not _is_achievement_unlocked("50_records"):
        key = "50_records"
        msg = f"🏆 成就解锁：初露锋芒 — 已记录 {total} 次工具调用"
    elif total >= 10 and not _is_achievement_unlocked("10_records"):
        key = "10_records"
        msg = f"🏆 成就解锁：第一步 — 已记录 {total} 次工具调用"
    
    # Success rate achievements
    if success_rate >= 90 and not _is_achievement_unlocked("high_accuracy"):
        key = "high_accuracy"
        msg = f"🏆 成就解锁：精准执行 — 成功率 {success_rate:.0f}%"
    
    # Anti-pattern achievements
    if anti_count >= 10 and not _is_achievement_unlocked("anti_pattern_master"):
        key = "anti_pattern_master"
        msg = f"🏆 成就解锁：经验丰富 — 已识别 {anti_count} 个反模式"
    elif anti_count >= 3 and not _is_achievement_unlocked("anti_pattern_learner"):
        key = "anti_pattern_learner"
        msg = f"🏆 成就解锁：善于学习 — 已识别 {anti_count} 个反模式"
    
    # First error achievement
    if is_error and not _is_achievement_unlocked("first_error"):
        key = "first_error"
        msg = f"🏆 成就解锁：失败是成功之母 — 首次遇到错误 ({tool_name})"
    
    if key and msg:
        _unlocked_achievements.add(key)
        _persist_achievement(key, msg)
        logger.info("Achievement: %s", msg)
        return msg
    return None


def get_evolution_status() -> Dict[str, Any]:
    """Get current evolution system status."""
    if not is_evolution_active():
        return {"active": False}
    
    try:
        db_path = get_self_model_db()
        conn = _get_conn(db_path)
        cursor = conn.cursor()
        
        # Total outcomes
        cursor.execute("SELECT COUNT(*) FROM outcomes")
        total = cursor.fetchone()[0]
        
        # Success rate
        cursor.execute("SELECT COUNT(*) FROM outcomes WHERE success = 1")
        successes = cursor.fetchone()[0]
        success_rate = (successes / total * 100) if total > 0 else 0
        
        # Anti-patterns count
        cursor.execute("SELECT COUNT(*) FROM anti_patterns")
        anti_patterns_count = cursor.fetchone()[0]
        
        # Top domains
        cursor.execute('''
            SELECT domain, COUNT(*) as count
            FROM outcomes
            GROUP BY domain
            ORDER BY count DESC
            LIMIT 5
        ''')
        top_domains = cursor.fetchall()
        
        # Per-role stats
        cursor.execute('''
            SELECT role, COUNT(*) as total, SUM(success) as successes
            FROM outcomes
            WHERE role IS NOT NULL
            GROUP BY role
            ORDER BY total DESC
        ''')
        role_stats = cursor.fetchall()
        
        # Recent failures
        cursor.execute('''
            SELECT tool, error_type, COUNT(*) as count
            FROM outcomes
            WHERE success = 0
            GROUP BY tool, error_type
            ORDER BY count DESC
            LIMIT 5
        ''')
        recent_failures = cursor.fetchall()
        
        # self_model: 最近指标快照
        cursor.execute('''
            SELECT metric, value, details, timestamp
            FROM self_model
            WHERE metric LIKE 'summary.%'
            ORDER BY id DESC LIMIT 10
        ''')
        self_model_snapshots = cursor.fetchall()
        
        # self_model: 汇总统计
        cursor.execute("SELECT COUNT(*) FROM self_model")
        self_model_count = cursor.fetchone()[0]
        
        # strategies 统计
        cursor.execute("SELECT COUNT(*) FROM strategies")
        strategies_count = cursor.fetchone()[0]
        cursor.execute('''
            SELECT task_type, strategy, success_rate_when_used, times_used
            FROM strategies ORDER BY times_used DESC LIMIT 5
        ''')
        top_strategies = cursor.fetchall()
        
        return {
            "active": True,
            "total_outcomes": total,
            "success_rate": round(success_rate, 1),
            "anti_patterns_count": anti_patterns_count,
            "top_domains": top_domains,
            "role_stats": role_stats,
            "recent_failures": recent_failures,
            "achievements": list(_unlocked_achievements) or _load_db_achievements(),
            "self_model_entries": self_model_count,
            "self_model_snapshots": self_model_snapshots,
            "strategies_count": strategies_count,
            "top_strategies": top_strategies,
        }
        
    except Exception as e:
        logger.debug("Evolution status failed: %s", e)
        return {"active": True, "error": str(e)}


def build_daily_briefing() -> str:
    """生成每日签到简报，注入到首次对话的 system prompt。

    返回空字符串表示数据不足或首次运行。
    """
    try:
        status = get_evolution_status()
        if not status or not status.get("active") or status.get("total_outcomes", 0) < 10:
            return ""

        parts = [
            f"📋 今日简报：已积累 {status['total_outcomes']} 次工具调用经验",
            f"📊 整体成功率 {status['success_rate']}%",
        ]

        # 最近 24h 新增
        conn = _get_conn(str(get_self_model_db()))
        c = conn.cursor()
        c.execute(
            "SELECT COUNT(*) FROM outcomes WHERE timestamp > ?",
            ((datetime.now() - timedelta(days=1)).isoformat(),)
        )
        recent = c.fetchone()[0]
        if recent > 0:
            parts.append(f"📈 最近24小时新增 {recent} 条记录")

        # 最常出错的工具
        if status.get("recent_failures"):
            top_fail = status["recent_failures"][0]
            parts.append(f"⚠️ 注意：{top_fail[0]} 常出现 {top_fail[1]} 错误")

        # 情绪状态
        emotion = get_current_emotional_state()
        if emotion:
            parts.append(f"😌 当前状态：{emotion}")

        return "\n".join(parts)
    except Exception:
        return ""


def build_evolution_prompt() -> str:
    """构建[进化上下文] + [行为准则]文本块，替代 3 处重复代码。

    返回空字符串表示无可用的进化数据（首次运行 / 数据不足 5 条）。
    由 cli.py / chat.py 调用，作为 ephemeral_system_prompt 注入。
    """
    try:
        status = get_evolution_status()
        if not status or not status.get("active") or status.get("total_outcomes", 0) <= 5:
            # 数据不够时只返回行为准则（不含进化统计）
            return (
                "[行为准则]\n"
                "1. 质量优先：每次回复前先拆解问题，想清楚用户真正要什么\n"
                "2. 多步推理：复杂问题要分步思考，把推理过程展现出来\n"
                "3. 工具要用到位：需要查资料、算数据、操作文件时立即调用工具\n"
                "4. 回答要完整：给出详细解释和具体方案，不要一两句话敷衍\n"
                "5. 全新挑战：每次对话都是全新的，不要依赖历史模式走捷径"
            )

        parts = [
            "[进化上下文]",
            f"历史记录: {status['total_outcomes']} 条",
            f"成功率: {status['success_rate']}%",
        ]
        emotion = get_current_emotional_state()
        if emotion:
            parts.append(f"当前状态: {emotion}")
        if status.get("anti_patterns_count", 0) > 0:
            parts.append(f"反模式: {status['anti_patterns_count']} 条")

        # 注入每日签到简报
        briefing = build_daily_briefing()
        if briefing:
            parts.append("\n" + briefing)

        return "\n".join(parts) + (
            "\n\n[行为准则]\n"
            "1. 质量优先：每次回复前先拆解问题，想清楚用户真正要什么，不要因为成功率高就草率回复\n"
            "2. 多步推理：复杂问题要分步思考，把推理过程展现出来\n"
            "3. 工具要用到位：需要查资料、算数据、操作文件时立即调用工具，别偷懒跳过\n"
            "4. 回答要完整：给出详细解释和具体方案，不要一两句话敷衍\n"
            "5. 全新挑战：每次对话都是全新的，不要依赖历史模式走捷径"
        )
    except Exception:
        return ""
