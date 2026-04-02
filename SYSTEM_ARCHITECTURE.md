# 日本小票处理系统架构设计（参考 Streamed 功能模型）

## 1. 目标与边界

面向对象：日本税理士事务所 A，以及其管理的客户企业 B（数百家规模）。

一期目标（MVP）：
- 上传/拖拽/批量导入本地目录票据。
- 自动 OCR 识别关键字段。
- 自动分类到会计科目（支持行业差异规则）。
- 异常自动分拣，进入人工复核队列。
- 按日系会计软件常用格式导出 CSV。

非目标（一期）：
- 自研高精度 OCR 核心算法。
- 复杂图像修复（只做基础预处理）。

说明：
- 数据库设计基于 Streamed 的公开产品工作流特征抽象，不是其内部私有结构的逐表复制。

---

## 2. 总体架构

```text
Vue3 Frontend
  - 上传中心（拖拽、批量、目录）
  - 识别结果确认
  - 分类规则管理
  - 异常复核台
  - 导出中心（模板映射）
        |
        v
FastAPI
  - Auth / Tenant / Users
  - Ingestion API（上传与目录任务）
  - OCR Orchestrator（可切换 provider）
  - Classification Engine
  - Review Workflow
  - Export Service
        |
        +--> OCR Providers
        |      - receipt-ocr
        |      - Azure Document Intelligence（可选）
        |      - Google Vision / 其他（可选）
        |
        +--> PostgreSQL
        |
        +--> File Storage (Local first, MinIO/S3 later)
        |
        +--> Redis + Worker（批量任务与异步处理）
```

---

## 3. 关键业务流程

### 3.1 票据处理闭环
1. 用户上传文件或触发目录扫描任务。
2. 系统落库文档元数据，进入队列。
3. OCR 服务识别并提取结构化字段。
4. 分类引擎按规则匹配会计科目。
5. 规则置信度不足或数据异常时，标记为待复核。
6. 人工复核后确认入账属性。
7. 导出模块按模板生成 CSV 并下载。

### 3.2 异常分拣策略
- OCR 失败：图像质量差、字段缺失。
- 逻辑异常：税额与税率不一致、合计与明细不一致。
- 低置信度：OCR 或分类分数低于阈值。
- 高风险关键字段缺失：交易日期、商户、总金额、登记号码。

---

## 4. 数据库设计（Streamed 风格的可落地建模）

### 4.1 多租户组织域
- accounting_firms：税理士事务所主体。
- client_companies：被服务企业主体。
- users：用户账号。
- memberships：用户与企业/事务所的角色关系。

### 4.2 数据采集域
- ingestion_sources：采集源（upload/local_folder/api）。
- local_folder_watches：本地目录绑定与扫描配置。
- ingestion_jobs：每次导入任务（批次）。
- documents：文档主表（图片/PDF）。

### 4.3 识别与抽取域
- ocr_runs：OCR 执行流水（支持多 provider 重跑）。
- receipt_extractions：结构化抽取结果。
- receipt_tax_lines：税率分段（8%/10%/混合）。
- document_flags：异常与风控标签。

### 4.4 分类与会计域
- account_subjects：会计科目主数据（支持企业自定义）。
- classification_rules：行业/企业规则。
- classification_results：每次分类结果和置信度。
- review_tasks：人工复核任务。

### 4.5 导出域
- export_templates：导出模板（字段映射、列顺序、格式）。
- export_jobs：导出任务。
- export_files：导出文件记录。

### 4.6 审计域
- audit_logs：关键操作审计日志。

---

## 5. 日本业务字段建议

在 receipt_extractions 中至少保留：
- 取引日（transaction_date）
- 取引先名（merchant_name）
- 登録番号（qualified invoice registration number，示例正则：^T\\d{13}$）
- 合計金額（total_amount）
- 税抜金額（subtotal_excl_tax）
- 消費税額（tax_amount）
- 税率区分（8%, 10%, mixed）
- 通貨（默认 JPY）
- 支払方法（cash/card/transfer 等）

---

## 6. OCR Provider 抽象设计

定义统一接口：
- extract(file_path) -> normalized_payload
- payload 最终统一映射到 receipt_extractions，不暴露 provider 差异给上层。

推荐策略：
- 默认：receipt-ocr（低成本）。
- 可切换：Azure/Google（提高复杂票据识别率）。
- 支持二次重跑并保留 ocr_runs 历史。

---

## 7. API 边界（一期）

- POST /api/v1/ingestion/upload
- POST /api/v1/ingestion/folder-watch
- POST /api/v1/ingestion/jobs/{id}/scan
- GET /api/v1/documents
- GET /api/v1/documents/{id}
- POST /api/v1/documents/{id}/re-ocr
- POST /api/v1/documents/{id}/classify
- POST /api/v1/review/tasks/{id}/resolve
- GET /api/v1/export/templates
- POST /api/v1/export/jobs
- GET /api/v1/export/jobs/{id}/download

---

## 8. 一期交付拆分

### Sprint 1
- 多租户基础表与认证。
- 上传与文档入库。
- receipt-ocr 接入与 OCR 流程。

### Sprint 2
- 分类规则引擎。
- 异常标记与人工复核队列。
- 日本字段校验（税率/登记号）。

### Sprint 3
- CSV 模板化导出。
- 本地目录扫描任务。
- 操作审计与基础统计。

---

## 9. 成本与可运维性

- 通过 OCR provider 分层，控制识别成本并保留替换空间。
- 通过模板导出，降低对单一会计软件的耦合。
- 通过批次任务与审计日志，支持面向数百企业的可追溯运营。