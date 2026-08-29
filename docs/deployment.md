# 部署手册

## 前提

- 服务器：Ubuntu，IP `193.112.26.217`
- SSH 用户：`ubuntu`
- 本机能执行：`ssh ubuntu@193.112.26.217`
- 安全组放行 `22`、`80`

## 首次部署

在公司标识、管理员姓名、姓名全拼处替换为自己的值：

```bash
cd /Users/feng/code/warehouse-management-system
./deploy.sh acme 193.112.26.217 张三 zhangsan
```

脚本会自动安装依赖、上传代码、初始化数据库、配置 Nginx 和 systemd，并显示一次性初始密码。

访问：

```text
http://193.112.26.217/acme/
```

## 增加公司

```bash
./deploy.sh beta 193.112.26.217 李四 lisi
```

访问：

```text
http://193.112.26.217/beta/
```

所有公司共用 80 端口；每家公司有独立目录、数据库、服务和内部端口：

| 公司标识 | 应用目录 | 服务 |
| --- | --- | --- |
| `acme` | `/opt/wms-acme` | `wms-acme` |
| `beta` | `/opt/wms-beta` | `wms-beta` |

## 升级

在原目录重新执行原部署命令即可。已有数据库会先自动备份，再升级代码并重启。

## 排查

```bash
ssh ubuntu@193.112.26.217
systemctl status wms-acme --no-pager
journalctl -u wms-acme -n 100 --no-pager
sudo nginx -t
```

把 `acme` 替换成对应公司标识。
