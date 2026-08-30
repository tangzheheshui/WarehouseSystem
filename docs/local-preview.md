# 本地预览（固定假数据）

一条命令启动带固定假数据的本地预览，重复启动不重复造数：

```bash
scripts/dev-preview.sh
```

- 地址：<http://127.0.0.1:8000/>
- 项目管理员：`preview / Preview123456`，用于查看项目切换、出入库、库存等业务界面，登录后不强制改密。
- 系统管理员：`admin / PreviewAdmin123`，用于查看商品、仓库、项目、用户管理。

## 假数据构成

- 预览数据库固定存放在 `.preview/warehouse.db`（已加入 `.gitignore`）。
- 首次启动自动初始化：1 个默认项目 + 4 个示例项目（上海冷链仓储项目、深圳电子仓项目、广州日用品仓项目、佛山家居仓项目）。
- 项目管理员 `preview` 授权全部项目，登录后可直接验证项目切换器和多项目数据隔离。
- 再次启动直接复用该数据库，不再调用接口造数，秒级完成。

## 重置假数据

```bash
rm -rf .preview
scripts/dev-preview.sh
```

## 更换端口

```bash
scripts/dev-preview.sh 8001
```
