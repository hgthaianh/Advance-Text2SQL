from src.nlp.entity_catalog import CatalogEntry, EntityCatalog
from src.nlp.entity_extractor import DeterministicEntityExtractor


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


def test_catalog_prefers_longest_overlapping_phrase():
    catalog = EntityCatalog((
        CatalogEntry('branch_name', 'North', ('north',)),
        CatalogEntry('branch_name', 'North Region', ('north region',)),
    ))
    values = DeterministicEntityExtractor(catalog=catalog).extract('north region')

    assert [(item.entity_type, item.normalized_value) for item in values] == [('branch_name', 'North Region')]
