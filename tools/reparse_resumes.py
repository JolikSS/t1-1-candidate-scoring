"""Explicit versioned re-extraction; source archives stay local and unchanged."""
import argparse
import hashlib
from pathlib import Path
import sys
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from db.mongo_store import MongoStore
from Parser.resume_parser import parse_resume_bytes
from Parser.vacancy_parser import SKILL_CATALOG


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--allow-cleaned-fallback", action="store_true")
    args = parser.parse_args()
    if not args.archive and not args.allow_cleaned_fallback:
        parser.error("Provide --archive or explicitly allow limited cleaned-text fallback")
    db = MongoStore()
    try:
        db.verify_and_prepare()
        records = db.list_resumes()
        catalog = list(dict.fromkeys([*SKILL_CATALOG, *(s for v in db.list_vacancies() for s in v.get("required_skills", []))]))
        seen = set(); updated = skipped = errors = fallback = 0
        if args.archive:
            with zipfile.ZipFile(args.archive) as archive:
                for member in archive.infolist():
                    suffix = Path(member.filename).suffix.lower()
                    if suffix not in {".pdf", ".txt"} or member.file_size > 20*1024*1024 or member.flag_bits & 1: continue
                    try:
                        raw = archive.read(member); digest = hashlib.sha256(raw).hexdigest()
                        old = db.get_resume_by_hash(digest)
                        if not old or digest in seen: continue
                        parsed = parse_resume_bytes("resume"+suffix, raw, catalog)
                        if not parsed.professional_text.strip(): errors += 1; continue
                        db.save_resume({**old, **parsed.to_anonymized_dict()}, reparse=True)
                        seen.add(digest); updated += 1
                    except (ValueError, zipfile.BadZipFile): errors += 1
        for old in records:
            if old["content_hash"] in seen: continue
            if not args.allow_cleaned_fallback: skipped += 1; continue
            # Reuse known section attribution; never reconstruct missing source fields.
            versions = db.parse_versions(old["resume_id"])
            current = next((v for v in versions if v["version_id"] == old.get("parse_version_id")), {})
            sections = current.get("sections") or {k: old.get(k, "") for k in ("title", "experience", "education")}
            sections = {**sections, "skills": sections.get("skills") or ", ".join(old.get("skills", []))}
            labels = {"title":"Job title", "experience":"Experience", "skills":"Skills", "education":"Education",
                      "summary":"Summary", "projects":"Projects", "achievements":"Achievements",
                      "certifications":"Certificates", "courses":"Courses"}
            text = "\n\n".join(labels[key]+"\n"+value for key,value in sections.items() if key in labels and value)
            parsed = parse_resume_bytes("resume.txt", text.encode(), catalog)
            details = parsed.parse_details
            details["source_mode"] = "cleaned_only"
            details["warnings"].append("original_unavailable_limited_reparse")
            # Preserve previously retained unstructured professional text as cleaned source.
            fields = parsed.to_anonymized_dict()
            fields["anonymized_text"] = old.get("anonymized_text", parsed.professional_text)
            db.save_resume({**old, **fields}, reparse=True); fallback += 1
        print({"reparsed_original": updated, "limited_fallback": fallback, "skipped": skipped, "errors": errors})
    finally: db.close()


if __name__ == "__main__": main()
