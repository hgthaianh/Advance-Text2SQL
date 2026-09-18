-- Text2SQL V2 benchmark / smoke-test queries
-- Fixed reference date used by synthetic data: 2026-09-18.

-- Q1. RLS demo: Tenant 1 / HR department should have exactly 80 ACTIVE employees.
SELECT COUNT(*) AS active_hr_employees
FROM hr.employees
WHERE tenant_id=1 AND department_id=1 AND employment_status='ACTIVE';

-- Q2. RLS demo: Tenant 1 / Finance department should have exactly 30 ACTIVE employees.
SELECT COUNT(*) AS active_finance_employees
FROM hr.employees
WHERE tenant_id=1 AND department_id=2 AND employment_status='ACTIVE';

-- Q3. Products below reorder level in tenant 1.
SELECT p.code, p.name, SUM(b.qty_available) AS available, p.reorder_level
FROM product.products p
JOIN inventory.inventory_balances b ON b.product_id=p.id
WHERE p.tenant_id=1
GROUP BY p.id,p.code,p.name,p.reorder_level
HAVING SUM(b.qty_available) < p.reorder_level
ORDER BY available;

-- Q4. Lots expiring within 30 days of 2026-09-18.
SELECT p.code,p.name,l.lot_number,l.expiry_date,l.remaining_qty
FROM inventory.stock_lots l
JOIN product.products p ON p.id=l.product_id
WHERE l.remaining_qty>0
  AND l.expiry_date BETWEEN DATE '2026-09-18' AND DATE '2026-10-18'
ORDER BY l.expiry_date;

-- Q5. Purchase orders with incomplete receipt quantities.
SELECT po.po_number, SUM(i.ordered_qty) ordered_qty, SUM(i.received_qty) received_qty
FROM procurement.purchase_orders po
JOIN procurement.purchase_order_items i ON i.purchase_order_id=po.id
GROUP BY po.id,po.po_number
HAVING SUM(i.received_qty)<SUM(i.ordered_qty)
ORDER BY po.id
LIMIT 30;

-- Q6. Supplier rejection rate.
SELECT s.name,
       SUM(gri.rejected_qty)/NULLIF(SUM(gri.received_qty),0) AS rejection_rate
FROM procurement.goods_receipt_items gri
JOIN procurement.goods_receipts gr ON gr.id=gri.receipt_id
JOIN procurement.suppliers s ON s.id=gr.supplier_id
GROUP BY s.id,s.name
HAVING SUM(gri.received_qty)>0
ORDER BY rejection_rate DESC NULLS LAST
LIMIT 10;

-- Q7. Top 5 products sold in September 2026. Product 1 is deliberately boosted.
SELECT p.id,p.code,p.name,SUM(oi.quantity) AS quantity_sold
FROM sales.order_items oi
JOIN sales.orders o ON o.id=oi.order_id
JOIN product.products p ON p.id=oi.product_id
WHERE o.tenant_id = 1
  AND o.order_date >= DATE '2026-09-01'
  AND o.order_date < DATE '2026-10-01'
  AND o.status <> 'CANCELLED'
GROUP BY p.id,p.code,p.name
ORDER BY quantity_sold DESC
LIMIT 5;

-- Q8. Overdue invoices not fully paid as of 2026-09-18.
SELECT i.invoice_no,i.total_amount,COALESCE(SUM(p.amount),0) AS paid_amount,
       i.total_amount-COALESCE(SUM(p.amount),0) AS outstanding
FROM sales.invoices i
LEFT JOIN sales.payments p ON p.invoice_id=i.id
WHERE i.due_date < DATE '2026-09-18'
GROUP BY i.id,i.invoice_no,i.total_amount
HAVING COALESCE(SUM(p.amount),0)<i.total_amount
ORDER BY outstanding DESC
LIMIT 30;

-- Q9. Budget versus actual by department. Selected departments deliberately overspend.
SELECT d.name,SUM(b.budget_amount) budget,SUM(b.actual_amount) actual,
       SUM(b.actual_amount-b.budget_amount) variance
FROM finance.budgets b
JOIN org.departments d ON d.id=b.department_id
WHERE b.fiscal_year=2026
GROUP BY d.id,d.name
ORDER BY variance DESC;

-- Q10. Journal balance by fiscal period: difference should be 0 for every period with entries.
SELECT fp.period_name,SUM(jl.debit_amount) debit,SUM(jl.credit_amount) credit,
       SUM(jl.debit_amount-jl.credit_amount) difference
FROM finance.journal_entry_lines jl
JOIN finance.journal_entries je ON je.id=jl.journal_entry_id
JOIN finance.fiscal_periods fp ON fp.id=je.period_id
GROUP BY fp.id,fp.period_name
ORDER BY fp.id;

-- Q11. SLA breach rate by customer tier.
SELECT c.customer_tier,
       AVG(CASE WHEN se.breached THEN 1.0 ELSE 0.0 END) AS breach_rate
FROM support.ticket_sla_events se
JOIN support.support_tickets t ON t.id=se.ticket_id
JOIN customer.customers c ON c.id=t.customer_id
GROUP BY c.customer_tier
ORDER BY breach_rate DESC;

-- Q12. Authorization metadata for an HR role.
SELECT r.code,p.schema_name,p.table_name,tap.row_scope_mode
FROM iam.roles r
JOIN iam.table_access_policies tap ON tap.role_id=r.id
JOIN iam.permissions p ON p.schema_name=tap.schema_name AND p.table_name=tap.table_name AND p.action='SELECT'
WHERE r.code='HR_T1'
ORDER BY p.schema_name,p.table_name;

-- Q13. Example authorized rewrite for user 10 (HR, Tenant 1, Department 1).
-- NL: "Công ty có bao nhiêu nhân viên?"
SELECT COUNT(*)
FROM hr.employees e
WHERE e.tenant_id=1 AND e.department_id=1;

-- Q14. Cross-domain: procurement spend by department.
SELECT d.name,SUM(poi.line_total) AS purchase_value
FROM procurement.purchase_orders po
JOIN procurement.purchase_requisitions pr ON pr.id=po.requisition_id
JOIN org.departments d ON d.id=pr.department_id
JOIN procurement.purchase_order_items poi ON poi.purchase_order_id=po.id
WHERE po.order_date BETWEEN DATE '2026-07-01' AND DATE '2026-09-30'
GROUP BY d.id,d.name
ORDER BY purchase_value DESC;

-- Q15. Customer value: revenue by customer segment.
SELECT cs.name AS segment,SUM(i.total_amount) revenue
FROM sales.invoices i
JOIN customer.customers c ON c.id=i.customer_id
LEFT JOIN customer.customer_segments cs ON cs.id=c.segment_id
WHERE i.invoice_date BETWEEN DATE '2026-01-01' AND DATE '2026-09-18'
GROUP BY cs.id,cs.name
ORDER BY revenue DESC;
