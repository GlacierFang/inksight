# InkSight 全站样式丢失事故（2026-10-31，已修复）

## 现象
所有页面元素丢样式（边框/背景/间距全无），但 CSS 文件正常返回 200、内容非空、
Content-Type 正确、nginx 日志零 404。

## 根因
Tailwind v4 的自动扫描**遵守 .gitignore**。
我把 skills/inksight/webapp/ 整目录忽略改成白名单放行（webapp/* + ! 反向规则），
结果 components/、lib/ 等源码被 Tailwind 当作「非源码」跳过 → 工具类被 purge。
类没了，元素就没样式 —— 看起来像「丢了一堆东西」。

## 对照实验（同一份源码，只改 .gitignore 各编译一遍）
| .gitignore | Tailwind 产出 |
|---|---|
| 原始（webapp/ 整目录忽略） | 50172 B / **302 类** |
| 白名单（webapp/* 放行） | 26612 B / **175 类** |
| @source "../" 显式声明 | 26612 B / **175 类**（无效，gitignore 过滤依然生效） |

## 修法
.gitignore 恢复原样（skills/inksight/webapp/ 整目录忽略）。
清 .next + node_modules/.cache 后重建 → CSS 48719 B / 301 类（新 hash f90452c1eaa5100f.css）。

## 关键认知（下次别再踩）
**已入库的文件本来就不受 gitignore 影响。** git ls-files 仍能看到
skills/inksight/webapp/app/config/page.tsx 与 gallery/page.tsx —— 纳管不需要改 gitignore。
改 gitignore 白名单的唯一效果就是「让 Tailwind 少扫文件」。

**排查这类问题的通用手法**：用 postcss + @tailwindcss/postcss 直接编译 globals.css，
对比不同配置下的 class 数量 —— 几秒钟就能定位「类被 purge」这类问题，比在浏览器里猜快得多。

## 教训
改动 build 工具链的配置（.gitignore / postcss / tailwind config）前，
先确认它是否影响构建时的源码扫描。Tailwind v4 是自动扫描，没有 tailwind.config.js，
源码集合完全由文件系统 + gitignore 决定。