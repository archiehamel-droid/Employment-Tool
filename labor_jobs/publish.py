"""Prepare the dashboard's dataset updates after a refresh: each dataset points
at its newly uploaded file, its update time is stamped, and the month in its
description moves to the new month.

The uploads themselves happen outside this program (they go to the dashboard's
file store). Give it what they returned and the datasets' current documents:

    python -m labor_jobs.publish --month 2026-11 --blobs blobs.json \\
        --datasets saved/datasets --versions versions.json --out updates

blobs.json maps dataset ids to uploaded urls ({"summary": "/_blob/..."}),
saved/datasets holds each dataset document as JSON (<id>.json), and
versions.json maps ids to the documents' current versions. It writes one
JSON file of fields per dataset into --out, and writes.json there: the list
of update writes, in batches of at most 50.
"""

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path

MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December"]


def month_label(month):
    """'2026-11' -> 'November 2026'."""
    year, m = month.split("-")
    return f"{MONTHS[int(m) - 1]} {year}"


def previous_month(month):
    year, m = map(int, month.split("-"))
    return f"{year - 1}-12" if m == 1 else f"{year}-{m - 1:02d}"


def retitle(text, old_month, new_month):
    """Move the data month in a description ("October 2026 postings") to the new
    month; other dates (OEWS's "May 2024") are left alone."""
    if not isinstance(text, str) or old_month == new_month:
        return text
    return re.sub(rf"\b{re.escape(month_label(old_month))}\b", month_label(new_month), text)


def updates(documents, blobs, month, old_month=None, now=None):
    """{id: fields to merge} for each dataset with a new upload."""
    old_month = old_month or previous_month(month)
    now = now or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    out = {}
    for ds_id, url in blobs.items():
        doc = documents.get(ds_id)
        if not doc or not isinstance(doc.get("source"), dict) or doc["source"].get("kind") != "file":
            continue
        fields = {"source": {**doc["source"], "url": url},
                  "updated": {"at": now, "by": "Claude"}, "edit": {"via": "claude", "at": now}}
        description = retitle(doc.get("description"), old_month, month)
        if description != doc.get("description"):
            fields["description"] = description
        out[ds_id] = fields
    return out


def main(argv=None):
    p = argparse.ArgumentParser(prog="labor_jobs.publish", description=__doc__.split("\n\n")[0])
    p.add_argument("--month", required=True, help="YYYY-MM of the new data")
    p.add_argument("--old-month", help="YYYY-MM the descriptions name now (default: the month before)")
    p.add_argument("--blobs", required=True)
    p.add_argument("--datasets", required=True, help="folder of saved dataset documents (<id>.json)")
    p.add_argument("--versions", required=True)
    p.add_argument("--out", required=True)
    args = p.parse_args(argv)
    blobs = json.loads(Path(args.blobs).read_text())
    versions = json.loads(Path(args.versions).read_text())
    documents = {f.stem: json.loads(f.read_text()) for f in Path(args.datasets).glob("*.json")}
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    writes = []
    for ds_id, fields in updates(documents, blobs, args.month, args.old_month).items():
        path = (out / f"{ds_id}.json").resolve()
        path.write_text(json.dumps(fields))
        writes.append({"op": "update", "collection": "datasets", "doc_id": ds_id, "file_path": str(path),
                       "if_version": versions[ds_id]})
    batches = [writes[i:i + 50] for i in range(0, len(writes), 50)]
    (out / "writes.json").write_text(json.dumps(batches))
    missing = sorted(set(blobs) - {w["doc_id"] for w in writes})
    print(f"{len(writes)} dataset updates in {len(batches)} batch(es) -> {out / 'writes.json'}")
    if missing:
        print("No file dataset to update for: " + ", ".join(missing))


if __name__ == "__main__":
    main()
