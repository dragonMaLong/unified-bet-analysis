# 自托管 BET 使用统计服务部署

**DragonScience 当前服务器已经部署完成，实际网址、服务路径和维护命令见 [当前部署说明](LIVE_DEPLOYMENT.md)。** 下文是其他服务器的通用部署示例，不要在已部署机器重复创建站点或替换 Web 服务。

这份说明部署的是统计接收端和受密码保护的管理页。仓库提供代码、配置和检查步骤，**不会自动连接服务器、修改 DNS 或发布客户端**。以下命令在你自己的 Linux 服务器执行；建议先完成测试，再发布带统计地址的 EXE。

目标结构：客户端 → `https://stats.dragonscience.top` → Nginx → `127.0.0.1:8765` Gunicorn → SQLite。Flask 开发服务器不用于公网；Gunicorn 使用独立的非 root 账号，初期一个 worker。应用工厂的加载语法见 [Flask 部署文档](https://flask.palletsprojects.com/en/stable/deploying/gunicorn/)，反向代理与进程管理见 [Gunicorn 文档](https://gunicorn.org/deploy/)。

## 统计口径与保留时间

- `install_id` 是随机安装标识，不是实名用户；重装或重置配置可能产生新标识，不能据此声称获得了独立人数。
- `startup` 记录启动；`import_file`、`export` 记录操作结果；`feature_view` 仅代表打开分析图或结果页面，不代表完成了分析、使用时长或分析成功。
- 客户端未配置地址时不记录行为。1.3.0 新安装在配置有效地址、成功保存本地设置后默认开启，保留此前保存的关闭偏好；坏配置和存储失败保持关闭。该版“隐私设置”入口暂时隐藏，禁用方法见项目 README 的“使用统计”。
- 协议只接受白名单字段，不上传样品名称、文件路径、原始数据、导出的文件或自由文本异常内容。公网 IP 由接收端读取连接信息，单独存入 `ip_observations`。
- 事件按事件发生时间保留 90 天；IP 观察记录按服务端最后接收时间保留 7 天。数据库时间和日常清理均使用 UTC。
- 每天的独立 systemd timer 会调用清理命令，即使没有客户端流量也会执行。服务离线时不能执行删除，恢复后应立即检查补跑结果。
- 接收接口没有客户端共享密钥；第三方可以构造符合格式的事件。限流和校验能限制滥用，不能证明事件一定来自正版软件。统计适合观察产品趋势，不应作为计费、身份认定或精确人数证据。

## 文件和目录

| 位置 | 用途 |
| --- | --- |
| `/opt/bet-telemetry/app/telemetry_server/` | 接收端及部署文件 |
| `/opt/bet-telemetry/app/bet_telemetry/` | 共用的事件协议校验模块 |
| `/opt/bet-telemetry/venv/` | 接收端独立 Python 环境 |
| `/etc/bet-telemetry/telemetry.env` | 服务端配置和管理密码，不能分发给客户端 |
| `/var/lib/bet-telemetry/telemetry.sqlite3` | 实时数据库 |
| `/var/backups/bet-telemetry/` | 可选的无 IP 备份 |

只需上传 `telemetry_server/`、`bet_telemetry/` 两个目录；不要把 BET 样品、客户数据或本机配置上传到 Web 根目录。Gunicorn 不能绑定 `0.0.0.0`；防火墙和云安全组只为网站开放 80/443，SSH 按你已有的管理方式限制，8765 不对外开放。

## SSH 部署示例（Debian 12+ / Ubuntu 24.04+）

下面假定代码已通过 SFTP/SSH 上传到 `~/bet-telemetry-upload/`，里面有上述两个目录。已有 Nginx 的机器不要重复安装另一套 Web 服务。

要求 **Python 3.11 或更新版本、SQLite 3.38 或更新版本**，并启用 SQLite JSON 函数和窗口函数。先用准备创建 venv 的同一个 Python 检查；检查失败时先升级系统 Python/SQLite 或改用符合要求的解释器，不要继续部署：

```bash
python3 - <<'PY'
import sys, sqlite3
assert sys.version_info >= (3, 11), sys.version
assert sqlite3.sqlite_version_info >= (3, 38, 0), sqlite3.sqlite_version
with sqlite3.connect(':memory:') as db:
    assert db.execute("SELECT json_valid('{}')").fetchone()[0] == 1
    assert db.execute('SELECT row_number() OVER ()').fetchone()[0] == 1
print('Python / SQLite / JSON / window functions: OK')
PY
```

管理页的环境统计用到 SQLite JSON 提取，版本归属用到窗口函数；只检查 Python 包安装成功还不够。旧发行版的默认 `python3` 可能不符合要求，下列命令中的解释器路径也需要同步替换。

```bash
sudo apt update
sudo apt install python3 python3-venv nginx certbot
sudo useradd --system --user-group --home-dir /var/lib/bet-telemetry --shell /usr/sbin/nologin bet-telemetry
sudo install -d -o root -g root -m 0755 /opt/bet-telemetry/app
sudo cp -a ~/bet-telemetry-upload/telemetry_server /opt/bet-telemetry/app/
sudo cp -a ~/bet-telemetry-upload/bet_telemetry /opt/bet-telemetry/app/
sudo chown -R root:root /opt/bet-telemetry/app
sudo chmod -R u=rwX,go=rX /opt/bet-telemetry/app
sudo python3 -m venv /opt/bet-telemetry/venv
sudo /opt/bet-telemetry/venv/bin/pip install -r /opt/bet-telemetry/app/telemetry_server/requirements.txt
sudo install -d -o root -g bet-telemetry -m 0750 /etc/bet-telemetry
sudo install -d -o bet-telemetry -g bet-telemetry -m 0700 /var/lib/bet-telemetry /var/backups/bet-telemetry
sudo install -o root -g bet-telemetry -m 0640 /opt/bet-telemetry/app/telemetry_server/deploy/telemetry.env.example /etc/bet-telemetry/telemetry.env
```

账号已存在时跳过 `useradd`，不要覆盖正在使用的环境配置。生成管理密码，然后用 `sudoedit /etc/bet-telemetry/telemetry.env` 填写自己选择的用户名和生成的密码：

```bash
python3 -c 'import secrets; print(secrets.token_urlsafe(32))'
sudoedit /etc/bet-telemetry/telemetry.env
```

`BET_TELEMETRY_ADMIN_USER`、`BET_TELEMETRY_ADMIN_PASSWORD` 没有默认值，两者都配置后才启用管理账户。密码仅留在服务器，不写入 EXE、JSON、截图或版本库。保持 `BET_TELEMETRY_DB` 为上述数据库路径；下面的单级反向代理配置使用 `BET_TELEMETRY_TRUST_PROXY=1`。

安装并启动服务及清理任务：

```bash
sudo install -m 0644 /opt/bet-telemetry/app/telemetry_server/deploy/bet-telemetry.service /etc/systemd/system/
sudo install -m 0644 /opt/bet-telemetry/app/telemetry_server/deploy/bet-telemetry-purge.service /etc/systemd/system/
sudo install -m 0644 /opt/bet-telemetry/app/telemetry_server/deploy/bet-telemetry-purge.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now bet-telemetry.service bet-telemetry-purge.timer
sudo systemctl start bet-telemetry-purge.service
sudo systemctl status bet-telemetry.service --no-pager
curl --fail --silent --show-error http://127.0.0.1:8765/healthz
```

健康检查只返回 `{"ok":true}`。定时任务每天 UTC 03:10 左右执行，允许最多 5 分钟随机延迟；`Persistent=true` 会在机器恢复后补跑错过的任务，见 [systemd timer 文档](https://freedesktop.org/software/systemd/man/latest/systemd.timer.html)。

## DNS、TLS 和 Nginx

1. 为 `stats.dragonscience.top` 创建指向服务器的 A 记录；只有 IPv6 已配置可用时才添加 AAAA 记录。
2. 本模板按“客户端直接连接 Nginx”设计。DNS 仅解析，不开启 CDN 代理；也不启用会把任意请求头当真实地址的全局 `real_ip`/PROXY protocol 配置。以后增加 CDN 或负载均衡时，需要重新限定可信代理链，不能直接沿用本模板。
3. 先使用 HTTP-only 配置申请证书，再启用引用证书的 HTTPS 配置，避免首次 `nginx -t` 因证书不存在而失败。

```bash
sudo install -d -m 0755 /var/www/bet-telemetry-acme
sudo install -m 0644 /opt/bet-telemetry/app/telemetry_server/deploy/nginx-http.conf /etc/nginx/conf.d/bet-telemetry-limits.conf
sudo install -m 0644 /opt/bet-telemetry/app/telemetry_server/deploy/nginx-bootstrap.conf /etc/nginx/sites-available/bet-telemetry
sudo ln -s /etc/nginx/sites-available/bet-telemetry /etc/nginx/sites-enabled/bet-telemetry
sudo nginx -t
sudo systemctl reload nginx
sudo certbot certonly --webroot -w /var/www/bet-telemetry-acme -d stats.dragonscience.top
sudo install -m 0644 /opt/bet-telemetry/app/telemetry_server/deploy/nginx-site.conf /etc/nginx/sites-available/bet-telemetry
sudo nginx -t
sudo systemctl reload nginx
sudo install -d -m 0755 /etc/letsencrypt/renewal-hooks/deploy
sudo install -m 0755 /opt/bet-telemetry/app/telemetry_server/deploy/certbot-reload-nginx.sh /etc/letsencrypt/renewal-hooks/deploy/bet-telemetry-nginx
sudo certbot renew --dry-run
```

已有同名站点时先合并配置，不重复创建 `server_name` 或软链接。确认系统安装的 Certbot 自动续期任务已启用；手工安装的 Certbot 按其安装方式设置续期，见 [Certbot 官方说明](https://certbot.eff.org/instructions)。模板域名与实际证书域名必须一致。

`nginx-http.conf` 中的 `limit_req_zone` 必须位于全局 `http {}` 层，不能放进站点 `server {}`。接收接口每 IP 限流 5 请求/秒、突发 20，全站另有限流；管理页使用更低的限流，超出返回 429。请求体上限为 64 KiB。服务端还有独立的校验与限流。限流区域和指令位置见 [Nginx 文档](https://nginx.org/en/docs/http/ngx_http_limit_req_module.html)。

### 真实 IP 的信任边界

代理配置明确使用：

```nginx
proxy_set_header Host stats.dragonscience.top;
proxy_set_header X-Forwarded-For $remote_addr;
proxy_set_header X-Forwarded-Proto https;
```

这里必须**覆盖**传入的 `X-Forwarded-For`，不能换成 `$proxy_add_x_forwarded_for`。应用只在直接连接来自确切 `127.0.0.1` 或 `::1`、且代理信任已启用时读取单个转发地址；错误的代理信任会让请求方伪造 IP，见 [Flask 代理说明](https://flask.palletsprojects.com/en/stable/deploying/proxy_fix/)。同机不受信任的进程也能访问 loopback，因此不要把本实例部署在允许其他不受信任租户执行代码的共享环境。

模板关闭该站点的 access log，并把 Nginx 请求错误日志输出到 `/dev/null`，防止日志另行长期保存 IP。Gunicorn 不记录 access log。不要在宝塔、CDN、WAF、备份工具或应用异常日志里重新启用包含请求 IP/请求体的无限期日志；临时排错时也要明确清理范围和期限。

## 宝塔部署方式

宝塔只负责域名、HTTPS 和 Nginx；接收端仍使用上述独立 venv、systemd service 和 purge timer，避免宝塔 Python 项目和 systemd 同时启动两份进程。

1. 通过宝塔文件管理器/SFTP 上传两个 Python 包，按前面的目录结构建立服务账户、venv 和环境配置。宝塔选择的解释器同样必须是 Python 3.11+、SQLite 3.38+；先用该解释器执行前面的 JSON/窗口函数检查，不默认信任面板自带 Python 版本。
2. 给 `stats.dragonscience.top` 单独建站，在 SSL 设置中签发并启用证书。无需给该站点设置 PHP，也不要开放 8765 公网端口。
3. 在 Nginx 主配置的 `http {}` 内加入 `deploy/nginx-http.conf` 的限流区域，且只加入一次。
4. 在此站点配置中参考 `nginx-site.conf` 设置 HTTPS 反向代理。证书路径使用宝塔实际生成的路径，保留其 ACME 验证配置和续期机制；不要直接覆盖宝塔的主 Nginx 配置。
5. 核对每个代理 location：目标必须为 `http://127.0.0.1:8765`；Host 为 `stats.dragonscience.top`；`X-Forwarded-For` 必须为 `$remote_addr`。宝塔生成的“追加转发链”行需要替换，同时清除同一层的重复 header 设置。
6. 核对 body limit、限流、日志设置。通过面板的配置检查后重新加载 Nginx，执行下一节的实际 HTTPS 验证。证书续期由宝塔负责时，不再另建一套 Certbot 任务。

宝塔版本的界面入口和 Nginx 路径可能不同，以当前面板显示的站点配置为准。无论从面板还是 SSH 启动，必须保留每天的独立清理任务；不要只依赖接收事件时顺带清理。

## 上线验证

```bash
curl --fail --silent --show-error https://stats.dragonscience.top/healthz
curl --silent --output /dev/null --write-out '%{http_code}\n' https://stats.dragonscience.top/admin
read -r -p '管理用户名: ' stats_admin_user
curl --silent --show-error --user "$stats_admin_user" --output /dev/null --write-out '%{http_code}\n' https://stats.dragonscience.top/admin
sudo ss -ltnp '( sport = :8765 )'
sudo systemctl list-timers bet-telemetry-purge.timer --all
sudo journalctl -u bet-telemetry-purge.service -n 20 --no-pager
```

第二个请求应返回 401；第三个命令由 curl 交互询问密码，正确凭证应返回 200。管理页必须经 HTTPS 访问，不能把 Basic 密码写进 URL。8765 的监听地址应只有 `127.0.0.1`。清理日志应有成功执行记录及事件/IP 删除计数，且 `checkpoint_busy` 为 false，不能只有 timer 处于 enabled 的状态。

如果清理遇到长查询或备份读事务导致 WAL 无法完整截断，命令会返回非零，systemd 标记失败。等相关读事务结束后，立即重新执行 `sudo systemctl start bet-telemetry-purge.service` 并确认成功，不能等到下一天。备份过期清理放在 `ExecStopPost`，数据库清理失败时仍会单独执行。

用下面的合成事件核验完整链路；它会形成一条测试安装/事件记录，版本 `0.0.0-smoke` 用于识别测试流量，不代表真实用户：

```bash
python3 - <<'PY' > /tmp/bet-telemetry-smoke.json
import json
from datetime import datetime, timezone
from uuid import uuid4
print(json.dumps({
    'schema_version': 1,
    'install_id': str(uuid4()), 'session_id': str(uuid4()),
    'app_version': '0.0.0-smoke',
    'events': [{'event_id': str(uuid4()),
                'occurred_at': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
                'name': 'feature_view',
                'data': {'feature': 'bet', 'surface': 'plot'}}]
}))
PY
curl --fail-with-body --silent --show-error \
  -H 'Content-Type: application/json' \
  --data-binary @/tmp/bet-telemetry-smoke.json \
  https://stats.dragonscience.top/api/v1/events
```

确认返回成功、管理页能看到测试事件，重复提交同一事件不会重复计数。`/admin/summary` 提供相同鉴权下的 JSON 汇总，也应检查无凭证返回 401。再生成一份新测试 JSON，从服务器外部发送并额外加 `-H 'X-Forwarded-For: 203.0.113.123'`，确认记录的 IP 是实际连接来源而不是伪造值。检查无效字段被拒绝，超过请求体上限会被 Nginx 拒绝，错误管理员密码无法访问管理页。

## 给客户端发布地址

**服务实际上线并完成上述验证之后**，才配置正式发布地址。在项目根目录复制 `telemetry_config.example.json` 为 `telemetry_config.json`，内容为：

```json
{"endpoint":"https://stats.dragonscience.top/api/v1/events"}
```

然后使用现有 `BET.spec` 打包。该文件可随单 EXE 内置，用户不需要填写统计地址或任何密码。默认开启及保存偏好的规则见项目 README；没有配置统计的旧版 EXE 不会凭服务器上线自动获得这个功能，需要发布新版本。没有上线验证时，不要把正式域名配置打入发布包，也不要在演示中向外网发送事件。

配置优先级为 `BET_TELEMETRY_URL` 环境变量 → EXE 旁的 `telemetry_config.json` → EXE 内置配置；源码运行读取项目根目录配置。EXE 旁文件存在但 endpoint 明确为空或无效时会禁用统计，不会回退到内置地址。开发调试的 HTTP 地址仅允许 localhost；正式分发使用 HTTPS。所有这些配置只包含 endpoint，**绝不能包含服务端管理账户或密码**。

## 备份与恢复

可选备份脚本只从只读事务中分批复制 `events` 的固定白名单列，每批最多 1000 行；完全不读取、不复制 `ip_observations`。备份中只保留最近 80 天的事件，副本 7 天后过期，留出每日清理调度余量，不把 90 天事件策略延长为“原始 90 天加备份 90 天”。不要用整盘快照或普通 SQLite 文件复制绕过这项策略。

```bash
sudo -u bet-telemetry /opt/bet-telemetry/venv/bin/python \
  /opt/bet-telemetry/app/telemetry_server/deploy/backup_redacted.py create \
  --db /var/lib/bet-telemetry/telemetry.sqlite3 \
  --directory /var/backups/bet-telemetry
```

脚本输出新文件路径、事件数量和 `ip_observations: 0`。每日 purge service 的退出阶段还会清理该目录中过期的本脚本备份及残留临时文件，无论本次数据库 purge 成功与否。若复制到异地，目标也必须执行不超过 7 天的副本清理，不能永久归档；暂停或移除服务时也要处理剩余数据库和副本。

恢复时先停止服务和 timer，使用 SQLite 的 backup API 从选定副本恢复到实时数据库；不要在 WAL 活跃时直接用 `cp` 覆盖数据库。先在独立目录检查副本 `PRAGMA integrity_check`，再恢复。副本只含 events，服务启动会建立其余空表；IP 历史不会被恢复。恢复后立即执行 purge，再启动服务和 timer，重新核验管理页与 HTTPS 接收端。

## 更新与日常检查

更新前创建上述无 IP 备份，保留当前代码版本。停止接收服务和 purge timer，替换 `/opt/bet-telemetry/app` 下两个包，按新 `requirements.txt` 更新独立 venv；保留 `/etc/bet-telemetry/telemetry.env` 和 `/var/lib/bet-telemetry`。更新了 unit 时执行 `systemctl daemon-reload`，执行一次 purge 后重新启动 service/timer，检查 `/healthz`、管理认证和测试事件。

定期检查磁盘余量、证书续期、失败服务、清理执行记录和数据库规模。主 SQLite 数据库设置了 512 MiB 的增长上限，容量不足时接收端返回 503，不确认未保存的事件；WAL、临时查询文件和备份仍会占用额外空间，这不是整个目录或磁盘的硬上限。遇到 503 应检查容量和清理状态，不盲目删除数据库或无限增大上限。

SQLite 首版采用单 worker；数据量或写入并发明显增长时，先压测并评估迁移数据库，不直接盲目增大 worker 数。管理页版本分布仅展示前 100 个版本，占比分母仍是全部活跃安装。仅 journal 中的进程健康日志可以按运维需要保留，事件内容、原始 IP 和管理密码不应进入日志。
