"""Fetch small official annotation files; large ViDoRe data stays in the HF cache."""

from __future__ import annotations

import shutil
import urllib.request
import argparse
import base64
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import parse_qs, urlparse


CRUD_RAG_URL = (
    "https://raw.githubusercontent.com/IAAR-Shanghai/CRUD_RAG/"
    "main/data/crud_split/split_merged.json"
)


def _copy_hf(repo_id: str, filename: str, target: Path) -> Path:
    from huggingface_hub import hf_hub_download

    source = Path(hf_hub_download(repo_id=repo_id, filename=filename, repo_type="dataset"))
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    return target


def fetch_annotations(data_dir: Path) -> dict[str, Path]:
    """Download official public annotations into the git-ignored eval data directory."""
    finance = _copy_hf(
        "PatronusAI/financebench",
        "financebench_merged.jsonl",
        data_dir / "financebench" / "financebench_merged.jsonl",
    )
    tatqa = _copy_hf(
        "next-tat/TAT-QA",
        "tatqa_dataset_dev.json",
        data_dir / "tatqa" / "tatqa_dataset_dev.json",
    )
    crud = data_dir / "crud-rag" / "split_merged.json"
    crud.parent.mkdir(parents=True, exist_ok=True)
    urllib.request.urlretrieve(CRUD_RAG_URL, crud)
    return {"financebench": finance, "tatqa": tatqa, "crud-rag": crud}


def fetch_financebench_pdfs(
    annotations: Path,
    pdf_dir: Path,
    *,
    downloader=urllib.request.urlretrieve,
    workers: int = 4,
) -> list[Path]:
    """Download each unique official FinanceBench source PDF once."""
    def is_pdf(path: Path) -> bool:
        if not path.exists():
            return False
        with path.open("rb") as stream:
            return stream.read(5) == b"%PDF-"

    rows = [
        json.loads(line)
        for line in annotations.read_text(encoding="utf-8-sig").splitlines()
        if line.strip()
    ]
    documents = {
        str(row["doc_name"]): str(row["doc_link"])
        for row in rows
        if row.get("doc_name") and row.get("doc_link")
    }
    pdf_dir.mkdir(parents=True, exist_ok=True)
    downloads: list[tuple[str, Path]] = []
    paths: list[Path] = []
    for doc_name, url in sorted(documents.items()):
        if not url.startswith("https://"):
            raise ValueError(f"refusing non-HTTPS FinanceBench document URL: {url}")
        target = pdf_dir / f"{doc_name}.pdf"
        paths.append(target)
        if is_pdf(target):
            continue
        downloads.append((url, target))

    def candidate_urls(url: str) -> list[str]:
        values = parse_qs(urlparse(url).query).get("pdfTarget", [])
        if not values:
            return [url]
        encoded = values[0]
        decoded = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)).decode()
        return [decoded, url]

    def download(item: tuple[str, Path]) -> tuple[Path, str | None]:
        url, target = item
        temporary = target.with_suffix(".pdf.part")
        last_error: Exception | None = None
        for candidate in candidate_urls(url):
            try:
                downloader(candidate, temporary)
                if not is_pdf(temporary):
                    raise ValueError("downloaded content is not a PDF")
                temporary.replace(target)
                return target, None
            except Exception as exc:  # each source is independently recoverable
                last_error = exc
                temporary.unlink(missing_ok=True)
        return target, f"{type(last_error).__name__}: {last_error}"

    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        results = list(executor.map(download, downloads))
    failures = [(path.name, error) for path, error in results if error]
    if failures:
        details = "; ".join(f"{name} ({error})" for name, error in failures)
        raise RuntimeError(f"failed to download {len(failures)} FinanceBench PDFs: {details}")
    return paths


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch official enterprise benchmark annotations")
    parser.add_argument("--data-dir", type=Path, default=Path("eval/data"))
    parser.add_argument(
        "--with-financebench-pdfs",
        action="store_true",
        help="also download all unique source PDFs required for full-document RAG",
    )
    parser.add_argument("--pdf-workers", type=int, default=4)
    args = parser.parse_args()
    fetched = fetch_annotations(args.data_dir)
    for name, path in fetched.items():
        print(f"{name}: {path}")
    if args.with_financebench_pdfs:
        pdfs = fetch_financebench_pdfs(
            fetched["financebench"],
            args.data_dir / "financebench" / "pdfs",
            workers=args.pdf_workers,
        )
        print(f"financebench-pdfs: {len(pdfs)} files")


if __name__ == "__main__":
    main()
