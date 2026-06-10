"""End-to-end verification of the File Manager save/load/review pipeline.

Runs against an isolated temporary documents directory so real user data
is never touched. Covers:
  1. Upload base -> add reviews -> Save (new entry, name from filename)
  2. FM Select as base -> Analysis -> import-reviews -> reviews visible
  3. Save with target -> overwrite (same doc_id, same name, reviews replaced)
  4. Save by duplicate title with overwrite_existing -> overwrites, no duplicate
  5. Save As -> new doc with given name + reviews copied
  6. Diff flow -> save old/new sides with correct filenames and side reviews
  7. Fresh local upload -> no reviews remain
"""
import io
import json
import sys
import tempfile
import shutil
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import fitz

import app as appmod

TMP = Path(tempfile.mkdtemp(prefix="fm_verify_"))
appmod.DOCUMENTS_DIR = TMP / "documents"
appmod.DOCUMENTS_DIR.mkdir(parents=True, exist_ok=True)
appmod.DEFAULT_FILE_MANAGER_SEEDED = True  # skip seeding defaults

client = appmod.app.test_client()

FAILURES = []


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}" + (f" :: {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


def make_pdf(lines):
    doc = fitz.open()
    page = doc.new_page()
    y = 72
    for line in lines:
        page.insert_text((72, y), line, fontsize=12)
        y += 24
    buf = doc.tobytes()
    doc.close()
    return buf


def upload_base(pdf_bytes, filename):
    res = client.post("/api/documents", data={"pdf": (io.BytesIO(pdf_bytes), filename)},
                      content_type="multipart/form-data")
    assert res.status_code == 201, res.get_json()
    return res.get_json()


def get_reviews(doc_id, run_id):
    res = client.get(f"/api/documents/{doc_id}/runs/{run_id}/reviews")
    return res.get_json() if res.status_code == 200 else []


def add_review(doc_id, run_id, side, word_ids, comment):
    res = client.post(f"/api/documents/{doc_id}/runs/{run_id}/reviews",
                      json={"side": side, "word_ids": word_ids, "comment": comment})
    assert res.status_code == 201, res.get_json()
    return res.get_json()


def fm_items():
    res = client.get("/api/file-manager")
    return res.get_json().get("items", [])


PDF_V1 = make_pdf(["Quarterly revenue grew strongly in 2025.",
                   "Operating margin improved by two points.",
                   "Cash flow remained stable across segments."])
PDF_V2 = make_pdf(["Quarterly revenue grew modestly in 2025.",
                   "Operating margin improved by three points.",
                   "Cash flow remained stable across segments."])

# ---------------------------------------------------------------- Scenario 1
print("\n--- Scenario 1: upload base, add reviews, Save ---")
d1 = upload_base(PDF_V1, "Report_v1.pdf")
doc1, run1 = d1["doc_id"], d1["run_id"]
rv1 = add_review(doc1, run1, "new", [0, 1, 2], "check revenue wording")
rv2 = add_review(doc1, run1, "new", [5, 6], "verify margin numbers")
check("reviews created on live run", len(get_reviews(doc1, run1)) == 2)

res = client.post(f"/api/documents/{doc1}/runs/{run1}/save-file/new",
                  json={"title": "Report_v1", "overwrite_existing": True})
save1 = res.get_json()
check("save returns 2xx", res.status_code in (200, 201), str(save1))
saved_doc = save1.get("doc_id")
check("save creates FM entry titled Report_v1", save1.get("title") == "Report_v1")

items = fm_items()
entry = next((i for i in items if i["doc_id"] == saved_doc), None)
check("FM lists exactly one entry", len(items) == 1, json.dumps(items))
check("FM display name is Report_v1.pdf", entry and entry.get("latest_filename") == "Report_v1.pdf",
      str(entry))

saved_reviews_raw = appmod.load_document_reviews(saved_doc)
check("saved doc stores 2 reviews", len(saved_reviews_raw) == 2)
check("saved reviews are canonical (have anchors)",
      all((r.get("anchors") or {}) for r in saved_reviews_raw))
file_run = entry["latest_run_id"]
proj = appmod.document_reviews_for_run(saved_doc, file_run)
check("saved reviews project for the saved run", len(proj) == 2,
      json.dumps(proj)[:200])
check("saved review keeps comment text",
      any("check revenue wording" in (p.get("comment") or "") for p in proj))

# ---------------------------------------------------------------- Scenario 2
print("\n--- Scenario 2: FM select -> analysis -> import-reviews ---")
res = client.get(f"/api/documents/{saved_doc}/runs/{file_run}/file/report")
check("saved file downloadable", res.status_code == 200)
pdf_back = res.data

d2 = upload_base(pdf_back, "Report_v1.pdf")
doc2, run2 = d2["doc_id"], d2["run_id"]
check("fresh analysis starts with no reviews", len(get_reviews(doc2, run2)) == 0)

res = client.post(f"/api/documents/{doc2}/runs/{run2}/import-reviews",
                  json={"source_doc_id": saved_doc})
imp = res.get_json()
check("import-reviews succeeds", res.status_code == 200, str(imp))
check("import-reviews imported 2", imp.get("imported") == 2, str(imp))
loaded = get_reviews(doc2, run2)
check("loaded run shows 2 reviews", len(loaded) == 2, json.dumps(loaded)[:300])
check("loaded review text anchors to words",
      any("revenue" in (p.get("text") or "").lower() for p in loaded), json.dumps(loaded)[:300])
check("import is idempotent",
      client.post(f"/api/documents/{doc2}/runs/{run2}/import-reviews",
                  json={"source_doc_id": saved_doc}).get_json().get("imported") == 0)

# ---------------------------------------------------------------- Scenario 3
print("\n--- Scenario 3: Save with target overwrites (same name, new reviews) ---")
add_review(doc2, run2, "new", [10, 11], "new round comment")
res = client.post(f"/api/documents/{doc2}/runs/{run2}/save-file/new",
                  json={"target_doc_id": saved_doc})
save2 = res.get_json()
check("overwrite save 200", res.status_code == 200, str(save2))
check("overwrite keeps doc_id", save2.get("doc_id") == saved_doc)
check("overwrite keeps title Report_v1", save2.get("title") == "Report_v1")
items = fm_items()
check("still exactly one FM entry", len(items) == 1, json.dumps(items))
check("name still Report_v1.pdf", items[0].get("latest_filename") == "Report_v1.pdf")
entry = items[0]
proj = appmod.document_reviews_for_run(saved_doc, entry["latest_run_id"])
check("overwritten doc now has 3 reviews", len(proj) == 3, json.dumps(proj)[:200])

# ---------------------------------------------------------------- Scenario 4
print("\n--- Scenario 4: Save by duplicate title (no target) overwrites ---")
res = client.post(f"/api/documents/{doc2}/runs/{run2}/save-file/new",
                  json={"title": "Report_v1", "overwrite_existing": True})
save3 = res.get_json()
check("duplicate-title save 200 (overwrite)", res.status_code == 200, str(save3))
check("overwrote same doc", save3.get("doc_id") == saved_doc, str(save3))
check("no duplicate FM entries", len(fm_items()) == 1)

# ---------------------------------------------------------------- Scenario 5
print("\n--- Scenario 5: Save As new name with reviews ---")
res = client.post(f"/api/documents/{doc2}/runs/{run2}/save-file/new",
                  json={"title": "My Final Report"})
save4 = res.get_json()
check("save-as 201", res.status_code == 201, str(save4))
new_doc = save4.get("doc_id")
check("save-as new doc id differs", new_doc and new_doc != saved_doc)
items = fm_items()
check("FM now has 2 entries", len(items) == 2, json.dumps(items))
e2 = next((i for i in items if i["doc_id"] == new_doc), None)
check("save-as display name follows title", e2 and e2.get("latest_filename") == "My Final Report.pdf",
      str(e2))
proj = appmod.document_reviews_for_run(new_doc, e2["latest_run_id"])
check("save-as doc carries 3 reviews", len(proj) == 3)
res = client.post(f"/api/documents/{doc2}/runs/{run2}/save-file/new",
                  json={"title": "My Final Report"})
check("save-as duplicate name rejected 409", res.status_code == 409)

# ---------------------------------------------------------------- Scenario 6
print("\n--- Scenario 6: diff run, save old and new sides ---")
d3 = upload_base(PDF_V1, "Base_2026.pdf")
doc3, run3a = d3["doc_id"], d3["run_id"]
res = client.post(f"/api/documents/{doc3}/runs",
                  data={"pdf": (io.BytesIO(PDF_V2), "Update_2026.pdf")},
                  content_type="multipart/form-data")
assert res.status_code == 201, res.get_json()
run3b = res.get_json()["run_id"]
res = client.post(f"/api/documents/{doc3}/runs/{run3b}/diff")
check("diff run succeeds", res.status_code == 200, str(res.get_json())[:200])
add_review(doc3, run3b, "old", [0, 1], "old side note")
add_review(doc3, run3b, "new", [0, 1], "new side note")

res = client.post(f"/api/documents/{doc3}/runs/{run3b}/save-file/old",
                  json={"title": "Base_2026", "overwrite_existing": True})
so = res.get_json()
check("save old side 2xx", res.status_code in (200, 201), str(so))
res = client.post(f"/api/documents/{doc3}/runs/{run3b}/save-file/new",
                  json={"title": "Update_2026", "overwrite_existing": True})
sn = res.get_json()
check("save new side 2xx", res.status_code in (200, 201), str(sn))
items = fm_items()
names = sorted(i.get("latest_filename") for i in items)
check("no internal names leak (prev_report/report.pdf)",
      all(n not in ("prev_report.pdf", "report.pdf", "old.pdf", "new.pdf") for n in names),
      str(names))
eo = next((i for i in items if i["doc_id"] == so.get("doc_id")), None)
en = next((i for i in items if i["doc_id"] == sn.get("doc_id")), None)
check("old side saved as Base_2026.pdf", eo and eo.get("latest_filename") == "Base_2026.pdf", str(eo))
check("new side saved as Update_2026.pdf", en and en.get("latest_filename") == "Update_2026.pdf", str(en))
po = appmod.document_reviews_for_run(so["doc_id"], eo["latest_run_id"])
pn = appmod.document_reviews_for_run(sn["doc_id"], en["latest_run_id"])
check("old-side save carries only old-side review",
      len(po) == 1 and "old side note" in (po[0].get("comment") or ""), json.dumps(po)[:200])
check("new-side save carries only new-side review",
      len(pn) == 1 and "new side note" in (pn[0].get("comment") or ""), json.dumps(pn)[:200])

# ---------------------------------------------------------------- Scenario 7
print("\n--- Scenario 7: fresh local upload has zero reviews ---")
d4 = upload_base(PDF_V1, "Report_v1.pdf")
check("fresh upload shows no reviews", len(get_reviews(d4["doc_id"], d4["run_id"])) == 0)

print("\n==================================================")
if FAILURES:
    print(f"RESULT: {len(FAILURES)} FAILED -> " + ", ".join(FAILURES))
    sys.exit(1)
print("RESULT: ALL CHECKS PASSED")
shutil.rmtree(TMP, ignore_errors=True)
