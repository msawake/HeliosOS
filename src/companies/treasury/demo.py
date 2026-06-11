"""Treasury demo — (re)generate the synthetic reconciliation dataset.

    python -m src.bootstrap --company treasury --demo
    # or directly:
    python -m src.companies.treasury.demo
"""

from __future__ import annotations

from src.companies.treasury import mock_data


def run_demo():
    out = mock_data.write_csvs()
    print(mock_data.summary())
    print(f"\nWrote CSVs to: {out}")
    print(
        "\nDeploy the reconciliation agents with:\n"
        "  forgeos deploy src/companies/treasury/agents/bank-sap-reconciliation.yaml\n"
        "Agents read these CSVs via shell__exec (`cat src/companies/treasury/data/<table>.csv`)."
    )


if __name__ == "__main__":
    run_demo()
