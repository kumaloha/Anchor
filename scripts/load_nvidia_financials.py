"""
将 NVIDIA 5年财报数据写入 DB（CompanyProfile + FinancialStatement + FinancialLineItem）
"""

import asyncio
from datetime import date

from anchor.database.session import AsyncSessionLocal
from anchor.models import CompanyProfile, FinancialStatement, FinancialLineItem


# ── NVIDIA 三表数据（单位：百万美元）──────────────────────────────────────

FISCAL_YEARS = ["FY2022", "FY2023", "FY2024", "FY2025", "FY2026"]
# NVIDIA fiscal year ends in January: FY2022 = Feb 2021 - Jan 2022
PERIOD_DATES = {
    "FY2022": date(2022, 1, 30),
    "FY2023": date(2023, 1, 29),
    "FY2024": date(2024, 1, 28),
    "FY2025": date(2025, 1, 26),
    "FY2026": date(2026, 1, 25),
}

INCOME = {
    "FY2022": [
        ("revenue", "Revenue", 26914),
        ("cost_of_revenue", "Cost of Revenue", 9439),
        ("gross_profit", "Gross Profit", 17475),
        ("rnd_expense", "Research & Development", 5268),
        ("sga_expense", "Selling, General & Administrative", 2166),
        ("operating_income", "Operating Income", 10041),
        ("net_income", "Net Income", 9752),
        ("eps_diluted", "EPS (Diluted)", 0.39),
    ],
    "FY2023": [
        ("revenue", "Revenue", 26974),
        ("cost_of_revenue", "Cost of Revenue", 11618),
        ("gross_profit", "Gross Profit", 15356),
        ("rnd_expense", "Research & Development", 7339),
        ("sga_expense", "Selling, General & Administrative", 2440),
        ("operating_income", "Operating Income", 4224),
        ("net_income", "Net Income", 4368),
        ("eps_diluted", "EPS (Diluted)", 0.17),
    ],
    "FY2024": [
        ("revenue", "Revenue", 60922),
        ("cost_of_revenue", "Cost of Revenue", 16621),
        ("gross_profit", "Gross Profit", 44301),
        ("rnd_expense", "Research & Development", 8675),
        ("sga_expense", "Selling, General & Administrative", 2654),
        ("operating_income", "Operating Income", 32972),
        ("net_income", "Net Income", 29760),
        ("eps_diluted", "EPS (Diluted)", 1.19),
    ],
    "FY2025": [
        ("revenue", "Revenue", 130497),
        ("cost_of_revenue", "Cost of Revenue", 32639),
        ("gross_profit", "Gross Profit", 97858),
        ("rnd_expense", "Research & Development", 12914),
        ("sga_expense", "Selling, General & Administrative", 3491),
        ("operating_income", "Operating Income", 81453),
        ("net_income", "Net Income", 72880),
        ("eps_diluted", "EPS (Diluted)", 2.94),
    ],
    "FY2026": [
        ("revenue", "Revenue", 215938),
        ("cost_of_revenue", "Cost of Revenue", 62475),
        ("gross_profit", "Gross Profit", 153463),
        ("rnd_expense", "Research & Development", 18497),
        ("sga_expense", "Selling, General & Administrative", 4579),
        ("operating_income", "Operating Income", 130387),
        ("net_income", "Net Income", 120067),
        ("eps_diluted", "EPS (Diluted)", 4.90),
    ],
}

BALANCE = {
    "FY2022": [
        ("cash_and_equivalents", "Cash & Equivalents", 1990),
        ("short_term_investments", "Short-Term Investments", 19218),
        ("accounts_receivable", "Accounts Receivable", 4650),
        ("inventory", "Inventory", 2605),
        ("total_current_assets", "Total Current Assets", 28829),
        ("ppe_net", "Property, Plant & Equipment (Net)", 3607),
        ("goodwill", "Goodwill", 4349),
        ("intangible_assets", "Intangible Assets", 2339),
        ("total_assets", "Total Assets", 44187),
        ("accounts_payable", "Accounts Payable", 1783),
        ("short_term_debt", "Short-Term Debt", 0),
        ("total_current_liabilities", "Total Current Liabilities", 4335),
        ("long_term_debt", "Long-Term Debt", 10946),
        ("total_liabilities", "Total Liabilities", 17575),
        ("shareholders_equity", "Shareholders' Equity", 26612),
    ],
    "FY2023": [
        ("cash_and_equivalents", "Cash & Equivalents", 3389),
        ("short_term_investments", "Short-Term Investments", 9907),
        ("accounts_receivable", "Accounts Receivable", 3827),
        ("inventory", "Inventory", 5159),
        ("total_current_assets", "Total Current Assets", 23073),
        ("ppe_net", "Property, Plant & Equipment (Net)", 4845),
        ("goodwill", "Goodwill", 4372),
        ("intangible_assets", "Intangible Assets", 1676),
        ("total_assets", "Total Assets", 41182),
        ("accounts_payable", "Accounts Payable", 1193),
        ("short_term_debt", "Short-Term Debt", 1250),
        ("total_current_liabilities", "Total Current Liabilities", 6563),
        ("long_term_debt", "Long-Term Debt", 9703),
        ("total_liabilities", "Total Liabilities", 19081),
        ("shareholders_equity", "Shareholders' Equity", 22101),
    ],
    "FY2024": [
        ("cash_and_equivalents", "Cash & Equivalents", 7280),
        ("short_term_investments", "Short-Term Investments", 18704),
        ("accounts_receivable", "Accounts Receivable", 9999),
        ("inventory", "Inventory", 5282),
        ("total_current_assets", "Total Current Assets", 44345),
        ("ppe_net", "Property, Plant & Equipment (Net)", 5260),
        ("goodwill", "Goodwill", 4430),
        ("intangible_assets", "Intangible Assets", 1112),
        ("total_assets", "Total Assets", 65728),
        ("accounts_payable", "Accounts Payable", 2699),
        ("short_term_debt", "Short-Term Debt", 1250),
        ("total_current_liabilities", "Total Current Liabilities", 10631),
        ("long_term_debt", "Long-Term Debt", 8459),
        ("total_liabilities", "Total Liabilities", 22750),
        ("shareholders_equity", "Shareholders' Equity", 42978),
    ],
    "FY2025": [
        ("cash_and_equivalents", "Cash & Equivalents", 8589),
        ("short_term_investments", "Short-Term Investments", 34621),
        ("accounts_receivable", "Accounts Receivable", 23065),
        ("inventory", "Inventory", 10080),
        ("total_current_assets", "Total Current Assets", 80126),
        ("ppe_net", "Property, Plant & Equipment (Net)", 8076),
        ("goodwill", "Goodwill", 5188),
        ("intangible_assets", "Intangible Assets", 807),
        ("total_assets", "Total Assets", 111601),
        ("accounts_payable", "Accounts Payable", 6310),
        ("short_term_debt", "Short-Term Debt", 0),
        ("total_current_liabilities", "Total Current Liabilities", 18047),
        ("long_term_debt", "Long-Term Debt", 8463),
        ("total_liabilities", "Total Liabilities", 32274),
        ("shareholders_equity", "Shareholders' Equity", 79327),
    ],
    "FY2026": [
        ("cash_and_equivalents", "Cash & Equivalents", 10605),
        ("short_term_investments", "Short-Term Investments", 51951),
        ("accounts_receivable", "Accounts Receivable", 38466),
        ("inventory", "Inventory", 21403),
        ("total_current_assets", "Total Current Assets", 125605),
        ("ppe_net", "Property, Plant & Equipment (Net)", 13250),
        ("goodwill", "Goodwill", 20832),
        ("intangible_assets", "Intangible Assets", 3306),
        ("total_assets", "Total Assets", 206803),
        ("accounts_payable", "Accounts Payable", 9812),
        ("short_term_debt", "Short-Term Debt", 999),
        ("total_current_liabilities", "Total Current Liabilities", 32163),
        ("long_term_debt", "Long-Term Debt", 7469),
        ("total_liabilities", "Total Liabilities", 49510),
        ("shareholders_equity", "Shareholders' Equity", 157293),
    ],
}

CASHFLOW = {
    "FY2022": [
        ("operating_cash_flow", "Operating Cash Flow", 9108),
        ("capital_expenditures", "Capital Expenditures", -976),
        ("free_cash_flow", "Free Cash Flow", 8132),
        ("depreciation_amortization", "Depreciation & Amortization", 1174),
        ("investing_cash_flow", "Investing Cash Flow", -9830),
        ("financing_cash_flow", "Financing Cash Flow", 1865),
        ("dividends_paid", "Dividends Paid", -399),
        ("share_repurchase", "Share Buybacks", 0),
        ("proceeds_from_debt_issuance", "Long-Term Debt Issued", 4977),
        ("debt_repaid", "Long-Term Debt Repaid", -1000),
        ("net_cash_change", "Net Cash Flow", 1143),
    ],
    "FY2023": [
        ("operating_cash_flow", "Operating Cash Flow", 5641),
        ("capital_expenditures", "Capital Expenditures", -1833),
        ("free_cash_flow", "Free Cash Flow", 3808),
        ("depreciation_amortization", "Depreciation & Amortization", 1544),
        ("investing_cash_flow", "Investing Cash Flow", 7375),
        ("financing_cash_flow", "Financing Cash Flow", -11617),
        ("dividends_paid", "Dividends Paid", -398),
        ("share_repurchase", "Share Buybacks", -10039),
        ("proceeds_from_debt_issuance", "Long-Term Debt Issued", 0),
        ("debt_repaid", "Long-Term Debt Repaid", 0),
        ("net_cash_change", "Net Cash Flow", 1399),
    ],
    "FY2024": [
        ("operating_cash_flow", "Operating Cash Flow", 28090),
        ("capital_expenditures", "Capital Expenditures", -1069),
        ("free_cash_flow", "Free Cash Flow", 27021),
        ("depreciation_amortization", "Depreciation & Amortization", 1508),
        ("investing_cash_flow", "Investing Cash Flow", -10566),
        ("financing_cash_flow", "Financing Cash Flow", -13633),
        ("dividends_paid", "Dividends Paid", -395),
        ("share_repurchase", "Share Buybacks", -9533),
        ("proceeds_from_debt_issuance", "Long-Term Debt Issued", 0),
        ("debt_repaid", "Long-Term Debt Repaid", -1250),
        ("net_cash_change", "Net Cash Flow", 3891),
    ],
    "FY2025": [
        ("operating_cash_flow", "Operating Cash Flow", 64089),
        ("capital_expenditures", "Capital Expenditures", -3236),
        ("free_cash_flow", "Free Cash Flow", 60853),
        ("depreciation_amortization", "Depreciation & Amortization", 1864),
        ("investing_cash_flow", "Investing Cash Flow", -20421),
        ("financing_cash_flow", "Financing Cash Flow", -42359),
        ("dividends_paid", "Dividends Paid", -834),
        ("share_repurchase", "Share Buybacks", -33706),
        ("proceeds_from_debt_issuance", "Long-Term Debt Issued", 0),
        ("debt_repaid", "Long-Term Debt Repaid", -1250),
        ("net_cash_change", "Net Cash Flow", 1309),
    ],
    "FY2026": [
        ("operating_cash_flow", "Operating Cash Flow", 102718),
        ("capital_expenditures", "Capital Expenditures", -6042),
        ("free_cash_flow", "Free Cash Flow", 96676),
        ("depreciation_amortization", "Depreciation & Amortization", 2843),
        ("investing_cash_flow", "Investing Cash Flow", -52228),
        ("financing_cash_flow", "Financing Cash Flow", -48474),
        ("dividends_paid", "Dividends Paid", -974),
        ("share_repurchase", "Share Buybacks", -40086),
        ("proceeds_from_debt_issuance", "Long-Term Debt Issued", 0),
        ("debt_repaid", "Long-Term Debt Repaid", 0),
        ("net_cash_change", "Net Cash Flow", 2016),
    ],
}


async def main():
    async with AsyncSessionLocal() as session:
        # 1. CompanyProfile
        company = CompanyProfile(
            name="NVIDIA Corporation",
            ticker="NVDA",
            market="us",
            industry="Semiconductors",
            summary="设计GPU和AI加速芯片，通过台积电代工，卖给数据中心/云厂商/游戏玩家",
        )
        session.add(company)
        await session.flush()
        print(f"CompanyProfile created: id={company.id}")

        stmt_count = 0
        item_count = 0

        for fy in FISCAL_YEARS:
            reported = PERIOD_DATES[fy]

            for stype, data_dict in [
                ("income", INCOME),
                ("balance_sheet", BALANCE),
                ("cashflow", CASHFLOW),
            ]:
                items = data_dict[fy]
                stmt = FinancialStatement(
                    company_id=company.id,
                    period=fy,
                    period_type="annual",
                    statement_type=stype,
                    currency="USD",
                    reported_at=reported,
                )
                session.add(stmt)
                await session.flush()
                stmt_count += 1

                for ordinal, (key, label, value) in enumerate(items, 1):
                    li = FinancialLineItem(
                        statement_id=stmt.id,
                        item_key=key,
                        item_label=label,
                        value=value,
                        ordinal=ordinal,
                    )
                    session.add(li)
                    item_count += 1

        await session.commit()
        print(f"写入完成: {stmt_count} statements, {item_count} line items")


if __name__ == "__main__":
    asyncio.run(main())
