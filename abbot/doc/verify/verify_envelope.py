"""abbot 05 契约端到端验证：真实 FastAPI 服务 + HMAC-SHA256 + AES-128-CTR 双向信封"""
import base64, hashlib, hmac, threading, time
import httpx, uvicorn
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

SEC = bytes.fromhex("00112233445566778899aabbccddeeff0123456789abcdef0123456789abcdef")
K16 = SEC[:16]
IV = bytes.fromhex("0102030405060708090a0b0c0d0e0f10")
PATH = "/api/v1/echo"

app = FastAPI()


def enc(plain: bytes, iv: bytes = IV) -> bytes:
    e = Cipher(algorithms.AES(K16), modes.CTR(iv)).encryptor()
    return iv + e.update(plain) + e.finalize()


def sign(method: str, path: str, ts: str, raw: bytes) -> str:
    return hmac.new(SEC, f"{method}\n{path}\n{ts}\n{hashlib.sha256(raw).hexdigest()}".encode(),
                    hashlib.sha256).hexdigest()


@app.get("/ping")
async def ping():
    return {"code": 0, "msg": "ok", "payload": ""}


@app.post(PATH)
async def echo(req: Request):
    raw = await req.body()
    ts = req.headers.get("X-Timestamp", "")
    got_sig = req.headers.get("X-Signature", "")
    want_sig = sign("POST", PATH, ts, raw)
    iv, ct = raw[:16], raw[16:]
    d = Cipher(algorithms.AES(K16), modes.CTR(iv)).decryptor()
    pt = d.update(ct) + d.finalize()
    resp_raw = JSONResponse({"code": 0, "msg": "ok", "payload": base64.b64encode(raw).decode(),
                              "decrypted": pt.decode(), "sig_ok": got_sig == want_sig})
    body = resp_raw.body
    resp_raw.headers["X-Resp-Signature"] = hmac.new(
        SEC, f"{ts}\n{hashlib.sha256(body).hexdigest()}".encode(), hashlib.sha256).hexdigest()
    return resp_raw


cfg = uvicorn.Config(app, host="127.0.0.1", port=18977, log_level="error")
srv = uvicorn.Server(cfg)
threading.Thread(target=srv.run, daemon=True).start()
for _ in range(80):
    if srv.started:
        break
    time.sleep(0.25)

ok = True
C = httpx.Client(trust_env=False)
r = C.get("http://127.0.0.1:18977/ping")
print("[1] GET /ping ->", r.status_code, r.text.strip())
ok &= (r.status_code == 200)

payload = enc(b'{"ping":1}')
ts = "1795412000"
sig = sign("POST", PATH, ts, payload)
r = C.post(f"http://127.0.0.1:18977{PATH}", content=payload,
              headers={"Content-Type": "application/json", "X-Timestamp": ts, "X-Signature": sig})
print("[2] POST /api/v1/echo ->", r.status_code)
j = r.json()
print("    请求签名校验通过 :", j["sig_ok"])
print("    服务端解密明文   :", j["decrypted"])
print("    响应验签头存在   :", "X-Resp-Signature" in r.headers)

resp_sig = r.headers.get("X-Resp-Signature", "")
want_resp = hmac.new(SEC, f"{ts}\n{hashlib.sha256(r.content).hexdigest()}".encode(),
                      hashlib.sha256).hexdigest()
print("    响应验签一致     :", resp_sig == want_resp)

envelope = base64.b64decode(j["payload"])
print("[3] 信封结构 IV||CT 长度:", len(envelope), "(16+10=26)")
d = Cipher(algorithms.AES(K16), modes.CTR(envelope[:16])).decryptor()
print("    回环解密明文     :", (d.update(envelope[16:]) + d.finalize()).decode())

ok &= j["sig_ok"] and (resp_sig == want_resp) and j["decrypted"] == '{"ping":1}'
print("\nRESULT:", "ALL PASS" if ok else "FAIL")
srv.should_exit = True
