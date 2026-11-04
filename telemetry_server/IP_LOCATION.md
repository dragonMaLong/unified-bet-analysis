# IP 归属地

统计页的“归属地（参考）”列使用服务器本地的 ip2region 数据。查询仅发生在已认证的管理页和管理汇总接口中，不在客户端上报链路中查询，不向第三方发送用户 IP。IP 记录长期保留，不按时间自动删除；页面支持所选时间范围与全部历史 IP，并按每页 200 组翻页。软件客户端无须更新，已有的 IP 记录也可显示归属地。

显示国家、省份、城市中可用的部分，例如“中国 · 上海”。不展示运营商、经纬度或街道。IP 对应网络出口，可能受共享网络、代理、VPN 和地址库更新情况影响，不能作为实际住址。

## 安装与数据来源

- Python 查询库：`py-ip2region==3.0.6`，已列入服务端 requirements。
- 地址库目录：配置 `BET_TELEMETRY_IP_DATABASE_DIR`，未配置时使用 Flask instance 目录下的 `ip-location`。
- 目录中放置 `ip2region_v4.xdb`、`ip2region_v6.xdb`。同时保留来源清单 `sources.json` 及上游许可 `LICENSE-ip2region.txt`。
- 本次地址库来源：[ip2region 固定提交](https://github.com/lionsoul2014/ip2region/tree/6a276cc7c4f9f2d7ff1d8ace14b91af59752f943/data)。该版本字段格式为 `Country|Province|City|ISP|ISO`，与旧版格式不同，不能直接替换为旧版数据库。
- 官方项目采用 Apache-2.0 / MIT 双许可，见[上游许可](https://github.com/lionsoul2014/ip2region/blob/6a276cc7c4f9f2d7ff1d8ace14b91af59752f943/LICENSE.md)。页面同时提供来源链接。

生产环境建议使用 `/opt/bet-telemetry/ip-location`，目录 root 所有、权限 0755，数据文件 0644；服务账户只需要读取。数据文件不属于用户统计数据库，不必放入可写的数据目录。

## 可用性与更新

缺少查询库、缺库、数据库损坏或查不到 IP 时，归属地显示“未知”，统计接收仍正常运行。内网、本机、链路本地和保留地址单独标明。每次请求只处理当前显示的最多 200 行，按地址族各打开一个查询对象，查询结束即关闭，不保留跨请求 IP 缓存。

地址库不会在用户访问页面时联网下载。更新时从上游获取完整数据，核验文件来源和摘要，使用当前查询库验证 IPv4 / IPv6，再替换本地文件并重启 `bet-telemetry.service`。保留上次可用数据库以便回退；记录数据版本，不能把旧库伪称为实时定位。
