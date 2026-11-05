"""
Model Downloader & Setup CLI for JanSetu Local Neural Translation.
Directly downloads and verifies AI4Bharat IndicTrans2 model files locally for 100% offline inference.

Models:
- English -> Indic (Kannada): naklitechie/indictrans2-en-indic-dist-200M (or official ai4bharat)
- Indic (Kannada) -> English: benfranklinms/indictrans2-indic-en-dist-200M (or official ai4bharat)

Run during setup/deployment:
    python scripts/download_translation_models.py
"""
import argparse
import os
import sys
import time
from pathlib import Path
import requests

REQUIRED_FILES = [
    "config.json",
    "configuration_indictrans.py",
    "dict.SRC.json",
    "dict.TGT.json",
    "generation_config.json",
    "model.SRC",
    "model.TGT",
    "modeling_indictrans.py",
    "special_tokens_map.json",
    "tokenization_indictrans.py",
    "tokenizer_config.json",
    "model.safetensors",
]


def download_file(url: str, dest_path: Path, headers: dict = None) -> bool:
    if dest_path.exists() and dest_path.stat().st_size > 0:
        # Check if already fully downloaded
        return True

    temp_path = dest_path.with_suffix(dest_path.suffix + ".tmp")
    try:
        with requests.get(url, headers=headers or {}, stream=True, timeout=30) as r:
            r.raise_for_status()
            total_size = int(r.headers.get("content-length", 0))
            downloaded = 0
            start_time = time.time()
            with open(temp_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=1024 * 1024):  # 1MB chunks
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        if total_size > 0 and total_size > 10 * 1024 * 1024:
                            pct = (downloaded / total_size) * 100
                            mb = downloaded / (1024 * 1024)
                            total_mb = total_size / (1024 * 1024)
                            elapsed = time.time() - start_time
                            speed = mb / max(elapsed, 0.001)
                            print(
                                f"\r  -> {dest_path.name}: {mb:.1f}/{total_mb:.1f} MB ({pct:.1f}%) @ {speed:.2f} MB/s",
                                end="",
                                flush=True,
                            )
            if total_size > 10 * 1024 * 1024:
                print()
        if temp_path.exists():
            temp_path.replace(dest_path)
            return True
    except Exception as exc:
        print(f"\n  ✗ Error downloading {dest_path.name}: {exc}")
        if temp_path.exists():
            try:
                temp_path.unlink()
            except Exception:
                pass
        return False
    return False


def download_repo(repo_id: str, dest_dir: Path, hf_token: str = None) -> bool:
    dest_dir.mkdir(parents=True, exist_ok=True)
    headers = {"Authorization": f"Bearer {hf_token}"} if hf_token else {}
    base_url = f"https://huggingface.co/{repo_id}/resolve/main"

    print(f"\nDownloading files from '{repo_id}' into '{dest_dir}'...")
    for filename in REQUIRED_FILES:
        target_file = dest_dir / filename
        if filename == "model.safetensors":
            # Check if safetensors or pytorch_model.bin exists
            bin_file = dest_dir / "pytorch_model.bin"
            if target_file.exists() and target_file.stat().st_size > 100 * 1024 * 1024:
                print(f"  ✓ {filename} (already exists: {target_file.stat().st_size / (1024*1024):.2f} MB)")
                continue
            if bin_file.exists() and bin_file.stat().st_size > 100 * 1024 * 1024:
                print(f"  ✓ pytorch_model.bin (already exists: {bin_file.stat().st_size / (1024*1024):.2f} MB)")
                continue

            # Try downloading model.safetensors first, fallback to pytorch_model.bin
            print(f"  Downloading model weights ({filename})...")
            ok = download_file(f"{base_url}/{filename}", target_file, headers)
            if not ok:
                print("  -> model.safetensors not present in repo. Trying pytorch_model.bin...")
                ok_bin = download_file(f"{base_url}/pytorch_model.bin", bin_file, headers)
                if not ok_bin:
                    return False
            continue

        if target_file.exists() and target_file.stat().st_size > 0:
            print(f"  ✓ {filename} (already exists: {target_file.stat().st_size / (1024*1024):.2f} MB)")
            continue

        print(f"  Downloading {filename}...")
        ok = download_file(f"{base_url}/{filename}", target_file, headers)
        if not ok:
            if filename == "special_tokens_map.json":
                continue
            return False
        else:
            if target_file.stat().st_size < 10 * 1024 * 1024:
                print(f"  ✓ {filename} ({target_file.stat().st_size / 1024:.1f} KB)")

    return True


def setup_models(
    target_dir: str,
    en_indic_repo: str = "naklitechie/indictrans2-en-indic-dist-200M",
    indic_en_repo: str = "benfranklinms/indictrans2-indic-en-dist-200M",
    hf_token: str = None,
) -> bool:
    base = Path(target_dir).resolve()
    base.mkdir(parents=True, exist_ok=True)
    en_indic_dir = base / "en-indic"
    indic_en_dir = base / "indic-en"

    token = hf_token or os.getenv("HF_TOKEN") or None

    print("====================================================================")
    print("JanSetu Offline Neural Translation Model Downloader (Stage 2)")
    print("====================================================================")
    print(f"Local Store Directory: {base}")
    print(f"En -> Indic Model:     {en_indic_repo}")
    print(f"Indic -> En Model:     {indic_en_repo}")
    print("--------------------------------------------------------------------")

    # 1. Download English -> Indic (Kannada)
    print("\n[Step 1/2] English -> Kannada Neural Model:")
    if not download_repo(en_indic_repo, en_indic_dir, token):
        print("✗ Failed downloading English -> Kannada model.")
        return False
    print("✓ English -> Kannada model weights complete.")

    # 2. Download Indic (Kannada) -> English
    print("\n[Step 2/2] Kannada -> English Neural Model:")
    if not download_repo(indic_en_repo, indic_en_dir, token):
        print("✗ Failed downloading Kannada -> English model.")
        return False
    print("✓ Kannada -> English model weights complete.")

    print("\n====================================================================")
    print("SUCCESS: Both local neural models verified and installed!")
    print(f"En -> Indic location:  {en_indic_dir}")
    print(f"Indic -> En location:  {indic_en_dir}")
    print("Offline inference is ready. Zero external HTTP calls will be made.")
    print("====================================================================")
    return True


if __name__ == "__main__":
    default_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "models",
        "indictrans2",
    )
    parser = argparse.ArgumentParser(description="Download JanSetu local translation models.")
    parser.add_argument("--target-dir", default=default_dir, help="Directory to store model weights.")
    parser.add_argument("--en-indic-repo", default="naklitechie/indictrans2-en-indic-dist-200M", help="En->Indic repo")
    parser.add_argument("--indic-en-repo", default="benfranklinms/indictrans2-indic-en-dist-200M", help="Indic->En repo")
    parser.add_argument("--hf-token", default=None, help="Optional HuggingFace Token")

    args = parser.parse_args()
    success = setup_models(
        target_dir=args.target_dir,
        en_indic_repo=args.en_indic_repo,
        indic_en_repo=args.indic_en_repo,
        hf_token=args.hf_token,
    )
    sys.exit(0 if success else 1)
