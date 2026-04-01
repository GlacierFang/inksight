---
name: gallery
description: OpenClaw 瀑布流相册系统。支持通过飞书/微信上传图片视频，局域网网页浏览。
---

# OpenClaw 相册

## 功能特性

- 📷 **瀑布流画廊** - 响应式 masonry 布局
- 📱 **多来源支持** - 自动识别飞书/微信上传
- 🔍 **筛选功能** - 按日期、按来源、按类型筛选
- 🖼️ **详情查看** - 点击查看大图/视频播放
- 📅 **日期归档** - 自动按日期整理媒体文件

## 访问地址

- **本地**：`http://localhost:18790/gallery`
- **局域网**：`http://<设备IP>:18790/gallery`

## 目录结构

```
/home/open/.openclaw/media/
├── inbound/          # 原始文件（OpenClaw 写入）
├── photos/           # 按日期整理的图片
│   └── YYYY-MM-DD/
└── videos/           # 按日期整理的视频
    └── YYYY-MM-DD/
```

## 媒体来源识别

| 来源 | 标识 |
|------|------|
| 飞书 | 文件名含 feishu、ou_ |
| 微信 | 文件名含 weixin、wx_ |

## 服务管理

```bash
# 启动服务
sudo systemctl start openclaw-gallery

# 停止服务
sudo systemctl stop openclaw-gallery

# 重启服务
sudo systemctl restart openclaw-gallery

# 查看状态
sudo systemctl status openclaw-gallery

# 设置开机启动
sudo systemctl enable openclaw-gallery
```

## 手动整理媒体

```bash
python3 /home/open/.openclaw/workspace/skills/gallery/organize.py
```

## 故障排查

1. **页面打不开**：检查服务是否运行 `sudo systemctl status openclaw-gallery`
2. **没有内容**：检查 inbound/ 是否有文件，运行 organize.py
3. **来源显示"未知"**：检查是否有 .meta 文件
