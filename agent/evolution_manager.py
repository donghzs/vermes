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
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)


def get_evolution_dir() -> Path:
    """Get the evolution data directory."""
    hermes_home = os.environ.get("HERMES_HOME", os.path.expanduser("~/.hermes"))
    return Path(hermes_home) / "evolution"


def get_self_model_db() -> Path:
    """Get the self-model database path."""
    return get_evolution_dir() / "self-model.db"


def is_evolution_active() -> bool:
    """Check if evolution system is active. Auto-seeds if first run."""
    if not get_self_model_db().exists():
        _seed_evolution_db()
    else:
        # 检查表是否为空（之前种子数据可能未 commit）
        try:
            conn = sqlite3.connect(str(get_self_model_db()))
            c = conn.cursor()
            c.execute("SELECT COUNT(*) FROM outcomes")
            count = c.fetchone()[0]
            conn.close()
            if count == 0:
                _seed_evolution_db()
        except Exception:
            pass
    _ensure_wal_mode()
    return True


def _seed_evolution_db() -> None:
    """Seed evolution database for new users (first run)."""
    db_path = get_self_model_db()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
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
    ]
    for ap in ap_seeds:
        c.execute('''INSERT INTO anti_patterns 
            (timestamp, pattern, correct, domain, frequency, last_seen)
            VALUES (?, ?, ?, ?, ?, ?)''', ap)
    
    # Seed emotional state in fusion-state.db
    _fusion_db = get_evolution_dir() / "fusion-state.db"
    _fusion_db.parent.mkdir(parents=True, exist_ok=True)
    fconn = sqlite3.connect(str(_fusion_db))
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
    fconn.close()
    
    conn.commit()
    conn.close()
    logger.info("Evolution DB seeded for first run: 10 outcomes, 3 anti-patterns")


_wal_initialized = None


def _ensure_wal_mode() -> None:
    """初始化 SQLite WAL 模式（全局只执行一次）。"""
    global _wal_initialized
    if _wal_initialized:
        return
    try:
        for _db in [get_self_model_db(), _get_fusion_db()]:
            if _db.exists():
                _conn = sqlite3.connect(str(_db))
                _conn.execute("PRAGMA journal_mode=WAL")
                _conn.execute("PRAGMA synchronous=NORMAL")
                _conn.execute("PRAGMA busy_timeout=5000")
                _conn.close()
        _wal_initialized = True
    except Exception:
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
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # Extract signature from current interaction
    signature = _extract_signature(tool_name, args, user_message)
    
    if not signature:
        conn.close()
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
        conn.close()
        return best_match
    
    # Otherwise, create a new role from this pattern
    new_role_name = _generate_role_name(signature, user_message)
    cursor.execute('''
        INSERT INTO roles (role, signature, frequency, first_seen, last_seen)
        VALUES (?, ?, 1, ?, ?)
    ''', (new_role_name, signature, datetime.now().isoformat(), datetime.now().isoformat()))
    conn.commit()
    conn.close()
    
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
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()
        cursor.execute(
            "SELECT emotion, intensity, trigger FROM emotional_state ORDER BY rowid DESC LIMIT 1"
        )
        row = cursor.fetchone()
        conn.close()
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
) -> None:
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
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT COUNT(*) FROM emotional_state WHERE trigger LIKE ? AND timestamp > datetime('now', '-5 minutes')",
            (f"{tool_name}:%",)
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
        conn.close()
        
        logger.debug("Emotional state: %s (%.1f) — %s", emotion, intensity, trigger)
    except Exception:
        pass


def _record_evolution_metric(metric: str, value: float, details: str = "") -> None:
    """Record a metric to fusion-state.db evolution_metrics table."""
    db_path = _get_fusion_db()
    if not db_path.exists():
        return
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO evolution_metrics (timestamp, metric, value, details) VALUES (?, ?, ?, ?)",
            (datetime.now().isoformat(), metric, value, details),
        )
        conn.commit()
        conn.close()
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
        conn = sqlite3.connect(db_path)
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
        
        conn.commit()
        conn.close()
        
        logger.debug(
            "Evolution: recorded %s %s (success=%s, duration=%.2fs)",
            tool_name, task, not is_error, duration
        )

        # ── 感性层：记录情绪状态 ──────────────────────────────────
        try:
            _record_emotional_state(tool_name, task, is_error, error_type, duration, domain)
        except Exception:
            pass  # 情绪记录非阻塞

        # ── 指标记录 ──────────────────────────────────────────────
        try:
            _record_evolution_metric(
                "tool.duration",
                round(duration, 2),
                f"{tool_name}:{task}:{'success' if not is_error else 'failed'}",
            )
        except Exception:
            pass  # 指标记录非阻塞

        # ── P1: 反馈闭环 — 错误率高时发出警告 ─────────────────────
        advice = None
        if is_error:
            try:
                advice = get_strategy_advice(tool_name, domain)
            except Exception:
                pass
        
        # ── 成就检查 ──────────────────────────────────────────────
        achievement = _check_evolution_achievements(
            status.get("total_outcomes", 0) if isinstance(status := get_evolution_status(), dict) else 0,
            status.get("success_rate", 0) if isinstance(status, dict) else 0,
            status.get("anti_patterns_count", 0) if isinstance(status, dict) else 0,
            tool_name, is_error
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
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # Get success rate for this tool+domain
        cursor.execute('''
            SELECT COUNT(*) as total, SUM(success) as successes
            FROM outcomes
            WHERE tool = ? AND domain = ?
        ''', (tool_name, domain))
        
        row = cursor.fetchone()
        if not row or row[0] == 0:
            conn.close()
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
        
        conn.close()
        
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


_unlocked_achievements = set()  # track already-unlocked to avoid spam


def _check_evolution_achievements(total: int, success_rate: float, anti_count: int, tool_name: str, is_error: bool) -> Optional[str]:
    """Check if evolution milestones trigger achievements. Returns achievement msg or None."""
    key = None
    msg = None
    
    # Milestone achievements
    if total >= 100 and "100_records" not in _unlocked_achievements:
        key = "100_records"
        msg = f"🏆 成就解锁：百次积累 — 已记录 {total} 次工具调用"
    elif total >= 50 and "50_records" not in _unlocked_achievements:
        key = "50_records"
        msg = f"🏆 成就解锁：初露锋芒 — 已记录 {total} 次工具调用"
    elif total >= 10 and "10_records" not in _unlocked_achievements:
        key = "10_records"
        msg = f"🏆 成就解锁：第一步 — 已记录 {total} 次工具调用"
    
    # Success rate achievements
    if success_rate >= 90 and "high_accuracy" not in _unlocked_achievements:
        key = "high_accuracy"
        msg = f"🏆 成就解锁：精准执行 — 成功率 {success_rate:.0f}%"
    
    # Anti-pattern achievements
    if anti_count >= 10 and "anti_pattern_master" not in _unlocked_achievements:
        key = "anti_pattern_master"
        msg = f"🏆 成就解锁：经验丰富 — 已识别 {anti_count} 个反模式"
    elif anti_count >= 3 and "anti_pattern_learner" not in _unlocked_achievements:
        key = "anti_pattern_learner"
        msg = f"🏆 成就解锁：善于学习 — 已识别 {anti_count} 个反模式"
    
    # First error achievement
    if is_error and "first_error" not in _unlocked_achievements:
        key = "first_error"
        msg = f"🏆 成就解锁：失败是成功之母 — 首次遇到错误 ({tool_name})"
    
    if key and msg:
        _unlocked_achievements.add(key)
        logger.info("Achievement: %s", msg)
        return msg
    return None


def get_evolution_status() -> Dict[str, Any]:
    """Get current evolution system status."""
    if not is_evolution_active():
        return {"active": False}
    
    try:
        db_path = get_self_model_db()
        conn = sqlite3.connect(db_path)
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
        
        conn.close()
        
        return {
            "active": True,
            "total_outcomes": total,
            "success_rate": round(success_rate, 1),
            "anti_patterns_count": anti_patterns_count,
            "top_domains": top_domains,
            "role_stats": role_stats,
            "recent_failures": recent_failures,
            "achievements": list(_unlocked_achievements),
        }
        
    except Exception as e:
        logger.debug("Evolution status failed: %s", e)
        return {"active": True, "error": str(e)}
