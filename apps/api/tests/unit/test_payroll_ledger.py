from decimal import Decimal as D

from src.modules.payroll.ledger import salary_journal


def test_salary_journal_balances_and_splits_payables():
    lines = [
        ("BASIC", "EARNING", D("20000")),
        ("HRA", "EARNING", D("8000")),
        ("LOP", "DEDUCTION", D("1000")),
        ("PF", "DEDUCTION", D("1800")),
        ("ESI", "DEDUCTION", D("0")),
        ("PT", "DEDUCTION", D("200")),
        ("TDS", "DEDUCTION", D("500")),
        ("LOAN", "DEDUCTION", D("1000")),
        ("CANTEEN", "DEDUCTION", D("300")),
        ("PF_EMPLOYER", "EMPLOYER", D("1800")),
        ("EPS", "EMPLOYER", D("1250")),
    ]
    net = D("28000") - D("1000") - D("1800") - D("200") - D("500") - D("1000") - D("300")
    journal = dict((a, (d, c)) for a, d, c in salary_journal([(net, lines)]))
    assert journal["SALARY_EXPENSE"] == (D("28800.00"), D("0.00"))
    assert journal["PF_PAYABLE"][1] == D("3600.00")
    assert journal["SALARY_PAYABLE"][1] == net
    assert journal["OTHER_DEDUCTIONS_PAYABLE"][1] == D("300.00")
    assert "ESI_PAYABLE" not in journal
    debits = sum(d for d, _ in journal.values())
    credits = sum(c for _, c in journal.values())
    assert debits == credits
