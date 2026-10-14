# 部署手册

## 前提

- 服务器：Ubuntu，IP `193.112.26.217`
- SSH 用户：`ubuntu`
- 本机能执行：`ssh ubuntu@193.112.26.217`
- 安全组放行 `22`、`80`、`443`
- DNS（DNSPod）添加两条 A 记录，指向 `193.112.26.217`：

| 主机记录 | 记录类型 | 记录值 | 用途 |
| --- | --- | --- | --- |
| `*.wms` | `A` | `193.112.26.217` | 所有公司子域名（`acme.wms.`、`beta.wms.` …），一次加好 |
| `wms` | `A` | `193.112.26.217` | 落地页 `wms.tangzheheshui.cn`，跳转到默认公司 |

## 首次部署

在公司标识、管理员姓名、姓名全拼处替换为自己的值：

```bash
cd /Users/feng/code/warehouse-management-system
./deploy.sh acme 193.112.26.217 张三 zhangsan
```

脚本会自动安装依赖、上传代码、初始化数据库、配置 Nginx 和 systemd，按公司子域名签发 Let's Encrypt 证书并启用 HTTPS，最后显示一次性初始密码。

访问：

```text
https://acme.wms.tangzheheshui.cn/
```

每家公司一个子域名（公司在前，SaaS 多租户惯例），应用部署在子域名根路径。旧路径 `http://193.112.26.217/acme/` 会 308 跳转到新地址，历史链接不断。

DNS 未生效时脚本会退回 HTTP 模式并提示，解析生效后重跑同一条命令即可自动补签证书并切到 HTTPS。

基础域名默认 `wms.tangzheheshui.cn`，可用环境变量覆盖：

```bash
WMS_DOMAIN=wms.example.com ./deploy.sh acme 193.112.26.217 张三 zhangsan
```

## 增加公司

```bash
./deploy.sh beta 193.112.26.217 李四 lisi
```

访问：

```text
https://beta.wms.tangzheheshui.cn/
```

泛解析已就位，加公司不需要再动 DNS；证书按子域名自动签发。每家公司有独立目录、数据库、服务和内部端口：

| 公司标识 | 应用目录 | 服务 | 地址 |
| --- | --- | --- | --- |
| `acme` | `/opt/wms-acme` | `wms-acme` | `https://acme.wms.tangzheheshui.cn/` |
| `beta` | `/opt/wms-beta` | `wms-beta` | `https://beta.wms.tangzheheshui.cn/` |

`wms.tangzheheshui.cn` 落地页始终跳转到最近部署的公司。

## 升级

在原目录重新执行原部署命令即可。已有数据库会先自动备份，再升级代码并重启。

## 排查

```bash
ssh ubuntu@193.112.26.217
systemctl status wms-acme --no-pager
journalctl -u wms-acme -n 100 --no-pager
sudo nginx -t
sudo certbot certificates
```

把 `acme` 替换成对应公司标识。证书续期由 certbot systemd timer 自动处理，与其他项目子域名一致。

服务器上的 nginx 文件分工：

| 文件 | 作用 |
| --- | --- |
| `/etc/nginx/sites-available/wms` | 80 端口 catch-all：ACME 验证 + 旧路径跳转 |
| `/etc/nginx/sites-available/wms-landing` | `wms.tangzheheshui.cn` 落地页跳转 |
| `/etc/nginx/sites-available/wms-<公司标识>` | 公司子域名站点（80 跳 443，443 反代本机端口） |
| `/etc/nginx/wms-routes/<公司标识>.conf` | 旧路径 `/<公司标识>/` → 子域名的 308 跳转 |
