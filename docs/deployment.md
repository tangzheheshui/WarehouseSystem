# 部署手册

## 前提

- 服务器：Ubuntu，IP `193.112.26.217`
- SSH 用户：`ubuntu`
- 本机能免密登录：`ssh ubuntu@193.112.26.217`
- 安全组放行 `22`、`80`；第二家公司部署在 `8081` 时再放行 `8081`

## 部署第一家公司

```bash
cd /Users/feng/code/warehouse-management-system
COMPANY_NAME="Acme" WMS_ADMIN_PHONE=13812345678 ./deploy.sh default 193.112.26.217
```

首次部署会生成并显示管理员初始密码。访问：

```text
http://193.112.26.217/
```

## 部署第二家公司

```bash
APP_PORT=8001 PUBLIC_PORT=8081 COMPANY_NAME="Beta" WMS_ADMIN_PHONE=13912345678 ./deploy.sh beta 193.112.26.217
```

访问：

```text
http://193.112.26.217:8081/
```

`default` 对应 `/opt/wms` 和 `wms.service`；其他公司标识对应 `/opt/wms-公司标识` 和 `wms-公司标识.service`。

## 升级

在本机执行原部署命令即可。脚本会先备份数据库，再上传代码、重启服务、执行测试和健康检查。

## 排查

```bash
ssh ubuntu@193.112.26.217
systemctl status wms --no-pager
journalctl -u wms -n 100 --no-pager
```

第二家公司把 `wms` 换成 `wms-beta` 这类服务名。
