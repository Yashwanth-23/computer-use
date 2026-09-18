"""Quick in-process smoke test for the ApexCore mock portal.
Not part of the formal pytest suite -- just a fast sanity check while iterating.
Run: python3 -m mock_target.smoke_test
"""
import re

from fastapi.testclient import TestClient

from mock_target import core_data
from mock_target.app import app

client = TestClient(app)


def extract(pattern: str, text: str) -> str | None:
    m = re.search(pattern, text)
    return m.group(1) if m else None


def check(label: str, condition: bool):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}")
    if not condition:
        raise SystemExit(1)


def main():
    core_data.reset_all()

    # 1. Home page
    r = client.get("/")
    check("home page 200", r.status_code == 200)
    check("home page has nav link", "Member Lookup" in r.text)

    # 2. Lookup form renders with legacy field id
    r = client.get("/portal/member-lookup")
    check("lookup form 200", r.status_code == 200)
    check("lookup form has ASP.NET-style field id", 'id="ctl00_MainContent_txtMemberId"' in r.text)

    # 3. Search existing member -> balances
    r = client.post("/portal/member-lookup", data={"ctl00_MainContent_txtMemberId": "1001"})
    check("member 1001 200", r.status_code == 200)
    savings = extract(r'lblSavingsBalance">([^<]*)', r.text)
    checking = extract(r'lblCheckingBalance">([^<]*)', r.text)
    name = extract(r'lblMemberName">([^<]*)', r.text)
    print(f"    -> name={name} savings={savings} checking={checking}")
    check("savings balance present", savings == "$24500.00")
    check("checking balance present", checking == "$4120.00")
    check("member name present", name == "John Doe")

    # 4. Search non-existent member -> business outcome, not a crash
    r = client.post("/portal/member-lookup", data={"ctl00_MainContent_txtMemberId": "9999"})
    check("member 9999 still 200 (business outcome, not error)", r.status_code == 200)
    msg = extract(r'lblResultMessage">([^<]*)', r.text)
    print(f"    -> message={msg}")
    check("not-found message correct", msg is not None and "Not Found" in msg)

    # 5. Frozen account blocks sub-account opening
    r = client.get("/portal/sub-account/new", params={"member_id": "1003"})
    check("frozen member returns 200", r.status_code == 200)
    check("frozen restriction message shown", "FROZEN" in r.text)

    # 6. Maintenance interstitial can be toggled on
    client.post("/admin/maintenance/on")
    r = client.get("/portal/member-lookup")
    check("maintenance interstitial appears when enabled", "pnlMaintenanceAlert" in r.text)
    client.post("/admin/maintenance/off")
    r = client.get("/portal/member-lookup")
    check("maintenance interstitial gone when disabled", "pnlMaintenanceAlert" not in r.text)

    # 7. Full sub-account open flow -> review -> confirm -> receipt
    r = client.get("/portal/sub-account/new", params={"member_id": "1001"})
    check("sub-account form 200", r.status_code == 200)
    check("product dropdown present", 'id="ctl00_MainContent_ddlProductType"' in r.text)

    r = client.post(
        "/portal/sub-account/review",
        data={
            "ctl00_MainContent_hidMemberId": "1001",
            "ctl00_MainContent_ddlProductType": "HOLIDAY_CLUB",
            "ctl00_MainContent_txtInitialDeposit": "50",
        },
    )
    check("review step 200", r.status_code == 200)
    check("review shows product", "HOLIDAY_CLUB" in r.text)

    r = client.post(
        "/portal/sub-account/confirm",
        data={
            "ctl00_MainContent_hidMemberId": "1001",
            "ctl00_MainContent_hidProductType": "HOLIDAY_CLUB",
            "ctl00_MainContent_hidDeposit": "50",
        },
    )
    check("confirm step 200", r.status_code == 200)
    receipt_id = extract(r'lblReceiptId">([^<]*)', r.text)
    print(f"    -> receipt_id={receipt_id}")
    check("receipt id generated", receipt_id is not None and receipt_id.startswith("APX-"))

    # 8. Validation error path: deposit below minimum
    r = client.post(
        "/portal/sub-account/review",
        data={
            "ctl00_MainContent_hidMemberId": "1001",
            "ctl00_MainContent_ddlProductType": "HOLIDAY_CLUB",
            "ctl00_MainContent_txtInitialDeposit": "5",
        },
    )
    check("validation error 200 (not a crash)", r.status_code == 200)
    check("validation message shown", "at least $25.00" in r.text)

    # 9. Static CSS served
    r = client.get("/static/_legacy.css")
    check("static css 200", r.status_code == 200)

    print("\nAll smoke tests passed.")


if __name__ == "__main__":
    main()
