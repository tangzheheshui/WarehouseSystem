---
name: localtest
description: 本地体验环境入口：启动本地平台服务（万顺控制台 + 演示公司），输出地址与账号密码清单；用户在本地体验提意见、我改代码验证，用户明确授权后才部署线上
---

# localtest — 本地体验环境

目标：让用户在本地完整体验产品（开发商后台 + 客户后台）并反馈意见；我负责起服务、给账号、按反馈改代码。全程不碰线上。

## 1. 确保服务在跑

1. 检查端口：`lsof -nP -iTCP:8000 -sTCP:LISTEN`
2. 已在跑 → 直接给第 2 步的清单；没在跑 → 后台执行 `./scripts/run.sh --platform --domain localhost`
3. 验证两个域名都通（各返回 200）：
   `curl -sS -o /dev/null -w "%{http_code}\n" http://console.localhost:8000/ http://acme.localhost:8000/`
4. acme.localhost 不通或提示无公司 → 跑 `python3 seed_demo.py`（幂等，已存在的公司会跳过）

## 2. 输出账号清单（固定格式，直接贴给用户）

| 后台 | 地址 | 账号 | 密码 |
|---|---|---|---|
| 万顺控制台（开发商） | http://console.localhost:8000/ | `wanshun` | `Local123456` |
| ACME物流（客户公司） | http://acme.localhost:8000/ | `admin` | `Admin123456` |

项目管理员（体验项目隔离）：

- `wangli / Wangli456789` → 只授权「味佳食品-2026秋季订单」
- `zhangchen / Zhangchen456789` → 只授权「洁雅日用-常备库存」

备注一并带上：

- 首次登录会强制改密，属正常产品流程。
- 用户说密码不对（之前登录时改过）→ 征得确认后重置演示数据：
  `rm -rf data/platform.json data/companies && python3 seed_demo.py`
  （只清本地演示库，会删掉本地新建的公司和数据；`data/warehouse.db` 单公司老库不受影响）

## 3. 本会话的工作方式

- 用户在页面上发现的任何问题/意见 → 直接在本地改代码 → 跑 `./scripts/run-tests.sh` 全绿 → 让用户刷新页面验证。
- 改动先留在工作区，用户验证满意后可提交 commit（用户开口才提交）。
- **只有用户明确说「部署」才执行 `scripts/deploy.sh` 同步线上**；绝不擅自部署，也不在正式环境上做测试性修改。
