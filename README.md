# Server Dashboard

一个极简的自托管服务器状态看板:实时指标 + 服务启停 + 网络历史曲线。

- 后端:Python 3 + Flask + psutil(单文件 `app.py`)
- 前端:单页 HTML + 原生 JS/SVG(无第三方依赖、无 CDN)
- 鉴权:HTTP Basic Auth
- 部署:systemd(以 root 运行,便于控制服务)+ 可选 Caddy 反向代理

## 功能

- **实时指标**:CPU 使用率/核心数/Load、内存、磁盘 `/`、网络实时上下行、开机时长
- **网络曲线**:最近约 6 分钟的上下行速率(每 3 秒采样一次)
- **服务状态**:可配置的项目列表,默认包含
  - TeamSpeak(进程 `ts3server`)
  - WebSpeak(Docker Compose)
  - Minecraft GTNH(screen `gtnh`,并解析日志显示在线人数与玩家名)
  - CS2(screen `cs2`)
  - Piik(Docker Compose)
- **一键启停**:每个服务都有启动/停止按钮,状态自动刷新

## 快速开始

```bash
sudo apt install -y python3-flask python3-psutil
sudo mkdir -p /opt/server-dashboard/templates
sudo cp app.py /opt/server-dashboard/
sudo cp templates/index.html /opt/server-dashboard/templates/
sudo cp deploy/server-dashboard.service /etc/systemd/system/
# 编辑 service 里的 DASH_USER / DASH_PASS
sudo systemctl daemon-reload
sudo systemctl enable --now server-dashboard
```

默认监听 `127.0.0.1:8088`,建议用反向代理 + HTTPS 暴露。

## 配置

| 环境变量 | 说明 | 默认 |
|---|---|---|
| `DASH_USER` | Basic Auth 用户名 | `admin` |
| `DASH_PASS` | Basic Auth 密码 | `change-me` |

服务路径/命令在 `app.py` 顶部的常量与 `SERVICES` 字典中定义,按需修改。

## 反向代理(Caddy)

```caddy
status.example.com {
    reverse_proxy 127.0.0.1:8088
}
```

## 注意

- 看板进程需要能执行 `screen` / `docker` / `pkill` 等,因此示例 unit 以 root 运行。
- Minecraft 在线人数通过解析 `~/gtnh-run.log` 的 join/leave 事件得出,日志路径可在 `app.py` 修改。
