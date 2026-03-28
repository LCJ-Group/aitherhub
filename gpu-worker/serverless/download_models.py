#!/usr/bin/env python3
"""
Download all MuseTalk models at Docker build time.
This script is called from the Dockerfile to bake models into the image,
eliminating cold-start download failures.
"""
import os
import sys
import shutil

def main():
    models_dir = "/app/MuseTalk/models"
    
    # Ensure huggingface_hub is available
    try:
        from huggingface_hub import hf_hub_download, snapshot_download
    except ImportError:
        print("ERROR: huggingface_hub not installed")
        sys.exit(1)
    
    # 1. face-parse-bisent (CRITICAL - root cause of worker crashes)
    print("[1/6] Downloading face-parse-bisent...")
    fp_dir = os.path.join(models_dir, "face-parse-bisent")
    os.makedirs(fp_dir, exist_ok=True)
    path = hf_hub_download(
        repo_id="ManyOtherFunctions/face-parse-bisent",
        filename="79999_iter.pth"
    )
    shutil.copy2(path, os.path.join(fp_dir, "79999_iter.pth"))
    assert os.path.isfile(os.path.join(fp_dir, "79999_iter.pth")), "79999_iter.pth missing!"
    print(f"  [done] face-parse-bisent/79999_iter.pth ({os.path.getsize(os.path.join(fp_dir, '79999_iter.pth')) / 1024 / 1024:.1f} MB)")
    
    # 2. dwpose
    print("[2/6] Downloading dwpose...")
    dw_dir = os.path.join(models_dir, "dwpose")
    os.makedirs(dw_dir, exist_ok=True)
    path = hf_hub_download(
        repo_id="yzd-v/DWPose",
        filename="dw-ll_ucoco_384.onnx"
    )
    shutil.copy2(path, os.path.join(dw_dir, "dw-ll_ucoco_384.onnx"))
    print(f"  [done] dwpose/dw-ll_ucoco_384.onnx")
    
    # 3. sd-vae-ft-mse
    print("[3/6] Downloading sd-vae-ft-mse...")
    vae_dir = os.path.join(models_dir, "sd-vae-ft-mse")
    os.makedirs(vae_dir, exist_ok=True)
    snapshot_download(
        repo_id="stabilityai/sd-vae-ft-mse",
        local_dir=vae_dir
    )
    # Create sd-vae symlink
    sd_vae_link = os.path.join(models_dir, "sd-vae")
    if not os.path.exists(sd_vae_link):
        os.symlink(vae_dir, sd_vae_link)
    print(f"  [done] sd-vae-ft-mse + sd-vae symlink")
    
    # 4. musetalk weights (repo has musetalk/ at root, NOT models/musetalk/)
    print("[4/6] Downloading musetalk weights...")
    mt_dir = os.path.join(models_dir, "musetalk")
    os.makedirs(mt_dir, exist_ok=True)
    tmp_dir = "/tmp/musetalk_hf"
    snapshot_download(
        repo_id="TMElyralab/MuseTalk",
        local_dir=tmp_dir,
        allow_patterns=["musetalk/*"]
    )
    src = os.path.join(tmp_dir, "musetalk")
    if os.path.isdir(src):
        for f in os.listdir(src):
            shutil.copy2(os.path.join(src, f), os.path.join(mt_dir, f))
            print(f"    copied {f}")
    else:
        print(f"    WARNING: {src} not found, listing tmp_dir:")
        for root, dirs, files in os.walk(tmp_dir):
            for f in files:
                print(f"      {os.path.join(root, f)}")
    shutil.rmtree(tmp_dir, ignore_errors=True)
    print(f"  [done] musetalk weights ({len(os.listdir(mt_dir))} files)")
    
    # 5. musetalkV15 weights
    print("[5/6] Downloading musetalkV15 weights...")
    mt15_dir = os.path.join(models_dir, "musetalkV15")
    os.makedirs(mt15_dir, exist_ok=True)
    tmp_dir = "/tmp/musetalk_hf_v15"
    snapshot_download(
        repo_id="TMElyralab/MuseTalk",
        local_dir=tmp_dir,
        allow_patterns=["musetalkV15/*"]
    )
    src = os.path.join(tmp_dir, "musetalkV15")
    if os.path.isdir(src):
        for f in os.listdir(src):
            shutil.copy2(os.path.join(src, f), os.path.join(mt15_dir, f))
    shutil.rmtree(tmp_dir, ignore_errors=True)
    print(f"  [done] musetalkV15 weights")
    
    # 6. whisper model
    print("[6/6] Downloading whisper...")
    wh_dir = os.path.join(models_dir, "whisper")
    os.makedirs(wh_dir, exist_ok=True)
    snapshot_download(
        repo_id="openai/whisper-tiny",
        local_dir=wh_dir
    )
    print(f"  [done] whisper")
    
    # Final verification
    print("\n=== Verification ===")
    critical_files = [
        os.path.join(fp_dir, "79999_iter.pth"),
        os.path.join(fp_dir, "resnet18-5c106cde.pth"),
        os.path.join(dw_dir, "dw-ll_ucoco_384.onnx"),
    ]
    critical_dirs = [vae_dir, mt_dir, mt15_dir, wh_dir]
    
    all_ok = True
    for f in critical_files:
        if os.path.isfile(f):
            size_mb = os.path.getsize(f) / 1024 / 1024
            print(f"  [OK] {f} ({size_mb:.1f} MB)")
        else:
            print(f"  [MISSING] {f}")
            all_ok = False
    
    for d in critical_dirs:
        if os.path.isdir(d) and os.listdir(d):
            count = len(os.listdir(d))
            print(f"  [OK] {d} ({count} files)")
        else:
            print(f"  [MISSING/EMPTY] {d}")
            all_ok = False
    
    if all_ok:
        print("\nALL MUSETALK MODELS VERIFIED OK")
    else:
        print("\nERROR: Some models are missing!")
        sys.exit(1)

if __name__ == "__main__":
    main()
