"""Evidence-backed business terms shared by deterministic NLP and QueryIR."""

# The order header is the established revenue fact: schema aliases, existing
# QueryIR rules, and regression tests all target this measure.
REVENUE_COLUMN = 'sales.orders.total_amount'
INVOICE_AMOUNT_COLUMN = 'sales.invoices.total_amount'
COLLECTED_AMOUNT_COLUMN = 'sales.payments.amount'

# Customer tier and group membership are separate classifications.  The seed
# data assigns this boolean independently of both, so it is the only canonical
# predicate for the phrase "khách hàng VIP".
VIP_CUSTOMER_COLUMN = 'customer.customers.vip_flag'
VIP_CUSTOMER_VALUE = True
VIP_CUSTOMER_ALIASES = ('khach hang vip', 'khách hàng vip', 'khach vip', 'khách vip', 'vip')

# These are the only order lifecycle values evidenced by the bundled seed.
# PENDING and DRAFT exist elsewhere in the ERP schema, not on sales.orders.
ORDER_STATUS_VALUES = frozenset({'CONFIRMED', 'CANCELLED'})
