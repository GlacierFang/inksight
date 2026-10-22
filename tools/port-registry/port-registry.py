#!/usr/bin/env python3
"""
OpenClaw 端口中央登记表管理工具

用法:
  port-registry.py list                    # 列出所有端口分配
  port-registry.py check <port>            # 检查端口使用情况
  port-registry.py register <port> <service> <path> [--owner=xiaoma] [--desc=...]
  port-registry.py verify                  # 交叉验证 nginx / systemd / listening
  port-registry.py audit                   # 审计 systemd scope 归属（对照 services.json）
  port-registry.py find-free               # 找空闲端口（自动跳过占用）

约定:
  - skills 范围 18790-18899
  - utilities 范围 18900-18999
  - 新增端口前必须先 register，否则 verify 会报警
"""
import json
import sys
import os
import subprocess
import argparse
from datetime import datetime
from pathlib import Path

import os as _os
_registry_dir = Path(_os.path.realpath(__file__)).parent
REGISTRY = _registry_dir / "ports.json"
SERVICES = _registry_dir / "services.json"


def load():
    with open(REGISTRY) as f:
        return json.load(f)


def save(data):
    data["updated"] = datetime.now().astimezone().isoformat()
    with open(REGISTRY, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def in_range(port, ranges):
    for name, r in ranges.items():
        if r["start"] <= port <= r["end"]:
            return name
    return None


def cmd_list(args):
    data = load()
    print(f"{'PORT':<7} {'SERVICE':<22} {'PATH':<14} {'OWNER':<8} DESC")
    print("-" * 90)
    for port, info in sorted(data["ports"].items(), key=lambda x: int(x[0])):
        path = info.get("path") or "-"
        print(
            f"{port:<7} {info['service']:<22} {path:<14} {info.get('owner','?'):<8} {info.get('description','')}"
        )
    print()
    # 范围使用率
    print("范围使用率:")
    for name, r in data["ranges"].items():
        if r["end"] - r["start"] < 5:  # 跳过单端口范围
            continue
        used = sum(1 for p in data["ports"] if r["start"] <= int(p) <= r["end"])
        total = r["end"] - r["start"] + 1
        print(f"  {name}: {used}/{total} ({r['description']})")


def cmd_check(args):
    if not args:
        print("用法: check <port>")
        return
    port = args[0]
    data = load()

    # 1. 注册表
    print(f"=== 端口 {port} 状态 ===\n")
    if port in data["ports"]:
        info = data["ports"][port]
        print(f"📋 注册表: {info['service']} ({info.get('description','')})")
        if info.get("path"):
            print(f"   URL path: {info['path']}")
        if info.get("notes"):
            print(f"   备注: {info['notes']}")
    else:
        print("📋 注册表: ❌ 未登记")

    # 2. 实际监听
    try:
        result = subprocess.run(
            ["ss", "-tlnH"], capture_output=True, text=True, timeout=5
        )
        listening = [l for l in result.stdout.split("\n") if f":{port} " in l]
        if listening:
            print(f"\n🔊 监听: ✅")
            for l in listening:
                print(f"   {l}")
        else:
            print(f"\n🔊 监听: ❌（无进程在 listen）")
    except Exception as e:
        print(f"\n🔊 监听: 查询失败 - {e}")

    # 3. nginx 配置
    try:
        with open("/etc/nginx/sites-enabled/openclaw") as f:
            nginx = f.read()
        if f"proxy_pass http://127.0.0.1:{port}" in nginx:
            # 找 path
            import re
            match = re.search(
                rf"location\s+(\S+)\s*\{{[^}}]*proxy_pass\s+http://127\.0\.0\.1:{port}",
                nginx,
            )
            if match:
                print(f"\n🌐 nginx: ✅ 已配置 location {match.group(1)} → :{port}")
            else:
                print(f"\n🌐 nginx: ✅ 有 proxy_pass 指向 :{port}")
        else:
            print(f"\n🌐 nginx: ❌ 没找到 proxy_pass 指向 :{port}")
    except FileNotFoundError:
        print(f"\n🌐 nginx: ⚠️ 没找到 /etc/nginx/sites-enabled/openclaw")
    except PermissionError:
        print(f"\n🌐 nginx: ⚠️ 无权限读取（需要 sudo）")

    # 4. systemd unit
    found = False
    for path in [
        Path.home() / ".config/systemd/user",
        Path("/etc/systemd/system"),
    ]:
        if not path.exists():
            continue
        try:
            for unit in path.glob("openclaw-*.service"):
                content = unit.read_text()
                if f"127.0.0.1:{port}" in content or f"--port {port}" in content or f":{port}" in content and "ExecStart" in content:
                    print(f"\n⚙️  systemd: ✅ {unit}")
                    found = True
        except PermissionError:
            pass
    if not found:
        print(f"\n⚙️  systemd: ❌ 没找到引用 {port} 的 unit 文件")


def cmd_register(args):
    if len(args) < 3:
        print("用法: register <port> <service> <path> [--owner=...] [--desc=...]")
        return
    port_str, service, path = args[0], args[1], args[2]
    try:
        port = int(port_str)
    except ValueError:
        print(f"❌ 端口必须是数字: {port_str}")
        return

    # 解析可选参数
    owner = "unknown"
    desc = ""
    for arg in args[3:]:
        if arg.startswith("--owner="):
            owner = arg.split("=", 1)[1]
        elif arg.startswith("--desc="):
            desc = arg.split("=", 1)[1]

    data = load()

    # 1. 端口未占用
    if port_str in data["ports"]:
        existing = data["ports"][port_str]
        print(f"❌ 端口 {port} 已被 {existing['service']} 占用")
        return

    # 2. 服务名未占用
    for p, info in data["ports"].items():
        if info["service"] == service:
            print(f"❌ 服务名 {service} 已被端口 {p} 使用")
            return

    # 3. 端口在合法范围
    range_name = in_range(port, data["ranges"])
    if range_name is None:
        valid = ", ".join(
            f"{r['start']}-{r['end']}({n})"
            for n, r in data["ranges"].items()
            if r["end"] - r["start"] > 0
        )
        print(f"⚠️  端口 {port} 不在任何已知范围: {valid}")
        resp = input("    仍然继续？(y/N): ")
        if resp.lower() != "y":
            return

    # 4. path 唯一性
    if path and path != "-":
        for p, info in data["ports"].items():
            if info.get("path") == path:
                print(f"❌ URL path {path} 已被 {info['service']}(端口 {p}) 占用")
                return

    # 5. 注册
    data["ports"][port_str] = {
        "service": service,
        "path": path if path != "-" else None,
        "owner": owner,
        "scope": "user",
        "description": desc,
    }
    save(data)
    print(f"✅ 端口 {port} 已分配给 {service}")
    print(f"   path:  {path}")
    print(f"   owner: {owner}")
    print(f"   desc:  {desc}")
    print()
    print("📝 接下来要做:")
    print(f"   1. 写 systemd unit 监听 :{port}")
    print(f"   2. nginx 加 location {path} → :{port}（如果对外）")
    print(f"   3. TOOLS.md 端口表加一行")


def cmd_verify(args):
    data = load()
    print("🔍 交叉验证端口分配\n")

    # 1. 收集实际监听端口
    listening = {}  # port -> [(pid, cmd)]
    try:
        result = subprocess.run(
            ["ss", "-tlnpH"], capture_output=True, text=True, timeout=5
        )
        for line in result.stdout.split("\n"):
            if "LISTEN" not in line:
                continue
            import re
            # ss 输出: LISTEN 0 128 0.0.0.0:80 0.0.0.0:* users:(...)
            # 第 4 列是 local address，形如 "addr:port" 或 "[::]:port"
            parts = line.split()
            if len(parts) < 4:
                continue
            local = parts[3]
            # 提取最后一个 : 后面的数字
            pm = re.search(r":(\d+)$", local)
            if not pm:
                # IPv6 形式 "[::ffff:127.0.0.1]:18807" → 找最后一个 ":"
                # 或者 "*:80" → ":80"
                if "]" in local:
                    port = local.rsplit(":", 1)[-1]
                else:
                    port = local.rsplit(":", 1)[-1]
                if not port.isdigit():
                    continue
            else:
                port = pm.group(1)
            pidm = re.search(r"pid=(\d+)", line)
            pid = pidm.group(1) if pidm else "?"
            listening[port] = listening.get(port, []) + [(pid, line)]
    except Exception as e:
        print(f"ss 失败: {e}")

    # 2. 已登记但没在监听
    print("--- 状态 1: 登记了但没在监听 ---")
    any1 = False
    for port, info in data["ports"].items():
        if port not in listening:
            # 跳过系统端口
            if info.get("scope") == "system" and info.get("service") != "openclaw-gateway-internal":
                continue
            print(f"  ⚠️  {port} {info['service']}: 登记了但没在 listening（可能挂了或没启）")
            any1 = True
    if not any1:
        print("  ✅ 无")

    # 3. 在监听但没登记
    print("\n--- 状态 2: 在监听但没在 ports.json 登记 ---")
    known_ports = set(data["ports"].keys())
    # 排除系统端口（SSH 22、HTTP 80、PostgreSQL 5432 等）
    system_ports = {22, 80, 443, 5432, 3306, 6379, 8080, 8443, 9000, 9090}
    any2 = False
    for port in sorted(listening.keys()):
        if port not in known_ports and int(port) not in system_ports:
            print(f"  ⚠️  {port}: 在监听但未登记 (pid {listening[port][0][0]})")
            any2 = True
    if not any2:
        print("  ✅ 无（系统端口已豁免）")

    # 4. 路径 vs nginx（仅对外服务）
    print("\n--- 状态 3: nginx 路径 vs 登记 ---")
    try:
        with open("/etc/nginx/sites-enabled/openclaw") as f:
            nginx = f.read()
        import re
        nginx_locations = re.findall(
            r"location\s+(\S+)\s*\{[^}]*proxy_pass\s+http://127\.0\.0\.1:(\d+)",
            nginx,
        )
        nginx_map = {p: path for path, p in nginx_locations}

        any3 = False
        for port, info in data["ports"].items():
            path = info.get("path")
            if not path:
                continue
            if port in nginx_map:
                if nginx_map[port] == path:
                    print(f"  ✅ {port} {info['service']}: nginx {path} 正确")
                else:
                    # 可能是同一端口有多个 location（合法拆分，如 /food/、/food/api/）
                    all_paths_for_port = [p for p, pt in nginx_locations if pt == port]
                    if path in all_paths_for_port:
                        print(f"  ✅ {port} {info['service']}: nginx {path} 正确（其他 location: {all_paths_for_port}）")
                    else:
                        print(f"  ⚠️  {port} {info['service']}: nginx 没找到 {path}（只有 {all_paths_for_port}）")
                        any3 = True
            else:
                if info.get("scope") not in ("system",):
                    # scope=user 或 both 的服务，对外需要 nginx location
                    if info.get("service") not in ("organize", "natfrp"):
                        print(f"  ⚠️  {port} {info['service']}: 对外服务但 nginx 找不到 location {path}")
                        any3 = True
        if not any3:
            print("  ✅ 无")
    except PermissionError:
        print("  ⚠️  无权限读 nginx 配置（需 sudo）")
    except FileNotFoundError:
        print("  ⚠️  /etc/nginx/sites-enabled/openclaw 不存在")

    # 5. systemd unit
    print("\n--- 状态 4: systemd unit 存在性 ---")
    user_dir = Path.home() / ".config/systemd/user"
    sys_dir = Path("/etc/systemd/system")
    any4 = False
    for port, info in data["ports"].items():
        if info.get("scope") == "system" and info.get("service") != "openclaw-gateway":
            continue
        service = info["service"]
        if service == "openclaw-gateway":
            continue
        # 找对应的 unit 文件
        candidates = [
            user_dir / f"openclaw-{service}.service",
            sys_dir / f"openclaw-{service}.service",
        ]
        found = any(p.exists() for p in candidates)
        if found:
            print(f"  ✅ {port} {service}: systemd unit 存在")
        else:
            print(f"  ⚠️  {port} {service}: 没找到 systemd unit（候选: {[str(p) for p in candidates]}）")
            any4 = True
    if not any4:
        print("  ✅ 无")

    # 6. 服务名重复
    print("\n--- 状态 5: 服务名 / 端口 / 路径 唯一性 ---")
    services = {}
    for port, info in data["ports"].items():
        s = info["service"]
        if s in services:
            print(f"  ❌ 服务名 {s} 重复：{services[s]} 和 {port}")
        services[s] = port
    paths = {}
    for port, info in data["ports"].items():
        p = info.get("path")
        if not p:
            continue
        if p in paths:
            print(f"  ❌ 路径 {p} 重复：{paths[p]} 和 {port}")
        paths[p] = port
    if not any(s in services for s in set()):
        print("  ✅ 唯一性 OK")

    print("\n" + "=" * 50)
    print("验证完成")


def _extra_units(registered, scope):
    """从 services.json 取出显式指名的 unit（unit 字段），按 scope 分组

    背景：审计靠 openclaw-* 通配符发现单元，但有些服务的 unit 不带该前缀
    （如 natfrp 的 sakura-frp.service），只能靠注册表指名。
    """
    extra = {}
    for name, reg in registered.items():
        if not isinstance(reg, dict):
            continue
        unit = reg.get("unit")
        if not unit:
            continue
        declared = reg.get("scope", "unknown")
        if declared in (scope, "both"):
            extra[name] = unit
    return extra


def get_unit_state(scope="system", extra=None):
    """返回 {服务短名: {enabled, active, running}} 字典（已去掉 openclaw- 前缀）"""
    import re
    import os as _os
    result = {}
    
    env = None
    if scope == "user":
        env = dict(_os.environ)
        env["XDG_RUNTIME_DIR"] = f"/run/user/{_os.getuid()}"
        env["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path=/run/user/{_os.getuid()}/bus"
        cmd_prefix = ["systemctl", "--user"]
    else:
        cmd_prefix = ["systemctl"]
    
    # 收集所有 openclaw-* unit（包括 timer）
    for kind in ["service", "timer"]:
        try:
            r = subprocess.run(
                cmd_prefix + ["list-unit-files", f"openclaw-*.{kind}", "--no-pager", "--no-legend"],
                capture_output=True, text=True, timeout=5, env=env
            )
            for line in r.stdout.strip().split("\n"):
                if not line.strip():
                    continue
                parts = line.split()
                if len(parts) < 2:
                    continue
                unit_file = parts[0]
                enabled = parts[1]
                # 提取服务名（去掉 .service / .timer，再去掉 openclaw- 前缀）
                svc = re.sub(r"\.(service|timer)$", "", unit_file)
                svc = re.sub(r"^openclaw-", "", svc)
                if svc not in result:
                    result[svc] = {"enabled": enabled, "active": False, "running": False, "types": []}
                result[svc]["types"].append(kind)
                if enabled == "enabled":
                    result[svc]["enabled"] = "enabled"
        except Exception:
            pass
    
    # 收集 active 状态
    try:
        r = subprocess.run(
            cmd_prefix + ["list-units", "openclaw-*", "--all", "--no-pager", "--no-legend"],
            capture_output=True, text=True, timeout=5, env=env
        )
        for line in r.stdout.strip().split("\n"):
            if not line.strip() or line.startswith("●"):
                continue
            parts = line.split()
            if len(parts) < 4:
                continue
            unit_name = parts[0]
            load, active, sub = parts[1], parts[2], parts[3]
            svc = re.sub(r"\.(service|timer)$", "", unit_name)
            svc = re.sub(r"^openclaw-", "", svc)
            if svc in result:
                result[svc]["active"] = (active == "active")
                result[svc]["running"] = (sub == "running")
    except Exception:
        pass

    # 收集显式指名的 unit（services.json 的 unit 字段）
    for name, unit_file in (extra or {}).items():
        entry = result.setdefault(
            name, {"enabled": "-", "active": False, "running": False, "types": []}
        )
        if "service" not in entry["types"]:
            entry["types"].append("service")
        try:
            r = subprocess.run(
                cmd_prefix + ["list-unit-files", unit_file, "--no-pager", "--no-legend"],
                capture_output=True, text=True, timeout=5, env=env
            )
            for line in r.stdout.strip().split("\n"):
                parts = line.split()
                if len(parts) < 2:
                    continue
                state = parts[1]
                if state == "enabled":
                    entry["enabled"] = "enabled"
                elif entry["enabled"] == "-":
                    entry["enabled"] = state
        except Exception:
            pass
        try:
            r = subprocess.run(
                cmd_prefix + ["is-active", unit_file],
                capture_output=True, text=True, timeout=5, env=env
            )
            state = r.stdout.strip()
            entry["active"] = (state == "active")
            entry["running"] = (state == "active")
        except Exception:
            pass

    return result


def cmd_audit(args):
    """审计 systemd scope 归属 vs 登记"""
    import re
    
    # 加载登记表
    if not SERVICES.exists():
        print("❌ services.json 不存在，请先创建服务归属登记表")
        return
    with open(SERVICES) as f:
        services_data = json.load(f)
    registered = services_data.get("services", {})
    
    # 收集 systemd 实际状态
    user_units = get_unit_state("user", _extra_units(registered, "user"))
    system_units = get_unit_state("system", _extra_units(registered, "system"))
    
    # 合并所有已知服务名
    all_units = {}
    for name, info in system_units.items():
        all_units[name] = {"system": info, "user": user_units.get(name, {"enabled": "-", "active": False, "running": False})}
    for name, info in user_units.items():
        if name not in all_units:
            all_units[name] = {"system": {"enabled": "-", "active": False, "running": False}, "user": info}
    
    issues = 0
    warnings = 0
    
    print("🔍 systemd Scope 归属审计\n")
    print("对照: services.json（登记）←→ systemctl user + system（实际）\n")
    
    # === 检查 1: 双重 enable（已登记的双 scope 除外） ===
    print("━" * 60)
    print("🔴 检查 1: 双重 enable（同一服务在两个 scope 都 enabled）")
    print("━" * 60)
    for name, scopes in sorted(all_units.items()):
        sys_enabled = scopes["system"]["enabled"] == "enabled"
        usr_enabled = scopes["user"]["enabled"] == "enabled"
        
        if sys_enabled and usr_enabled:
            reg = registered.get(name, {})
            declared_scope = reg.get("scope", "unknown")
            notes = reg.get("notes", "")
            
            # natfrp 是合法双通道
            if declared_scope == "both":
                print(f"  ✅ {name}: both（设计如此: {reg.get('desc','')}）")
                continue
            # knowledge-briefing 是合法拆分（timer user + daemon system）
            if "合法拆分" in notes:
                print(f"  ✅ {name}: 合法拆分（{notes}）")
                continue
            
            print(f"  ❌ {name}: system=enabled, user=enabled  |  登记: {declared_scope}")
            issues += 1
    
    if issues == 0:
        print("  ✅ 无双重 enable 冲突")
    
    # === 检查 2: 实际 scope 与登记不符 ===
    print("\n" + "━" * 60)
    print("🔴 检查 2: enabled scope 与 services.json 登记不符")
    print("━" * 60)
    check2_ok = True
    for name, reg in sorted(registered.items()):
        declared = reg.get("scope", "unknown")
        notes = reg.get("notes", "")
        if declared in ("both", "unknown"):
            continue
        if "合法拆分" in notes:
            continue
        
        sys_enabled = all_units.get(name, {}).get("system", {}).get("enabled", "-") == "enabled"
        usr_enabled = all_units.get(name, {}).get("user", {}).get("enabled", "-") == "enabled"
        
        unit_name = f"openclaw-{name}"
        
        if declared == "user" and sys_enabled:
            print(f"  ❌ {name}: 登记 user scope，但 system scope enabled")
            print(f"     → 建议: sudo systemctl disable {unit_name}.service")
            issues += 1
            check2_ok = False
        elif declared == "system" and usr_enabled:
            print(f"  ❌ {name}: 登记 system scope，但 user scope enabled")
            print(f"     → 建议: systemctl --user disable {unit_name}.service")
            issues += 1
            check2_ok = False
    
    if check2_ok:
        print("  ✅ 所有服务 scope 与登记一致")
    
    # === 检查 3: 已登记但没在任何 scope enable ===
    print("\n" + "━" * 60)
    print("🟡 检查 3: 已登记但未在任何 scope enabled")
    print("━" * 60)
    check3_ok = True
    for name, reg in sorted(registered.items()):
        sys_enabled = all_units.get(name, {}).get("system", {}).get("enabled", "-") == "enabled"
        usr_enabled = all_units.get(name, {}).get("user", {}).get("enabled", "-") == "enabled"
        
        if not sys_enabled and not usr_enabled:
            sys_active = all_units.get(name, {}).get("system", {}).get("active", False)
            usr_active = all_units.get(name, {}).get("user", {}).get("active", False)
            print(f"  ⚠️  {name}: 登记了但两边都未 enabled（active: system={sys_active}, user={usr_active}）")
            warnings += 1
            check3_ok = False
    
    if check3_ok:
        print("  ✅ 所有登记服务都有对应 enabled unit")
    
    # === 检查 4: enabled 但未在 services.json 登记 ===
    print("\n" + "━" * 60)
    print("🟡 检查 4: systemd enabled 但未在 services.json 登记")
    print("━" * 60)
    check4_ok = True
    for name, scopes in sorted(all_units.items()):
        sys_enabled = scopes["system"]["enabled"] == "enabled"
        usr_enabled = scopes["user"]["enabled"] == "enabled"
        
        if (sys_enabled or usr_enabled) and name not in registered:
            where = []
            if sys_enabled:
                where.append("system")
            if usr_enabled:
                where.append("user")
            print(f"  ⚠️  {name}: enabled ({'/'.join(where)}) 但未登记")
            warnings += 1
            check4_ok = False
    
    if check4_ok:
        print("  ✅ 所有 enabled 服务都已登记")
    
    # === 检查 5: port 级 — 谁在监听 vs 登记 scope ===
    print("\n" + "━" * 60)
    print("🟡 检查 5: 端口监听进程的 systemd scope 与登记是否一致")
    print("━" * 60)
    check5_ok = True
    try:
        r = subprocess.run(
            ["ss", "-tlnpH"], capture_output=True, text=True, timeout=5
        )
        # 建立 port -> pid 映射
        port_pid = {}
        for line in r.stdout.split("\n"):
            if "LISTEN" not in line:
                continue
            pm = re.search(r":(\d+)\s", line)
            if not pm:
                continue
            port = pm.group(1)
            pidm = re.search(r"pid=(\d+)", line)
            if pidm:
                port_pid[port] = pidm.group(1)
            elif port not in port_pid:
                port_pid[port] = "unknown-pid"
        
        # 找出每个 port 对应的 systemd unit scope
        for name, reg in sorted(registered.items()):
            port = reg.get("port")
            if not port:
                continue
            declared = reg.get("scope")
            if declared == "both":
                continue
            
            port_str = str(port)
            if port_str not in port_pid:
                print(f"  ⚠️  {name}: 端口 {port} 未在监听")
                warnings += 1
                continue
            
            pid = port_pid[port_str]
            # 查这个 PID 属于哪个 systemd scope
            try:
                pr = subprocess.run(
                    ["ps", "-o", "unit=", "-p", pid], capture_output=True, text=True, timeout=3
                )
                unit = pr.stdout.strip()
                if unit:
                    # 判断 unit 是 user 还是 system scope
                    # user scope unit 格式: user@1000.service/... 或 app-...
                    # system scope unit 格式: openclaw-xxx.service
                    is_user = "user@" in unit
                    actual_scope = "user" if is_user else "system"
                    if actual_scope != declared:
                        print(f"  ❌ {name}: 端口 {port} 由 {scope} scope 进程持有，但登记为 {declared}")
                        issues += 1
                        check5_ok = False
            except Exception:
                pass
    except Exception:
        print("  ⚠️  无法检查端口归属")
    
    if check5_ok:
        print("  ✅ 端口监听 scope 与登记一致")
    
    # === 总结 ===
    print("\n" + "=" * 60)
    total = issues + warnings
    if total == 0:
        print("✅ 全部通过！systemd scope 审计无问题")
    else:
        print(f"审计完成: 🔴 {issues} 个错误  🟡 {warnings} 个警告")
    
    print("\n说明:")
    print("  🔴 = 必须修复（双重 enable、scope 不符）")
    print("  🟡 = 建议关注（未登记、未启用、端口未监听）")
    return 0 if issues == 0 else 1


def cmd_find_free(args):
    """找一个空闲端口（在 skills 范围内）"""
    data = load()
    used = set(int(p) for p in data["ports"].keys())
    listening = set()
    try:
        result = subprocess.run(
            ["ss", "-tlnH"], capture_output=True, text=True, timeout=5
        )
        import re
        for line in result.stdout.split("\n"):
            m = re.search(r":(\d+)\b", line)
            if m:
                listening.add(int(m.group(1)))
    except Exception:
        pass

    print("空闲端口（skills 范围 18790-18899，避开已用和正在 listening）:\n")
    candidates = []
    for port in range(18790, 18900):
        if port not in used and port not in listening:
            candidates.append(port)
    # 显示前 10 个
    for p in candidates[:10]:
        print(f"  {p}")
    if len(candidates) > 10:
        print(f"  ... 还有 {len(candidates) - 10} 个")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 0
    cmd = sys.argv[1]
    args = sys.argv[2:]
    cmds = {
        "list": cmd_list,
        "check": cmd_check,
        "register": cmd_register,
        "verify": cmd_verify,
        "audit": cmd_audit,
        "find-free": cmd_find_free,
    }
    if cmd in cmds:
        return cmds[cmd](args) or 0
    else:
        print(f"未知命令: {cmd}")
        print(f"可用: {', '.join(cmds.keys())}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
