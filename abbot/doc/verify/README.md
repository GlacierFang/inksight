# 端云安全信封验证脚本

两个脚本，共同守住 05 契约 §3.2 / §3.4。

| 脚本 | 验证范围 | 依赖 |
|---|---|---|
| `gen_vectors.py` | **产出**：先过 NIST SP 800-38A 自检，再产出 `05` §3.4 锁定向量组；`--update-doc` 回写文档 | `cryptography` |
| `verify_vectors.py` | **静态**：从 `05` 文档正文**抓取**向量并用标准实现复算，证明文档与实现自洽 | `cryptography` |
| `verify_envelope.py` | **动态**：真实起 FastAPI 服务，走完整 HTTP 链路 | `fastapi uvicorn httpx cryptography` |

**用法**（从 `abbot/` 目录下执行）：

```bash
./.venv/bin/python doc/verify/gen_vectors.py --emit        # 产出锁定向量（核对用）
./.venv/bin/python doc/verify/gen_vectors.py --update-doc  # 回写 05 §3.4 向量区块
./.venv/bin/python doc/verify/verify_vectors.py     # 文档向量自洽性（改 05 文档后必跑）
./.venv/bin/python doc/verify/verify_envelope.py    # 端到端信封（改安全模块后必跑）
```

> **三个脚本的分工**：`gen_vectors` 是**唯一产出源**（NIST 自检不过则拒绝产出）；`verify_vectors` 是**反向闸门**（从文档抓取重算，改文档不改实现必报 FAIL）；`verify_envelope` 是**端到端闭环**。向量数值**一律由 `gen_vectors` 产出，禁止手抄** —— 本项目历史上的错误向量正是手抄所致。

> 本机系统 Python 受 PEP 668 保护（`pip install` 直接被拒），依赖装在项目内的 `abbot/.venv`：
> `python3 -m venv --system-site-packages .venv` 后
> `env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY ./.venv/bin/python -m pip install "fastapi>=0.115" "pypdf"`
> （**必须 unset 代理变量**，否则 pypi 走出口代理会被挡成 403）

**它验证什么**：
1. `GET /ping` 标准响应信封 `{code,msg,payload}`；
2. 请求头 `X-Signature` 验签（HMAC-SHA256 over `METHOD\nPATH\nTS\nSHA256(body)`）；
3. 应用层信封 `Base64(IV16 || AES-128-CTR ciphertext)` 的服务端解密回环；
4. 响应头 `X-Resp-Signature` 验签（HMAC over `REQ_TS\nSHA256(resp_body)`）；
5. 信封结构长度校验（16 + 明文长度）。

**注意**：脚本内 `httpx.Client(trust_env=False)` 不可省略 ——
否则 loopback 请求会被出口代理劫持并返回 403。

## 05 §3.4 向量组修订

原文档向量组不可复现（详见 `../../ALIGN-NOTES.md` A 项）。修订后的权威向量（已写入 05 文档正文）：

```text
AES-128 key (first16) : 00112233445566778899aabbccddeeff
IV_base               : 0102030405060708090a0b0c0d0e0f10
plaintext             : 7b2270696e67223a317d  ({"ping":1})
CTR block0 cipher     : c44759d703127191d6c9
CTR block2 nonce      : 0102030405060708090a0b0c0d0e0f12
CTR block2 cipher     : 41da37cc48e5aa3f8d06
envelope payload b64  : AQIDBAUGBwgJCgsMDQ4PEMRHWdcDEnGR1sk=

REQ raw body (50 bytes): {"payload":"AQIDBAUGBwgJCgsMDQ4PEMRHWdcDEnGR1sk="}
REQ raw_body_sha256   : 0ac4d1b2be7fff8b8d2fabfd520add4656718547d456b728ab3f0049244fee0a
REQ X-Signature       : e93d4a3e4aaeb49a2b413c5edfb7c7b877d9f7a85bf1d4e5c01fcdab7cf45b35

RESP raw body (34 bytes): {"code":0,"msg":"ok","payload":""}
RESP raw_body_sha256  : 5eaa24073527cf9b66a37318bd50123aac4d649814ea6d65281189b6bef49ef3
RESP X-Resp-Signature : c4fc10efeec73e428b7e63bcb3777658e11a850ccf2a75047847771556f0ce3c
```

生成命令（须先过 NIST SP800-38A F.5.1 CTR-AES128 官方向量自检，再产出上表）。

`verify_vectors.py` 会直接读取上表所在文档的正文并逐项复算 —— **改了 05 文档里的向量而不改实现（或反之），该脚本必报 FAIL**。
