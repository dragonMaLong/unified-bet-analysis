# DragonScience 统计后台：当前部署

部署日期：2026-10-29。以下是当前服务器的实际配置；`DEPLOY.md` 中的新建子域名和 Nginx 步骤是其他环境的示例，不需要在这台服务器重复执行。

## 如何使用

- 统计页面：<https://dragonscience.top/bet-stats/>。
- 浏览器会要求输入独立的统计后台账号、密码。登录信息保存在本机项目的 `outputs/bet-stats-access.txt`，不是宝塔或 WordPress 密码。
- 页面可切换近 30 天、90 天、半年（180 天）、1 年（365 天），显示版本分布、IP、归属地、活跃安装、启动次数、系统与显示环境、导入/导出结果、耗时和分析页面浏览。IP 支持“全部历史 IP”和每页 200 组翻页。
- 刷新页面即可查看最新统计。用户需要使用包含统计功能的新客户端；1.3.0 新安装默认开启，保留此前保存的关闭偏好。
- 1.3.0 的“隐私设置”入口暂时隐藏。测试默认开启时使用独立的临时用户配置，不覆盖真实用户的偏好。禁用方法见项目 README 的“使用统计”。

已接收事件和 IP 长期保存，不再按时间自动删除。统计窗口只改变查询结果，不影响保留数据。页面时间与每日启动分组使用北京时间（UTC+8），数据库及 JSON 原有时间字段仍保留 UTC。IP 是网络出口地址，安装标识是随机 UUID；两者都不能直接当作真实用户身份或人数。测试记录不计入正式统计。

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
| `/opt/bet-telemetry/ip-location/` | IPv4 / IPv6 本地归属地库、来源与许可 |
| `/opt/bet-telemetry/apache-routes.conf` | 新路径的代理规则 |
| `/etc/bet-telemetry/telemetry.env` | 服务配置与管理密码，root / 服务账户可读 |
| `/etc/bet-telemetry/access.txt` | 管理员登录信息，只有 root 可读 |
| `/var/lib/bet-telemetry/telemetry.sqlite3` | 统计数据，独立服务账户拥有 |
| `/var/backups/bet-telemetry/` | 旧版可选的有限窗口、无 IP 导出副本 |
| `/opt/bet-telemetry/rollback/original-site.conf` | 部署前的 Apache 站点配置备份 |

新增路径不写入原网站访问日志；应用不记录请求体、原始 IP 或登录密码到进程日志。后台认证经 HTTPS，转发 IP 由 Apache 覆盖，不能用客户端传入的 `X-Forwarded-For` 伪造。公开上报接口有字段校验、请求体限制和应用限流，但不是用户身份认证接口。

2026-10-30 已上线“归属地（参考）”列，显示国家、省份、城市中可用的部分。使用 `py-ip2region==3.0.6` 和固定来源的本地 IPv4 / IPv6 数据，不向第三方查询服务发送用户 IP。客户端无须更新，原有记录也可查询。归属地是网络出口的大致位置，不代表实际住址，详细数据来源和维护方式见 [IP_LOCATION.md](IP_LOCATION.md)。

此次更新只替换统计服务的四个文件，新增本地地址库并追加其目录配置；部署前后 Apache vhost 配置与网站 `index.php` 摘要一致。更新备份位于 `/opt/bet-telemetry/backups/ip-location-*`，权限 0700，含原服务代码和配置。35 项相关测试通过，公网已验证新列、鉴权、健康检查及无效上报拒绝，未向正式统计添加测试事件。

同日已上线长期保留、四档统计窗口、历史 IP 分页和北京时间显示。39 项相关测试通过，公网四档 HTML / JSON、鉴权和上报校验均正常；实际执行维护任务后事件与 IP 数量未减少，定时器恢复原有启用状态。代码、unit 及完整私有数据库快照保存在 `/opt/bet-telemetry/backups/retention-0zi8bto6`，代码回退不会自动覆盖实时库。网站 vhost 配置和 `index.php` 摘要保持一致。

2026-11-04 已上线使用统计新字段：启动事件的系统语言环境 `locale`、导入事件的仪器型号类别 `instrument`（新增 `asap_2010`、`bsd`），以及 `session_end` 的打开时长 `duration_s` 与活跃时长 `active_s`（窗口在前台且 2 分钟内有键盘或鼠标操作，不超过打开时长）。后台将原“使用时长”拆为“打开时长”“活跃时长”两列。此次只替换 `telemetry_server/app.py`、`telemetry_server/templates/admin.html`、`bet_telemetry/schema.py` 三个文件，未改数据库、环境文件、systemd unit 和网站。更新包 `tmp/telemetry-deploy/usage-update.zip`（SHA-256 `8087a09b…a01c57`）经宝塔文件接口上传、解压到 `/opt/bet-telemetry/usage-update/`，由管理员在宝塔终端以 root 执行 `apply_update.py`：线上代码版本核对、副本加临时库演练、三文件备份、原子替换、重启、健康检查、后台页面与无效上报拒收均通过（`PREFLIGHT_OK`、`REHEARSAL_OK`、`UPDATE_OK`），未向正式统计写入测试事件。原文件备份在 `/opt/bet-telemetry/backups/usage-im2le57s`（含 `restore-manifest.json`）。旧客户端不受影响；发送新字段的客户端须在本次服务端更新之后发布。

同日修正：上次部署的模板取自本地工作副本，其中“每日启动次数”位于“IP 统计”之下，与原线上页面不同（原线上为启动图在上）。已从 `usage-im2le57s` 备份逐行核对：`app.py`、`schema.py` 与原线上的差异仅为本次新增字段；模板除时长列外仅有这一处位置差异。已将启动图移回 IP 统计上方，并在服务器上核对暂存模板相对原线上版本只差时长相关四处修改，再以同一脚本部署（`PREFLIGHT_OK`、`REHEARSAL_OK`、`UPDATE_OK`），本次备份 `/opt/bet-telemetry/backups/usage-fnkdc_sn`。新增测试固定“启动图在 IP 统计之上”的顺序。以后部署前须先与线上文件比对，不能直接以本地工作副本为准。

## 日常管理

服务和维护定时器均已设置开机启动。现有 `purge` 命令及 unit 名称保留，但任务只维护 WAL 和轮换旧脱敏导出副本，不删除实时库的事件或 IP。当前旧版 systemd 每小时执行，启动后约 5 分钟也执行一次；不要照通用文档换成其他环境的 unit。

在宝塔终端或 SSH 执行：

```bash
systemctl status bet-telemetry.service --no-pager
systemctl status bet-telemetry-purge.timer --no-pager
systemctl list-timers bet-telemetry-purge.timer --all
journalctl -u bet-telemetry-purge.service -n 20 --no-pager
curl --fail --silent --show-error http://127.0.0.1:8765/healthz
```

手动执行 WAL 维护：

```bash
systemctl start bet-telemetry-purge.service
systemctl status bet-telemetry-purge.service --no-pager
```

需要更新统计程序时，替换 `/opt/bet-telemetry/app/` 中的两个包并更新独立运行环境，保留配置与数据库，然后 `systemctl restart bet-telemetry.service`。检查公开健康接口和后台登录。管理密码如需修改，编辑 `/etc/bet-telemetry/telemetry.env` 并重启该服务，同时更新私有登录记录；密码不要放到网站目录、客户端或版本库。

旧版可选导出命令（仅最近 80 天的非 IP 事件，副本保留 7 天，**不能作为长期完整备份**）：

```bash
runuser -u bet-telemetry -- /opt/bet-telemetry/python/bin/python3 \
  /opt/bet-telemetry/app/telemetry_server/deploy/backup_redacted.py create \
  --db /var/lib/bet-telemetry/telemetry.sqlite3 \
  --directory /var/backups/bet-telemetry
```

完整历史备份应使用 SQLite 在线 backup API，包含全部事件和 IP，并保存在专用 0700 目录中、文件权限 0600。本次保留期变更前已创建这样的私有快照；普通代码回退不会覆盖实时数据库。原有脱敏副本不足以完整恢复历史数据。详见 `DEPLOY.md` 的备份与恢复说明。

数据库主文件有 512 MiB 上限，后台展示用量，达到 80% 时提示扩容；WAL、地址库和备份另占磁盘空间。达到上限时新上报可能无法保存，已有记录不会自动删除。仍需定期关注磁盘、服务状态及证书续期。独立运行环境和现有操作系统的后续维护分开进行。

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

脚本会先核对当前站点配置是否仍与本次部署一致；若之后通过宝塔改过配置，它会拒绝覆盖，应由管理员保留后来修改、仅移除统计 Include 和对应日志过滤。回退路径或停止统计服务不会自动删除历史记录；退役时由管理员决定保留、备份或删除数据，不将停用服务视为删除数据。
