import hashlib
import hmac


def verify_github_signature(body: bytes, signature_header: str | None, secret: str) -> bool:
    # GitHub 使用 HMAC-SHA256 签名请求体；缺少 secret 或签名时一律拒绝。
    if not secret or not signature_header:
        return False
    prefix = "sha256="
    if not signature_header.startswith(prefix):
        return False

    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    supplied = signature_header[len(prefix) :]
    # compare_digest 避免普通字符串比较带来的时序侧信道风险。
    return hmac.compare_digest(expected, supplied)
