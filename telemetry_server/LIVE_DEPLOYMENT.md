# DragonScience 统计后台：当前部署

部署日期：2026-10-29。以下是当前服务器的实际配置；`DEPLOY.md` 中的新建子域名和 Nginx 步骤是其他环境的示例，不需要在这台服务器重复执行。

## 如何使用

- 统计页面：<https://dragonscience.top/bet-stats/>。
- 浏览器会要求输入独立的统计后台账号、密码。登录信息保存在本机项目的 `outputs/bet-stats-access.txt`，不是宝塔或 WordPress 密码。
- 页面优先显示版本分布、最近 7 天的 IP 与版本，还包括活跃安装、启动次数、系统与显示环境、导入/导出结果、耗时和分析页面浏览。
- 刷新页面即可查看最新统计。用户需要使用包含统计功能的新客户端；1.3.0 新安装默认开启，保留此前保存的关闭偏好。
- 1.3.0 的“隐私设置”入口暂时隐藏。测试默认开启时使用独立的临时用户配置，不覆盖真实用户的偏好。禁用方法见项目 README 的“使用统计”。

事件保留 90 天，原始 IP 保留 7 天。IP 是网络出口地址，安装标识是随机 UUID；两者都不能直接当作真实用户身份或人数。测试记录在上线验证后清除，不计入正式统计。

## 现有网站与新增服务

原网站的 WordPress 文件、数据库、主题、文章、导航均未改动。沿用现有 HTTPS 证书，在现有 Apache 站点中加入两个独立路径：

| 公网路径 | 用途 |
| --- | --- |
| `/bet-stats/` | 需要登录的统计页面 |
| `/bet-stats/summary` | 同样需要登录的 JSON 汇总 |
| `/bet-telemetry/api/v1/events` | 客户端上报接口 |
| `/bet-telemetry/healthz` | 健康检查，仅返回 `{"ok":true}` |

服务器为 CentOS 7 / Apache 2.4.46 / systemd 219。统计服务使用独立的 Python 3.12.16、SQLite 3.53.1，不替换系统或宝塔 Python，不改网站的 PHP 环境。Gunicorn 仅监听 `127.0.0.1:8765`，没有新增公网端口。

| 服务器位置 | 内容 |
| --- | --- |
| `/opt/bet-telemetry/python/` | 独立 Python 运行环境 |
| `/opt/bet-telemetry/app/` | 接收服务与白名单协议 |
| `/opt/bet-telemetry/apache-routes.conf` | 新路径的代理规则 |
| `/etc/bet-telemetry/telemetry.env` | 服务配置与管理密码，root / 服务账户可读 |
| `/etc/bet-telemetry/access.txt` | 管理员登录信息，只有 root 可读 |
| `/var/lib/bet-telemetry/telemetry.sqlite3` | 统计数据，独立服务账户拥有 |
| `/var/backups/bet-telemetry/` | 可选的无 IP 备份 |
| `/opt/bet-telemetry/rollback/original-site.conf` | 部署前的 Apache 站点配置备份 |

新增路径不写入原网站访问日志；应用不记录请求体、原始 IP 或登录密码到进程日志。后台认证经 HTTPS，转发 IP 由 Apache 覆盖，不能用客户端传入的 `X-Forwarded-For` 伪造。公开上报接口有字段校验、请求体限制和应用限流，但不是用户身份认证接口。

## 日常管理

服务和清理定时器均已设置开机启动。当前旧版 systemd 使用每小时执行的清理定时器，启动后约 5 分钟也执行一次；不要照通用文档换成其他环境的 unit。

在宝塔终端或 SSH 执行：

```bash
systemctl status bet-telemetry.service --no-pager
systemctl status bet-telemetry-purge.timer --no-pager
systemctl list-timers bet-telemetry-purge.timer --all
journalctl -u bet-telemetry-purge.service -n 20 --no-pager
curl --fail --silent --show-error http://127.0.0.1:8765/healthz
```

手动清理：

```bash
systemctl start bet-telemetry-purge.service
systemctl status bet-telemetry-purge.service --no-pager
```

需要更新统计程序时，替换 `/opt/bet-telemetry/app/` 中的两个包并更新独立运行环境，保留配置与数据库，然后 `systemctl restart bet-telemetry.service`。检查公开健康接口和后台登录。管理密码如需修改，编辑 `/etc/bet-telemetry/telemetry.env` 并重启该服务，同时更新私有登录记录；密码不要放到网站目录、客户端或版本库。

可选备份命令（只备份最近 80 天的非 IP 事件，副本保留 7 天）：

```bash
runuser -u bet-telemetry -- /opt/bet-telemetry/python/bin/python3 \
  /opt/bet-telemetry/app/telemetry_server/deploy/backup_redacted.py create \
  --db /var/lib/bet-telemetry/telemetry.sqlite3 \
  --directory /var/backups/bet-telemetry
```

数据库主文件有 512 MiB 上限，仍需定期关注磁盘、服务状态及证书续期。独立运行环境和现有操作系统的后续维护分开进行。

## 客户端与发布

本机 `telemetry_config.json` 已配置验证过的地址：

```json
{"endpoint":"https://dragonscience.top/bet-telemetry/api/v1/events"}
```

`BET.spec` 会将该公开地址打入单文件 EXE。配置文件不含管理密码；默认开启及保存偏好的规则见项目 README。此文件被 Git 忽略，因此在其他机器打包前，需要从 `telemetry_config.example.json` 复制它；Windows 发布工作流已包含该步骤。

1.3.0 正式包位于 `dist_v1.3.0/BET-DragonScience.exe`；`dist_telemetry_trial/` 是此前的试用包。旧版 EXE 不会因为服务器上线自动增加统计功能，需要安装新版本。

## 回退新路径

部署脚本保留了原站点配置。需要撤销新路径时：

```bash
/opt/bet-telemetry/python/bin/python3 /opt/bet-telemetry/activate_routes.py --rollback
```

脚本会先核对当前站点配置是否仍与本次部署一致；若之后通过宝塔改过配置，它会拒绝覆盖，应由管理员保留后来修改、仅移除统计 Include 和对应日志过滤。回退路径后，若也停止统计服务，需先导出必要的无 IP 统计并妥善清理数据，不能停止定时清理后让原始 IP 无限保留。
