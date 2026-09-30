from src.nlp.entity_catalog import CatalogEntry, EntityCatalog
from src.nlp.entity_extractor import DeterministicEntityExtractor
import pytest


def test_catalog_extracts_exact_business_values_and_labelled_codes():
    catalog = EntityCatalog((
        CatalogEntry('branch_name', 'Central', ('central branch',)),
        CatalogEntry('department_name', 'Sales', ('sales department',)),
        CatalogEntry('status', 'COMPLETED', ('completed',)),
    ))
    values = DeterministicEntityExtractor(catalog=catalog).extract(
        'completed at central branch, sales department; customer code c-01 and product code p-02'
    )

    assert {(item.entity_type, item.normalized_value) for item in values} == {
        ('status', 'COMPLETED'), ('branch_name', 'Central'), ('department_name', 'Sales'),
        ('customer_code', 'C-01'), ('product_code', 'P-02'),
    }


def test_code_abbreviation_does_not_mistake_customer_phrase_for_a_code():
    values = DeterministicEntityExtractor().extract('khach hang vip')

    assert ('customer_code', 'ACH') not in {(item.entity_type, item.normalized_value) for item in values}


def test_vip_customer_is_a_boolean_flag_not_customer_type_or_tier():
    values = DeterministicEntityExtractor().extract('danh sach khach hang vip')

    assert {(item.entity_type, item.normalized_value) for item in values} == {('vip_flag', True)}


def test_catalog_prefers_longest_overlapping_phrase():
    catalog = EntityCatalog((
        CatalogEntry('branch_name', 'North', ('north',)),
        CatalogEntry('branch_name', 'North Region', ('north region',)),
    ))
    values = DeterministicEntityExtractor(catalog=catalog).extract('north region')

    assert [(item.entity_type, item.normalized_value) for item in values] == [('branch_name', 'North Region')]


def test_finance_context_does_not_turn_accounting_into_a_department_filter():
    values = DeterministicEntityExtractor().extract('he thong tai khoan ke toan')

    assert 'department_name' not in {item.entity_type for item in values}


def test_erp_enum_values_are_typed_not_dropped():
    from src.nlp.entity_extractor import DeterministicEntityExtractor

    extractor = DeterministicEntityExtractor()
    values = extractor.extract('don hang CONFIRMED, hoa don OVERDUE, khach hang GOLD, ticket URGENT')

    assert {(item.entity_type, item.normalized_value) for item in values} >= {
        ('status', 'CONFIRMED'), ('status', 'OVERDUE'),
        ('customer_tier', 'GOLD'), ('priority', 'URGENT'),
    }


def test_prefixed_business_codes_are_grounded_without_a_marker():
    from src.nlp.entity_extractor import DeterministicEntityExtractor

    extractor = DeterministicEntityExtractor()
    values = extractor.extract('EMP000120 SO000123 TKT000123 CUST000120 PROD000100')

    assert {(item.entity_type, item.normalized_value) for item in values} == {
        ('employee_code', 'EMP000120'), ('order_code', 'SO000123'),
        ('ticket_code', 'TKT000123'), ('customer_code', 'CUST000120'),
        ('product_code', 'PROD000100'),
    }


def test_catalog_rejects_alias_shared_by_different_business_values():
    with pytest.raises(ValueError, match='Ambiguous catalog alias'):
        EntityCatalog((
            CatalogEntry('branch_name', 'North', ('north',)),
            CatalogEntry('department_name', 'North', ('north',)),
        ))


def test_tenant_id_is_grounded_but_sales_representative_is_not_a_department_value():
    extractor = DeterministicEntityExtractor()

    tenant = extractor.extract('co bao nhieu nhan vien cua tenant 2')
    representative = extractor.extract('doanh thu theo sales representative')

    assert ('tenant_id', '2') in {(item.entity_type, item.normalized_value) for item in tenant}
    assert 'department_name' not in {item.entity_type for item in representative}
