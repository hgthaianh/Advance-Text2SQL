from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from src.pipeline.orchestrator import Text2SQLPipeline
from src.pipeline.observability import PipelineEvent, PipelineObserver
from src.nlp.scope_router import ScopeRouter
from src.permission.models import PermissionContext
from src.shared.models import UserContext


@pytest.fixture
def pipeline():
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    with engine.begin() as connection:
        connection.exec_driver_sql("ATTACH DATABASE ':memory:' AS hr")
        connection.exec_driver_sql("ATTACH DATABASE ':memory:' AS sales")
        connection.exec_driver_sql("ATTACH DATABASE ':memory:' AS org")
        connection.exec_driver_sql("ATTACH DATABASE ':memory:' AS customer")
        connection.exec_driver_sql(
            'CREATE TABLE hr.employees ('
            'id TEXT, department_id TEXT, branch_id TEXT, team_id TEXT, tenant_id TEXT, '
            'full_name TEXT, base_salary NUMERIC, manager_id TEXT)'
        )
        connection.exec_driver_sql('CREATE TABLE org.branches (id TEXT, tenant_id TEXT, name TEXT)')
        connection.exec_driver_sql("INSERT INTO org.branches VALUES ('B1', 'T1', 'HCM'), ('B2', 'T1', 'Ha Noi'), ('B3', 'T2', 'Other Branch')")
        connection.exec_driver_sql('CREATE TABLE customer.customers (id TEXT, tenant_id TEXT, customer_type TEXT, name TEXT)')
        connection.exec_driver_sql("INSERT INTO customer.customers VALUES ('C1', 'T1', 'VIP', 'VIP Co'), ('C2', 'T1', 'NORMAL', 'Normal Co'), ('C3', 'T2', 'VIP', 'Other VIP')")
        connection.exec_driver_sql(
            'CREATE TABLE sales.orders ('
            'id TEXT, tenant_id TEXT, customer_id TEXT, branch_id TEXT, order_date DATE, total_amount NUMERIC)'
        )
        connection.exec_driver_sql(
            "INSERT INTO sales.orders VALUES "
            "('O1', 'T1', 'C1', 'B1', '2026-08-10', 100), "
            "('O2', 'T1', 'C1', 'B1', '2026-08-20', 200), "
            "('O3', 'T1', 'C2', 'B2', '2026-08-20', 900), "
            "('O4', 'T2', 'C3', 'B3', '2026-08-20', 700)"
        )
        connection.exec_driver_sql(
            "INSERT INTO hr.employees VALUES "
            "('E1', 'D1', 'B1', 'T1', 'T1', 'An', 100, 'E2'), "
            "('E2', 'D1', 'B1', 'T2', 'T1', 'Binh', 200, NULL), "
            "('E3', 'D2', 'B2', 'T3', 'T1', 'Chi', 300, 'E2'), "
            "('E4', 'D1', 'B1', 'T1', 'T2', 'Other Tenant', 400, NULL)"
        )
    yield Text2SQLPipeline(engine=engine)
    engine.dispose()


def _run(pipeline: Text2SQLPipeline, question: str, user: UserContext):
    return pipeline.execute(question, user, reference_date=date(2026, 9, 20))


def test_count_executes_for_global_and_department_scope(pipeline: Text2SQLPipeline):
    global_result = _run(pipeline, 'co bao nhieu nhan vien', UserContext(user_id='admin', role='admin', tenant_id='T1'))
    department_result = _run(pipeline, 'co bao nhieu nhan vien', UserContext(
        user_id='manager', role='department_manager', department_id='D1', tenant_id='T1',
    ))

    assert global_result.success and global_result.scalar == 3
    assert department_result.success and department_result.scalar == 2


def test_tenant_scope_is_required_and_excludes_other_tenants(pipeline: Text2SQLPipeline):
    tenant_result = _run(pipeline, 'co bao nhieu nhan vien', UserContext(
        user_id='tenant-two', role='admin', tenant_id='T2',
    ))
    missing_tenant = _run(pipeline, 'co bao nhieu nhan vien', UserContext(user_id='missing', role='admin'))

    assert tenant_result.success and tenant_result.scalar == 1
    assert not missing_tenant.success
    assert missing_tenant.error_code == 'PERMISSION_DENIED'


def test_self_scope_uses_employee_identity_and_real_primary_key(pipeline: Text2SQLPipeline):
    result = _run(pipeline, 'thong tin nhan vien', UserContext(
        user_id='login-1', employee_id='E1', role='employee', tenant_id='T1',
    ))

    assert result.success
    assert result.rows == [{'id': 'E1'}]


def test_unauthorized_salary_is_controlled_denial(pipeline: Text2SQLPipeline):
    result = _run(pipeline, 'luong nhan vien', UserContext(user_id='viewer', role='viewer', tenant_id='T1'))

    assert not result.success
    assert result.error_code == 'PERMISSION_DENIED'


def test_table_allowlist_denies_planner_added_join_table(pipeline: Text2SQLPipeline):
    class OrdersOnlyResolver:
        def resolve(self, _user):
            return PermissionContext(
                allowed_tables=['sales.orders'], allowed_columns={'*': ['*']}, tenant_id='T1',
            )

    pipeline.permission_resolver = OrdersOnlyResolver()
    result = _run(pipeline, 'doanh thu theo chi nhanh', UserContext(
        user_id='viewer', role='viewer', tenant_id='T1',
    ))

    assert not result.success
    assert result.error_code == 'PERMISSION_DENIED'


def test_revenue_date_and_entity_filters_execute(pipeline: Text2SQLPipeline):
    result = _run(pipeline, 'tong doanh thu khach hang vip thang 8 nam 2026', UserContext(
        user_id='admin', role='admin', tenant_id='T1',
    ))

    assert result.success
    assert result.scalar == 300


def test_grouped_revenue_join_executes(pipeline: Text2SQLPipeline):
    result = _run(pipeline, 'doanh thu theo chi nhanh', UserContext(user_id='admin', role='admin', tenant_id='T1'))

    assert result.success
    assert {tuple(row.values()) for row in result.rows} == {('HCM', 300), ('Ha Noi', 900)}


def test_employee_manager_self_join_executes_through_public_pipeline(pipeline: Text2SQLPipeline):
    result = _run(pipeline, 'List each employee and their manager', UserContext(
        user_id='admin', role='admin', tenant_id='T1',
    ))

    assert result.success
    assert result.rows == [
        {'full_name': 'An', 'full_name_1': 'Binh'},
        {'full_name': 'Binh', 'full_name_1': None},
        {'full_name': 'Chi', 'full_name_1': 'Binh'},
    ]


def test_employee_manager_self_join_keeps_employee_when_manager_is_outside_department_scope(pipeline: Text2SQLPipeline):
    class DepartmentResolver:
        def resolve(self, _user):
            return PermissionContext(
                allowed_tables=['*'], allowed_columns={'hr.employees': ['full_name']},
                tenant_id='T1', scope='DEPARTMENT',
                row_filters=[{'column': 'department_id', 'value': 'D2', 'source': 'permission'}],
            )

    pipeline.permission_resolver = DepartmentResolver()
    result = _run(pipeline, 'List each employee and their manager', UserContext(
        user_id='manager', role='department_manager', department_id='D2', tenant_id='T1',
    ))

    assert result.success
    assert result.rows == [{'full_name': 'Chi', 'full_name_1': None}]


def test_unsupported_question_is_controlled(pipeline: Text2SQLPipeline):
    result = _run(pipeline, 'thoi tiet hom nay the nao', UserContext(user_id='admin', role='admin', tenant_id='T1'))

    assert not result.success
    assert result.error_code == 'UNSUPPORTED_QUERY'


def test_out_of_scope_request_does_not_call_schema_linker(pipeline: Text2SQLPipeline):
    class LinkerMustNotRun:
        registry = pipeline.registry
        graph = pipeline.linker.graph

        def link(self, *_args, **_kwargs):
            raise AssertionError('Schema linker must not run for out-of-scope input')

    pipeline.linker = LinkerMustNotRun()
    result = _run(pipeline, 'thoi tiet HCM hom nay the nao', UserContext(user_id='admin', role='admin', tenant_id='T1'))

    assert not result.success
    assert result.error_code == 'UNSUPPORTED_QUERY'


def test_ambiguous_scope_is_a_controlled_rejection(pipeline: Text2SQLPipeline):
    result = _run(pipeline, 'tai khoan', UserContext(user_id='admin', role='admin', tenant_id='T1'))

    assert not result.success
    assert result.error_code == 'AMBIGUOUS_QUERY'


class _CaptureObserver(PipelineObserver):
    def __init__(self) -> None:
        self.events: list[PipelineEvent] = []

    def record(self, event: PipelineEvent) -> None:
        self.events.append(event)


def test_pipeline_observability_excludes_question_identity_and_values(pipeline: Text2SQLPipeline):
    observer = _CaptureObserver()
    pipeline.observer = observer
    question = 'thoi tiet cua alice@example.com la gi'
    result = _run(pipeline, question, UserContext(user_id='private-user', role='admin', tenant_id='T1'))

    assert not result.success
    assert len(observer.events) == 1
    assert observer.events[0].query_id == result.query_id
    assert observer.events[0].outcome == 'failure'
    assert observer.events[0].stage == 'scope_routing'
    assert observer.events[0].error_code == 'UNSUPPORTED_QUERY'
    serialized = observer.events[0].model_dump_json()
    assert question not in serialized
    assert 'private-user' not in serialized
    assert 'alice@example.com' not in serialized


def test_observer_failure_does_not_change_controlled_pipeline_failure(pipeline: Text2SQLPipeline):
    class BrokenObserver(PipelineObserver):
        def record(self, event: PipelineEvent) -> None:
            raise RuntimeError('collector unavailable')

    pipeline.observer = BrokenObserver()
    result = _run(pipeline, 'thoi tiet hom nay the nao', UserContext(user_id='admin', role='admin', tenant_id='T1'))

    assert not result.success
    assert result.error_code == 'UNSUPPORTED_QUERY'
