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

# The production seed uses CONFIRMED/CANCELLED; the deterministic PostgreSQL
# result-accuracy fixture also exercises COMPLETED/PENDING order rows.  DRAFT
# remains unsupported for orders: it is evidenced only for payroll runs.
ORDER_STATUS_VALUES = frozenset({'CONFIRMED', 'CANCELLED', 'COMPLETED', 'PENDING'})
