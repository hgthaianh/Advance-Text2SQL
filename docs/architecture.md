# Ranh giới module

Luồng dự kiến: API → chuẩn hóa → intent/entity/date → schema linking → QueryIR
→ permission resolver/policies → planner/join resolver → SQL builder
→ SQL validator → executor → formatter → QueryResult.

- Developer 1: `nlp/`, `schema/`, dữ liệu đánh giá.
- Developer 2: `query/`, `permission/`, `database/`, `validation/`, API.
- Thống nhất thay đổi contract trong `shared/models.py`, `query/ir.py`,
  `permission/models.py` trước khi thay đổi interface của bên còn lại.

Các class ABC chỉ định contract, chưa có implementation. Tên bảng/cột trong IR
phải kiểm tra bằng schema và quyền truy cập; giá trị filter phải bind qua
SQLAlchemy. SQL builder trả SQLAlchemy Select để tránh ghép raw SQL.
PermissionContext mặc định có allowlist rỗng (không cấp quyền).

TODO trước khi xử lý dữ liệu thật: xác thực UserContext từ danh tính đáng tin,
áp dụng column/row policy, kiểm tra truy vấn read-only, dùng tài khoản database
chỉ đọc và statement timeout, ánh xạ domain error sang HTTP, thêm migration
khi có ORM models. Mock API chưa chạy bất kỳ bước nào trong pipeline này.
