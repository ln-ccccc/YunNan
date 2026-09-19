"""mmseg 推理链路自检脚本。

用法：
    python verify_mmseg.py <data_path> [out_dir]

data_path 为包含待推理影像（png/tif/tiff/jpg/jpeg）的目录；
out_dir 缺省为 <data_path>/output。原先硬编码的外来开发机绝对路径已移除。
"""
import argparse
import os
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent))

from applications.interface.mmseg_inference_caller import call_mmseg_inference


def test_inference(data_path: str, out_dir: str, device: str = "cuda:0"):
    model_id = "cc-ln/CUGRS"

    try:
        files = [f for f in os.listdir(data_path) if f.lower().endswith((".png", ".tif", ".tiff", ".jpg", ".jpeg"))]
        print(f"Found {len(files)} images to process: {files}")
    except FileNotFoundError:
        print(f"Error: Test directory not found: {data_path}")
        return

    if not files:
        print("No image files found in test directory.")
        return

    try:
        print("Starting inference...")
        results = call_mmseg_inference(
            model_id=model_id,
            data_path=data_path,
            out_dir=out_dir,
            names=files,
            device=device,
            timeout=300,
        )
        print("Inference completed successfully!")
        print("Results:", results)
    except Exception as e:
        print(f"Inference failed with error: {e}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="mmseg 推理链路自检")
    parser.add_argument("data_path", help="包含待推理影像的目录")
    parser.add_argument("out_dir", nargs="?", default=None, help="输出目录，缺省 <data_path>/output")
    parser.add_argument("--device", default="cuda:0", help="推理设备，缺省 cuda:0")
    args = parser.parse_args()

    test_inference(args.data_path, args.out_dir or os.path.join(args.data_path, "output"), args.device)
