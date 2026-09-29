# ERP Text2SQL QA Regression Set

Tổng số case: **114**

## Phân bố
- `HR`: 12
- `SALES`: 12
- `INVENTORY`: 8
- `PROCUREMENT`: 8
- `FINANCE`: 8
- `SUPPORT`: 8
- `CUSTOMER_PRODUCT`: 8
- `CROSS_DOMAIN`: 12
- `PERMISSION`: 12
- `PARAPHRASE`: 16
- `AMBIGUOUS_UNSUPPORTED`: 10

## Lưu ý

- Không hard-code expected numeric result khi chưa chạy seed DB thật.
- Test theo từng stage: scope → intent → entity → linker → permission → SQL validation → execution result.
- `expected_table_concepts` cần được đối chiếu với `01_schema.sql`/`table_manifest.csv` trước khi dùng làm exact-table assertion.
- Nhóm `PARAPHRASE` phải resolve về cùng semantic query.
- Nhóm `PERMISSION` phải kiểm tra tenant isolation và department scope, không chỉ kiểm tra SQL hợp lệ.

## Ví dụ case

```json
{
  "id": "QA-HR-001",
  "category": "HR",
  "username": "user0001",
  "question": "Phòng nhân sự hiện có bao nhiêu nhân viên?",
  "expected_intent": "COUNT",
  "expected_entities": [
    "employee",
    "department"
  ],
  "expected_table_concepts": [
    "hr.employees",
    "org.departments"
  ],
  "expected_join_path": [
    "employee -> department"
  ],
  "expected_filters": [
    "department=HR"
  ],
  "expected_scope": "DEPARTMENT",
  "expected_behavior": "SUCCESS",
  "expected_result_type": "scalar",
  "expected_sql_shape": "COUNT",
  "expected_permission": "ALLOW",
  "paraphrase_group": null,
  "notes": null
}
```

## Canonical schema and RBAC notes

- `expected_table_concepts` uses canonical tables from `dataset_v2/sql/01_schema.sql`; the suite currently has zero unknown table labels.
- `base_salary` is denied to non-admin users by `iam.column_access_policies`.
- Finance facts without a deterministic department attribution are denied for department-scoped finance roles. This is fail-closed behavior, not an execution failure.
- A request naming another tenant must be denied or return an empty result after intersecting with the authenticated tenant scope.
