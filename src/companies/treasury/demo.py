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
        "\nNext: upload each agent's CSVs to the Google Drive folder shared with its\n"
        "service account, set the folder ids, and deploy — see docs/treasury-demo.md\n"
        "(or run ./scripts/set_treasury_folders.sh then `forgeos deploy <manifest>`).\n"
        "Agents read these CSVs from their Drive folder via drive__read_file."
    )


if __name__ == "__main__":
    run_demo()
