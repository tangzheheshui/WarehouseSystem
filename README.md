# 小型公司仓库管理系统

零第三方依赖的服务端和 Web 端实现，适合单台 Ubuntu 服务器部署。

## 技术栈

- Python 3.9+ 标准库 HTTP 服务
- SQLite 数据库
- 原生 HTML / CSS / JavaScript
- 自定义 `.xlsx` 生成与解析，不依赖 Excel 或第三方组件

## 已实现功能

- 手机号密码登录、PBKDF2 密码加密、24小时会话、5次失败锁定10分钟
- 首次登录强制修改密码
- 管理员 / 仓库人员双角色接口权限
- 用户创建、禁用启用、重置密码
- 入库、出库 Excel 模板下载与上传
- 行列级导入校验、错误明细 Excel 下载
- 入库自动建档、同批次数量累计
- 出库按商品总量校验，库存不足整单拒绝
- 出库按先入库先出扣减批次，并按库位排序生成拣货单
- 库存查询、批次明细、库存流水、Excel 导出
- 拣货单查看、A4 打印、Excel 导出、拣货完成确认
- 管理员操作日志

## 本地运行

```bash
cd warehouse-management-system
python3 server/main.py --host 127.0.0.1 --port 8000
```

或使用脚本：

```bash
./run.sh
```

访问：<http://127.0.0.1:8000>

默认管理员：

- 手机号：`13800000000`
- 初始密码：`Admin123456`

首次登录会强制修改密码。生产环境请用环境变量初始化：

```bash
WMS_ADMIN_PHONE=你的手机号 WMS_ADMIN_PASSWORD=你的初始密码 python3 server/main.py --host 127.0.0.1 --port 8000
```

环境变量只在数据库第一次创建用户时生效；修改环境变量不会自动修改已有账号密码。

## 自动化测试

```bash
PYTHONPYCACHEPREFIX=.pycache python3 -m unittest discover -s tests -v
```

或使用脚本：

```bash
./test.sh
```

覆盖内容：

- `.xlsx` 生成与解析
- 登录、强制改密、账号锁定
- 管理员创建仓库人员
- 入库自动建档与库存累计
- 出库库存不足时整单回滚
- 同商品多行累计校验
- 先入库先出扣减、拣货单排序
- 拣货完成状态流转
- 库存流水与操作日志

## 数据文件

默认数据库：

```text
data/warehouse.db
```

可通过 `--db` 指定：

```bash
python3 server/main.py --host 127.0.0.1 --port 8000 --db /opt/wms/data/warehouse.db
```

## 第一版边界

- 忘记密码采用管理员重置，未接阿里云短信
- 无库存下限预警、出库冲正、多单合并拣货
- 模板保留版本号用于单据追踪，但不保留每版模板文件快照
- 单机部署，未做多实例负载均衡

## 生产部署

公司名 `COMPANY_NAME` 必填，只能使用英文、数字、空格和 `.,-`。SSH 固定使用 `ubuntu`。

部署第一个公司：

```bash
COMPANY_NAME="Acme" WMS_ADMIN_PHONE=13812345678 ./deploy.sh default 193.112.26.217
```

同一台服务器部署第二家公司，必须换公司标识、内部端口和公网端口：

```bash
APP_PORT=8001 PUBLIC_PORT=8081 COMPANY_NAME="Beta" WMS_ADMIN_PHONE=13912345678 ./deploy.sh beta 193.112.26.217
```

访问地址：

```text
default: http://193.112.26.217/
beta: http://193.112.26.217:8081/
```

腾讯云安全组放行 `22`、`80`，多公司时再放行对应公网端口（如 `8081`）。更多说明见 [deployment.md](docs/deployment.md)，日常使用见 [operation-manual.md](docs/operation-manual.md)。
