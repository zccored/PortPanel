# -*- coding: utf-8 -*-
"""安全存储模块：敏感文件加密（密文落盘）、明文仅存内存、密码会话（UTC 时效 3 天免重输）。

用于 user.csv / sql_connections.json / package-lock.json 等敏感文件：
- 本地磁盘与上传 OSS 均为密文
- 读取时解密到内存缓存，不产生明文临时文件
- 密码不落盘明文，仅保存随机盐 + HMAC 验证串 + 上次验证的 UTC 时间戳

用法：
    import secure_store
    secure_store.unlock("我的密码")          # 首次自动建会话；之后用 HMAC 验证
    plain = secure_store.get_plaintext(path) # 密文文件 → 解密到内存；旧明文 → 原样缓存
    secure_store.set_plaintext(path, data)   # 明文写内存 + 密文写盘（未解锁则不落盘）
    ct = secure_store.encrypt_bytes_for_upload(plain)      # OSS 上传用密文
    plain = secure_store.decrypt_bytes_for_download(ct)    # OSS 下载后内存解密
"""
import os
import json
import base64
import hashlib
import hmac
import datetime
import threading

try:
    from cryptography.fernet import Fernet
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    HAS_CRYPTOGRAPHY = True
except ImportError:  # pragma: no cover
    HAS_CRYPTOGRAPHY = False
    Fernet = None

_BASE = os.path.dirname(os.path.abspath(__file__))
_SESSION_FILE = os.path.join(_BASE, 'data', 'secure_session.json')
_MAGIC = b'SEC2'           # 密文容器魔数
_PBKDF2_ITER = 200_000
_SESSION_DAYS = 3          # 密码会话时效（UTC 天数）
_lock = threading.Lock()

# 内存态（绝不落盘）：明文缓存 + 会话密钥
_cache = {}                # abs_path -> bytes（明文，仅内存）
_key = None                # bytes：PBKDF2(密码, session_salt) 派生（仅内存）
_session_salt = b''        # 会话盐（首次解锁生成，持久化于会话文件）
_unlocked_at = None        # datetime(UTC)：上次成功解锁时间


def _now_utc() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _derive_key(password: str, salt: bytes) -> bytes:
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32,
                     salt=salt, iterations=_PBKDF2_ITER)
    return kdf.derive(password.encode('utf-8'))


def crypto_available() -> bool:
    return HAS_CRYPTOGRAPHY


# ---------------- 密码会话 ----------------

def _load_session() -> dict:
    try:
        with open(_SESSION_FILE, 'r', encoding='utf-8') as f:
            s = json.load(f)
        return s if isinstance(s, dict) else {}
    except Exception:
        return {}


def _save_session(s: dict):
    try:
        os.makedirs(os.path.dirname(_SESSION_FILE), exist_ok=True)
        tmp = _SESSION_FILE + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(s, f, ensure_ascii=False, indent=2)
        os.replace(tmp, _SESSION_FILE)
    except Exception:
        pass


def unlock(password: str):
    """用密码建立/恢复加密会话。成功返回 (True, 消息)，失败返回 (False, 原因)。

    - 首次使用：生成随机会话盐并派生密钥，仅保存 HMAC 验证串与时间（不存密码）。
    - 已有会话：用会话盐派生密钥并用 HMAC 验证，通过则解锁。
    解锁后密钥仅存内存，UTC 时效 3 天。
    """
    global _key, _session_salt, _unlocked_at
    if not HAS_CRYPTOGRAPHY:
        return False, "缺少 cryptography 库，无法进行加密"
    if not password:
        return False, "密码不能为空"
    with _lock:
        s = _load_session()
        salt_b64 = s.get('key_salt', '')
        verifier = s.get('verifier', '')
        try:
            if salt_b64 and verifier:
                salt = base64.b64decode(salt_b64)
                key = _derive_key(password, salt)
                v = hmac.new(key, b'secure-store-verify', hashlib.sha256).digest()
                if not hmac.compare_digest(v, base64.b64decode(verifier)):
                    return False, "密码错误"
                _session_salt = salt
            else:
                # 首次：生成随机会话盐并保存验证串
                salt = os.urandom(16)
                key = _derive_key(password, salt)
                v = hmac.new(key, b'secure-store-verify', hashlib.sha256).digest()
                _save_session({
                    'key_salt': base64.b64encode(salt).decode(),
                    'verifier': base64.b64encode(v).decode(),
                })
                _session_salt = salt
            _key = key
            _unlocked_at = _now_utc()
            _save_session({**_load_session(), 'last_auth_utc': _unlocked_at.isoformat()})
            return True, "解锁成功"
        except Exception as e:
            return False, f"解锁失败: {str(e)}"


def lock():
    """手动锁定：清除内存密钥与缓存。"""
    global _key, _session_salt, _unlocked_at
    with _lock:
        _key, _session_salt, _unlocked_at = None, b'', None
        _cache.clear()


def is_unlocked() -> bool:
    """内存中是否有有效会话密钥，且未超过 3 天 UTC 时效。"""
    with _lock:
        if not _key or _unlocked_at is None:
            return False
        return (_now_utc() - _unlocked_at).total_seconds() <= _SESSION_DAYS * 86400


def session_remaining_seconds() -> float:
    """当前会话剩余秒数（未解锁或已过期返回 0）。"""
    with _lock:
        if not _key or _unlocked_at is None:
            return 0.0
        remain = _SESSION_DAYS * 86400 - (_now_utc() - _unlocked_at).total_seconds()
        return max(0.0, remain)


def last_auth_utc() -> str:
    with _lock:
        return (_load_session() or {}).get('last_auth_utc', '')


# ---------------- 加密 / 解密（会话密钥） ----------------

def _require_key() -> bytes:
    if not HAS_CRYPTOGRAPHY or not _key:
        raise RuntimeError('加密会话未解锁，请先输入密码')
    return _key


def is_encrypted(data: bytes) -> bool:
    return isinstance(data, bytes) and data.startswith(_MAGIC)


def encrypt_bytes_for_upload(plain: bytes) -> bytes:
    """将明文加密为传输/落盘容器（密文）。会话未解锁时抛 RuntimeError。"""
    if not HAS_CRYPTOGRAPHY:
        return plain
    fernet = Fernet(base64.urlsafe_b64encode(_require_key()))
    return _MAGIC + fernet.encrypt(plain)


def decrypt_bytes_for_download(data: bytes) -> bytes:
    """解密 upload/落盘容器；非密文原样返回；密码错误抛异常。"""
    if not HAS_CRYPTOGRAPHY:
        return data
    if not is_encrypted(data):
        return data
    fernet = Fernet(base64.urlsafe_b64encode(_require_key()))
    return fernet.decrypt(data[len(_MAGIC):])


def ensure_file_encrypted(path: str) -> bool:
    """把指定本地文件转译为密文（旧明文 → 密文），返回是否已为密文。

    会话未解锁时抛 RuntimeError（绝不把明文落盘）。用于 monitored_files
    列表新增文件时自动转译。
    """
    if not os.path.exists(path):
        return False
    with open(path, 'rb') as f:
        raw = f.read()
    if is_encrypted(raw):
        return True
    enc = encrypt_bytes_for_upload(raw)
    os.makedirs(os.path.dirname(os.path.abspath(path)) or '.', exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'wb') as f:
        f.write(enc)
    os.replace(tmp, path)
    return True


# ---------------- 敏感文件读写（密文落盘，明文在内存） ----------------

def get_plaintext(path: str) -> bytes:
    """读取敏感文件明文（仅内存缓存）。

    - 磁盘为密文：需解锁，解密后缓存
    - 磁盘为旧明文：原样缓存（兼容迁移前）
    - 未解锁且为密文：抛 RuntimeError
    """
    key = os.path.abspath(path)
    with _lock:
        if key in _cache:
            return _cache[key]
    if not os.path.exists(path):
        return b''
    with open(path, 'rb') as f:
        raw = f.read()
    if is_encrypted(raw):
        if not is_unlocked():
            raise RuntimeError('文件已加密，请输入密码解锁后读取')
        plain = decrypt_bytes_for_download(raw)
    else:
        plain = raw  # 旧明文兼容
    with _lock:
        _cache[key] = plain
    return plain


def set_plaintext(path: str, plain: bytes):
    """更新内存明文缓存，并加密写盘（密文落盘）。

    未解锁时不落盘（仅内存缓存，避免明文泄漏）。
    """
    key = os.path.abspath(path)
    with _lock:
        _cache[key] = plain
    if not is_unlocked():
        return
    enc = encrypt_bytes_for_upload(plain)
    os.makedirs(os.path.dirname(os.path.abspath(path)) or '.', exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'wb') as f:
        f.write(enc)
    os.replace(tmp, path)


def clear_cache(path: str = None):
    with _lock:
        if path is None:
            _cache.clear()
        else:
            _cache.pop(os.path.abspath(path), None)
