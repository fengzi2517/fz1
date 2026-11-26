"""P0-1 鉴权与安全校验测试"""
import pytest
from fastapi import HTTPException

from app.core.security import (
    create_token, decode_token, hash_password, validate_id, verify_password)
from app.store import store


class TestPassword:
    def test_roundtrip(self):
        h = hash_password("s3cret!")
        assert verify_password("s3cret!", h)
        assert not verify_password("wrong", h)

    def test_salt_unique(self):
        assert hash_password("a") != hash_password("a")

    def test_garbage_hash(self):
        assert not verify_password("x", "not-a-hash")
        assert not verify_password("x", "")


class TestToken:
    def test_roundtrip(self):
        tok = create_token("u_abc123")
        assert decode_token(tok) == "u_abc123"

    def test_expired(self):
        assert decode_token(create_token("u_1", ttl=-10)) is None

    def test_tampered(self):
        tok = create_token("u_1")
        h, p, s = tok.split(".")
        assert decode_token(f"{h}.{p}.{s[:-2]}xx") is None
        assert decode_token("garbage") is None
        assert decode_token("") is None

    def test_wrong_secret(self):
        from app.core import security
        tok = create_token("u_1")
        orig = security.AUTH_SECRET
        security.AUTH_SECRET = "other-secret"
        try:
            assert decode_token(tok) is None
        finally:
            security.AUTH_SECRET = orig


class TestValidateId:
    @pytest.mark.parametrize("bad", ["../etc", "a/b", "中文", "", "x" * 65, ".."])
    def test_rejects(self, bad):
        with pytest.raises(HTTPException) as e:
            validate_id(bad, "user_id")
        assert e.value.status_code == 400

    def test_accepts(self):
        assert validate_id("u_9f8a7b6c5d4e") == "u_9f8a7b6c5d4e"


class TestApiAuth:
    def test_missing_token_401(self, client):
        assert client.get("/api/characters").status_code == 401

    def test_bad_token_401(self, client):
        r = client.get("/api/characters",
                       headers={"Authorization": "Bearer not.a.jwt"})
        assert r.status_code == 401

    def test_token_user_ok(self, client, user):
        assert client.get("/api/characters", headers=user["headers"]).status_code == 200

    def test_traversal_blocked(self, client, user):
        r = client.get("/api/chat/history",
                       params={"character_id": "../../etc/passwd"},
                       headers=user["headers"])
        assert r.status_code == 400

    def test_tenant_isolation(self, client, user, memory_db):
        from app.core.security import create_token as ct
        from app.core.security import hash_password as hp
        uid2 = store.create_user("intruder", hp("pass1234"))
        sid = store.create_session(user["id"], "char", character_id="c_1")
        r = client.get("/api/chat/sessions",
                       headers={"Authorization": f"Bearer {ct(uid2)}"})
        assert r.status_code == 200
        assert all(s["id"] != sid for s in r.json())
        r = client.delete(f"/api/chat/sessions/{sid}",
                          headers={"Authorization": f"Bearer {ct(uid2)}"})
        assert r.status_code == 404

    def test_register_login(self, client):
        r = client.post("/api/auth/register",
                        json={"username": "newbie", "password": "abcdef"})
        assert r.status_code == 200
        tok = r.json()["token"]
        assert decode_token(tok) == r.json()["user_id"]
        r = client.post("/api/auth/register",
                        json={"username": "newbie", "password": "abcdef"})
        assert r.status_code == 409
        r = client.post("/api/auth/login",
                        json={"username": "newbie", "password": "wrongpass"})
        assert r.status_code == 401
        r = client.post("/api/auth/login",
                        json={"username": "newbie", "password": "abcdef"})
        assert r.status_code == 200
