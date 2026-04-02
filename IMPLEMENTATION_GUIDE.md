# 实施指南（基于最新要件）

## 1. 一期交付范围确认

必须实现：
- 上传能力：拖拽上传、批量上传。
- 本地目录能力：绑定目录、扫描入库（不强依赖云盘）。
- 自动识别：OCR 提取关键字段。
- 自动分类：按会计科目和行业规则归类。
- 异常复核：自动分拣到人工复核队列。
- 导出：CSV 模板化导出（含日系常用模板）。

暂缓：
- 高级图像增强。
- 深度 BI 报表。
- 对外商业 API 市场化能力。

---

## 2. 技术栈建议（与现有仓库对齐）

后端：
- FastAPI
- SQLAlchemy + PostgreSQL
- Redis + RQ/Celery（异步任务）
- receipt-ocr（默认 OCR）
- 可选 Azure Document Intelligence（增强 OCR）

前端：
- Vue 3 + Vite + TypeScript
- Pinia
- Element Plus（先保证交付速度）

---

## 3. 目录结构建议

```text
backend-example/
  app/
    main.py
    config.py
    database.py
    routers/
      auth.py
      ingestion.py
      documents.py
      review.py
      exports.py
      categories.py
    services/
      ocr_provider/
        base.py
        receipt_ocr_provider.py
        azure_provider.py
      ingestion_service.py
      ocr_service.py
      classification_service.py
      review_service.py
      export_service.py
    models/
    schemas/
  db/
    init.sql
frontend/
  src/
    pages/
      UploadCenter.vue
      DocumentList.vue
      ReviewQueue.vue
      ExportCenter.vue
      RuleConfig.vue
```

---

## 4. 开发顺序（按 6-8 周）

### 阶段 A（第 1-2 周）
- 建好数据库核心表。
- 完成登录鉴权和多租户隔离。
- 完成上传 API 与文档列表 API。

验收标准：
- 能上传并在列表页看到状态。

### 阶段 B（第 3-4 周）
- 接入 receipt-ocr。
- 完成 OCR 异步任务与状态回写。
- 建立异常标记（字段缺失、低置信度、税额逻辑异常）。

验收标准：
- 上传后可自动识别并出现结果/异常状态。

### 阶段 C（第 5-6 周）
- 完成分类规则引擎（关键词、商户、金额区间、正则）。
- 完成人工复核页面与提交动作。

验收标准：
- 识别后可自动分类；异常单可人工修正并确认。

### 阶段 D（第 7-8 周）
- 实现导出模板配置与 CSV 导出。
- 增加本地目录扫描任务。
- 完成审计日志与基础监控。

验收标准：
- 可按模板导出 CSV，支持会计软件导入。

---

## 5. OCR 抽象层（关键）

接口统一：
```python
class OcrProvider:
    def extract(self, file_path: str) -> dict:
        ...
```

统一输出字段：
- merchant_name
- transaction_date
- registration_number
- total_amount
- tax_amount
- tax_rate
- raw_text
- confidence

说明：
- 上层业务只消费统一字段，不直接依赖某个 OCR 提供商。

---

## 6. 分类规则设计

优先级由高到低：
1. 企业手工指定规则。
2. 企业行业规则。
3. 事务所公共规则。
4. 系统默认规则。

规则类型：
- keyword
- merchant_exact
- amount_range
- regex

建议：
- 每条规则记录命中分值，最终输出 classification_confidence。

---

## 7. CSV 模板机制

模板元素：
- 字段来源（DB 字段）
- 列名
- 列顺序
- 格式化函数（日期/金额）
- 缺省值策略

一期预置模板：
- 日本通用会计导入模板 A
- 日本通用会计导入模板 B

---

## 8. 质量保障

后端测试：
- OCR 任务状态流转测试。
- 分类规则命中测试。
- 导出模板映射测试。

前端测试：
- 上传流程 E2E。
- 复核流程 E2E。
- 导出流程 E2E。

---

## 9. 运维建议

- 每日目录扫描任务可配置时段。
- OCR 失败重试最多 2 次。
- 导出文件设置有效期（例如 7 天）。
- 保留审计日志至少 1 年。