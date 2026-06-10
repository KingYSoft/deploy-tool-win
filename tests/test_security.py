import hashlib
import hmac

from webhook_deployer.security import verify_github_signature


def test_verify_github_signature_accepts_valid_sha256_signature():
    body = b'{"ref":"refs/heads/main"}'
    secret = "top-secret"
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

    assert verify_github_signature(body, f"sha256={digest}", secret)


def test_verify_github_signature_rejects_invalid_signature():
    body = b'{"ref":"refs/heads/main"}'

    assert not verify_github_signature(body, "sha256=bad", "top-secret")


def test_verify_github_signature_rejects_missing_secret():
    body = b"{}"

    assert not verify_github_signature(body, "sha256=abc", "")

