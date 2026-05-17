#!/usr/bin/env python3
"""Extract individual OTF/TTF files from NotoSansCJK TTC collections.

This is needed because libass 0.15.0 on Azure App Service cannot reliably
resolve CJK glyphs from TTC (TrueType Collection) files. By extracting
individual font files, libass can find and use them correctly.

Usage: python extract_cjk_fonts.py [output_dir]
Default output: /tmp/aitherhub_fonts/
"""
import os
import sys

OUTPUT_DIR = sys.argv[1] if len(sys.argv) > 1 else "/tmp/aitherhub_fonts"

TTC_SEARCH_PATHS = [
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/noto-cjk/NotoSansCJK-Bold.ttc",
]

TTC_REGULAR_PATHS = [
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
]

# Face index 0 = JP variant in Noto Sans CJK TTC files
JP_FACE_INDEX = 0


def extract_face(ttc_path: str, face_index: int, output_path: str) -> bool:
    """Extract a single face from a TTC file and save as OTF."""
    try:
        from fontTools.ttLib import TTCollection
        ttc = TTCollection(ttc_path)
        if face_index >= len(ttc):
            print(f"  [WARN] Face index {face_index} out of range (total: {len(ttc)})")
            return False
        font = ttc[face_index]
        font.save(output_path)
        size_mb = os.path.getsize(output_path) / (1024 * 1024)
        print(f"  [OK] Extracted face {face_index} -> {output_path} ({size_mb:.1f} MB)")
        return True
    except ImportError:
        print("  [WARN] fontTools not available, trying alternative method...")
        return extract_face_alternative(ttc_path, face_index, output_path)
    except Exception as e:
        print(f"  [ERROR] Failed to extract: {e}")
        return False


def extract_face_alternative(ttc_path: str, face_index: int, output_path: str) -> bool:
    """Alternative: use pyftsubset or just copy the TTC and rename."""
    import subprocess
    try:
        # Try using pyftsubset to extract
        result = subprocess.run(
            ["pyftsubset", ttc_path, f"--font-number={face_index}",
             "--output-file=" + output_path, "--unicodes=*"],
            capture_output=True, text=True, timeout=120
        )
        if result.returncode == 0 and os.path.exists(output_path):
            size_mb = os.path.getsize(output_path) / (1024 * 1024)
            print(f"  [OK] Extracted via pyftsubset -> {output_path} ({size_mb:.1f} MB)")
            return True
    except Exception:
        pass
    
    print(f"  [WARN] Could not extract individual face, copying TTC as fallback")
    import shutil
    shutil.copy2(ttc_path, output_path)
    return True


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    print(f"[extract_cjk_fonts] Output dir: {OUTPUT_DIR}")
    
    extracted = False
    
    # Extract Bold JP
    for p in TTC_SEARCH_PATHS:
        if os.path.exists(p):
            print(f"[extract_cjk_fonts] Found Bold TTC: {p}")
            out = os.path.join(OUTPUT_DIR, "NotoSansCJK-JP-Bold.otf")
            if os.path.exists(out):
                print(f"  [SKIP] Already extracted: {out}")
                extracted = True
            else:
                extracted = extract_face(p, JP_FACE_INDEX, out)
            break
    
    # Extract Regular JP
    for p in TTC_REGULAR_PATHS:
        if os.path.exists(p):
            print(f"[extract_cjk_fonts] Found Regular TTC: {p}")
            out = os.path.join(OUTPUT_DIR, "NotoSansCJK-JP-Regular.otf")
            if os.path.exists(out):
                print(f"  [SKIP] Already extracted: {out}")
            else:
                extract_face(p, JP_FACE_INDEX, out)
            break
    
    if extracted:
        # Update fontconfig cache for the new directory
        try:
            import subprocess
            subprocess.run(["fc-cache", "-fv", OUTPUT_DIR], capture_output=True, timeout=30)
            print(f"[extract_cjk_fonts] fontconfig cache updated for {OUTPUT_DIR}")
        except Exception as e:
            print(f"[extract_cjk_fonts] fc-cache failed: {e}")
        
        print(f"[extract_cjk_fonts] Done. Fonts available at: {OUTPUT_DIR}")
    else:
        print("[extract_cjk_fonts] WARNING: No TTC files found to extract")
    
    return 0 if extracted else 1


if __name__ == "__main__":
    sys.exit(main())
